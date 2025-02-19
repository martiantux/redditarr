# app/core/state.py

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from pathlib import Path
import json
import os
import asyncio

from app.services.task_queues import TaskQueue, TaskType
from app.reddit import RedditAPI
from app.database import DatabasePool
from app.clients import ClientManager
from app.services.download_queue import DownloadQueue
from app.services import MetadataManager, MediaManager, CommentManager
from app.downloader import downloader

@dataclass
class ApplicationConfig:
    nsfw_mode: bool = False
    batch_size: int = 50
    batch_delay: int = 300
    download_comments: bool = True
    comment_depth: int = 10
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ApplicationConfig':
        return cls(
            nsfw_mode=bool(data.get('nsfw_mode', False)),
            batch_size=int(data.get('batch_size', 50)),
            batch_delay=int(data.get('batch_delay', 300)),
            download_comments=bool(data.get('download_comments', True)),
            comment_depth=int(data.get('comment_depth', 10))
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'nsfw_mode': self.nsfw_mode,
            'batch_size': self.batch_size,
            'batch_delay': self.batch_delay,
            'download_comments': self.download_comments,
            'comment_depth': self.comment_depth
        }

class ApplicationState:
    def __init__(self):
        self.base_dir = Path("metadata")
        self.db_dir = self.base_dir / "db"
        self.media_dir = Path("media")
        self.temp_dir = self.media_dir / "temp"
        self.db_path = self.db_dir / "metadata.db"
        
        self.config: Optional[ApplicationConfig] = None
        self.db_pool: Optional[DatabasePool] = None
        self.client_manager: Optional[ClientManager] = None
        self.reddit_api: Optional[RedditAPI] = None
        self.download_queue: Optional[DownloadQueue] = None
        self.metadata_manager: Optional[MetadataManager] = None
        self.media_manager: Optional[MediaManager] = None
        self.comment_manager: Optional[CommentManager] = None
        
        self._initialized = False
        self._shutting_down = False

    async def initialize(self) -> None:
        """Initialize all application components in the correct order"""
        if self._initialized:
            return

        try:
            logging.info("Starting application initialization...")
            
            # Create required directories
            self._create_directories()
            
            # Initialize core services first
            self.db_pool = DatabasePool(str(self.db_path))
            await self.db_pool.initialize()
            
            downloader.set_db_pool(self.db_pool)

            await self._load_config()
            
            # Initialize client manager first
            self.client_manager = ClientManager()
            await self.client_manager.initialize()
            
            # Set up downloader with dependencies
            downloader.set_client_manager(self.client_manager)
            await downloader.initialize()
            
            # Initialize RedditAPI with client_manager
            self.reddit_api = RedditAPI(self.client_manager)
            await self.reddit_api.initialize()
            
            self.download_queue = DownloadQueue(
                db_pool=self.db_pool,
                client_manager=self.client_manager
            )
            await self.download_queue.initialize()

            self.metadata_manager = MetadataManager(
                client_manager=self.client_manager,
                db_pool=self.db_pool,
                reddit_api=self.reddit_api
            )
            await self.metadata_manager.initialize(self.client_manager)

            self.media_manager = MediaManager(
                client_manager=self.client_manager,
                db_pool=self.db_pool,
                download_queue=self.download_queue,
                reddit_api=self.reddit_api
            )
            await self.media_manager.initialize(self.client_manager)
            
            self.comment_manager = CommentManager(
                client_manager=self.client_manager,
                db_pool=self.db_pool,
                reddit_api=self.reddit_api
            )
            await self.comment_manager.initialize(self.client_manager)
            
            self._initialized = True
            logging.info("Application initialization complete")
            
        except Exception as e:
            logging.error(f"Application initialization failed: {e}")
            await self.shutdown()
            raise

    def _create_directories(self) -> None:
        directories = [
            self.db_dir,
            self.media_dir,
            self.temp_dir,
            self.base_dir / "tokens",
            Path("static")
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            logging.info(f"Ensuring directory exists: {directory}")

    async def _load_config(self) -> None:
        try:
            async with self.db_pool.connection() as db:
                cursor = await db.execute("SELECT key, value FROM config")
                config_data = dict(await cursor.fetchall())
                self.config = ApplicationConfig.from_dict(config_data)
                logging.info("Configuration loaded successfully")
        except Exception as e:
            logging.error(f"Error loading configuration: {e}")
            self.config = ApplicationConfig()
            logging.info("Using default configuration")

    async def save_config(self) -> None:
        if not self.config:
            return

        try:
            async with self.db_pool.connection() as db:
                for key, value in self.config.to_dict().items():
                    await db.execute("""
                        INSERT OR REPLACE INTO config (key, value, updated_at)
                        VALUES (?, ?, strftime('%s', 'now'))
                    """, (key, str(value)))
                await db.commit()
                logging.info("Configuration saved successfully")
        except Exception as e:
            logging.error(f"Error saving configuration: {e}")
            raise

    async def shutdown(self) -> None:
        if self._shutting_down:
            return
            
        self._shutting_down = True
        logging.info("Starting application shutdown...")
        
        services = [
            (self.metadata_manager, "Metadata Manager"),
            (self.comment_manager, "Comment Manager"),
            (self.media_manager, "Media Manager"),
            (self.download_queue, "Download Queue"),
            (self.reddit_api, "Reddit API"),
        ]
        
        for service, name in services:
            if service:
                try:
                    await service.shutdown()
                    logging.info(f"Shut down {name}")
                except Exception as e:
                    logging.error(f"Error shutting down {name}: {e}")
        
        if self.client_manager:
            try:
                await self.client_manager.close()
                logging.info("Shut down Client Manager")
            except Exception as e:
                logging.error(f"Error shutting down Client Manager: {e}")
        
        if self.db_pool:
            try:
                await self.db_pool.close_all()
                logging.info("Shut down Database Pool")
            except Exception as e:
                logging.error(f"Error shutting down Database Pool: {e}")
        
        self._initialized = False
        self._shutting_down = False
        logging.info("Application shutdown complete")

    def is_ready(self) -> bool:
        return (self._initialized and
                self.config is not None and
                self.db_pool is not None and
                self.client_manager is not None and
                self.reddit_api is not None and
                self.download_queue is not None and
                self.media_manager is not None and
                self.comment_manager is not None)

app_state = ApplicationState()