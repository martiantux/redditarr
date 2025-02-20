# app/routes/config.py

from fastapi import APIRouter, HTTPException, Request
import logging
import time
from app.core.state import app_state

router = APIRouter()

@router.post("/nsfw_mode")
async def set_nsfw_mode(request: Request):
    try:
        data = await request.json()
        enabled = data.get('enabled')
        if enabled is None:
            raise HTTPException(status_code=400, detail="Missing 'enabled' field")
            
        logging.info(f"Setting NSFW mode to: {enabled}")
        
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

@router.get("/nsfw_mode")
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