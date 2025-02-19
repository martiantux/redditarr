# app/services/token_manager.py

import json
import os
import logging
from typing import Optional, Dict
from datetime import datetime, timedelta

class TokenManager:
    def __init__(self, storage_path: str = "metadata/tokens"):
        self.storage_path = storage_path
        self.token_file = os.path.join(storage_path, "redgifs_token.json")
        os.makedirs(storage_path, exist_ok=True)
        self._token_data = None
        self._load_token()

    def _load_token(self) -> None:
        """Load token data from disk if it exists"""
        try:
            if os.path.exists(self.token_file):
                with open(self.token_file, 'r') as f:
                    self._token_data = json.load(f)
                    #logging.info("Loaded existing RedGifs token from disk")
        except Exception as e:
            logging.error(f"Error loading RedGifs token: {e}")
            self._token_data = None

    def get_token(self) -> Optional[str]:
        """Get current token if valid, otherwise None"""
        if not self._token_data:
            return None
            
        try:
            expires_at = datetime.fromisoformat(self._token_data['expires_at'])
            if datetime.now() >= expires_at - timedelta(minutes=5):
                logging.info("RedGifs token expired or close to expiry")
                return None
            return self._token_data['token']
        except Exception as e:
            logging.error(f"Error parsing token data: {e}")
            return None

    def save_token(self, token: str, expires_at: datetime) -> None:
        """Save new token data"""
        try:
            self._token_data = {
                "token": token,
                "expires_at": expires_at.isoformat()
            }
            with open(self.token_file, 'w') as f:
                json.dump(self._token_data, f)
            logging.info(f"Saved new RedGifs token, expires: {expires_at}")
        except Exception as e:
            logging.error(f"Error saving RedGifs token: {e}")

    def clear_token(self) -> None:
        """Clear token data"""
        self._token_data = None
        try:
            if os.path.exists(self.token_file):
                os.remove(self.token_file)
                logging.info("Cleared RedGifs token data")
        except Exception as e:
            logging.error(f"Error clearing RedGifs token: {e}")