---
feature: nl-laptop
status: ready
---
## Line
- **Laptop app at /player** (built): pages at `/player` reuse the TV templates, with laptop controls and original quality: a laptop profile (400 Mbps) sends the video untouched or only repackaged, the audio is converted only when needed (FLAC or AAC stereo), and anything that would re-encode the video, including picture subtitles, asks first with "Play a converted copy (lower quality)". Dolby Vision with a base layer (profile 7 `DOVIWithEL`, 8.x) is listed in the laptop's HEVC range types so Jellyfin 10.11 copies it; profile 5 (`DOVI`) still converts. The laptop gets Jellyfin's `main.m3u8` (the copied stream), not `master.m3u8`: for HDR films the master list also offers an h264 SDR re-encode, which hls.js picks on a non-HDR screen (`original_picture_playlist`). Jellyfin calls every HLS stream "Transcode", so `describe_conversion` judges by the reasons: container only = no note, container + sound only = state `sound` (Picture menu only, no pop-up). VP9 films (e.g. The Matrix) still ask for a converted copy.

## Open work
- **Laptop app, next:** after deploy, play Michael and The Menu on /player in Chrome and Safari and confirm the FFmpeg log shows `-codec:v:0 copy`, colours look right and seeking is quick.

## What changed
Seen on prod 2026-11-02: Michael, The Menu and Silence of the Lambs were re-encoded to h264 (dull colours, slow seeks) because hls.js chose the master list's SDR choice (`VideoCodec=h264&AllowVideoStreamCopy=false`).
