# app/main.py

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
import logging
import time
import json
import os
import asyncio
import aiosqlite
from pathlib import Path
from app.version import VERSION, USER_AGENT
from fastapi.responses import HTMLResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("redditarr.log"),
        logging.StreamHandler()
    ]
)

# Initialize FastAPI app
app = FastAPI(
    title="Redditarr",
    version=VERSION,
    description="Reddit Content Archival Tool"
)

from app.downloader import downloader
from app.services.download_queue import DownloadQueue
from app.services.task_managers import MediaManager, CommentManager
from app.clients import ClientManager

# Add our new application state
from app.core.state import app_state

# Mount static directories
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/media", StaticFiles(directory="media"), name="media")

# Define request models
class NSFWModeConfig(BaseModel):
    enabled: bool

class SubredditAdd(BaseModel):
    name: str
    should_monitor: bool = True

class SubredditDiscovery(BaseModel):
    name: str

async def fetch_subreddit_posts(subreddit_name: str):
    try:
        await app_state.db_pool.update_subreddit_status(subreddit_name, "indexing")
        
        hot_posts = await app_state.reddit_api.get_posts(subreddit_name, "hot", 1000)
        top_posts = await app_state.reddit_api.get_posts(subreddit_name, "top", 1000)
        
        all_posts = {post['id']: post for post in hot_posts}
        for post in top_posts:
            if post['id'] not in all_posts:
                all_posts[post['id']] = post
                
        await app_state.db_pool.save_posts(list(all_posts.values()), subreddit_name)
        
        await app_state.db_pool.update_subreddit_status(
            subreddit_name, 
            "ready",
            None  # error_message
        )
        
    except Exception as e:
        logging.error(f"Error fetching posts for r/{subreddit_name}: {e}")
        await app_state.db_pool.update_subreddit_status(
            subreddit_name, 
            "error",
            str(e)
        )

def get_directory_size(directory: str) -> int:
    """Calculate total size of a directory in bytes"""
    total = 0
    try:
        for entry in os.scandir(directory):
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_directory_size(entry.path)
    except FileNotFoundError:
        return 0
    return total

@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/")
async def read_root():
    return FileResponse("static/index.html")

