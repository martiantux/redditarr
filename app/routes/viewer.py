# app/routes/viewer.py

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
import logging
from app.core.state import app_state

router = APIRouter()

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