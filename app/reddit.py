# app/reddit.py

import os
import httpx
import logging
import asyncio
import aiohttp
from urllib.parse import urlparse, unquote
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dotenv import load_dotenv
from .utils import RateLimiter
from app.version import USER_AGENT
from functools import lru_cache

load_dotenv()

class RedditAPI:
    def __init__(self, client_manager):
        self.client_manager = client_manager
        self.client_id = os.getenv('REDDIT_CLIENT_ID')
        self.client_secret = os.getenv('REDDIT_CLIENT_SECRET')
        self.username = os.getenv('REDDIT_USERNAME')
        self.password = os.getenv('REDDIT_PASSWORD')
        
        if not all([self.client_id, self.client_secret, 
                   self.username, self.password]):
            raise ValueError("Missing Reddit API credentials")

        self.token = None
        self.token_expires = None
        self.token_manager = None
        self.rate_limiter = RateLimiter(calls_per_minute=60)
        self.redgifs_limiter = RateLimiter(calls_per_minute=30)
        self.client = None

    async def initialize(self):
        """Initialize the API client session"""
        from .services.token_manager import TokenManager
        
        if not self.client:
            self.client = httpx.AsyncClient(
                timeout=30.0,
                headers={"User-Agent": USER_AGENT}
            )
        
        self.token_manager = TokenManager()

    @lru_cache(maxsize=1000)
    async def get_redgifs_url(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """Cache RedGifs API responses to reduce API calls"""
        """
        Get the actual video URL from a RedGifs URL.
        Returns (url, error_message) tuple.
        """
        try:
            parsed = urlparse(url.lower())
            if not any(domain in parsed.netloc for domain in ['redgifs.com', 'v3.redgifs.com']):
                return None, "Not a RedGifs URL"

            # Handle i.redgifs.com direct URLs
            if parsed.netloc == 'i.redgifs.com':
                # If it's already a direct media URL, return it
                if any(parsed.path.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.mp4']):
                    return url, None

            # Clean path parts and remove known non-ID components
            path_parts = [p for p in parsed.path.split('/') if p]
            known_path_elements = {'watch', 'ifr', 'i', 'gifs', 'v3', 'v2'}
            
            # Find the actual ID (last path component that's not a known element)
            gif_id = None
            for part in reversed(path_parts):
                # Remove file extensions if present
                part = part.split('.')[0]
                if part.lower() not in known_path_elements:
                    gif_id = part
                    break

            if not gif_id:
                return None, f"Could not extract RedGifs ID from URL: {url}"

            #logging.info(f"Extracted RedGifs ID: {gif_id} from URL: {url}")

            # Get token
            await self.redgifs_limiter.acquire()
            token = self.token_manager.get_token()
            
            if not token:
                token = await self._fetch_new_redgifs_token()
                if not token:
                    return None, "Failed to get RedGifs auth token"

            # Make API request
            headers = {
                'Authorization': f'Bearer {token}',
                'User-Agent': USER_AGENT
            }
            
            try:
                response = await self.client.get(
                    f'https://api.redgifs.com/v2/gifs/{gif_id}',
                    headers=headers
                )
                
                if response.status_code == 404:
                    return None, "Content not found"
                elif response.status_code == 410:
                    return None, "Content permanently removed"
                
                response.raise_for_status()
                data = response.json()

                urls = data.get('gif', {}).get('urls', {})
                if not urls:
                    return None, "No URLs found in RedGifs response"

                # Try to get HD version first, then fall back to SD
                video_url = urls.get('hd') or urls.get('sd')
                if not video_url:
                    return None, "No video URL found in RedGifs response"

                return video_url, None

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 410:
                    return None, "Content permanently removed"
                elif e.response.status_code == 404:
                    return None, "Content not found"
                else:
                    return None, f"RedGifs API error: {str(e)}"

        except Exception as e:
            return None, f"Error processing RedGifs URL: {str(e)}"

    async def _fetch_new_redgifs_token(self) -> Optional[str]:
        """Fetch a new RedGifs token."""
        try:
            await self.redgifs_limiter.acquire()
            response = await self.client.get("https://api.redgifs.com/v2/auth/temporary")
            response.raise_for_status()
            data = response.json()
            
            token = data.get('token')
            if not token:
                raise ValueError("No token in RedGifs response")

            # Token expires in 24 hours, we'll set it to expire in 23 to be safe
            expires_at = datetime.now() + timedelta(hours=23)
            self.token_manager.save_token(token, expires_at)
            
            return token

        except Exception as e:
            logging.error(f"Error getting RedGifs token: {e}")
            self.token_manager.clear_token()
            return None

    async def get_token(self) -> str:
        if (self.token and self.token_expires and 
            datetime.now() < self.token_expires):
            return self.token

        auth = httpx.BasicAuth(self.client_id, self.client_secret)
        data = {
            'grant_type': 'password',
            'username': self.username,
            'password': self.password,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                'https://www.reddit.com/api/v1/access_token',
                auth=auth,
                data=data
            )
            response.raise_for_status()
            token_data = response.json()
            
            self.token = token_data['access_token']
            self.token_expires = (
                datetime.now() + 
                timedelta(seconds=token_data['expires_in'] - 60)
            )
            return self.token

    async def _make_request(self, method: str, url: str, **kwargs) -> Dict:
        await self.rate_limiter.acquire()
        
        headers = {
            'Authorization': f'Bearer {await self.get_token()}',
            **kwargs.get('headers', {})
        }
        kwargs['headers'] = headers

        try:
            response = await self.client.request(method, url, **kwargs)
            
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 60))
                logging.warning(f"Rate limit hit, waiting {retry_after} seconds")
                await asyncio.sleep(retry_after)
                response = await self.client.request(method, url, **kwargs)
                
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self.token = None
                headers['Authorization'] = f'Bearer {await self.get_token()}'
                response = await self.client.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json()
            raise

    async def get_user_info(self, username: str) -> Optional[Dict]:
        try:
            url = f'https://oauth.reddit.com/user/{username}/about'
            data = await self._make_request('GET', url)
            return data.get('data')
        except Exception as e:
            logging.error(f"Error fetching user info: {e}")
            return None

    async def get_subreddit_info(self, subreddit: str) -> Dict[str, Any]:
        subreddit = subreddit.lower()
        url = f'https://oauth.reddit.com/r/{subreddit}/about'
        try:
            data = await self._make_request('GET', url)
            return data.get('data', {})
        except Exception as e:
            logging.error(f"Error fetching subreddit info: {e}")
            raise

    async def get_posts(self, subreddit: str, sort: str = "hot", limit: int = 1000) -> List[Dict]:
        """Fetch posts from a subreddit."""
        posts = []
        after = None
        subreddit = subreddit.lower()
        
        while len(posts) < limit:
            params = {
                'limit': min(100, limit - len(posts)),
                'after': after
            }
            
            # Add t=all for top posts to get all time
            if sort == "top":
                params['t'] = 'all'
            
            try:
                url = f'https://oauth.reddit.com/r/{subreddit}/{sort}'
                data = await self._make_request('GET', url, params=params)
                
                for post in data['data']['children']:
                    try:
                        processed_post = await self._process_post(post['data'])
                        if processed_post.get('media_items') or processed_post['post_type'] == 'text':
                            posts.append(processed_post)
                    except Exception as e:
                        logging.error(f"Error processing post {post.get('data', {}).get('id', 'unknown')}: {e}")
                        continue
                
                after = data['data']['after']
                if not after or len(posts) >= limit:
                    break
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logging.error(f"Error fetching posts for r/{subreddit}: {e}")
                raise
                
        return posts[:limit]

    async def _process_post(self, post_data: Dict) -> Dict:
        """Process a post into our normalized format."""
        if not isinstance(post_data, dict):
            raise ValueError("Invalid post data")

        # Extract media items first
        media_items = []
        try:
            media_items = await self._extract_media_urls(post_data)  # Add await here
        except Exception as e:
            logging.error(f"Error extracting media from post {post_data.get('id')}: {e}")

        try:
            processed = {
                'id': post_data['id'],
                'subreddit': post_data['subreddit'],
                'author': post_data.get('author', '[deleted]'),
                'title': post_data['title'],
                'url': f"https://reddit.com{post_data['permalink']}",
                'created_utc': post_data['created_utc'],
                'score': post_data['score'],
                'post_type': 'text' if post_data.get('is_self') else (
                    'gallery' if post_data.get('is_gallery') else
                    'video' if post_data.get('is_video') else
                    'image' if media_items else
                    'unknown'
                ),
                'selftext': post_data.get('selftext', ''),
                'metadata': {
                    'permalink': post_data['permalink'],
                    'num_comments': post_data.get('num_comments', 0),
                    'upvote_ratio': post_data.get('upvote_ratio', 1.0),
                    'crosspost_parent': post_data.get('crosspost_parent_list', [{}])[0].get('id') if post_data.get('crosspost_parent_list') else None
                }
            }

            if media_items:
                processed['media_items'] = media_items
                if media_items[0].get('url'):
                    processed['media_url'] = media_items[0]['url']

            return processed

        except KeyError as e:
            logging.error(f"Missing required field in post data: {e}")
            logging.debug(f"Post data: {post_data}")
            raise
        except Exception as e:
            logging.error(f"Error processing post {post_data.get('id')}: {e}")
            logging.debug(f"Post data: {post_data}")
            raise

    async def _extract_media_urls(self, post_data: Dict) -> List[Dict]:
        """Extract all media URLs from a post with proper metadata."""
        media_items = []
        post_id = post_data.get('id', 'unknown')
        
        try:
            # Handle RedGifs URLs
            url = post_data.get('url', '')
            if 'redgifs.com' in url.lower():
                # Just store the original URL - media worker will handle fetching
                media_items.append({
                    'url': url,  # Store original URL
                    'media_type': 'video',
                    'position': 0
                })
                return media_items

            # Handle gallery posts
            if post_data.get('is_gallery'):
                gallery_metadata = post_data.get('gallery_data', {})
                media_metadata = post_data.get('media_metadata', {})
                
                if gallery_metadata and media_metadata:
                    for item in gallery_metadata.get('items', []):
                        media_id = item.get('media_id')
                        if media_id and media_id in media_metadata:
                            media_item = media_metadata[media_id]
                            
                            if media_item.get('status') == 'valid':
                                highest_quality = None
                                if 's' in media_item:
                                    highest_quality = media_item['s']
                                elif 'p' in media_item:
                                    highest_quality = sorted(
                                        media_item['p'], 
                                        key=lambda x: x.get('x', 0),
                                        reverse=True
                                    )[0]
                                    
                                if highest_quality:
                                    media_items.append({
                                        'url': highest_quality.get('u'),
                                        'width': highest_quality.get('x'),
                                        'height': highest_quality.get('y'),
                                        'media_type': media_item.get('e', 'image'),
                                        'position': item.get('position', len(media_items))
                                    })
                                
            # Handle video posts
            elif post_data.get('is_video'):
                if post_data.get('media') and post_data['media'].get('reddit_video'):
                    reddit_video = post_data['media']['reddit_video']
                    media_items.append({
                        'url': reddit_video.get('fallback_url'),
                        'width': reddit_video.get('width'),
                        'height': reddit_video.get('height'),
                        'duration': reddit_video.get('duration'),
                        'media_type': 'video',
                        'position': 0
                    })
                        
            # Handle direct image links and other media
            elif post_data.get('url'):
                url = post_data['url']
                
                # Handle direct image links
                if any(url.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                    preview = post_data.get('preview', {}).get('images', [{}])[0]
                    source = preview.get('source', {}) if preview else {}
                    
                    media_items.append({
                        'url': url,
                        'width': source.get('width'),
                        'height': source.get('height'),
                        'media_type': self._determine_media_type(url),
                        'position': 0
                    })
                
                # Handle Reddit hosted images with preview data
                elif post_data.get('preview') and post_data['preview'].get('images'):
                    image = post_data['preview']['images'][0]
                    if image.get('source'):
                        source = image['source']
                        media_items.append({
                            'url': source.get('url'),
                            'width': source.get('width'),
                            'height': source.get('height'),
                            'media_type': 'image',
                            'position': 0
                        })
            
                # Handle i.redd.it URLs
                elif 'i.redd.it' in url:
                    media_items.append({
                        'url': url,
                        'media_type': 'image',
                        'position': 0
                    })
                
                # Handle imgur links
                elif 'imgur.com' in url.lower():
                    if not any(url.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                        if '/a/' not in url and '/gallery/' not in url:
                            url = f"{url}.jpg"
                    media_items.append({
                        'url': url,
                        'media_type': 'image',
                        'position': 0
                    })
                    
        except Exception as e:
            logging.error(f"Error extracting media URLs from post {post_id}: {e}")
            logging.debug(f"Post data: {json.dumps(post_data, indent=2)}")
        
        # Clean up media items - ensure all URLs are unescaped and valid
        for item in media_items:
            if item.get('url'):
                item['url'] = unquote(item['url'].replace('&amp;', '&'))
                
        return media_items

    def _determine_media_type(self, url: str) -> str:
        """Determine media type from URL."""
        lower_url = url.lower()
        
        if any(lower_url.endswith(ext) for ext in ['.jpg', '.jpeg', '.png']):
            return 'image'
        elif any(lower_url.endswith(ext) for ext in ['.gif', '.gifv']):
            return 'gif'
        elif any(lower_url.endswith(ext) for ext in ['.mp4', '.webm']):
            return 'video'
        elif 'redgifs.com' in lower_url:
            return 'video'
        elif 'imgur.com' in lower_url:
            return 'image'
            
        return 'unknown'

    async def search_subreddits(self, query: str) -> List[Dict]:
        try:
            url = 'https://oauth.reddit.com/api/search_subreddits'
            params = {'query': query, 'include_over_18': True}
            
            data = await self._make_request('POST', url, data=params)
            
            subreddits = []
            for sub in data.get('subreddits', []):
                if isinstance(sub, dict):
                    description = sub.get('public_description', '').strip()
                    if description:
                        if len(description) > 150:
                            description = description[:150].rsplit(' ', 1)[0] + '...'
                    
                    subreddits.append({
                        'display_name': sub.get('name'),
                        'title': sub.get('title'),
                        'public_description': description,
                        'subscribers': sub.get('subscriber_count'),
                        'over18': sub.get('over_18', False),
                        'icon_img': sub.get('icon_img', '')
                    })
                    
            return subreddits
            
        except Exception as e:
            logging.error(f"Error searching subreddits: {e}")
            return []

    async def get_post_comments(self, post_id: str, subreddit: str, limit: int = 500, depth: int = 10) -> List[Dict]:
        """Fetch comments for a post with proper rate limiting."""
        try:
            url = f'https://oauth.reddit.com/r/{subreddit}/comments/{post_id}'
            params = {
                'limit': min(100, limit),  # Reddit's max per request
                'depth': min(depth, 10),   # Reasonable depth limit
                'sort': 'top',             # Get highest voted comments
                'threaded': True,          # Maintain conversation structure
                'comment_limit': limit     # Total comments to fetch
            }
            
            data = await self._make_request('GET', url, params=params)
            if not data or len(data) < 2:  # Reddit returns [post_data, comments_data]
                return []

            comments_data = data[1]['data']['children']
            processed = self._process_comments(comments_data, depth=0)
            
            # Sort by score while maintaining thread structure
            return sorted(
                processed,
                key=lambda x: (x.get('score', 0), -x.get('depth', 0)),
                reverse=True
            )[:limit]

        except Exception as e:
            logging.error(f"Error fetching comments for post {post_id}: {e}")
            return []

    def _process_comments(self, comments: List, depth: int = 0, path: str = '') -> List[Dict]:
        """Process comment tree into flat structure with depth/path info."""
        processed = []
        for comment in comments:
            if comment.get('kind') != 't1':  # Skip non-comment objects
                continue
                
            data = comment.get('data', {})
            if data.get('body') is None:  # Skip deleted/removed comments
                continue

            comment_id = data.get('id')
            comment_path = f"{path}/{comment_id}" if path else comment_id
            
            processed_comment = {
                'id': comment_id,
                'post_id': data.get('link_id', '').split('_')[1],
                'parent_id': data.get('parent_id', ''),  # Will be cleaned in task processor
                'author': data.get('author'),
                'body': data.get('body'),
                'created_utc': data.get('created_utc'),
                'score': data.get('score'),
                'edited': data.get('edited'),
                'depth': depth,
                'path': comment_path
            }
            
            processed.append(processed_comment)
            
            # Process replies if they exist and we haven't hit max depth
            if depth < 5 and data.get('replies'):
                try:
                    replies = data['replies']['data']['children']
                    processed.extend(self._process_comments(
                        replies,
                        depth + 1,
                        comment_path
                    ))
                except (KeyError, TypeError):
                    pass
                    
        return processed

    async def shutdown(self):
        """Shutdown API client and cleanup resources"""
        logging.info("Shutting down Reddit API client...")
        try:
            if self.client:
                await self.client.aclose()
                self.client = None
            logging.info("Reddit API client shutdown complete")
        except Exception as e:
            logging.error(f"Error during Reddit API shutdown: {e}")