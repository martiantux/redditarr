# app/utils.py

import asyncio
import random
import time
import os
import logging
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlparse
import mimetypes
from datetime import datetime

class RateLimiter:
    """
    A flexible rate limiter for controlling API request frequencies.
    
    This implementation uses an async lock to ensure thread safety and provides
    features like random delay jitter and burst allowance to better handle real-world
    API interaction patterns.
    
    Attributes:
        delay: Base delay between requests in seconds
        burst_allowance: Number of requests allowed to bypass delay
        random_delay: Whether to add random jitter to delays
    """
    def __init__(self, 
                 calls_per_minute: int = 60, 
                 random_delay: bool = True,
                 burst_allowance: Optional[int] = None):
        self.delay = 60.0 / calls_per_minute
        self.last_call = 0
        self._lock = asyncio.Lock()
        self.random_delay = random_delay
        self.burst_allowance = burst_allowance
        self.burst_tokens = burst_allowance if burst_allowance else 0

    async def acquire(self):
        """
        Wait until a request is allowed according to the rate limit.
        Handles burst allowance and random jitter if configured.
        """
        async with self._lock:
            now = time.time()
            
            # Replenish burst tokens if we have an allowance
            if self.burst_allowance:
                tokens_to_add = int((now - self.last_call) / self.delay)
                self.burst_tokens = min(self.burst_allowance, 
                                     self.burst_tokens + tokens_to_add)
            
            # Calculate required delay
            if self.burst_tokens > 0:
                self.burst_tokens -= 1
                required_delay = 0
            else:
                required_delay = max(0, self.delay - (now - self.last_call))
            
            # Add random jitter if enabled
            if self.random_delay and required_delay > 0:
                required_delay += random.uniform(0, required_delay * 0.2)
            
            if required_delay > 0:
                await asyncio.sleep(required_delay)
            
            self.last_call = time.time()

    async def __aenter__(self):
        """Support using the rate limiter as an async context manager."""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - nothing to clean up."""
        pass

class PathManager:
    """
    Centralizes all path handling logic for media files, metadata, and temporary storage.
    
    This class ensures consistent path generation and management across the application,
    handling directory creation, path generation, and file operations in a unified way.
    
    The directory structure is organized as:
    - base_dir/
        - media/
            - temp/
            - subreddit_name/
                - post_id/
                    - 0.jpg (first image)
                    - 1.mp4 (second media item)
                    - etc...
        - metadata/
            - db/
            - tokens/
        - static/
    """
    def __init__(self, base_dir: str = "."):
        self.base_dir = Path(base_dir)
        self.media_dir = self.base_dir / "media"
        self.temp_dir = self.media_dir / "temp"
        self.metadata_dir = self.base_dir / "metadata"
        self.static_dir = self.base_dir / "static"
        
        # Set up directory structure
        self._initialize_directories()
        
        # Initialize mimetype detection
        mimetypes.init()
    
    def _initialize_directories(self) -> None:
        """Creates all required application directories if they don't exist."""
        directories = [
            self.media_dir,
            self.temp_dir,
            self.metadata_dir / "db",
            self.metadata_dir / "tokens",
            self.static_dir
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            
    def ensure_directories_exist(self) -> None:
        """Creates all required application directories if they don't exist."""
        try:
            for directory in [
                self.media_dir,
                self.temp_dir,
                self.metadata_dir / "db",
                self.metadata_dir / "tokens",
                self.static_dir
            ]:
                directory.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logging.error(f"Error creating directories: {e}")

    def get_media_path(self, post_id: str, media_url: str, subreddit: str, 
                    position: int = 0) -> Path:
        """
        Generates the appropriate path for storing media content.
        
        Args:
            post_id: Reddit post identifier
            media_url: Original URL of the media
            subreddit: Name of the subreddit
            position: Position of this media item in the post
            
        Returns:
            Path object representing the final storage location
        """
        subreddit_dir = self.media_dir / subreddit
        extension = self._determine_extension(media_url)
        filename = f"{post_id}_{position}{extension}"
        full_path = subreddit_dir / filename
        
        # Ensure directory exists
        subreddit_dir.mkdir(parents=True, exist_ok=True)
        
        return full_path
    
    def get_temp_path(self, post_id: str, position: int = 0) -> Path:
        """
        Generates a temporary file path for downloads in progress.
        
        Args:
            post_id: Reddit post identifier
            position: Position of this media item in the post
            
        Returns:
            Path object for temporary storage
        """
        return self.temp_dir / f"{post_id}_{position}_temp"
    
    def get_metadata_path(self, filename: str) -> Path:
        """
        Generates path for metadata storage.
        
        Args:
            filename: Name of the metadata file
            
        Returns:
            Path object for metadata storage
        """
        return self.metadata_dir / filename
    
    def _determine_extension(self, url: str) -> str:
        """
        Determines appropriate file extension based on URL and content type.
        
        Uses a combination of URL parsing and known service patterns to determine
        the correct file extension for media storage.
        
        Args:
            url: Media URL to analyze
            
        Returns:
            String containing the file extension (including the dot)
        """
        parsed_url = urlparse(url.lower())
        path = parsed_url.path
        extension = os.path.splitext(path)[1]
        
        # Handle known services
        if 'redgifs.com' in parsed_url.netloc:
            return '.mp4'
            
        if extension in {'.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm'}:
            return extension
            
        # Default extensions based on domain
        domain_defaults = {
            'i.redd.it': '.jpg',
            'i.imgur.com': '.jpg',
            'v.redd.it': '.mp4'
        }
        
        return domain_defaults.get(parsed_url.netloc, '.jpg')
    
    def clean_temp_directory(self, max_age_hours: int = 24) -> None:
        """
        Removes old temporary files from the temp directory.
        
        Args:
            max_age_hours: Maximum age of temp files in hours before deletion
        """
        try:
            current_time = time.time()
            for temp_file in self.temp_dir.glob('*_temp'):
                if temp_file.is_file():
                    file_age = current_time - temp_file.stat().st_mtime
                    if file_age > (max_age_hours * 3600):
                        temp_file.unlink()
        except Exception as e:
            logging.error(f"Error cleaning temp directory: {e}")

    def get_directory_size(self, directory: Union[str, Path]) -> int:
        """
        Calculates total size of a directory and its contents recursively.
        
        Args:
            directory: Path to the directory to measure
            
        Returns:
            Total size in bytes
            
        Note:
            Handles permission errors gracefully and logs warnings for inaccessible files
        """
        directory = Path(directory)
        total_size = 0
        
        try:
            for entry in directory.rglob('*'):
                try:
                    if entry.is_file():
                        total_size += entry.stat().st_size
                except (PermissionError, OSError) as e:
                    logging.warning(f"Error accessing {entry}: {e}")
                    continue
        except Exception as e:
            logging.error(f"Error calculating directory size for {directory}: {e}")
            
        return total_size

def safe_json_dumps(data: Any) -> str:
    """
    Safely convert data to JSON string, handling potential serialization issues.
    
    Args:
        data: Any Python object to convert to JSON
        
    Returns:
        JSON string representation or empty JSON object if conversion fails
    """
    try:
        if isinstance(data, str):
            return data
        return json.dumps(data)
    except (TypeError, ValueError):
        logging.warning(f"Failed to serialize data to JSON: {data}")
        return '{}'