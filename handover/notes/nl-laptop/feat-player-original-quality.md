---
feature: nl-laptop
status: ready
new: true
---
## Line
- **Laptop app at /player** (built): pages at `/player` reuse the TV templates, with laptop controls and original quality: a laptop profile (400 Mbps) sends the video untouched or only repackaged, the audio is converted only when needed (FLAC or AAC stereo), and anything that would re-encode the video, including picture subtitles and Dolby Vision 7, asks first with "Play a converted copy (lower quality)".

## Open work
- **Laptop app, next:** check on mars whether Jellyfin can copy Dolby Vision 7's HDR10 base layer (e.g. "Michael (2026)"); until then such films ask for a converted copy.

## What changed
`laptop_profile(caps)` and `client=laptop` in `/api/nl/play`; `stream_for(copy_video_only=True)` accepts a file or an HLS stream only when `video_is_copied()` says Jellyfin copies the video (only container and audio TranscodeReasons), else 415 `can_convert`. Saved quality cap is ignored for the laptop; a cap picked for one viewing counts as agreeing to a converted copy. `player.js` sends `client=laptop` plus caps (`NLPlayerExtras.playDeviceParams`) and asks before burning in a picture subtitle.
