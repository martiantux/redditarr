# app/routes/workers.py

from fastapi import APIRouter, HTTPException, Request
import time
import logging
from app.core.state import app_state

router = APIRouter()

@router.get("/status")
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

@router.post("/{worker_type}")
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