# app/core/paths.py

import os
import logging
from pathlib import Path
import mimetypes
from typing import Union

class PathManager:
    """Centralizes all path handling logic for media files, metadata, and temporary storage."""
    
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
        """
        return self.temp_dir / f"{post_id}_{position}_temp"
    
    def get_metadata_path(self, filename: str) -> Path:
        """
        Generates path for metadata storage.
        """
        return self.metadata_dir / filename
    
    def _determine_extension(self, url: str) -> str:
        """
        Determines appropriate file extension based on URL.
        """
        from urllib.parse import urlparse
        
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
        """Removes old temporary files from the temp directory."""
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