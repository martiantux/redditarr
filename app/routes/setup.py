# app/routes/setup.py

from fastapi import APIRouter, HTTPException
import logging
import os
from app.core.state import app_state

router = APIRouter()

@router.get("/status")
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

@router.post("/initialize")
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