@app.get("/r/{subreddit}")
async def view_subreddit(subreddit: str):
    subreddit = subreddit.lower()
    try:
        # First check if we already have this subreddit and its status
        async with app_state.db_pool.connection() as db:
            cursor = await db.execute(
                "SELECT status, error_message FROM subreddits WHERE name = ?", 
                (subreddit,)
            )
            result = await cursor.fetchone()
            
            if result:
                status = result[0]
                error_message = result[1]
                # Only show viewer if subreddit is ready
                if status == 'ready':
                    return FileResponse("static/viewer.html")
                # Otherwise show appropriate status page
                return HTMLResponse(f"""
                    <html>
                        <head>
                            <title>Archiving r/{subreddit}</title>
                            <script src="https://cdn.tailwindcss.com"></script>
                            <meta http-equiv="refresh" content="5;url=/r/{subreddit}">
                        </head>
                        <body class="bg-gray-100">
                            <div class="container mx-auto px-4 py-8">
                                <div class="bg-white p-8 rounded-lg shadow-lg max-w-2xl mx-auto text-center">
                                    <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
                                    <h1 class="text-2xl font-bold mb-4">Archiving in Progress</h1>
                                    <p class="text-gray-600 mb-4">
                                        Status: {status.title()}<br>
                                        {f"Error: {error_message}<br>" if error_message else ""}
                                        Collecting data for r/{subreddit}. This page will automatically refresh...
                                    </p>
                                    <a href="/" class="text-blue-500 hover:text-blue-600">Return to Library</a>
                                </div>
                            </div>
                        </body>
                    </html>
                """)

            # Get config values
            cursor = await db.execute(
                "SELECT key, value FROM config WHERE key IN ('auto_discover_enabled', 'min_subscribers')"
            )
            config = dict(await cursor.fetchall())
            auto_discover = config.get('auto_discover_enabled', 'true').lower() == 'true'
            min_subscribers = int(config.get('min_subscribers', '10000'))

            if not auto_discover:
                return HTMLResponse("""
                    <html>
                        <head>
                            <title>Subreddit Not Archived</title>
                            <script src="https://cdn.tailwindcss.com"></script>
                        </head>
                        <body class="bg-gray-100">
                            <div class="container mx-auto px-4 py-8">
                                <div class="bg-white p-8 rounded-lg shadow-lg max-w-2xl mx-auto text-center">
                                    <h1 class="text-2xl font-bold mb-4">Subreddit Not Found</h1>
                                    <p class="text-gray-600 mb-4">This subreddit has not been archived yet and auto-discovery is disabled.</p>
                                    <a href="/" class="text-blue-500 hover:text-blue-600">Return to Library</a>
                                </div>
                            </div>
                        </body>
                    </html>
                """)

            # Check if subreddit exists and meets criteria
            sub_info = await app_state.reddit_api.get_subreddit_info(subreddit)
            if not sub_info:
                return HTMLResponse("""
                    <html>
                        <head>
                            <title>Subreddit Not Found</title>
                            <script src="https://cdn.tailwindcss.com"></script>
                        </head>
                        <body class="bg-gray-100">
                            <div class="container mx-auto px-4 py-8">
                                <div class="bg-white p-8 rounded-lg shadow-lg max-w-2xl mx-auto text-center">
                                    <h1 class="text-2xl font-bold mb-4">Subreddit Not Found</h1>
                                    <p class="text-gray-600 mb-4">This subreddit does not exist or is private.</p>
                                    <a href="/" class="text-blue-500 hover:text-blue-600">Return to Library</a>
                                </div>
                            </div>
                        </body>
                    </html>
                """)

            if sub_info.get('subscribers', 0) < min_subscribers:
                return HTMLResponse(f"""
                    <html>
                        <head>
                            <title>Subreddit Below Threshold</title>
                            <script src="https://cdn.tailwindcss.com"></script>
                        </head>
                        <body class="bg-gray-100">
                            <div class="container mx-auto px-4 py-8">
                                <div class="bg-white p-8 rounded-lg shadow-lg max-w-2xl mx-auto text-center">
                                    <h1 class="text-2xl font-bold mb-4">Subreddit Below Threshold</h1>
                                    <p class="text-gray-600 mb-4">
                                        This subreddit has fewer than {min_subscribers:,} subscribers and won't be automatically archived.
                                    </p>
                                    <a href="/" class="text-blue-500 hover:text-blue-600">Return to Library</a>
                                </div>
                            </div>
                        </body>
                    </html>
                """)

            # Add subreddit to archive
            await app_state.db_pool.add_subreddit(
                subreddit,
                over_18=sub_info.get('over18', False),
                metadata=sub_info
            )

            # Add to metadata queue with high priority
            await app_state.metadata_manager.queue.add_task({
                'name': subreddit,
                'priority': 1
            })

            return HTMLResponse(f"""
                <html>
                    <head>
                        <title>Archiving r/{subreddit}</title>
                        <script src="https://cdn.tailwindcss.com"></script>
                        <meta http-equiv="refresh" content="5;url=/r/{subreddit}">
                    </head>
                    <body class="bg-gray-100">
                        <div class="container mx-auto px-4 py-8">
                            <div class="bg-white p-8 rounded-lg shadow-lg max-w-2xl mx-auto text-center">
                                <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
                                <h1 class="text-2xl font-bold mb-4">Initializing Archive</h1>
                                <p class="text-gray-600 mb-4">
                                    Starting archive process for r/{subreddit}. This page will automatically refresh...
                                </p>
                                <a href="/" class="text-blue-500 hover:text-blue-600">Return to Library</a>
                            </div>
                        </div>
                    </body>
                </html>
            """)

    except Exception as e:
        logging.error(f"Error handling subreddit view request: {e}")
        return HTMLResponse("""
            <html>
                <head>
                    <title>Error</title>
                    <script src="https://cdn.tailwindcss.com"></script>
                </head>
                <body class="bg-gray-100">
                    <div class="container mx-auto px-4 py-8">
                        <div class="bg-white p-8 rounded-lg shadow-lg max-w-2xl mx-auto text-center">
                            <h1 class="text-2xl font-bold mb-4">Error</h1>
                            <p class="text-gray-600 mb-4">An error occurred processing this request.</p>
                            <a href="/" class="text-blue-500 hover:text-blue-600">Return to Library</a>
                        </div>
                    </div>
                </body>
            </html>
        """)

