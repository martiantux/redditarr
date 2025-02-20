# app/models.py

from pydantic import BaseModel

class NSFWModeConfig(BaseModel):
    enabled: bool

class SubredditAdd(BaseModel):
    name: str 
    should_monitor: bool = True

class SubredditDiscovery(BaseModel):
    name: str