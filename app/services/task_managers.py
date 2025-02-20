# app/services/task_managers.py

import json
import logging
import asyncio
from typing import Dict, Any, Optional
#from datetime import datetime
from .task_queues import TaskQueue, TaskType
from app.clients import ClientManager
from ..core.paths import PathManager

class MetadataManager:
    """
    Manages metadata fetching and processing for subreddits.
    Controls the initial post fetching and metadata population.
    """
    def __init__(self, client_manager: Optional[ClientManager] = None, 
                 db_pool = None, reddit_api = None):
        self.client_manager = client_manager
        self.db_pool = db_pool
        self.reddit_api = reddit_api
        
        self.queue = TaskQueue(
            task_type=TaskType.METADATA_FETCH,
            batch_size=1,  # Process one subreddit at a time
            rate_limit=30
        )
        
        self._initialized = False
        self._shutting_down = False

    async def initialize(self, client_manager: Optional[ClientManager] = None):
        """Initialize the metadata manager"""
        if self._initialized:
            return
            
        try:
            logging.info("Initializing metadata manager...")
            
            if client_manager:
                self.client_manager = client_manager
                
            if not self.client_manager:
                raise ValueError("ClientManager is required for initialization")
                
            await self.queue.initialize(self.client_manager, self.db_pool, self.reddit_api)
            
            self._initialized = True
            logging.info("Metadata manager initialized successfully")
            
        except Exception as e:
            logging.error(f"Failed to initialize metadata manager: {e}")
            await self.shutdown()
            raise

    async def process_subreddit(self, subreddit_name: str) -> bool:
        """Process a subreddit's metadata and posts"""
        subreddit_name = subreddit_name.lower()
        try:
            if not self._initialized:
                raise RuntimeError("MetadataManager not initialized")
                
            logging.info(f"Processing metadata for r/{subreddit_name}")
            
            # Update status to indexing
            await self.db_pool.update_subreddit_status(subreddit_name, "indexing")
            
            # Fetch posts
            hot_posts = await self.reddit_api.get_posts(subreddit_name, "hot", 1000)
            top_posts = await self.reddit_api.get_posts(subreddit_name, "top", 1000)
            
            # Combine and deduplicate posts
            all_posts = {post['id']: post for post in hot_posts}
            for post in top_posts:
                if post['id'] not in all_posts:
                    all_posts[post['id']] = post
                    
            # Save posts to database
            await self.db_pool.save_posts(list(all_posts.values()), subreddit_name)
            
            # Update status to ready
            await self.db_pool.update_subreddit_status(subreddit_name, "ready")
            
            return True
            
        except Exception as e:
            logging.error(f"Error processing metadata for r/{subreddit_name}: {e}")
            await self.db_pool.update_subreddit_status(
                subreddit_name, 
                "error",
                str(e)
            )
            return False

    async def shutdown(self):
        """Perform clean shutdown of the metadata manager"""
        if self._shutting_down:
            return
            
        self._shutting_down = True
        logging.info("Starting metadata manager shutdown...")
        
        try:
            if self.queue:
                await self.queue.stop()
            
            self._initialized = False
            logging.info("Metadata manager shutdown complete")
            
        except Exception as e:
            logging.error(f"Error during metadata manager shutdown: {e}")
        finally:
            self._shutting_down = False

