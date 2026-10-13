# M3 On the TV

**Features:** T1–T5, S2 (TV profile), S5.
**Done when:** a "Northern Lights" icon sits on the TCL Google TV home screen, and you can browse and watch with the remote.

## Pieces
1. **TV layout (T1):** `/nl/tv/` serves the same data as M2 with a 10-foot layout: 1920×1080 base, a few wide rows (Continue Watching, Movies, Shows), big cards, a clear focus ring (scale plus glow), text at least 24 px, and no hover-only actions.
2. **Remote navigation (T2):** arrow keys move focus between cards and rows, Enter selects, Back goes back. Use Norigin Spatial Navigation if adding a small dependency is OK (it's React-oriented; for vanilla JS, `@noriginmedia/norigin-spatial-navigation` has a core you can use, or write a small focus manager: rows × columns grid, remember the column per row). **Decision for the builder:** prefer a small hand-written focus manager (no build step in this repo).
3. **Android TV app (T3):** a new top-level folder `tv-app/` with a Capacitor (or minimal Kotlin WebView) Android project.
   - App name "Northern Lights", a banner icon (320×180, required for the Google TV home screen) and the `LEANBACK_LAUNCHER` intent filter.
   - Starts at `http://<mars-lan-ip>:8000/nl/tv/`. The address comes from a settings screen on first launch, saved locally (S5). Allow cleartext HTTP to that host only (network security config).
   - Build the APK with GitHub Actions (a new workflow, only on changes under `tv-app/` or manual), uploaded as an artifact. Installing it means sideloading via `adb install` or the "Downloader" app. Write the steps in `tv-app/README.md`.
4. **Native playback (T4, T5):** when running inside the app, play through Media3/ExoPlayer instead of `<video>`, because it supports far more formats on the TV.
   - The web page calls a small JS bridge (`window.NorthernLightsNative.play({url, startSeconds, title, audioTracks, subtitleTracks})`).
   - The native player reports progress back through the bridge, so M1's progress calls still happen.
   - Native controls: left/right skip by the chosen length, hold to seek faster, Back returns to the web page, and a MediaSession so the system and remote buttons work.
   - The `PlayerAdapter` in `player-core.js` gets a `NativeAdapter` used when the bridge exists.
5. **TV device profile (S2):** `TV_PROFILE` in `services/device_profiles.py` adds HEVC, AC3/EAC3 and MKV direct play (what the TCL's decoder handles). The page passes `?client=tv` so `/api/nl/play` picks it.

## Decisions to confirm with Labiba
- Capacitor vs a hand-written Kotlin WebView. Recommendation: plain Kotlin WebView plus Media3. It's fewer moving parts, and Capacitor's benefits mostly matter for phone apps.

## Checks
Unit tests for profile selection. `make check`. The APK builds in CI. Manual on the TV: install, set the address, browse with the remote, play an HEVC mkv, skip with left/right, leave with Back and check that the progress saved.

## Build prompt
```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M03-tv-app.md

This milestone has two parts; do them as two PRs into northern-lights-player-app:
(a) the /nl/tv/ web layout + focus manager + TV_PROFILE (Python/JS, tested by make check),
(b) tv-app/ Android project (Kotlin WebView + Media3, JS bridge, LEANBACK launcher, banner)
    and a GitHub Actions workflow that builds the debug APK as an artifact.
Never put the Jellyfin key in the Android app; it only talks to our FastAPI server.
```
