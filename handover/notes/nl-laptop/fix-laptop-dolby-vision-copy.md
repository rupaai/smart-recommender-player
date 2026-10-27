---
feature: nl-laptop
status: ready
new: true
---
## Line
- **Laptop app at /player** (built): pages at `/player` reuse the TV templates, with laptop controls and original quality: a laptop profile (400 Mbps) sends the video untouched or only repackaged, the audio is converted only when needed (FLAC or AAC stereo), and anything that would re-encode the video, including picture subtitles, asks first with "Play a converted copy (lower quality)". Dolby Vision with a base layer (profile 7 `DOVIWithEL`, 8.x) is listed in the laptop's HEVC range types so Jellyfin 10.11 copies it (tagged hvc1, browser shows the HDR10 base); profile 5 (`DOVI`) still converts. Jellyfin's tone mapping is off, so a converted HDR copy looks dull.

## Open work
- **Laptop app, next:** confirm "Michael (2026)" (DV 7.6) plays untouched in Chrome and Safari after this deploy.

## What changed
Checked on mars 2026-10-27 (Jellyfin 10.11.11): without DOVIWithEL in VideoRangeType, Michael got VideoRangeTypeNotSupported and the converted copy was an h264_qsv re-encode relabelled bt709 (dull, slow seeks). With it, only ContainerNotSupported and AudioCodecNotSupported remain.
