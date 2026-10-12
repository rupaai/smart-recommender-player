# M5 Subtitles that fix themselves (switch-over point)

**Features:** D1–D4, P15, P16.
**Done when:** a missing or out-of-sync subtitle gets fixed without leaving the player.

## Design
- **Prerequisite (Labiba, once):** install Jellyfin's OpenSubtitles plugin and sign it in to an opensubtitles.com account. The free tier has a daily download limit.
- **Search (D1, D4):** our server calls `GET /Items/{id}/RemoteSearch/Subtitles/{language}` and returns a list: name, language, format, rating, download count, `IsHashMatch` and the provider.
- **Choose (D2):** the Subtitles menu gets "Find subtitles…". It opens a list with an exact-match badge (hash match) sorted first, plus language, SDH and forced markers. On the TV it's a plain focusable list; typing a language isn't needed (it defaults to the viewer's preferred language and offers a switch).
- **Apply (D3):** `POST /Items/{id}/RemoteSearch/Subtitles/{subtitleId}`, then re-fetch PlaybackInfo and select the new track. Text subs load as a `<track>` without reloading the video.
- **Appearance (P15):** size S/M/L/XL, background none/shadow/box, and position (raise the line). Per person (M4 prefs) and applied with `::cue` CSS on web; Media3 has a `CaptionStyleCompat` equivalent.
- **Delay (P16):** −/+ 0.25 s steps from the Subtitles menu, shown as "+0.75 s". Web: shift cue times on the TextTrack. TV: Media3 subtitle offset. Not saved (it's per file, and per-session is fine).
- **Errors:** "No subtitles found for English", plus a message when the plugin isn't set up or the daily limit is reached (map Jellyfin's errors to these).

## After M5: switch over
Merge `northern-lights-player-app` into `main` once you're happy, and make `/nl/` the default page (decide then whether the old ticket-look UI stays at `/classic`).

## Build prompt
```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M05-subtitles.md

Implement subtitle search/download/apply via Jellyfin's RemoteSearch API (server-side only),
subtitle appearance and delay, on both the web player and the TV native player. Friendly
errors for plugin-missing and quota-reached. Tests with fake Jellyfin responses; make check;
handover + README; PR into northern-lights-player-app.
```
