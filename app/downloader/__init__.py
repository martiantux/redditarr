# app/downloader/__init__.py

import os
import logging
import time
import aiohttp
import asyncio
import hashlib
from pathlib import Path
from typing import Any, Optional, Dict, Tuple
import json
from app.version import USER_AGENT

from app.utils import RateLimiter, PathManager

class Downloader:
    def __init__(self):
        self.db_pool = None
        self.client_manager = None
        self.imgur_limiter = RateLimiter(
            calls_per_minute=30,
            random_delay=True,
            burst_allowance=2
        )
        self.redgifs_limiter = RateLimiter(
            calls_per_minute=30,
            random_delay=True,
            burst_allowance=5
        )
        self.reddit_limiter = RateLimiter(
            calls_per_minute=60,
            random_delay=True,
            burst_allowance=5
        )
        
        self.path_manager = PathManager()
        self.session = None
        self.headers = {
            "User-Agent": USER_AGENT,
        }
        self._initialized = False

    def set_client_manager(self, client_manager):
        """Set the client manager after initialization"""
        self.client_manager = client_manager

    def set_db_pool(self, db_pool):
        """Set the database pool after initialization"""
        self.db_pool = db_pool

    async def initialize(self):
        """Initialize the downloader with required connections"""
        if self._initialized:
            return
            
        if not self.session:
            self.session = aiohttp.ClientSession(headers=self.headers)
            logging.info("Created new aiohttp session for downloader")
            
        self._initialized = True
        logging.info("Downloader initialized successfully")

    async def ensure_initialized(self):
        """Ensure downloader is initialized before use"""
        if not self._initialized:
            await self.initialize()

    async def close(self):
        """Close all connections and cleanup"""
        if self.session:
            await self.session.close()
            self.session = None
        self._initialized = False
        logging.info("Downloader shutdown complete")

    def _safe_json_dumps(self, data: Any) -> str:
        try:
            if isinstance(data, str):
                return data
            return json.dumps(data)
        except (TypeError, ValueError):
            logging.warning(f"Failed to serialize data to JSON: {data}")
            return '{}'

    def _calculate_file_hash(self, file_path: str) -> str:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    def _guess_mime_type(self, file_path: str) -> str:
        """Guess MIME type based on file extension"""
        ext = Path(file_path).suffix.lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.mp4': 'video/mp4',
            '.webm': 'video/webm'
        }
        return mime_types.get(ext, 'application/octet-stream')

    def _determine_service_type(self, url: str) -> str:
        """Determine the service type from a URL."""
        url_lower = url.lower()
        
        # Discontinued services
        if 'gfycat.com' in url_lower:
            return 'gfycat'
        if 'giphy.com' in url_lower:
            return 'giphy'
            
        # RedGifs detection
        if any(domain in url_lower for domain in [
            'redgifs.com', 'v3.redgifs.com', 'api.redgifs.com'
        ]):
            return 'redgifs'
            
        # Imgur detection
        if 'imgur.com' in url_lower:
            return 'imgur'
            
        # Reddit detection
        if any(domain in url_lower for domain in [
            'i.redd.it', 'v.redd.it', 'preview.redd.it'
        ]):
            return 'reddit'
            
        return 'unknown'

    async def process_post(self, post: Dict, subreddit: str) -> bool:
        await self.ensure_initialized()

        subreddit = subreddit.lower()
        try:
            # Special handling for text posts - mark as processed but not downloaded
            if post.get('post_type') == 'text':
                async with self.db_pool.connection() as db:
                    await db.execute("""
                        UPDATE posts 
                        SET media_status = 'not_applicable',
                            error = NULL
                        WHERE id = ?
                    """, (post['id'],))
                return True  # Return true as processing succeeded

            media_items = post.get('media_items', [])
            if isinstance(media_items, str):
                try:
                    media_items = json.loads(media_items)
                except json.JSONDecodeError:
                    media_items = []
            
            if not media_items and post.get('media_url'):
                media_items = [{
                    'url': post['media_url'],
                    'media_type': 'image' if any(post['media_url'].lower().endswith(ext) 
                        for ext in ['.jpg', '.jpeg', '.png', '.gif']) else 'video',
                    'position': 0
                }]

            if not media_items:
                logging.warning(f"No media items found for post {post['id']}")
                await self.db_pool.mark_post_downloaded(
                    post_id=post['id'],
                    subreddit=subreddit,
                    success=False,
                    error="No media items found",
                    media_status='error'
                )
                return False

            all_success = True
            any_permanent_failure = False
            for idx, media_item in enumerate(media_items):
                media_url = media_item.get('url')
                if not media_url:
                    continue

                service_type = self._determine_service_type(media_url)
                final_path = self.path_manager.get_media_path(post['id'], media_url, subreddit, position=idx)
                temp_path = self.path_manager.get_temp_path(post['id'], position=idx)

                success, error = await self.download_with_retry(media_url, temp_path, service_type)
                
                if success:
                    try:
                        # Calculate file hashes before moving
                        file_hash = self._calculate_file_hash(temp_path)
                        quick_hash = file_hash[:16]  # First 16 chars for quick matching
                        
                        os.makedirs(os.path.dirname(str(final_path)), exist_ok=True)
                        os.rename(str(temp_path), str(final_path))
                        
                        # Store file metadata including hashes
                        async with self.db_pool.connection() as db:
                            # Store in media_deduplication table
                            await db.execute("""
                                INSERT INTO media_deduplication 
                                (canonical_hash, quick_hash, canonical_path, first_seen_timestamp,
                                total_size, mime_type)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (
                                file_hash,
                                quick_hash,
                                str(final_path),
                                int(time.time()),
                                os.path.getsize(str(final_path)),
                                self._guess_mime_type(str(final_path))
                            ))

                            # Update post_media record
                            await db.execute("""
                                UPDATE post_media 
                                SET download_path = ?,
                                    downloaded = 1,
                                    downloaded_at = ?,
                                    error = NULL,
                                    media_status = 'downloaded',
                                    last_attempt = ?
                                WHERE post_id = ? AND position = ?
                            """, (
                                str(final_path),
                                int(time.time()),
                                int(time.time()),
                                post['id'],
                                idx
                            ))
                            
                    except Exception as e:
                        logging.error(f"Error processing successful download for post {post['id']}, item {idx}: {e}")
                        all_success = False
                else:
                    all_success = False
                    if error and any(x in error.lower() for x in [
                        'removed', '410', '404', 'permanently removed', 'content not found'
                    ]):
                        any_permanent_failure = True
                    logging.error(f"Failed to download media item {idx} for post {post['id']}: {error}")
                    async with self.db_pool.connection() as db:
                        await db.execute("""
                            UPDATE post_media 
                            SET error = ?,
                                download_attempts = download_attempts + 1,
                                last_attempt = ?,
                                media_status = 'error'
                            WHERE post_id = ? AND position = ?
                        """, (
                            error,
                            int(time.time()),
                            post['id'],
                            idx
                        ))

            # Update final post status
            if all_success:
                await self.db_pool.mark_post_downloaded(
                    post_id=post['id'],
                    subreddit=subreddit,
                    success=True,
                    media_status='downloaded'
                )
            else:
                status = 'permanently_removed' if any_permanent_failure else 'error'
                await self.db_pool.mark_post_downloaded(
                    post_id=post['id'],
                    subreddit=subreddit,
                    success=False,
                    error='Some media items failed to download',
                    media_status=status
                )

            return all_success

        except Exception as e:
            logging.error(f"Error processing post {post['id']}: {e}")
            await self.db_pool.mark_post_downloaded(
                post_id=post['id'],
                subreddit=subreddit,
                success=False,
                error=str(e),
                media_status='error'
            )
            return False

    async def download_with_retry(self, url: str, save_path: str, service: str, 
                                max_retries: int = 3, initial_delay: float = 1.0) -> Tuple[bool, Optional[str]]:
        await self.ensure_initialized()
        delay = initial_delay
        last_error = None
        permanent_failures = [
            'removed',
            '410',
            '404', 
            '503 bytes',
            'Content permanently removed',
            'Content not found'
        ]
        
        for attempt in range(max_retries):
            try:
                success, error = await self.download_file(url, save_path, service)
                if success:
                    return True, None
                    
                if error and any(x in error.lower() for x in permanent_failures):
                    # Update database to mark as permanently removed
                    async with self.db_pool.connection() as db:
                        await db.execute("""
                            UPDATE post_media 
                            SET media_status = 'permanently_removed',
                                error = ?,
                                last_attempt = ?
                            WHERE media_url = ?
                        """, (error, int(time.time()), url))
                        await db.commit()
                    return False, error
                    
                last_error = error
                
            except Exception as e:
                last_error = str(e)
                if any(x in str(e).lower() for x in permanent_failures):
                    return False, str(e)
            
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2
        
        return False, f"Failed after {max_retries} attempts: {last_error}"

    async def download_file(self, url: str, save_path: str, service: str = 'reddit') -> Tuple[bool, Optional[str]]:
        await self.ensure_initialized()
        #logging.info(f"Starting download from {url} ({service} service)")
        
        try:
            # Handle discontinued services first
            if 'gfycat.com' in url.lower():
                return False, "Gfycat service discontinued"
            if 'giphy.com' in url.lower():
                return False, "Giphy links no longer supported"

            # Normalize URL
            url = url.replace('&amp;', '&')
            
            # Handle URL-specific logic before downloading
            url = await self._prepare_url(url, service)
            if not url:
                return False, "Could not process URL"

            os.makedirs(os.path.dirname(save_path), exist_ok=True)

            # Apply appropriate rate limiting
            if service == 'imgur':
                await self.imgur_limiter.acquire()
            elif service == 'redgifs':
                await self.redgifs_limiter.acquire()
            else:
                await self.reddit_limiter.acquire()

            async with self.session.get(url) as response:
                #logging.info(f"Download response status: {response.status} for {url}")
                
                # Handle common error responses
                if response.status == 404:
                    return False, "Media no longer available on server"
                elif response.status == 410:
                    return False, "Media permanently removed"
                elif response.status == 429:
                    return False, f"{service} rate limit exceeded - will retry"
                elif response.status == 403:
                    return False, "Access forbidden - media may be private or removed"
                    
                response.raise_for_status()
                
                content_length = int(response.headers.get('content-length', 0))
                content_type = response.headers.get('content-type', '').lower()
                #logging.info(f"Content length: {content_length} bytes, type: {content_type} for {url}")
                
                # Handle known error cases
                if service == 'imgur' and content_length == 503:
                    return False, "Imgur removed image (503 bytes)"
                
                # Don't download suspiciously small files unless they're known GIFs
                if content_length > 0 and content_length < 1024 and 'image/gif' not in content_type:
                    return False, f"Suspiciously small file ({content_length} bytes)"

                with open(save_path, 'wb') as f:
                    bytes_written = 0
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)
                        bytes_written += len(chunk)
                    #logging.info(f"Wrote {bytes_written} bytes to {save_path}")

                # Post-download validation
                final_size = os.path.getsize(save_path)
                if final_size == 0:
                    os.remove(save_path)
                    return False, "Empty file downloaded"
                
                if service == 'imgur' and final_size == 503:
                    os.remove(save_path)
                    return False, "Imgur removed image (503 bytes)"

                # Validate file contents
                is_valid, error = self._validate_downloaded_file(save_path, content_type)
                if not is_valid:
                    os.remove(save_path)
                    return False, error

                #logging.info(f"Successfully downloaded {url} to {save_path}")
                return True, None

        except Exception as e:
            logging.error(f"Error downloading {url}: {e}", exc_info=True)
            if os.path.exists(save_path):
                os.remove(save_path)
            return False, str(e)

    async def _prepare_url(self, url: str, service: str) -> Optional[str]:
        """Prepare URL for downloading by handling service-specific logic."""
        try:
            # Handle RedGifs
            if service == 'redgifs' and any(domain in url.lower() 
                for domain in ['redgifs.com', 'v3.redgifs.com']):
                if not self.client_manager:
                    logging.error("Cannot process RedGifs URL without client_manager")
                    return None
                
                from app.reddit import RedditAPI
                reddit_api = RedditAPI(self.client_manager)
                await reddit_api.initialize()
                
                video_url, error = await reddit_api.get_redgifs_url(url)
                if error:
                    logging.error(f"RedGifs error: {error}")
                    return None
                
                if video_url:
                    #logging.info(f"Retrieved actual video URL from RedGifs: {video_url}")
                    return video_url
                return None

            # Handle Imgur
            if service == 'imgur':
                # Skip albums and galleries
                if '/a/' in url or '/gallery/' in url:
                    logging.info(f"Skipping Imgur album/gallery: {url}")
                    return None
                
                # Append .jpg to direct imgur.com links if they don't have an extension
                if 'imgur.com' in url and not any(url.lower().endswith(ext) 
                    for ext in ['.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm']):
                    url = f"{url}.jpg"

            # Handle Reddit URLs
            if service == 'reddit':
                if 'preview.redd.it' in url:
                    # Convert preview URL to i.redd.it
                    try:
                        # Extract base filename without query params
                        base_name = url.split('?')[0].split('/')[-1]
                        if any(base_name.lower().endswith(ext) for ext in 
                            ['.jpg', '.jpeg', '.png', '.gif']):
                            direct_url = f"https://i.redd.it/{base_name}"
                            logging.info(f"Converting preview URL to direct URL: {direct_url}")
                            return direct_url
                    except Exception as e:
                        logging.warning(f"Failed to convert preview URL, using original: {e}")
                    
                    # If conversion fails, remove query parameters
                    return url.split('?')[0]
                
                elif 'v.redd.it' in url:
                    # v.redd.it URLs might need special handling for DASH content
                    # For now, we'll use the direct URL
                    return url

            return url

        except Exception as e:
            logging.error(f"Error preparing URL {url}: {e}")
            return None

    def _validate_downloaded_file(self, file_path: str, content_type: str) -> Tuple[bool, Optional[str]]:
        """Validate downloaded file based on content type and file signatures."""
        try:
            with open(file_path, 'rb') as f:
                header = f.read(12)  # Read enough for all our file types
                
            # Map of file signatures
            signatures = {
                'jpeg': [
                    (b'\xFF\xD8\xFF\xE0', 0),  # JPEG/JFIF
                    (b'\xFF\xD8\xFF\xE1', 0),  # JPEG/Exif
                    (b'\xFF\xD8\xFF\xDB', 0),  # JPEG/Raw
                ],
                'png': [(b'\x89PNG\r\n\x1A\n', 0)],
                'gif': [
                    (b'GIF87a', 0),
                    (b'GIF89a', 0),
                ],
                'mp4': [
                    (b'ftyp', 4),  # MP4/ISO Base Media
                    (b'mdat', 4),  # MP4 data
                ],
                'webm': [(b'\x1A\x45\xDF\xA3', 0)]  # WebM
            }

            # Check content type first
            if 'image/jpeg' in content_type:
                valid_sigs = signatures['jpeg']
            elif 'image/png' in content_type:
                valid_sigs = signatures['png']
            elif 'image/gif' in content_type:
                valid_sigs = signatures['gif']
            elif 'video/mp4' in content_type:
                valid_sigs = signatures['mp4']
            elif 'video/webm' in content_type:
                valid_sigs = signatures['webm']
            else:
                # If no specific content type, check all signatures
                valid_sigs = [sig for sigs in signatures.values() for sig in sigs]

            # Check file against signatures
            for signature, offset in valid_sigs:
                if header[offset:offset + len(signature)] == signature:
                    return True, None

            return False, f"Invalid file signature for content type {content_type}"

        except Exception as e:
            logging.error(f"Error validating file {file_path}: {e}")
            return False, f"Validation error: {str(e)}"

# Initialize global downloader instance
downloader = Downloader()