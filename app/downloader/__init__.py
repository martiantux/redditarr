# app/downloader/__init__.py

import os
import logging
import time
import aiohttp
import asyncio
import aiosqlite
import hashlib
from pathlib import Path
import shutil
from typing import Any, Optional, Dict, Tuple
import json
from app.core.version import USER_AGENT
from app.core.paths import PathManager
from app.core.utils import RateLimiter

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

    async def _check_for_duplicates(self, file_hash: str, quick_hash: str, subreddit: str, post_id: str) -> Tuple[bool, Optional[Dict]]:
        """Check if this media file already exists in the same subreddit."""
        try:
            async with self.db_pool.connection() as db:
                # First check by quick hash (faster)
                db.row_factory = aiosqlite.Row
                cursor = await db.execute("""
                    SELECT d.canonical_hash, d.canonical_path, d.first_seen_post_id, 
                        m.download_path, p.id, p.score, p.created_utc
                    FROM media_deduplication d
                    JOIN post_media m ON m.post_id = d.first_seen_post_id
                    JOIN posts p ON m.post_id = p.id
                    WHERE d.quick_hash = ?
                    AND p.subreddit = ?
                    AND p.id != ?
                """, (quick_hash, subreddit, post_id))
                
                potential_match = await cursor.fetchone()
                
                if not potential_match:
                    return False, None
                    
                # If quick hash matches, verify with full hash
                if potential_match and potential_match['canonical_hash'] == file_hash:
                    return True, dict(potential_match)
                    
            return False, None
        except Exception as e:
            logging.error(f"Error checking for duplicates: {e}")
            return False, None

    async def _handle_duplicate(self, current_post: Dict, canonical_post: Dict, temp_path: str, url_path: str, position: int, file_hash: str):
        """Handle duplicate media according to chosen strategy."""
        try:
            # Get deduplication strategy from config
            async with self.db_pool.connection() as db:
                cursor = await db.execute("SELECT value FROM config WHERE key = 'subreddit_duplicate_strategy'")
                result = await cursor.fetchone()
                strategy = result[0] if result else 'highest_voted'
            
            # Determine which post to keep based on strategy
            keep_current = False
            if strategy == 'highest_voted':
                keep_current = current_post.get('score', 0) > canonical_post.get('score', 0)
            elif strategy == 'oldest':
                keep_current = current_post.get('created_utc', 0) < canonical_post.get('created_utc', 0)
            
            if keep_current:
                # Keep the current post's media and replace the canonical one
                canonical_path = canonical_post.get('canonical_path')
                
                # Replace the file
                if canonical_path and os.path.exists(canonical_path):
                    os.remove(canonical_path)  # Remove old file
                
                new_final_path = self.path_manager.get_media_path(
                    current_post['id'], 
                    current_post.get('media_url', ''), 
                    current_post['subreddit'], 
                    position=position
                )
                
                # Ensure directory exists
                os.makedirs(os.path.dirname(str(new_final_path)), exist_ok=True)
                
                # Move temp file to final location
                shutil.copy2(str(temp_path), str(new_final_path))
                
                # Update database to reflect new canonical file
                async with self.db_pool.connection() as db:
                    # Update media_deduplication to point to new canonical file
                    await db.execute("""
                        UPDATE media_deduplication
                        SET canonical_path = ?,
                            first_seen_post_id = ?,
                            total_size = ?
                        WHERE canonical_hash = ?
                    """, (
                        str(new_final_path),
                        current_post['id'],
                        os.path.getsize(str(temp_path)),
                        file_hash
                    ))
                    
                    # Update all post_media records that pointed to old canonical file
                    await db.execute("""
                        UPDATE post_media
                        SET download_path = ?
                        WHERE download_path = ? AND post_id != ?
                    """, (
                        url_path,
                        canonical_post.get('download_path'),
                        current_post['id']
                    ))
                    
                    # Update the current post's media record
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
                        url_path,
                        int(time.time()),
                        int(time.time()),
                        current_post['id'],
                        position
                    ))
                    
                    # Mark the previous canonical post as a duplicate if it wasn't already
                    await db.execute("""
                        INSERT INTO media_links
                        (post_id, canonical_hash, symlink_path, created_timestamp, is_crosspost)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                    """, (
                        canonical_post['id'],
                        file_hash,
                        canonical_post.get('download_path'),
                        int(time.time()),
                        0  # Not a crosspost, same subreddit
                    ))
                    
                logging.info(f"Replaced canonical file for hash {file_hash[:8]} with higher {strategy} post {current_post['id']}")
                    
            else:
                # Keep the canonical post's media, discard the temp file
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                    
                # Mark current post as a duplicate
                async with self.db_pool.connection() as db:
                    # Record the link to the canonical file
                    await db.execute("""
                        INSERT INTO media_links
                        (post_id, canonical_hash, symlink_path, created_timestamp, is_crosspost)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        current_post['id'],
                        file_hash,
                        url_path,
                        int(time.time()),
                        0  # Not a crosspost, same subreddit
                    ))
                    
                    # Update post_media to point to canonical file
                    await db.execute("""
                        UPDATE post_media 
                        SET download_path = ?,
                            downloaded = 1,
                            downloaded_at = ?,
                            error = NULL,
                            media_status = 'duplicate',
                            last_attempt = ?
                        WHERE post_id = ? AND position = ?
                    """, (
                        canonical_post.get('download_path'),  # Use canonical media path
                        int(time.time()),
                        int(time.time()),
                        current_post['id'],
                        position
                    ))
                    
                    # Update the deduplication record to increment duplicate count
                    await db.execute("""
                        UPDATE media_deduplication
                        SET duplicate_count = duplicate_count + 1
                        WHERE canonical_hash = ?
                    """, (file_hash,))
                    
                logging.info(f"Marked post {current_post['id']} as duplicate of {canonical_post['id']} based on {strategy} strategy")
                
        except Exception as e:
            logging.error(f"Error handling duplicate: {e}")
            # Clean up temp file if it exists
            if os.path.exists(temp_path):
                os.remove(temp_path)

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
                
                # Get the filesystem path for file operations
                final_path = self.path_manager.get_media_path(post['id'], media_url, subreddit, position=idx)
                
                # Get the URL path for storing in the database
                url_path = self.path_manager.get_media_url_path(post['id'], media_url, subreddit, position=idx)
                
                temp_path = self.path_manager.get_temp_path(post['id'], position=idx)

                success, error = await self.download_with_retry(media_url, temp_path, service_type)
                
                if success:
                    try:
                        # Calculate file hashes before moving
                        file_hash = self._calculate_file_hash(temp_path)
                        quick_hash = file_hash[:16]  # First 16 chars for quick matching
                        
                        # Check for duplicates in the same subreddit
                        is_duplicate, canonical_post = await self._check_for_duplicates(file_hash, quick_hash, subreddit, post['id'])
                        
                        if is_duplicate:
                            # Handle duplicate according to strategy
                            logging.warning(f"found duplicate for post {post['id']}")
                            await self._handle_duplicate(post, canonical_post, temp_path, url_path, idx, file_hash)
                        else:
                            # Not a duplicate, proceed as usual
                            os.makedirs(os.path.dirname(str(final_path)), exist_ok=True)
                            
                            shutil.copy2(str(temp_path), str(final_path))
                            os.remove(str(temp_path))  # Clean up the temp file
                            
                            # Store file metadata including hashes
                            async with self.db_pool.connection() as db:
                                # Store in media_deduplication table
                                await db.execute("""
                                    INSERT INTO media_deduplication 
                                    (canonical_hash, quick_hash, canonical_path, first_seen_timestamp,
                                    total_size, mime_type, first_seen_post_id)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    file_hash,
                                    quick_hash,
                                    str(final_path),
                                    int(time.time()),
                                    os.path.getsize(str(final_path)),
                                    self._guess_mime_type(str(final_path)),
                                    post['id']
                                ))

                                # Update post_media record with URL path for browser access
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
                                    url_path,  # Store the URL path in the database
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