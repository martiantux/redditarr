# app/clients.py

import time
import aiohttp
from app.utils import RateLimiter
from app.version import USER_AGENT

class RedditClient:
    def __init__(self):
        self.headers = {
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'User-Agent': USER_AGENT
        }
        self.requests_per_minute = 30
        self.rate_limiter = RateLimiter(
            calls_per_minute=self.requests_per_minute,
            random_delay=True
        )
        self.session = None
        self.last_activity = time.time()
        
    async def initialize(self):
        """Initialize the API client session"""
        self.session = aiohttp.ClientSession(headers=self.headers)
        
    async def close(self):
        if self.session:
            await self.session.close()

class ClientManager:
    def __init__(self):
        self.clients = {}
        self.active_client = None
        
    async def initialize(self):
        # Create a single client instance
        self.active_client = RedditClient()
        await self.active_client.initialize()
            
    def get_client_for_task(self, task_type: str) -> RedditClient:
        """Get the client for any task type"""
        self.active_client.last_activity = time.time()
        return self.active_client

    async def close(self):
        if self.active_client:
            await self.active_client.close()