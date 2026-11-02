---
feature: nl-laptop
status: ready
---
## Line
- **Laptop app at /player** (built): pages at `/player` reuse the TV templates, with laptop controls and original quality: a laptop profile (400 Mbps) sends the video untouched or only repackaged, the audio is converted only when needed (FLAC or AAC stereo), and anything that would re-encode the video, including picture subtitles, asks first with "Play a converted copy (lower quality)". Dolby Vision with a base layer (profile 7 `DOVIWithEL`, 8.x) is listed in the laptop's HEVC range types so Jellyfin 10.11 copies it; profile 5 (`DOVI`) still converts. Jellyfin calls every HLS stream "Transcode", so `describe_conversion` judges by the reasons: container only = no note, container + sound only = state `sound` ("Original picture. Only the TrueHD sound is converted...", Picture menu only, no pop-up). VP9 films (e.g. The Matrix) still ask for a converted copy.

## Open work
- **Laptop app, next:** confirm in Jellyfin's FFmpeg log that Michael is sent with `-codec:v:0 copy`, and that Chrome and Safari play it.

## What changed
Checked on prod 2026-11-02: every laptop stream said "can't play this format, so it's being converted" although only the sound (or nothing) was converted.
