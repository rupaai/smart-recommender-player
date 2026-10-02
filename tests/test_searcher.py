"""
Offline Prowlarr adapter tests (tests/test_searcher.py)

Pure-Python: no network, no Prowlarr instance needed.
Run standalone:

    uv run python tests/test_searcher.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.torrent_service import (  # noqa: E402
    PROWLARR_CATEGORY_MAP,
    ProwlarrSearcher,
    _bencode_decode,
    _bencode_encode,
    _map_prowlarr_results,
    _magnet_info_hash,
)

failures = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


FIXTURE = [
    {
        "title": "Interstellar 2014 1080p BluRay x264",
        "protocol": "torrent",
        "indexer": "YTS",
        "size": 2147483648,
        "seeders": 120,
        "leechers": 10,
        "magnetUrl": "magnet:?xt=urn:btih:ABCDEF0123456789ABCDEF0123456789ABCDEF01&dn=x",
    },
    {  # duplicate hash → must be dropped
        "title": "Interstellar 2014 1080p (dupe)",
        "protocol": "torrent",
        "size": 1073741824,
        "seeders": 50,
        "leechers": 5,
        "magnetUrl": "magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01&dn=y",
    },
    {  # usenet → must be dropped
        "title": "Interstellar 2014 NZB",
        "protocol": "usenet",
        "size": 2147483648,
        "seeders": 0,
        "leechers": 0,
        "downloadUrl": "https://example.com/nzb/1.nzb",
    },
    {  # proxied grab URL instead of a magnet → kept with magnet = downloadUrl
        "title": "Interstellar 2014 720p WEBRip",
        "protocol": "torrent",
        "size": 734003200,
        "seeders": 30,
        "leechers": 3,
        "magnetUrl": "http://localhost:9696/2/download?apikey=x&link=abc",
        "downloadUrl": "http://localhost:9696/2/download?apikey=x&link=abc",
    },
    {  # 0-seed metadata → kept (indexers under-report seeders; the downloader
    # aborts dead/fake torrents itself)
        "title": "Interstellar 2014 CAM",
        "protocol": "torrent",
        "size": 524288000,
        "seeders": 0,
        "leechers": 2,
        "magnetUrl": "magnet:?xt=urn:btih:1111111111111111111111111111111111111111&dn=z",
    },
]


def test_mapping():
    print("\n[1] _map_prowlarr_results")
    results = _map_prowlarr_results(FIXTURE, "movie")
    check("real magnet + proxied URL + 0-seed survive", len(results) == 3, f"got {len(results)}")
    r = results[0]
    check("title mapped", r["name"] == "Interstellar 2014 1080p BluRay x264")
    check("quality badge", r["quality"] == "1080p")
    check("size mapped", r["size_bytes"] == 2147483648)
    check("size_display", r["size_display"] == "2.00 GB", f"got {r['size_display']}")
    check("seeds mapped", r["seeds"] == 120)
    check("media_type stamped", r["media_type"] == "movie")
    proxied = [x for x in results if x["name"].startswith("Interstellar 2014 720p")]
    check(
        "proxied grab URL kept as magnet for on-demand conversion",
        len(proxied) == 1 and proxied[0]["magnet"].startswith("http://localhost:9696/"),
    )
    check("empty input", _map_prowlarr_results([], "movie") == [])
    check("malformed entries skipped", _map_prowlarr_results([{"garbage": True}, None], "movie") == [])  # type: ignore[list-item]


def test_bencode():
    print("\n[2] bencode roundtrip")
    samples = [
        42, b"spam", [b"a", 7, [b"b"]],
        {b"info": {b"name": b"x.mkv", b"length": 5, b"piece length": 16384}},
        {b"b": 1, b"a": 2},
    ]
    for s in samples:
        encoded = _bencode_encode(s)
        decoded, _ = _bencode_decode(encoded)
        check(f"roundtrip {type(s).__name__}", decoded == s)
    check(
        "canonical sorted dict keys",
        _bencode_encode({b"b": 1, b"a": 2}) == b"d1:ai2e1:bi1ee",
    )


def test_info_hash():
    print("\n[2] _magnet_info_hash")
    check(
        "hex hash",
        _magnet_info_hash("magnet:?xt=urn:btih:ABCDEF0123456789ABCDEF0123456789ABCDEF01") == "abcdef0123456789abcdef0123456789abcdef01",
    )
    check("base32 hash kept", _magnet_info_hash("magnet:?xt=urn:btih:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567") is not None)
    check("no hash → None", _magnet_info_hash("magnet:?dn=nothing") is None)
    check("empty → None", _magnet_info_hash("") is None)


def test_categories_and_config():
    print("\n[3] category map + unconfigured behavior")
    check("movie → 2000", PROWLARR_CATEGORY_MAP["movie"] == "2000")
    check("tv → 5000", PROWLARR_CATEGORY_MAP["tv"] == "5000")
    # Simulate unconfigured instance: dotenv does not override *existing* env
    # vars, so setting empty strings here forces the disabled path even after
    # load_dotenv() runs inside ProwlarrSearcher.__init__.
    saved = os.environ.get("PROWLARR_URL"), os.environ.get("PROWLARR_API_KEY")
    os.environ["PROWLARR_URL"] = ""
    os.environ["PROWLARR_API_KEY"] = ""
    searcher = ProwlarrSearcher()
    check("unconfigured flag", not searcher.configured)
    check("unconfigured → []", searcher.search("anything") == [])
    os.environ.pop("PROWLARR_URL", None)
    os.environ.pop("PROWLARR_API_KEY", None)
    if saved[0] is not None:
        os.environ["PROWLARR_URL"] = saved[0]
    if saved[1] is not None:
        os.environ["PROWLARR_API_KEY"] = saved[1]


if __name__ == "__main__":
    test_mapping()
    test_bencode()
    test_info_hash()
    test_categories_and_config()
    print()
    if failures:
        print(f"❌ {len(failures)} check(s) failed: {failures}")
        sys.exit(1)
    print("✅ All searcher tests passed.")