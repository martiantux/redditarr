# app/main.py

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging
import os
from app.core.version import VERSION
from app.routes import router
from app.core.state import app_state
from app.routes.setup import get_setup_status, initialize_setup

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

# Include router
app.include_router(router)

# Mount static directories with absolute paths
app.mount("/static", StaticFiles(directory="/app/static"), name="static")
app.mount("/media", StaticFiles(directory="/app/media"), name="media")

@app.get("/")
async def read_root():
    return FileResponse("static/index.html")

@app.get("/r/{subreddit}")
async def serve_viewer(subreddit: str):
    return FileResponse("static/viewer.html")

@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}

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
    await app_state.shutdown()