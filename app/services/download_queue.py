# app/services/download_queue.py

import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime
from collections import deque
import time
import json
import aiosqlite

class DownloadQueue:
    """
    Manages the download queue for Reddit posts and media.
    Handles prioritization, batching, and resource management.
    """
    def __init__(self, db_pool, client_manager):
        """Initialize download queue with required dependencies"""
        if not db_pool or not client_manager:
            raise ValueError("db_pool and client_manager are required")
            
        # Core dependencies
        self.db_pool = db_pool
        self.client_manager = client_manager
        
        # Queue management
        self.queue = asyncio.Queue()
        self.active_downloads: Dict[str, Dict] = {}
        self.history: deque = deque(maxlen=1000)
        
        # State management
        self.running = False
        self._worker_task = None
        self._stop_event = asyncio.Event()
        
        # Configuration (will be loaded from database)
        self.batch_size = 50
        self.batch_delay = 300

    async def initialize(self):
        """Initialize the download queue and load configuration"""
        try:
            # Load configuration
            await self.load_config()
            
            # Initialize in stopped state
            self.running = False
            self._worker_task = None
            self._stop_event.clear()
            
            logging.info("Download queue initialized")
            
        except Exception as e:
            logging.error(f"Error initializing download queue: {e}")
            raise

    async def load_config(self):
        """Load queue configuration from database"""
        try:
            async with self.db_pool.connection() as db:
                async with db.execute("SELECT key, value FROM config") as cursor:
                    config = dict(await cursor.fetchall())
                    self.batch_size = int(config.get('batch_size', 50))
                    self.batch_delay = int(config.get('batch_delay', 300))
                    logging.info(f"Loaded configuration: batch_size={self.batch_size}, batch_delay={self.batch_delay}")
        except Exception as e:
            logging.error(f"Error loading config: {e}")
            logging.info("Using default configuration values")

    async def get_next_batch(self) -> List[Dict]:
        """Get the next batch of posts to process, prioritizing subreddits with least downloaded content"""
        async with self.db_pool.connection() as db:
            # First get subreddit with least complete downloads
            query = """
                WITH subreddit_stats AS (
                    SELECT 
                        s.name,
                        COUNT(DISTINCT p.id) as total_posts,
                        COUNT(DISTINCT CASE WHEN p.downloaded = 1 THEN 1 END) as downloaded_count
                    FROM subreddits s
                    LEFT JOIN posts p ON s.name = p.subreddit
                    WHERE s.status = 'ready'  -- Only consider ready subreddits
                    AND EXISTS (
                        SELECT 1 FROM posts 
                        WHERE subreddit = s.name 
                        AND downloaded = 0 
                        AND error IS NULL
                    )
                    GROUP BY s.name
                )
                SELECT *,
                    ROUND(CAST(downloaded_count AS FLOAT) / CAST(total_posts AS FLOAT) * 100, 2) as percent_complete
                FROM subreddit_stats
                ORDER BY percent_complete ASC, total_posts DESC
                LIMIT 1;
            """
            
            logging.info("Fetching next batch - Subreddit selection query:")
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            subreddit = await cursor.fetchone()
            
            if subreddit:
                logging.info(f"Selected subreddit: {subreddit['name']}, "
                            f"downloaded: {subreddit['downloaded_count']}/{subreddit['total_posts']} "
                            f"({subreddit['percent_complete']}%)")
            
            if not subreddit:
                return []

            # Then get highest voted unprocessed posts for that subreddit
            query = """
                SELECT p.*, 
                    GROUP_CONCAT(m.media_url) as media_urls,
                    GROUP_CONCAT(m.media_type) as media_types,
                    GROUP_CONCAT(m.position) as positions,
                    GROUP_CONCAT(m.download_path) as download_paths
                FROM posts p
                LEFT JOIN post_media m ON p.id = m.post_id
                WHERE p.subreddit = ? 
                AND p.downloaded = 0 
                AND p.error IS NULL
                AND (
                    EXISTS (
                        SELECT 1 FROM post_media 
                        WHERE post_id = p.id AND media_url IS NOT NULL
                    )
                    OR p.post_type IN ('image', 'video', 'gallery')
                )
                GROUP BY p.id
                ORDER BY p.score DESC  -- Still prioritize by votes within the subreddit
                LIMIT ?
            """
            cursor = await db.execute(query, (subreddit['name'], self.batch_size))
            posts = []
            async for row in cursor:
                post_dict = dict(row)
                # Process media items
                if post_dict.get('media_urls'):
                    urls = post_dict['media_urls'].split(',')
                    types = post_dict['media_types'].split(',') if post_dict.get('media_types') else ['unknown'] * len(urls)
                    positions = post_dict['positions'].split(',') if post_dict.get('positions') else range(len(urls))
                    paths = post_dict['download_paths'].split(',') if post_dict.get('download_paths') else [None] * len(urls)
                    
                    post_dict['media_items'] = [
                        {
                            'url': url,
                            'media_type': mtype,
                            'position': int(pos),
                            'download_path': path
                        }
                        for url, mtype, pos, path in zip(urls, types, positions, paths)
                        if url  # Only include non-empty URLs
                    ]
                posts.append(post_dict)
                
            return posts

    async def add_posts(self, posts: List[Dict], subreddit: str):
        """Add multiple posts to the download queue"""
        logging.info(f"Adding {len(posts)} posts from r/{subreddit} to download queue")
        
        for post in posts:
            post_data = {
                'id': post['id'],
                'subreddit': subreddit,
                'post_type': post.get('post_type', 'unknown'),
                'media_items': post.get('media_items', []),
            }
            await self.queue.put(post_data)
            logging.debug(f"Added post {post['id']} to download queue")

        logging.info(f"Successfully queued {len(posts)} posts from r/{subreddit}")

    async def start_worker(self):
        """Start the queue worker if not already running"""
        if not self.running:
            self._stop_event.clear()
            self.running = True
            if self._worker_task is None or self._worker_task.done():
                self._worker_task = asyncio.create_task(self._process_queue())
                logging.info("Download queue worker started")

    async def stop(self):
        """Gracefully stop the queue worker"""
        if self.running:
            logging.info("Stopping download queue worker...")
            self.running = False
            self._stop_event.set()
            
            if self._worker_task and not self._worker_task.done():
                try:
                    await asyncio.wait_for(self._worker_task, timeout=5.0)
                except asyncio.TimeoutError:
                    self._worker_task.cancel()
                    try:
                        await self._worker_task
                    except asyncio.CancelledError:
                        pass
                self._worker_task = None
            
            logging.info("Download queue worker stopped")

    async def _process_queue(self):
        """Process downloads from the queue"""
        while self.running:
            try:
                # Load fresh config each iteration
                await self.load_config()
                
                try:
                    # Get an item from the queue with a timeout
                    post = await asyncio.wait_for(self.queue.get(), timeout=5.0)
                    
                    try:
                        logging.info(f"Processing post {post['id']} from r/{post['subreddit']}")
                        
                        self.active_downloads[post['id']] = {
                            'post': post,
                            'subreddit': post['subreddit'],
                            'started_at': datetime.utcnow()
                        }

                        client = self.client_manager.get_client_for_task(
                            'media' if post.get('media_items') else 'comments'
                        )

                        if post['post_type'] == 'text':
                            success = await self._process_text_post(post, client)
                        else:
                            success = await self._process_media_post(post, client)

                        self.history.appendleft({
                            'post_id': post['id'],
                            'subreddit': post['subreddit'],
                            'success': success,
                            'timestamp': datetime.utcnow()
                        })

                    except Exception as e:
                        logging.error(f"Error processing post {post['id']}: {e}")
                    finally:
                        self.active_downloads.pop(post['id'], None)
                        self.queue.task_done()

                except asyncio.TimeoutError:
                    # No items in queue, check for new batch
                    new_posts = await self.get_next_batch()
                    if new_posts:
                        await self.add_posts(new_posts, new_posts[0]['subreddit'])
                    else:
                        await asyncio.sleep(self.batch_delay)
                    continue

            except Exception as e:
                logging.error(f"Queue processing error: {e}")
                await asyncio.sleep(self.batch_delay)

            # Check stop event
            if self._stop_event.is_set():
                break

    async def _process_text_post(self, post: Dict, client) -> bool:
        """Process a text post including comments"""
        try:
            async with self.db_pool.connection() as db:
                await db.execute("""
                    UPDATE posts 
                    SET downloaded = 1,
                        downloaded_at = ?
                    WHERE id = ?
                """, (int(time.time()), post['id']))

                # Fetch and save comments if we have them
                comments = await client.get_post_comments(post['id'], post['subreddit'])
                if comments:
                    await db.execute_many("""
                        INSERT INTO comments 
                        (id, post_id, parent_id, author, body, body_html, 
                         created_utc, score, edited, depth, path, downloaded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, comments)
                
                await db.commit()
            return True

        except Exception as e:
            logging.error(f"Error processing text post {post['id']}: {e}")
            return False

    async def _process_media_post(self, post: Dict, client) -> bool:
        """Process a media post"""
        try:
            # Import here to avoid circular imports
            from app.downloader import downloader
            success = await downloader.process_post(post, post['subreddit'])
            return success

        except Exception as e:
            logging.error(f"Error processing media post {post['id']}: {e}")
            return False

    def get_status(self) -> Dict:
        """Get current status of the download queue"""
        return {
            'queue_size': self.queue.qsize(),
            'active_downloads': list(self.active_downloads.values()),
            'recent_history': list(self.history),
            'is_running': self.running
        }

    async def shutdown(self):
        """Clean shutdown of the download queue"""
        await self.stop()
        # Clear any remaining items
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break