@app.get("/api/setup/status")
async def get_setup_status():
    try:
        async with app_state.db_pool.connection() as db:
            cursor = await db.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='subreddits'
            """)
            db_initialized = await cursor.fetchone() is not None
            
        return {
            "is_setup": db_initialized,
            "db_initialized": db_initialized,
        }
    except Exception as e:
        logging.error(f"Status check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/setup/initialize")
async def initialize_setup():
    try:
        os.makedirs('metadata', exist_ok=True)
        
        await app_state.db_pool.initialize()
        
        status = await get_setup_status()
        if not status["is_setup"]:
            missing = []
            if not status["db_initialized"]:
                missing.append("database initialization")
            raise Exception(f"Setup incomplete - missing: {', '.join(missing)}")
            
        return {"status": "success", "details": status}
    except Exception as e:
        logging.error(f"Setup failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/downloads/status")
async def get_download_status():
    """Get current download queue status"""
    return download_queue.get_status()

@app.get("/api/subreddits/suggest")
async def suggest_subreddits(query: str):
    try:
        async with app_state.db_pool.connection() as db:
            cursor = await db.execute(
                "SELECT value FROM config WHERE key = 'nsfw_mode'"
            )
            result = await cursor.fetchone()
            nsfw_mode = result[0].lower() == 'true' if result else False

        if not query or len(query) < 2:
            return []

        sub_name = query[2:] if query.startswith('r/') else query
        try:
            sub_info = await app_state.reddit_api.get_subreddit_info(sub_name)
            if sub_info:
                is_nsfw = sub_info.get('over18', False)
                if (nsfw_mode and is_nsfw) or (not nsfw_mode and not is_nsfw):
                    return [{
                        'name': sub_info['display_name'],
                        'metadata': {
                            'title': sub_info['title'],
                            'description': sub_info.get('public_description', '').strip(),
                            'subscribers': sub_info['subscribers'],
                            'over18': sub_info['over18'],
                            'icon_img': sub_info.get('icon_img', '')
                        }
                    }]
        except Exception as e:
            logging.info(f"Subreddit {sub_name} not found or not accessible: {e}")
            
        similar_subs = await app_state.reddit_api.search_subreddits(sub_name)
        filtered_subs = []
        for sub in similar_subs:
            is_nsfw = sub.get('over18', False)
            if (nsfw_mode and is_nsfw) or (not nsfw_mode and not is_nsfw):
                filtered_subs.append({
                    'name': sub['display_name'],
                    'metadata': {
                        'title': sub['title'],
                        'description': sub.get('public_description', '').strip(),
                        'subscribers': sub.get('subscribers', 0),
                        'over18': sub.get('over18', False),
                        'icon_img': sub.get('icon_img', '')
                    }
                })
        return filtered_subs[:10]
        
    except Exception as e:
        logging.error(f"Error suggesting subreddits: {e}")
        return []

@app.post("/api/config/nsfw_mode")
async def set_nsfw_mode(request: Request):
    try:
        data = await request.json()
        enabled = data.get('enabled')
        if enabled is None:
            raise HTTPException(status_code=400, detail="Missing 'enabled' field")
            
        logging.info(f"Setting NSFW mode to: {enabled}")
        
        # Use app_state.db_pool instead of direct db_pool access
        async with app_state.db_pool.connection() as db:
            await db.execute(
                "UPDATE config SET value = ?, updated_at = ? WHERE key = 'nsfw_mode'",
                (str(bool(enabled)).lower(), int(time.time()))
            )
            await db.commit()
            
        logging.info("NSFW mode update successful")
        return {"status": "success"}
    except Exception as e:
        logging.error(f"Error updating NSFW mode: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/nsfw_mode")
async def get_nsfw_mode():
    try:
        async with app_state.db_pool.connection() as db:
            cursor = await db.execute(
                "SELECT value FROM config WHERE key = 'nsfw_mode'"
            )
            result = await cursor.fetchone()
            enabled = result[0].lower() == 'true' if result else False
        return {"enabled": enabled}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/subreddits/")
async def add_subreddit(subreddit: SubredditAdd):
    try:
        # First check if subreddit already exists
        async with app_state.db_pool.connection() as db:
            cursor = await db.execute(
                "SELECT name, status FROM subreddits WHERE name = ?",
                (subreddit.name,)
            )
            existing = await cursor.fetchone()
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Subreddit r/{subreddit.name} is already in your library"
                )

        # Continue with existing validation
        sub_info = await app_state.reddit_api.get_subreddit_info(subreddit.name)
        if not sub_info:
            raise HTTPException(
                status_code=404, 
                detail=f"Subreddit {subreddit.name} not found or private"
            )
        
        # NSFW check
        async with app_state.db_pool.connection() as db:
            cursor = await db.execute(
                "SELECT value FROM config WHERE key = 'nsfw_mode'"
            )
            result = await cursor.fetchone()
            nsfw_mode = result[0].lower() == 'true' if result else False
            
            if nsfw_mode != sub_info.get('over18', False):
                status = "NSFW" if sub_info.get('over18', False) else "SFW"
                mode = "NSFW" if nsfw_mode else "SFW"
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot add {status} subreddit in {mode} mode"
                )
        
        # Add to database in pending state
        await app_state.db_pool.add_subreddit(
            subreddit.name, 
            over_18=sub_info.get('over18', False)
        )
        
        # Add to metadata queue
        await app_state.metadata_manager.queue.add_task({
            'name': subreddit.name,
            'priority': 1
        })
        
        return await app_state.db_pool.get_subreddits()
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error adding subreddit: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/subreddits/")
async def list_subreddits():
    try:
        async with app_state.db_pool.connection() as db:
            # First get nsfw_mode setting
            cursor = await db.execute(
                "SELECT value FROM config WHERE key = 'nsfw_mode'"
            )
            result = await cursor.fetchone()
            nsfw_mode = result[0].lower() == 'true' if result else False

            db.row_factory = aiosqlite.Row
            
            query = """
                SELECT s.*, 
                    (SELECT COUNT(DISTINCT p.id) FROM posts p 
                     LEFT JOIN post_media m ON p.id = m.post_id
                     WHERE p.subreddit = s.name) as total_posts,
                    (SELECT COUNT(DISTINCT p.id) FROM posts p 
                     LEFT JOIN post_media m ON p.id = m.post_id
                     WHERE p.subreddit = s.name 
                     AND p.downloaded = 1) as downloaded_count,
                    (SELECT COUNT(DISTINCT p.id) FROM posts p 
                     WHERE p.subreddit = s.name 
                     AND p.post_type = 'image') as image_count,
                    (SELECT COUNT(DISTINCT p.id) FROM posts p 
                     WHERE p.subreddit = s.name 
                     AND p.post_type = 'video') as video_count
                FROM subreddits s
                WHERE s.over_18 = ?
                ORDER BY s.name ASC
            """
            
            cursor = await db.execute(query, (nsfw_mode,))
            subreddits = []
            async for row in cursor:
                subreddit = dict(row)
                sub_path = Path('media') / subreddit['name']
                subreddit['disk_usage'] = get_directory_size(str(sub_path))
                subreddits.append(subreddit)
                
            return subreddits
            
    except Exception as e:
        logging.error(f"Database error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/subreddits/{subreddit}/posts")
async def get_subreddit_posts(
    subreddit: str, 
    limit: int = 100, 
    offset: int = 0, 
    sort_by: str = "score", 
    sort_order: str = "DESC",
    view_mode: str = "reddit"
):
    valid_sorts = {
        "score": "score",
        "new": "created_utc",
        "random": "RANDOM()"
    }
    
    sort_field = valid_sorts.get(sort_by, "score")
    
    subreddit = subreddit.lower()

    async with app_state.db_pool.connection() as db:
        db.row_factory = aiosqlite.Row
        
        # Base query with common parts
        query = """
            SELECT p.*, 
                GROUP_CONCAT(m.media_url) as media_urls,
                GROUP_CONCAT(m.media_type) as media_types,
                GROUP_CONCAT(m.position) as positions,
                GROUP_CONCAT(m.download_path) as download_paths,
                GROUP_CONCAT(m.downloaded) as downloaded_statuses
            FROM posts p
            LEFT JOIN post_media m ON p.id = m.post_id
            WHERE p.subreddit = ?
        """

        # Add view-specific filtering
        if view_mode in ('single', 'grid'):
            query += """
                AND p.post_type IN ('image', 'video', 'gallery')
                AND p.downloaded = 1
                AND p.media_status = 'downloaded'
            """
        else:  # reddit view
            query += """
                AND (
                    (p.post_type IN ('image', 'video', 'gallery') AND p.downloaded = 1)
                    OR p.post_type = 'text'
                )
            """

        query += f"""
            GROUP BY p.id
            ORDER BY {sort_field} {sort_order}
            LIMIT ? OFFSET ?
        """

        cursor = await db.execute(query, (subreddit, limit, offset))
        posts = [dict(row) for row in await cursor.fetchall()]
        
        # Process media items
        for post in posts:
            if post.get('media_urls'):
                urls = post['media_urls'].split(',')
                types = post['media_types'].split(',') if post.get('media_types') else ['unknown'] * len(urls)
                positions = post['positions'].split(',') if post.get('positions') else range(len(urls))
                paths = post['download_paths'].split(',') if post.get('download_paths') else [None] * len(urls)
                downloaded = [s == '1' for s in post['downloaded_statuses'].split(',')] if post.get('downloaded_statuses') else [False] * len(urls)
                
                post['media_items'] = [
                    {
                        'url': url,
                        'media_type': mtype,
                        'position': int(pos),
                        'download_path': path,
                        'downloaded': dl
                    }
                    for url, mtype, pos, path, dl in zip(urls, types, positions, paths, downloaded)
                    if dl or post['post_type'] == 'text'  # Include undownloaded items only for text posts
                ]
                
        return posts

@app.get("/api/subreddits/{subreddit}/status")
async def get_subreddit_status(subreddit: str):
    try:
        subreddit = subreddit.lower()

        async with app_state.db_pool.connection() as db:
            cursor = await db.execute("""
                SELECT s.status, s.error_message,
                       COUNT(DISTINCT p.id) as posts_indexed
                FROM subreddits s
                LEFT JOIN posts p ON s.name = p.subreddit
                WHERE s.name = ?
                GROUP BY s.name
            """, (subreddit,))
            result = await cursor.fetchone()
            
            if not result:
                return {"status": "not_found"}
                
            return {
                "status": result[0],
                "error_message": result[1],
                "posts_indexed": result[2]
            }
    except Exception as e:
        logging.error(f"Error getting subreddit status: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/posts/{post_id}/comments")
async def get_post_comments(post_id: str):
    """Get comments for a specific post"""
    async with app_state.db_pool.connection() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM comments 
            WHERE post_id = ?
            ORDER BY path, created_utc DESC
        """, (post_id,))
        return [dict(row) for row in await cursor.fetchall()]

