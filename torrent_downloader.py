from qbittorrent.client import Client
from typing import Dict, Any
import time
import json
from ws_manager import manager

class TorrentDownloader:
    def __init__(self, config):
        """Initialize with qBittorrent configuration"""
        qbt_config = config.get("qbittorrent", {})
        base_url = f"http://{qbt_config.get('host', 'localhost')}:{qbt_config.get('port', 8080)}"
        self.client = Client(base_url)
        self.client.login(
            username=qbt_config.get("username", "admin"),
            password=qbt_config.get("password", "adminadmin")
        )
        
    async def download(self, magnet_link: str) -> Dict[str, Any]:
        """
        Add torrent to qBittorrent and return download status
        Returns:
            {
                "hash": str,
                "name": str,
                "save_path": str,
                "progress": float,
                "status": str
            }
        """
        try:
            # Add torrent
            self.client.download_from_link(magnet_link)
            
            # Get torrent info and emit progress updates
            while True:
                time.sleep(1)
                torrents = self.client.torrents()
                torrent = next((t for t in torrents if t.magnet_uri == magnet_link), None)
                
                if not torrent:
                    return {"error": "Torrent not found after adding"}
                
                # Broadcast progress update
                progress_data = {
                    "hash": torrent.hash,
                    "name": torrent.name,
                    "progress": torrent.progress,
                    "status": torrent.state,
                    "download_speed": torrent.dlspeed,
                    "upload_speed": torrent.upspeed
                }
                await manager.broadcast(json.dumps({
                    "type": "download_progress",
                    "data": progress_data
                }))
                
                # Return if download is complete
                if torrent.progress == 1.0:
                    return {
                        "hash": torrent.hash,
                        "name": torrent.name,
                        "save_path": torrent.save_path,
                        "progress": torrent.progress,
                        "status": torrent.state
                    }
        except Exception as e:
            print(f"Torrent download failed: {e}")
            await manager.broadcast(json.dumps({
                "type": "download_error",
                "error": str(e)
            }))
            return {"error": str(e)}
