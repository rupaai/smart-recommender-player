# M1 Player MVP ⭐

**Features:** P1–P10, P13, P25, S4, plus a bare movie list.
**Done when:** on a laptop or phone you pick a movie at `/nl/`, watch it in the Northern Lights player, change skip length without leaving the player, switch audio and subtitles, close it, and come back to "Resume from 42:10".

**Builds on M0:** `/api/nl/play/{id}`, the `/nl/jf/` proxy, `player-core.js` (the adapter). All player UI talks to the adapter only, never to hls.js or `<video>` directly.

## Player screen (`templates/nl/player.html`, `static/nl/player.js`, `static/nl/player.css`)

Dark, full-bleed video. Controls overlay, hidden until input (P25: show on mouse move, tap, or any key; hide after 3 s of no input while playing; always visible while paused).

- **Top bar:** ← back (to `/nl/`), title, plus "S1 · E3 · Episode name" for episodes (P1).
- **Centre:** big play/pause.
- **Bottom bar:**
  - seek bar with elapsed, remaining and total time (P4);
  - ⟲ skip back and ⟳ skip ahead (P2), each showing a brief "−10s / +10s" toast;
  - **skip-length chip** (P3): tap cycles 10 → 15 → 30 → 60 s, or long-press/click opens a small menu. The value shows on the skip buttons and is remembered in `localStorage` (`nl.skipSeconds`, with try/catch).
  - volume and mute (P5);
  - **Audio** menu (P6) and **Subtitles** menu (P7, details below);
  - fullscreen.
- **Keyboard (P13):** Space/K play-pause, ←/→ skip by the chosen length, J/L the same, ↑/↓ volume, M mute, F fullscreen, C cycle subtitles, `[` / `]` change skip length, `?` shows a help overlay listing these.

## Tracks (P6, P7)

- `/api/nl/play/{id}` must also return `audio_tracks` and `subtitle_tracks` from `MediaSources[0].MediaStreams`: index, language, display title, codec, channels, `IsDefault`, `IsForced`, `IsHearingImpaired`, and `IsExternal`/`DeliveryMethod`.
- Labels must be readable: "English · 5.1 · AAC", "English (SDH)", "Spanish · Forced". Always include "Off" for subtitles.
- **Changing audio** needs a new stream from Jellyfin: call `/api/nl/play/{id}?audio=<index>&start=<seconds>` (pass `AudioStreamIndex` and `StartTimeTicks` to PlaybackInfo), then reload the adapter at the same position. The UI keeps the position, so it feels like a hiccup, not a restart.
- **Subtitles:** prefer text subs delivered as external WebVTT (`DeliveryMethod: External`, `DeliveryUrl`, proxied through `/nl/jf/` with keys stripped) and add them as `<track>`s, so switching is instant. Image subs (PGS/VobSub) need burning in: request them with `SubtitleStreamIndex` plus `SubtitleMethod=Encode`, then reload like an audio change. The proxy allowlist may need `Videos/{id}/{msid}/Subtitles/...`, which is already under `Videos/`.

## Resume and progress (P8, P9, P10)

- New API routes (server-side calls to Jellyfin as `JELLYFIN_USER_ID`):
  - `POST /api/nl/progress {jellyfin_id, play_session_id, media_source_id, position_seconds, paused, event: start|progress|stop}` → `/Sessions/Playing`, `/Sessions/Playing/Progress`, `/Sessions/Playing/Stopped`. Jellyfin uses ticks (seconds × 10,000,000).
  - The player sends `start` when playback begins, `progress` every 10 s and on pause and seek, and `stop` on back, page hide (`visibilitychange`/`pagehide`, with `navigator.sendBeacon`) and on the video ending.
- `/api/nl/play/{id}` also returns `resume_seconds` (from `UserData.PlaybackPositionTicks`) and `played`.
- If `resume_seconds > 30` and the title isn't finished, show "Resume from 42:10" / "Start over" before playing (P8).
- Jellyfin marks an item played on its own when stopped past about 90%. Also add `POST /api/nl/played/{jellyfin_id}` and `DELETE` for manual marking (P10).
- Also call our existing `/api/watch/start` when playback starts, so the recommender's watch history keeps working (check `server.py` for the request shape).

## Server unreachable (S4)

If `/api/nl/play` returns 503 or the stream errors repeatedly, show a calm full-screen message with the retry and back buttons: "Can't reach the movie server. Is mars on?" No stack traces.

## Bare movie list (`/nl/`)

`GET /api/nl/library?type=movie` returns our `media_items` that have a source file: id, title, year, runtime and poster (we already store TMDB posters, see `database.py`). `/nl/` shows a plain poster grid, and clicking one opens `/nl/play/{id}`. This is a stopgap, M2 replaces it.

## Checks

- `tests/test_nl_player_api.py`: track extraction and labels from a sample MediaStreams payload; the progress endpoint converts seconds to ticks and calls the right Jellyfin URL per event; resume_seconds comes from UserData; audio and subtitle params reach PlaybackInfo.
- `make check` passes.
- Manual, in the PR description: play an mp4 and an mkv, switch audio on a multi-track file, turn on a text subtitle and an image subtitle, change skip length with the chip and with `]`, leave and resume.

## Build prompt

```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M01-player-mvp.md

Implement M1 on top of the M0 code (services/jellyfin_playback.py, static/nl/player-core.js).
All player UI goes through the PlayerAdapter in player-core.js; extend the adapter if you need
something new (e.g. textTracks) rather than reaching around it. Keep the Jellyfin API key
server-side and keep the proxy allowlist. Write tests/test_nl_player_api.py with Jellyfin faked.
Run `make check`. Update PROJECT_HANDOVER.md §14 and the milestones README. Open the PR into
northern-lights-player-app with a manual-test checklist in the description.
```
