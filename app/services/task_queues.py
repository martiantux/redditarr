# app/services/task_queues.py

import logging
import asyncio
import random
import time
from datetime import datetime
from collections import deque
from typing import Dict, Any
from enum import Enum
from app.clients import ClientManager
from app.core.utils import RateLimiter

class TaskType(Enum):
    MEDIA_DOWNLOAD = "media_download"
    COMMENT_FETCH = "comment_fetch"
    METADATA_UPDATE = "metadata_update"
    METADATA_FETCH = "metadata_fetch"

class TaskQueue:
    def __init__(self, task_type: TaskType, batch_size: int = 50, rate_limit: int = 60):
        self.task_type = task_type
        self.queue = asyncio.Queue()
        self.batch_size = batch_size
        self.rate_limit = rate_limit
        self.running = False
        self._worker_task = None
        self.active_tasks: Dict[str, Dict[str, Any]] = {}
        self.history = deque(maxlen=1000)
        self.client_manager = None
        self.reddit_api = None
        self.db_pool = None
        self._stop_event = asyncio.Event()

    async def add_task(self, task_data: Dict[str, Any]):
        """Add a task to the queue"""
        await self.queue.put(task_data)

    async def initialize(self, client_manager: ClientManager, db_pool, reddit_api):
        """Initialize task queue with required dependencies"""
        if not client_manager or not db_pool or not reddit_api:
            raise ValueError("client_manager, db_pool, and reddit_api are required")
        self.client_manager = client_manager
        self.db_pool = db_pool
        self.reddit_api = reddit_api
        self.running = False
        self._worker_task = None

    async def start_worker(self):
        """Start the queue worker if not already running"""
        if not self.running:
            self._stop_event.clear()
            self.running = True
            if self._worker_task is None or self._worker_task.done():
                self._worker_task = asyncio.create_task(self._process_queue())
                logging.info(f"Started {self.task_type.value} worker")

    async def stop(self):
        """Stop the queue worker"""
        if self.running:
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
            logging.info(f"Stopped {self.task_type.value} worker")

    async def _process_queue(self):
        """Process tasks from the queue with focused logging"""
        logging.info(f"Starting {self.task_type.value} queue worker loop")
        
        task_count = 0
        
        while self.running:
            try:
                current_size = self.queue.qsize()
                
                # Handle empty queue case
                if current_size == 0:
                    # Load appropriate type of tasks based on worker type
                    if self.task_type == TaskType.MEDIA_DOWNLOAD:
                        await self._add_pending_media_tasks()
                    elif self.task_type == TaskType.COMMENT_FETCH:
                        await self._add_pending_comment_tasks()
                    elif self.task_type == TaskType.METADATA_FETCH:
                        await self._add_pending_metadata_tasks()
                            
                    # If still empty, sleep before next check
                    if self.queue.qsize() == 0:
                        await asyncio.sleep(5)
                        await self._cleanup_stuck_posts()
                    continue

                # Get and process next task
                task_data = await self.queue.get()
                
                try:
                    # Process based on task type
                    if self.task_type == TaskType.MEDIA_DOWNLOAD:
                        await self._process_media_download(task_data)
                    elif self.task_type == TaskType.COMMENT_FETCH:
                        await self._process_comment_fetch(task_data)
                    elif self.task_type == TaskType.METADATA_FETCH:
                        logging.info(f"Processing metadata for r/{task_data['name']}")
                        # Process subreddit metadata
                        try:
                            hot_posts = await self.reddit_api.get_posts(task_data['name'], "hot", 1000)
                            top_posts = await self.reddit_api.get_posts(task_data['name'], "top", 1000)
                            
                            # Combine and deduplicate posts
                            all_posts = {post['id']: post for post in hot_posts}
                            for post in top_posts:
                                if post['id'] not in all_posts:
                                    all_posts[post['id']] = post
                                    
                            # Save posts to database
                            await self.db_pool.save_posts(list(all_posts.values()), task_data['name'])
                            
                            # Update status to ready
                            await self.db_pool.update_subreddit_status(task_data['name'], "ready")
                            logging.info(f"Successfully processed metadata for r/{task_data['name']}")
                        except Exception as e:
                            logging.error(f"Error processing metadata for r/{task_data['name']}: {e}")
                            await self.db_pool.update_subreddit_status(
                                task_data['name'], 
                                "error",
                                str(e)
                            )
                    
                    task_count += 1
                    if task_count % 10 == 0:  # Log progress every 10 tasks
                        logging.info(f"{self.task_type.value} worker: {task_count} tasks completed")
                    
                except Exception as e:
                    logging.error(f"Task processing error: {e}", exc_info=True)
                finally:
                    self.queue.task_done()
                        
            except Exception as e:
                logging.error(f"Queue processing error: {e}", exc_info=True)
                if self.running:
                    await asyncio.sleep(5)
                        
            # Check stop event
            if self._stop_event.is_set():
                break
                    
        logging.info(f"Stopping {self.task_type.value} queue worker loop after {task_count} tasks")

    async def _add_pending_metadata_tasks(self):
        """Load subreddits that need metadata processing"""
        try:
            async with self.db_pool.connection() as db:
                cursor = await db.execute("""
                    SELECT name 
                    FROM subreddits 
                    WHERE status = 'pending'
                    ORDER BY last_updated ASC NULLS FIRST
                    LIMIT 10
                """)
                subreddits = await cursor.fetchall()
                
                if subreddits:
                    logging.info(f"Adding {len(subreddits)} subreddits to metadata queue")
                    for sub in subreddits:
                        await self.queue.put({
                            'name': sub[0],
                            'task_type': 'metadata_fetch'
                        })
                        
        except Exception as e:
            logging.error(f"Error loading pending metadata tasks: {e}")

    async def _cleanup_stuck_posts(self):
        """Clean up posts that are stuck in pending state"""
        try:
            async with self.db_pool.connection() as db:
                # Find posts stuck in pending state for over 24 hours
                stuck_posts = await db.execute("""
                    SELECT id, subreddit 
                    FROM posts 
                    WHERE media_status = 'pending'
                    AND last_status_check < ?
                    AND error IS NULL
                """, (int(time.time() - 86400),))
                
                async for post in stuck_posts:
                    # Recheck post status
                    await db.execute("""
                        UPDATE posts 
                        SET media_status = 'error',
                            error = 'Download timeout - post stuck in pending state',
                            downloaded = 0
                        WHERE id = ?
                    """, (post[0],))
                
                await db.commit()
        except Exception as e:
            logging.error(f"Error cleaning up stuck posts: {e}")

    async def _add_pending_comment_tasks(self):
            """Load posts needing comments with focused logging"""
            try:
                async with self.db_pool.connection() as db:
                    cursor = await db.execute("""
                        WITH comment_counts AS (
                            SELECT post_id, COUNT(*) as comment_count 
                            FROM comments 
                            GROUP BY post_id
                        )
                        SELECT p.id, p.subreddit, p.comment_count as expected_comments,
                            COALESCE(c.comment_count, 0) as current_comments,
                            p.comment_fetch_attempts
                        FROM posts p
                        LEFT JOIN comment_counts c ON p.id = c.post_id
                        WHERE (
                            -- Never processed
                            (c.comment_count IS NULL AND p.comment_fetch_attempts = 0)
                            OR
                            -- Has comments but not all
                            (p.comment_count > COALESCE(c.comment_count, 0) 
                            AND p.comment_fetch_attempts < 3
                            AND p.comment_count > 0)
                            OR
                            -- Should have comments but none downloaded
                            (c.comment_count IS NULL 
                            AND p.comment_count > 0 
                            AND p.comment_fetch_attempts < 3)
                        )
                        AND p.error IS NULL
                        AND (p.media_status IS NULL OR p.media_status != 'missing')
                        AND p.reddit_status = 'active'
                        ORDER BY p.score DESC
                        LIMIT 25
                    """)
                    
                    posts = await cursor.fetchall()
                    post_count = len(posts) if posts else 0
                    
                    if posts:
                        logging.info(f"Adding {post_count} posts to comment fetch queue")
                        
                        for post in posts:
                            task = {
                                'id': post[0],
                                'subreddit': post[1],
                                'expected_comments': post[2],
                                'current_comments': post[3],
                                'fetch_attempts': post[4],
                                'task_type': 'comment_fetch'
                            }
                            await self.queue.put(task)

            except Exception as e:
                logging.error(f"Error loading comment tasks: {e}", exc_info=True)
                raise

    async def _add_pending_media_tasks(self):
        """Add pending media downloads to queue"""
        async with self.db_pool.connection() as db:
            cursor = await db.execute("""
                SELECT name FROM subreddits 
                WHERE status = 'ready'
                ORDER BY RANDOM()
                LIMIT 1
            """)
            subreddit = await cursor.fetchone()
            
            if subreddit:
                pending = await self.db_pool.get_pending_downloads(subreddit[0], limit=50)
                for post in pending:
                    await self.queue.put(post)
                    #logging.info(f"Added media task for post {post['id']} from r/{post['subreddit']}")

    async def _process_media_download(self, task_data: Dict):
        """Process a media download task"""
        from app.downloader import downloader  # Import here to avoid circular imports
        
        #logging.info(f"Processing media download for post {task_data['id']}")
        post_id = task_data['id']
        subreddit = task_data['subreddit']
        
        try:
            self.active_tasks[post_id] = {
                'post': task_data,
                'subreddit': subreddit,
                'started_at': datetime.utcnow()
            }
            
            downloader.db_pool = self.db_pool
            success = await downloader.process_post(task_data, subreddit)
            
            self.history.appendleft({
                'post_id': post_id,
                'subreddit': subreddit,
                'success': success,
                'timestamp': datetime.utcnow()
            })
            
        except Exception as e:
            logging.error(f"Error processing post {post_id}: {e}")
        finally:
            self.active_tasks.pop(post_id, None)

    async def _process_comment_fetch(self, task_data: Dict):
        """Process a comment fetch task with focused logging"""
        post_id = task_data['id']
        subreddit = task_data['subreddit']
        expected_comments = task_data.get('expected_comments', 0)
        current_comments = task_data.get('current_comments', 0)
        
        try:
            comments = await self.reddit_api.get_post_comments(post_id, subreddit)
            
            if comments:
                async with self.db_pool.connection() as db:
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        # Update post first
                        await db.execute("""
                            UPDATE posts 
                            SET last_comment_update = ?,
                                comment_fetch_attempts = comment_fetch_attempts + 1,
                                comment_count = ?,
                                last_comment_failure = NULL
                            WHERE id = ?
                        """, (int(time.time()), len(comments), post_id))
                        
                        # Save comments in batch
                        for comment in comments:
                            comment_id = comment['id']
                            parent_id = comment.get('parent_id', '')
                            
                            # Clean up parent_id format
                            if parent_id.startswith('t1_'):
                                parent_id = parent_id[3:]
                            elif parent_id.startswith('t3_'):
                                parent_id = None

                            await db.execute("""
                                INSERT OR REPLACE INTO comments (
                                    id, post_id, parent_id, author, body,
                                    created_utc, score, edited,
                                    depth, path, downloaded_at, metadata
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                comment_id,
                                post_id,
                                parent_id,
                                comment.get('author'),
                                comment.get('body', ''),
                                comment.get('created_utc', 0),
                                comment.get('score', 0),
                                1 if comment.get('edited') else 0,
                                comment.get('depth', 0),
                                comment.get('path', ''),
                                int(time.time()),
                                '{}'  # Empty JSON for metadata
                            ))

                        await db.commit()
                        logging.info(f"Saved {len(comments)} comments for post {post_id}")

                    except Exception as e:
                        await db.rollback()
                        await db.execute("""
                            UPDATE posts 
                            SET last_comment_failure = ?
                            WHERE id = ?
                        """, (str(e), post_id))
                        await db.commit()
                        logging.error(f"Error saving comments for {post_id}, rolling back: {e}")
                        raise

            else:
                # No comments found but mark as processed
                async with self.db_pool.connection() as db:
                    await db.execute("""
                        UPDATE posts 
                        SET comment_fetch_attempts = comment_fetch_attempts + 1,
                            last_comment_update = ?
                        WHERE id = ?
                    """, (int(time.time()), post_id))
                    await db.commit()
                    
        except Exception as e:
            logging.error(f"Error processing comments for {post_id}: {e}", exc_info=True)