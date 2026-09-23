"""
Block 9: Jellyfin Refresher (jellyfin.py)
Handles refreshing Jellyfin's media library.
"""

from __future__ import annotations

import os
import requests
from typing import Dict, Optional

from dotenv import load_dotenv


class JellyfinRefresher:
    """
    Handles communication with Jellyfin server to refresh libraries.
    """

    def __init__(self):
        load_dotenv()
        self.base_url = os.getenv("JELLYFIN_URL")
        self.api_key = os.getenv("JELLYFIN_API_KEY")
        self.user_id = os.getenv("JELLYFIN_USER_ID")
        self.session = requests.Session()
        
        if self.base_url:
            self.session.headers.update({
                "X-Emby-Token": self.api_key,
                "Accept": "application/json"
            })

    def refresh_library(self, library_id: Optional[str] = None) -> bool:
        """
        Triggers a library refresh in Jellyfin.
        If no library_id is provided, refreshes all libraries.
        Returns True if successful.
        """
        if not all([self.base_url, self.api_key, self.user_id]):
            print("Error: Missing Jellyfin configuration in .env file")
            return False
            
        endpoint = f"{self.base_url}/Library/Refresh"
        params = {"api_key": self.api_key}
        
        if library_id:
            params["Id"] = library_id
        
        try:
            response = self.session.post(endpoint, params=params)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error refreshing Jellyfin library: {e}")
            return False

    def get_libraries(self) -> Optional[Dict[str, str]]:
        """
        Retrieves list of libraries from Jellyfin.
        Returns dict of {library_id: library_name}.
        """
        if not all([self.base_url, self.api_key, self.user_id]):
            return None
            
        try:
            response = self.session.get(
                f"{self.base_url}/Library/VirtualFolders"
            )
            response.raise_for_status()
            
            return {
                lib["ItemId"]: lib["Name"] 
                for lib in response.json()
            }
        except Exception:
            return None