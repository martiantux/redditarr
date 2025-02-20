# app/routes/downloads.py

from fastapi import APIRouter, HTTPException
from app.core.state import app_state
from typing import Dict

router = APIRouter()

@router.get("/status")
async def get_download_status() -> Dict:
    """Get current download queue status"""
    try:
        return app_state.download_queue.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats")
async def get_download_stats() -> Dict:
    """Get download statistics"""
    try:
        return await app_state.db_pool.get_download_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))