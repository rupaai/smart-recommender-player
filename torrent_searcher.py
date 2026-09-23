import requests
from typing import List, Dict, Any

class TorrentSearcher:
    def __init__(self, config):
        qbt_config = config.get("qbittorrent", {})
        host = qbt_config.get("host", "localhost")
        port = qbt_config.get("port", 8080)
        self.base_url = f"http://{host}:{port}"
        self.username = qbt_config.get("username", "admin")
        self.password = qbt_config.get("password", "adminadmin")
        self.session = requests.Session()
        
    def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Searches for torrents matching the query using qBittorrent's search plugin.
        Returns list of torrent results with name, size, seeds and magnet link.
        """
        try:
            # First login to qBittorrent Web UI
            login_url = f"{self.base_url}/api/v2/auth/login"
            login_data = {
                "username": self.username,
                "password": self.password
            }
            self.session.post(login_url, data=login_data)
            
            # Search for torrents
            search_url = f"{self.base_url}/api/v2/search/start"
            search_data = {
                "pattern": query,
                "plugins": "all",
                "category": "movies"
            }
            search_id = self.session.post(search_url, data=search_data).json()["id"]
            
            # Get results
            results_url = f"{self.base_url}/api/v2/search/results?id={search_id}"
            results = self.session.get(results_url).json()["results"]
            
            # Format results
            formatted = []
            for r in results:
                formatted.append({
                    "name": r["name"],
                    "size": r["size"],
                    "seeds": r["nb_seeds"],
                    "peers": r["nb_peers"],
                    "magnet": r["magnet_uri"],
                    "provider": r["engine_url"]
                })
            return formatted
            
        except Exception as e:
            print(f"Torrent search failed: {e}")
            return []
