# M0 Foundation: a movie plays through our code

**Features:** S1 (backend talks to Jellyfin), S2 (stream choice, browser only), S3 (player adapter).
**Done when:** opening `/nl/play/<media_item_id>` on a laptop or phone plays that movie or episode in our own page, with the video served through our app, never straight from Jellyfin.

Nothing pretty yet: a full-screen video with the browser's native controls is fine. M1 builds the real player on top.

## How it works

```
browser ──/api/nl/play/{id}──▶ FastAPI ──PlaybackInfo──▶ Jellyfin
browser ──/nl/jf/videos/...──▶ FastAPI proxy ──────────▶ Jellyfin (stream / HLS)
```

1. **Find the Jellyfin item.** Our `media_items` rows know the file path (`item["sources"][0]["file_path"]`). In the server the app and Jellyfin both mount the media at `/media`, so the paths match. Ask Jellyfin for all Movie and Episode items with their `Path` (`GET /Items?Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path&UserId=...`), keep a `path -> jellyfin_id` dict in memory, and refresh it once when a path isn't found (new downloads).
2. **Ask Jellyfin how to play it (S2).** `POST /Items/{jf_id}/PlaybackInfo?UserId=...` with a JSON body containing our browser `DeviceProfile`, `MaxStreamingBitrate` (e.g. 20 Mbps), and `EnableDirectPlay/EnableDirectStream/EnableTranscoding: true`. Jellyfin answers with `MediaSources[0]` and a `PlaySessionId`.
   - If `SupportsDirectPlay` or `SupportsDirectStream`: the stream is `/Videos/{jf_id}/stream?static=true&MediaSourceId=...&PlaySessionId=...` (the `Container` field tells you mp4/mkv/webm).
   - Else use `TranscodingUrl` (an HLS `.m3u8` path, relative to the Jellyfin base).
3. **Proxy (hides Jellyfin and the API key).** `GET /nl/jf/{path:path}` forwards to `{JELLYFIN_URL}/{path}` with the API key header, passing through the `Range` header and the status code (206 for ranges), `Content-Type`, `Content-Length`, `Content-Range` and `Accept-Ranges`. Stream the body in chunks (`requests` with `stream=True` and a `StreamingResponse`); don't buffer whole files.
   - **Allowlist:** only paths starting with `videos/` or `Videos/` are proxied; everything else returns 404. This stops the proxy from becoming an open door to Jellyfin's admin API.
   - **Strip secrets:** remove `api_key` / `ApiKey` query parameters from every URL we hand the browser, including the URLs inside `.m3u8` playlist bodies (rewrite playlists line by line). Playlist URLs are relative, so they keep working through the proxy because the proxy mirrors Jellyfin's paths.
4. **Player adapter (S3).** `static/nl/player-core.js` exposes a small interface the rest of our UI will use: `load({url, kind})`, `play()`, `pause()`, `seek(seconds)`, `currentTime`, `duration`, `on(event, fn)`, `destroy()`. Behind it: hls.js for `kind: "hls"` when `Hls.isSupported()`; native `<video>` for direct files, and for HLS on Safari/iOS, which plays HLS natively. Nothing outside `player-core.js` may touch hls.js.
   - Vendor hls.js (1.5.x, the `hls.min.js` build) into `static/nl/vendor/` so the app works without a CDN. If downloading it isn't possible in your environment, load it from `https://cdn.jsdelivr.net/npm/hls.js@1.5.20/dist/hls.min.js` and add a TODO to vendor it.

## Files

| File | What |
|---|---|
| `services/jellyfin_playback.py` (new) | `JellyfinPlayback(jellyfin_service)`: `find_item_id(file_path)`, `playback_info(jf_id, profile)`, `stream_for(item)` returning `{url, kind, play_method, play_session_id, media_source_id, jellyfin_id, container, duration_seconds, title}`. Reuse `JellyfinService.session` and `base_url`. Plus pure helpers `strip_api_key(url)` and `rewrite_playlist(text)`, so they're easy to test. |
| `services/device_profiles.py` (new) | `BROWSER_PROFILE` dict: direct play mp4/m4v (h264, aac/mp3) and webm (vp9/av1, opus/vorbis); transcoding profile `{"Container": "ts", "Type": "Video", "VideoCodec": "h264", "AudioCodec": "aac", "Protocol": "hls", "Context": "Streaming", "MaxAudioChannels": "2"}`; subtitle profile external `vtt`. Keep it conservative; TV and Cast profiles come in M3/M8. |
| `server.py` | `GET /api/nl/play/{item_id}` (404 if the item or file is unknown, 503 with a clear message if Jellyfin is unreachable or not configured), `GET /nl/jf/{path:path}` (proxy), `GET /nl/play/{item_id}` (page). |
| `templates/nl/player.html` (new) | Minimal dark page: one `<video>`, loads `/static/nl/player-core.js` and `/static/nl/play.js`. |
| `static/nl/player-core.js`, `static/nl/play.js` (new) | Adapter as above; `play.js` reads the id from the URL, calls `/api/nl/play/{id}`, loads the stream and starts playback. Shows a plain error message on failure. |
| `tests/test_nl_playback.py` (new) | See checks. |

## Checks

`tests/test_nl_playback.py`, with Jellyfin faked (monkeypatch the `requests` session or pass in a fake):
- path lookup finds the id, and refreshes once on a miss;
- direct play is chosen when `SupportsDirectPlay` is true, HLS otherwise;
- `strip_api_key` removes `api_key=` and `ApiKey=` and keeps other params;
- `rewrite_playlist` strips keys on URI lines and on `URI="..."` attributes and leaves `#EXT` lines alone;
- the proxy refuses `System/Info`, `Users`, `../` tricks, and allows `videos/...`;
- `/api/nl/play/{id}` returns 503 (not 500) when Jellyfin is down.

Then `make check`. If you can, run the app locally against a real Jellyfin and play one mp4 and one mkv. Otherwise say so in the PR.

## Decisions taken (change them here if you disagree)
- The proxy goes through FastAPI rather than sending the browser to port 8096. It's one address for everything (LAN, Tailscale, later the TV), and the key stays hidden. The cost is a little CPU on mars, which is fine for two viewers.
- No login yet. M4 adds who's watching. Until then, playback uses `JELLYFIN_USER_ID`.

## Build prompt

```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M00-foundation.md

Implement M0 exactly as the milestone file describes: services/jellyfin_playback.py,
services/device_profiles.py, the three routes in server.py, templates/nl/player.html,
static/nl/player-core.js + play.js, and tests/test_nl_playback.py.
Security matters here: the Jellyfin API key must never appear in any response body
or playlist, and the proxy only forwards videos/ and Videos/ paths.
Keep changes to existing files minimal (server.py routes only). Run `make check`
and fix anything it reports. Update PROJECT_HANDOVER.md (branch list + a short
"Northern Lights M0" section) and set M0 to "done" in the milestones README.
Commit with a clear message.
```