class MediaManager:
    """
    Manages media download tasks and processing.
    Works with the download queue to handle media content efficiently.
    """
    def __init__(self, client_manager: Optional[ClientManager] = None, 
                 db_pool = None, download_queue = None, reddit_api = None):
        # Store injected dependencies
        self.client_manager = client_manager
        self.db_pool = db_pool
        self.download_queue = download_queue
        self.reddit_api = reddit_api
        
        # Initialize task management
        self.queue = TaskQueue(
            task_type=TaskType.MEDIA_DOWNLOAD,
            batch_size=50,
            rate_limit=60
        )
        
        # Initialize utilities
        self.path_manager = PathManager()
        
        # State tracking
        self._initialized = False
        self._shutting_down = False

    async def initialize(self, client_manager: Optional[ClientManager] = None):
        """Initialize the media manager with proper error handling"""
        if self._initialized:
            return
            
        try:
            logging.info("Initializing media manager...")
            
            # Allow dependency injection during initialization if not provided in constructor
            if client_manager:
                self.client_manager = client_manager
                
            if not self.client_manager:
                raise ValueError("ClientManager is required for initialization")
                
            # Pass both dependencies to TaskQueue
            await self.queue.initialize(self.client_manager, self.db_pool, self.reddit_api)
            
            # Create required directories
            self.path_manager.ensure_directories_exist()
            
            self._initialized = True
            logging.info("Media manager initialized successfully")
            
        except Exception as e:
            logging.error(f"Failed to initialize media manager: {e}")
            await self.shutdown()
            raise

    async def process_media(self, post_data: Dict[str, Any]) -> bool:
        """Process a media item with proper error handling and logging"""
        try:
            if not self._initialized:
                raise RuntimeError("MediaManager not initialized")
                
            post_id = post_data.get('id')
            if not post_id:
                raise ValueError("Missing post ID in post data")
                
            logging.info(f"Processing media for post {post_id}")
            
            # Add to task queue
            await self.queue.add_task({
                'id': post_id,
                'subreddit': post_data.get('subreddit'),
                'media_items': post_data.get('media_items', []),
                'post_type': post_data.get('post_type', 'unknown')
            })
            
            return True
            
        except Exception as e:
            logging.error(f"Error processing media for post {post_id}: {e}")
            return False

    async def start_processing(self):
        """Start media processing if not already running"""
        if not self._initialized:
            raise RuntimeError("Cannot start uninitialized MediaManager")
            
        await self.queue.start_worker()
        logging.info("Media processing started")

    async def stop_processing(self):
        """Stop media processing gracefully"""
        if self.queue:
            await self.queue.stop()
            logging.info("Media processing stopped")

    async def shutdown(self):
        """Perform clean shutdown of the media manager"""
        if self._shutting_down:
            return
            
        self._shutting_down = True
        logging.info("Starting media manager shutdown...")
        
        try:
            await self.stop_processing()
            
            # Clean up any temporary files
            self.path_manager.clean_temp_directory()
            
            self._initialized = False
            logging.info("Media manager shutdown complete")
            
        except Exception as e:
            logging.error(f"Error during media manager shutdown: {e}")
        finally:
            self._shutting_down = False

class CommentManager:
    """
    Manages comment fetching and processing.
    Handles comment tree traversal and storage.
    """
    def __init__(self, client_manager: Optional[ClientManager] = None, 
                 db_pool = None, reddit_api = None):
        self.client_manager = client_manager
        self.db_pool = db_pool
        self.queue = TaskQueue(
            task_type=TaskType.COMMENT_FETCH,
            batch_size=100,
            rate_limit=30
        )
        self._initialized = False
        self._shutting_down = False
        self.reddit_api = reddit_api
        
    async def initialize(self, client_manager: Optional[ClientManager] = None):
        """Initialize the comment manager"""
        if self._initialized:
            return
            
        try:
            logging.info("Initializing comment manager...")
            
            if client_manager:
                self.client_manager = client_manager
                
            if not self.client_manager:
                raise ValueError("ClientManager is required for initialization")
                
            # Pass all dependencies to TaskQueue
            await self.queue.initialize(self.client_manager, self.db_pool, self.reddit_api)
            
            self._initialized = True
            logging.info("Comment manager initialized successfully")
            
        except Exception as e:
            logging.error(f"Failed to initialize comment manager: {e}")
            await self.shutdown()
            raise

    async def shutdown(self):
        """Perform clean shutdown of the comment manager"""
        if self._shutting_down:
            return
            
        self._shutting_down = True
        logging.info("Starting comment manager shutdown...")
        
        try:
            if self.queue:
                await self.queue.stop()
            
            self._initialized = False
            logging.info("Comment manager shutdown complete")
            
        except Exception as e:
            logging.error(f"Error during comment manager shutdown: {e}")
        finally:
            self._shutting_down = False