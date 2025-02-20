# app/database/pool.py

import os
import aiosqlite
import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Any
from contextlib import asynccontextmanager
from pathlib import Path
import json
import time
from datetime import datetime
from .schema import DB_SCHEMA
from ..core.paths import PathManager

class DatabasePool:
    def __init__(self, database_path: str, max_connections: int = 5):
        self.database_path = database_path
        self.max_connections = max_connections
        self.pool: Optional[asyncio.Queue] = None
        self._active_connections: Dict = {}
        self._write_lock = asyncio.Lock()
        self.path_manager = PathManager()

    def get_directory_size(self, directory: str) -> int:
        """Delegate to PathManager for consistent directory size calculation."""
        return self.path_manager.get_directory_size(directory)

    async def initialize(self):
        """Initializes the connection pool and database schema."""
        try:
            os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
            logging.info(f"Ensuring database directory exists at {self.database_path}")
            
            self.pool = asyncio.Queue(maxsize=self.max_connections)
            for _ in range(self.max_connections):
                conn = await aiosqlite.connect(self.database_path)
                await conn.execute("PRAGMA busy_timeout = 60000")  # 60 second timeout
                await conn.execute("PRAGMA journal_mode = WAL")
                await conn.execute("PRAGMA synchronous = NORMAL")
                await conn.execute("PRAGMA foreign_keys = ON")
                await self.pool.put(conn)

            # Initialize database schema
            async with self.connection() as db:
                logging.info("Applying database schema...")
                await db.executescript(DB_SCHEMA)
                await db.commit()
                
                # Verify all required tables exist
                cursor = await db.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name IN ('posts', 'post_media', 'comments')
                """)
                tables = await cursor.fetchall()
                logging.info(f"Verified tables exist: {[t[0] for t in tables]}")
                
                # Verify comments table structure
                cursor = await db.execute("PRAGMA table_info(comments)")
                columns = await cursor.fetchall()
                logging.info(f"comments table columns: {[c[1] for c in columns]}")
                    
        except Exception as e:
            logging.error(f"Failed to initialize database pool: {e}")
            raise

    @asynccontextmanager
    async def connection(self):
        """Provides a managed database connection from the pool."""
        if not self.pool:
            await self.initialize()

        conn = None
        try:
            conn = await self.pool.get()
            yield conn
        except Exception as e:
            if conn:
                await conn.rollback()
            logging.error(f"Database error: {e}")
            raise
        finally:
            if conn:
                try:
                    await conn.commit()
                except Exception as e:
                    logging.error(f"Error committing transaction: {e}")
                    await conn.rollback()
                finally:
                    await self.pool.put(conn)

    async def close_all(self):
        """Closes all database connections in the pool."""
        if not self.pool:
            return
            
        while not self.pool.empty():
            conn = await self.pool.get()
            await conn.close()

    def _safe_json_dumps(self, data: Any) -> str:
        """Safely convert data to JSON string, handling potential serialization issues."""
        try:
            if isinstance(data, str):
                return data
            return json.dumps(data)
        except (TypeError, ValueError):
            logging.warning(f"Failed to serialize data to JSON: {data}")
            return '{}'

    def get_media_path(self, post_id: str, media_url: Optional[str], subreddit: str) -> str:
        """Get the expected file path for a post's media."""
        if media_url:
            if media_url.endswith(('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm')):
                ext = Path(media_url).suffix
            elif 'redgifs.com' in media_url.lower():
                ext = '.mp4'
            else:
                ext = '.mp4'
        else:
            ext = '.mp4'

        base_path = Path('media') / subreddit
        os.makedirs(base_path, exist_ok=True)
        return str(base_path / f"{post_id}{ext}")

    async def store_media_metadata(self, file_hash: str, post_id: str, file_path: str, post: Dict):
        """Store media file metadata in the database."""
        async with self._write_lock:
            async with self.connection() as db:
                metadata = {"original_url": post.get("media_url")}
                metadata_json = self._safe_json_dumps(metadata)

                await db.execute("""
                    INSERT OR IGNORE INTO media_files 
                    (hash, path, size, first_seen_post_id, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    file_hash,
                    file_path,
                    os.path.getsize(file_path),
                    post_id,
                    metadata_json
                ))
                
                await db.execute("""
                    UPDATE posts 
                    SET media_hash = ?
                    WHERE id = ?
                """, (file_hash, post_id))

    async def store_user_metadata(self, username: str, metadata: Dict):
        """Store user metadata in the database."""
        async with self._write_lock:
            async with self.connection() as db:
                metadata_json = self._safe_json_dumps(metadata)

                await db.execute("""
                    INSERT OR IGNORE INTO users
                    (username, first_seen, metadata, last_updated)
                    VALUES (?, ?, ?, ?)
                """, (
                    username,
                    int(time.time()),
                    metadata_json,
                    int(time.time())
                ))

    async def add_subreddit(self, name: str, over_18: bool = False, metadata: Optional[Dict] = None):
        """Adds or updates a subreddit in the database."""
        name = name.lower()
        async with self._write_lock:
            async with self.connection() as db:
                # Normalize name to lowercase
                name = name.lower()
                await db.execute("""
                    INSERT OR REPLACE INTO subreddits 
                    (name, over_18, status, last_updated, metadata) 
                    VALUES (?, ?, 'pending', ?, ?)
                    """,
                    (name, over_18, int(time.time()), self._safe_json_dumps(metadata))
                )

    async def update_subreddit_status(self, name: str, status: str, error_message: Optional[str] = None):
        """Updates subreddit processing status and metadata."""
        async with self._write_lock:
            async with self.connection() as db:
                # Normalize name to lowercase
                name = name.lower()
                await db.execute("""
                    UPDATE subreddits 
                    SET status = ?, 
                        error_message = ?, 
                        last_updated = ?
                    WHERE name = ?
                """, (status, error_message, int(time.time()), name))

    async def get_subreddits(self) -> List[Dict]:
        """Retrieves all monitored subreddits with their statistics."""
        async with self.connection() as db:
            # First get nsfw_mode setting
            cursor = await db.execute(
                "SELECT value FROM config WHERE key = 'nsfw_mode'"
            )
            result = await cursor.fetchone()
            nsfw_mode = result[0].lower() == 'true' if result else False

            db.row_factory = aiosqlite.Row
            query = """
                SELECT 
                    s.*,
                    COUNT(DISTINCT p.id) as total_posts,
                    COUNT(DISTINCT CASE WHEN p.downloaded = 1 THEN p.id END) as downloaded_count,
                    COUNT(DISTINCT CASE WHEN p.post_type = 'image' THEN p.id END) as image_count,
                    COUNT(DISTINCT CASE WHEN p.post_type = 'video' THEN p.id END) as video_count
                FROM subreddits s
                LEFT JOIN posts p ON s.name = p.subreddit
                WHERE s.over_18 = ?  -- Filter by NSFW mode
                GROUP BY s.name
                ORDER BY s.name ASC
            """
            cursor = await db.execute(query, (nsfw_mode,))
            subreddits = [dict(row) for row in await cursor.fetchall()]
            
            for sub in subreddits:
                sub_path = Path('media') / sub['name']
                sub['disk_usage'] = self.get_directory_size(str(sub_path))
                if isinstance(sub.get('metadata'), str):
                    try:
                        sub['metadata'] = json.loads(sub['metadata'])
                    except json.JSONDecodeError:
                        sub['metadata'] = {}
                        
            return subreddits

    async def get_subreddit_posts(self, subreddit: str, limit: int = 100, 
                                offset: int = 0, sort_by: str = "score", 
                                sort_order: str = "DESC") -> List[Dict]:
        """Retrieves posts from a specific subreddit with sorting options."""
        valid_sorts = {
            "score": "score",
            "new": "created_utc",
            "random": "RANDOM()"
        }
        
        sort_field = valid_sorts.get(sort_by, "score")
        
        subreddit = subreddit.lower()

        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            query = f"""
                SELECT p.*, 
                    GROUP_CONCAT(m.media_url) as media_urls,
                    GROUP_CONCAT(m.media_type) as media_types,
                    GROUP_CONCAT(m.position) as positions
                FROM posts p
                LEFT JOIN post_media m ON p.id = m.post_id
                WHERE p.subreddit = ?
                GROUP BY p.id
                ORDER BY {sort_field} {sort_order}
                LIMIT ? OFFSET ?
            """
            cursor = await db.execute(query, (subreddit, limit, offset))
            return [dict(row) for row in await cursor.fetchall()]

    async def save_posts(self, posts: List[Dict], subreddit: str):
        async with self._write_lock:
            async with self.connection() as db:
                for post in posts:
                    try:
                        logging.debug(f"Processing post {post['id']} with {len(post.get('media_items', []))} media items")

                        # Update user tracking - only insert if new user
                        if post.get('author'):
                            await db.execute("""
                                INSERT OR IGNORE INTO users 
                                (username, first_seen, last_updated)
                                VALUES (?, ?, ?)
                            """, (
                                post['author'],
                                int(time.time()),
                                int(time.time())
                            ))

                        # Determine media status
                        media_items = post.get('media_items', [])
                        media_status = 'pending'
                        if post['post_type'] in ('image', 'video', 'gallery'):
                            if media_items:
                                urls_present = [item for item in media_items if item.get('url')]
                                if not urls_present:
                                    media_status = 'error'
                                    logging.warning(f"Post {post['id']} has no valid media URLs")
                                elif len(urls_present) < len(media_items):
                                    media_status = 'temporarily_unavailable'
                                    logging.warning(f"Post {post['id']} has {len(urls_present)}/{len(media_items)} valid media URLs")

                        subreddit = subreddit.lower()

                        # Insert main post
                        await db.execute("""
                            INSERT OR REPLACE INTO posts 
                            (id, subreddit, author, title, url, created_utc,
                            score, post_type, selftext, metadata, media_status,
                            last_status_check)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            post['id'],
                            subreddit,
                            post['author'],
                            post['title'],
                            post['url'],
                            post['created_utc'],
                            post['score'],
                            post['post_type'],
                            post.get('selftext'),
                            self._safe_json_dumps(post.get('metadata', {})),
                            media_status,
                            int(time.time())
                        ))

                        # Handle media items
                        if media_items:
                            # Clear existing media items for this post
                            await db.execute(
                                "DELETE FROM post_media WHERE post_id = ?",
                                (post['id'],)
                            )

                            # Insert valid media items
                            for position, media_item in enumerate(media_items):
                                if media_item and media_item.get('url'):
                                    await db.execute("""
                                        INSERT INTO post_media
                                        (post_id, media_url, media_type, original_url,
                                        position, downloaded, download_attempts,
                                        last_attempt, media_status)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """, (
                                        post['id'],
                                        media_item['url'],
                                        media_item.get('media_type', 'unknown'),
                                        media_item.get('original_url', media_item['url']),
                                        position,
                                        False,
                                        0,
                                        int(time.time()),  # Current time as first attempt
                                        'pending'
                                    ))

                    except Exception as e:
                        logging.error(f"Error saving post {post.get('id')}: {e}")
                        if post:
                            logging.debug(f"Post data: {json.dumps(post, default=str)}")
                        continue

    async def mark_post_downloaded(self, post_id: str, subreddit: str, 
                                success: bool = True, error: Optional[str] = None,
                                media_status: Optional[str] = None):
        """Marks a post as downloaded or failed in the database."""
        async with self._write_lock:
            async with self.connection() as db:
                if success:
                    await db.execute("""
                        UPDATE posts 
                        SET downloaded = 1,
                            downloaded_at = ?,
                            error = NULL,
                            media_status = ?
                        WHERE id = ?
                    """, (int(time.time()), media_status or 'downloaded', post_id))
                else:
                    await db.execute("""
                        UPDATE posts
                        SET downloaded = 0,
                            error = ?,
                            media_status = ?
                        WHERE id = ?
                    """, (error, media_status or 'error', post_id))

    async def get_pending_downloads(self, subreddit: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Retrieves posts pending download with improved filtering."""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            
            query = """
                SELECT p.*, 
                    GROUP_CONCAT(m.media_url) as media_urls,
                    GROUP_CONCAT(m.media_type) as media_types,
                    GROUP_CONCAT(m.position) as positions
                FROM posts p
                LEFT JOIN post_media m ON p.id = m.post_id
                WHERE p.downloaded = 0 
                AND p.error IS NULL
                AND (
                    EXISTS (
                        SELECT 1 FROM post_media 
                        WHERE post_id = p.id AND media_url IS NOT NULL
                    )
                    OR p.post_type IN ('image', 'video', 'gallery')
                )
            """
            
            params = []
            if subreddit:
                subreddit = subreddit.lower()
                query += " AND p.subreddit = ?"
                params.append(subreddit)
                
            query += """ 
                GROUP BY p.id
                ORDER BY p.score DESC, p.created_utc DESC 
                LIMIT ?
            """
            params.append(limit)
            
            logging.debug(f"Executing query: {query} with params: {params}")
            cursor = await db.execute(query, params)
            posts = [dict(row) for row in await cursor.fetchall()]
            
            # Additional post-processing to ensure we have valid media
            valid_posts = []
            for post in posts:
                # Check for media items from the join
                if post.get('media_urls'):
                    urls = post['media_urls'].split(',')
                    types = post['media_types'].split(',') if post.get('media_types') else ['unknown'] * len(urls)
                    positions = post['positions'].split(',') if post.get('positions') else list(range(len(urls)))
                    
                    post['media_items'] = [
                        {
                            'url': url,
                            'media_type': mtype,
                            'position': int(pos)
                        }
                        for url, mtype, pos in zip(urls, types, positions)
                        if url  # Only include non-empty URLs
                    ]
                    
                    if post['media_items']:
                        valid_posts.append(post)
                        continue
                
                # Check if it's a media post type that might need URL extraction
                if post['post_type'] in ('image', 'video', 'gallery'):
                    valid_posts.append(post)
                    continue
            
            logging.info(f"Found {len(valid_posts)} posts with valid media out of {len(posts)} total pending posts")
            return valid_posts

    async def update_user(self, username: str, data: Dict[str, Any]):
        """Updates or creates a user record with the provided data."""
        async with self._write_lock:
            async with self.connection() as db:
                current_time = int(time.time())
                
                try:
                    await db.execute("""
                        INSERT INTO users (
                            username, first_seen, last_seen, last_updated
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(username) DO UPDATE SET
                            last_seen = ?,
                            last_updated = ?
                    """, (
                        username,
                        data.get('first_seen', current_time),
                        data.get('last_seen', current_time),
                        current_time,
                        data.get('last_seen', current_time),
                        current_time
                    ))
                except Exception as e:
                    logging.error(f"Error updating user {username}: {e}")
                    raise

    async def save_comments(self, post_id: str, comments: List[Dict]):
        """Saves a comment thread for a post with proper transaction and logging."""
        async with self._write_lock:
            try:
                async with self.connection() as db:
                    # First verify post exists
                    cursor = await db.execute(
                        "SELECT id FROM posts WHERE id = ?", 
                        (post_id,)
                    )
                    if not await cursor.fetchone():
                        logging.error(f"Cannot save comments - post {post_id} not found")
                        return

                    test_time = int(time.time())
                    
                    # Start transaction explicitly
                    await db.execute("BEGIN IMMEDIATE")
                    
                    try:
                        # Update post first
                        await db.execute("""
                            UPDATE posts 
                            SET last_comment_update = ?, 
                                comment_count = ?
                            WHERE id = ?
                        """, (test_time, len(comments), post_id))

                        # Save each comment with explicit logging
                        for comment in comments:
                            comment_id = comment['id']
                            logging.info(f"Inserting comment {comment_id} into database")
                            
                            # Clean up parent_id format
                            parent_id = comment.get('parent_id', '')
                            if parent_id.startswith('t1_'):
                                parent_id = parent_id[3:]
                            elif parent_id.startswith('t3_'):
                                parent_id = None

                            # Do the actual insert with all fields explicitly specified
                            await db.execute("""
                                INSERT INTO comments (
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
                                test_time,
                                self._safe_json_dumps(comment.get('metadata', {}))
                            ))

                            # Verify the insert worked
                            verify = await db.execute(
                                "SELECT id FROM comments WHERE id = ?", 
                                (comment_id,)
                            )
                            if not await verify.fetchone():
                                raise Exception(f"Failed to verify comment {comment_id} was inserted")

                        # Verify final count
                        cursor = await db.execute(
                            "SELECT COUNT(*) FROM comments WHERE post_id = ?",
                            (post_id,)
                        )
                        final_count = (await cursor.fetchone())[0]
                        logging.info(f"Verified {final_count} comments saved for post {post_id}")

                        await db.commit()
                        logging.info(f"Successfully committed {len(comments)} comments to database")

                    except Exception as e:
                        await db.rollback()
                        logging.error(f"Error saving comments for post {post_id}, rolling back: {e}")
                        raise

            except Exception as e:
                logging.error(f"Database error saving comments for post {post_id}: {e}")
                raise

    async def update_post_analysis(self, post_id: str, analysis_data: Dict[str, Any]):
        """Updates the content and bot analysis data for a post."""
        async with self._write_lock:
            async with self.connection() as db:
                await db.execute("""
                    UPDATE posts SET
                    content_analysis_version = ?,
                    content_analysis_score = ?,
                    content_analysis_flags = ?,
                    content_analysis_timestamp = ?,
                    bot_probability = ?,
                    bot_detection_version = ?,
                    bot_detection_timestamp = ?
                    WHERE id = ?
                """, (
                    analysis_data.get('content_version'),
                    analysis_data.get('content_score'),
                    self._safe_json_dumps(analysis_data.get('content_flags', {})),
                    analysis_data.get('content_timestamp'),
                    analysis_data.get('bot_probability'),
                    analysis_data.get('bot_version'),
                    analysis_data.get('bot_timestamp'),
                    post_id
                ))

    async def update_comment_analysis(self, comment_id: str, analysis_data: Dict[str, Any]):
        """Updates the content and bot analysis data for a comment."""
        async with self._write_lock:
            async with self.connection() as db:
                await db.execute("""
                    UPDATE comments SET
                    content_analysis_version = ?,
                    content_analysis_score = ?,
                    content_analysis_flags = ?,
                    content_analysis_timestamp = ?,
                    bot_probability = ?,
                    bot_detection_version = ?,
                    bot_detection_timestamp = ?
                    WHERE id = ?
                """, (
                    analysis_data.get('content_version'),
                    analysis_data.get('content_score'),
                    self._safe_json_dumps(analysis_data.get('content_flags', {})),
                    analysis_data.get('content_timestamp'),
                    analysis_data.get('bot_probability'),
                    analysis_data.get('bot_version'),
                    analysis_data.get('bot_timestamp'),
                    comment_id
                ))