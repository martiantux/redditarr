# app/routes/__init__.py

from fastapi import APIRouter
from . import subreddits, posts, config, workers, setup, downloads

router = APIRouter()

# Include all route modules
router.include_router(setup.router, prefix="/api/setup", tags=["setup"])
router.include_router(config.router, prefix="/api/config", tags=["config"])
router.include_router(workers.router, prefix="/api/workers", tags=["workers"])
router.include_router(downloads.router, prefix="/api/downloads", tags=["downloads"])
router.include_router(subreddits.router, prefix="/api/subreddits", tags=["subreddits"])
router.include_router(posts.router, prefix="/api/posts", tags=["posts"])