"""Linked downloads and their Jellyfin folders use canonical catalogue metadata."""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import nl_fake  # noqa: E402


def main():
    tmp = tempfile.mkdtemp(prefix="linked_download_metadata_test_")
    try:
        ctx = nl_fake.boot(tmp)
        server = ctx["server"]
        state = ctx["state"]
        client = ctx["client"]
        db = ctx["db"]
        state.downloads_store.clear()

        movie_id = db.get_or_create_media_item("Canonical Feature", 2024, "movie")
        show_id = db.get_or_create_media_item("Canonical Series", 2022, "tv")
        started = []

        async def fake_download(magnet, download_id, item_id, title, media_type="movie"):
            started.append((item_id, title, media_type))

        state.torrent_downloader.download = fake_download

        for item_id, release_name, declared_type, expected_title, expected_type in (
            (movie_id, "Wrong.Feature.1.2024.1080p.WEB-DL", "tv", "Canonical Feature", "movie"),
            (show_id, "Another.Series.S01E01.1080p.WEB-DL", "movie", "Canonical Series", "tv"),
        ):
            response = client.post("/api/torrent/download", json={
                "magnet_link": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                "movie_id": item_id,
                "movie_title": release_name,
                "media_type": declared_type,
            })
            body = response.json()
            download_id = body.get("download_id")
            remembered = state.download_requests.get(download_id)
            status = state.downloads_store.get(download_id)
            assert response.status_code == 200 and body.get("success"), response.text
            assert body.get("clean_title") == expected_title, body
            assert remembered and remembered["title"] == expected_title, remembered
            assert remembered["media_type"] == expected_type, remembered
            assert status and status["name"] == expected_title, status
            assert status["media_type"] == expected_type, status
        assert started == [
            (movie_id, "Canonical Feature", "movie"),
            (show_id, "Canonical Series", "tv"),
        ], started

        unlinked = client.post("/api/torrent/download", json={
            "magnet_link": "magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01",
            "movie_title": "Sample.Film.2.2025.1080p.WEB-DL",
            "media_type": "movie",
        }).json()
        unlinked_item = db.get_item_by_id(unlinked["movie_id"])
        assert unlinked["clean_title"] == "Sample Film 2", unlinked
        assert unlinked_item and unlinked_item["title"] == "Sample Film 2", unlinked_item

        downloader = state.torrent_downloader
        verified = []
        downloader.jellyfin.refresh_and_verify = lambda title, media_type: (
            verified.append((title, media_type)) or {"visible": True}
        )

        movie_staging = downloader._torrent_staging_dir("metadata-movie")
        movie_input = os.path.join(movie_staging, "Wrong.Feature.1.2024.1080p.mkv")
        with open(movie_input, "wb") as video:
            video.write(b"video")
        movie_outputs = downloader._organize_for_jellyfin(
            "metadata-movie", movie_id, "Wrong Feature 1", "tv"
        )
        movie_path = os.path.join(
            downloader.movie_target,
            "Canonical Feature (2024)",
            "Canonical Feature (2024).mkv",
        )
        assert movie_path in movie_outputs and os.path.isfile(movie_path), movie_outputs

        show_staging = downloader._torrent_staging_dir("metadata-show")
        show_input = os.path.join(show_staging, "Other.Series.S01E01.1080p.mkv")
        with open(show_input, "wb") as video:
            video.write(b"video")
        show_outputs = downloader._organize_for_jellyfin(
            "metadata-show", show_id, "Other Series", "movie"
        )
        show_path = os.path.join(
            downloader.tv_target,
            "Canonical Series (2022)",
            "Season 01",
            "Canonical Series - S01E01.mkv",
        )
        assert show_path in show_outputs and os.path.isfile(show_path), show_outputs
        assert verified == [("Canonical Feature", "movie"), ("Canonical Series", "tv")], verified
        print("✅ linked movies and shows keep their canonical title and media type through Jellyfin organization")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