@app.get("/api/workers/status")
async def get_worker_status():
    """Get current status of all workers"""
    try:
        async with app_state.db_pool.connection() as db:
            cursor = await db.execute("SELECT worker_type, enabled FROM worker_status")
            status = dict(await cursor.fetchall())
            return {
                "media": bool(status.get("media", 0)),
                "comments": bool(status.get("comments", 0)),
                "metadata": bool(status.get("metadata", 0))
            }
    except Exception as e:
        logging.error(f"Error getting worker status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get worker status")

@app.post("/api/workers/{worker_type}")
async def toggle_worker(worker_type: str, request: Request):
    if worker_type not in ["media", "comments", "metadata", "discovery"]: 
        raise HTTPException(status_code=400, detail="Invalid worker type")
        
    try:
        data = await request.json()
        enabled = bool(data.get('enabled', False))
        
        # Update worker state first
        if worker_type == 'media':
            if enabled:
                await app_state.media_manager.queue.start_worker()
            else:
                await app_state.media_manager.queue.stop()
        elif worker_type == 'comments':
            if enabled:
                await app_state.comment_manager.queue.start_worker()
            else:
                await app_state.comment_manager.queue.stop()
        elif worker_type == 'metadata': 
            if enabled:
                await app_state.metadata_manager.queue.start_worker()
            else:
                await app_state.metadata_manager.queue.stop()
        elif worker_type == 'discovery': 
            if enabled:
                await app_state.discovery_manager.queue.start_worker()
            else:
                await app_state.discovery_manager.queue.stop()
        
        # Then update database
        async with app_state.db_pool.connection() as db:
            await db.execute("""
                INSERT OR REPLACE INTO worker_status (worker_type, enabled, last_updated)
                VALUES (?, ?, ?)
            """, (worker_type, int(enabled), int(time.time())))
            await db.commit()
        
        logging.info(f"{worker_type.capitalize()} worker {'enabled' if enabled else 'disabled'}")
        
        return {
            "status": "success",
            "enabled": enabled,
            "message": f"{worker_type} worker {'started' if enabled else 'stopped'}"
        }
        
    except Exception as e:
        logging.error(f"Error toggling {worker_type} worker: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update {worker_type} worker status"
        )

