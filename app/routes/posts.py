# app/routes/posts.py

from fastapi import APIRouter, HTTPException
import aiosqlite
import logging
from app.core.state import app_state

router = APIRouter()

@router.get("/{post_id}/comments")
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

@router.get("/{post_id}/media")
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

@router.get("/{post_id}/full")
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