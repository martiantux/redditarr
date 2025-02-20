# app/core/utils.py

import asyncio
import random
import time
import os
import logging
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlparse
import mimetypes
from datetime import datetime

class RateLimiter:
    """
    A flexible rate limiter for controlling API request frequencies.
    
    This implementation uses an async lock to ensure thread safety and provides
    features like random delay jitter and burst allowance to better handle real-world
    API interaction patterns.
    
    Attributes:
        delay: Base delay between requests in seconds
        burst_allowance: Number of requests allowed to bypass delay
        random_delay: Whether to add random jitter to delays
    """
    def __init__(self, 
                 calls_per_minute: int = 60, 
                 random_delay: bool = True,
                 burst_allowance: Optional[int] = None):
        self.delay = 60.0 / calls_per_minute
        self.last_call = 0
        self._lock = asyncio.Lock()
        self.random_delay = random_delay
        self.burst_allowance = burst_allowance
        self.burst_tokens = burst_allowance if burst_allowance else 0

    async def acquire(self):
        """
        Wait until a request is allowed according to the rate limit.
        Handles burst allowance and random jitter if configured.
        """
        async with self._lock:
            now = time.time()
            
            # Replenish burst tokens if we have an allowance
            if self.burst_allowance:
                tokens_to_add = int((now - self.last_call) / self.delay)
                self.burst_tokens = min(self.burst_allowance, 
                                     self.burst_tokens + tokens_to_add)
            
            # Calculate required delay
            if self.burst_tokens > 0:
                self.burst_tokens -= 1
                required_delay = 0
            else:
                required_delay = max(0, self.delay - (now - self.last_call))
            
            # Add random jitter if enabled
            if self.random_delay and required_delay > 0:
                required_delay += random.uniform(0, required_delay * 0.2)
            
            if required_delay > 0:
                await asyncio.sleep(required_delay)
            
            self.last_call = time.time()

    async def __aenter__(self):
        """Support using the rate limiter as an async context manager."""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - nothing to clean up."""
        pass