@app.get("/api/posts/{post_id}/media")
async def get_post_media(post_id: str):
    async with app_state.db_pool.connection() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM post_media 
            WHERE post_id = ? 
            ORDER BY position ASC
        """, (post_id,))
        media_items = [dict(row) for row in await cursor.fetchall()]
        return media_items

@app.get("/api/posts/{post_id}/full")
async def get_full_post(post_id: str):
    """Get complete post details including comments if available."""
    async with app_state.db_pool.connection() as db:
        db.row_factory = aiosqlite.Row
        
        # Get post details
        cursor = await db.execute(
            "SELECT * FROM posts WHERE id = ?", 
            (post_id,)
        )
        post = dict(await cursor.fetchone())
        
        # Get media items
        cursor = await db.execute(
            "SELECT * FROM post_media WHERE post_id = ? ORDER BY position",
            (post_id,)
        )
        post['media_items'] = [dict(row) for row in await cursor.fetchall()]
        
        # Get comments
        cursor = await db.execute("""
            SELECT * FROM comments 
            WHERE post_id = ?
            ORDER BY path, created_utc DESC
        """, (post_id,))
        post['comments'] = [dict(row) for row in await cursor.fetchall()]
        
        return post

# Update the app state
client_manager = None

@app.on_event("startup")
async def startup_event():
    try:
        await app_state.initialize()
        
        os.makedirs("static", exist_ok=True)
        
        status = await get_setup_status()
        if not status["is_setup"]:
            await initialize_setup()
    except Exception as e:
        logging.error(f"Startup failed: {e}")
        raise

@app.on_event("shutdown")
async def shutdown_event():
    if client_manager:
        await client_manager.close()
    await app_state.media_manager.shutdown()
    await app_state.comment_manager.shutdown()
    await app_state.db_pool.close_all()
    await downloader.close()