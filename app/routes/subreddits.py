# app/routes/subreddits.py

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
import aiosqlite
from pathlib import Path
from pydantic import BaseModel
import logging
from typing import List, Optional
from app.core.state import app_state
from app.models import SubredditAdd

router = APIRouter()

@router.get("/suggest")
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

@router.post("/")
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

@router.get("/")
async def list_subreddits():
    try:
        return await app_state.db_pool.get_subreddits()
    except Exception as e:
        logging.error(f"Database error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/r/{subreddit}")
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

@router.get("/{subreddit}/posts")
async def get_subreddit_posts(
    subreddit: str, 
    limit: int = 100, 
    offset: int = 0, 
    sort: str = "score", 
    view_mode: str = "reddit"
):
    valid_sorts = {
        "score": "p.score DESC",
        "new": "p.created_utc DESC",
        "random": "RANDOM()"
    }
    
    sort_field = valid_sorts.get(sort, "p.score DESC")
    
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
            ORDER BY {sort_field}
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

@router.get("/{subreddit}/status")
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