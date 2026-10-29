---
feature: nl-subtitles
status: ready
---
## Line
- **TV subtitle seek (2026-10-29, needs the TV app update, not yet run on the TV):** side-loaded subtitles are read once as a single sample (`experimentalParseSubtitlesDuringExtraction(false)` in `PlayerController`, legacy decoding on the `TextRenderer` in `NlRenderersFactory`). Media3 1.5's default gave each subtitle file its own source merged with the film, and a seek the subtitle queues could not serve (between lines, or past the last one) downloaded the whole file again before playback went on. Track ids may now read "1:nlsub3"; `isSideLoadedSubtitleId` already takes that.

## What changed
- On the TV, a subtitle file now loads once, as one piece, and a seek no longer downloads it again. Seeking with subtitles on is as quick as with them off.
- Needs the TV app update. Not yet tried on the real TV (no Android SDK here).
