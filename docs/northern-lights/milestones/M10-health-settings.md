# M10 Health and settings

**Features:** H1–H6, P18–P20, P24.

## Design
- **Downloads in the new app (H1, H2):** a Downloads screen that reuses `/api/torrent/status` and the download store: queued, downloading (with speed and time left; Labiba wants these visible), importing, ready or failed, with Retry. "Ready" links straight to Play.
- **Compact status (H3):** a small indicator in the header: Jellyfin reachable, free disk space (reuse `services/disk_guard.py`), and the download queue size.
- **Admin screen (H4):** `/nl/admin` shows Jellyfin diagnostics (`/api/jellyfin/status`), library repair, and links to Jellystat, Uptime Kuma and Dozzle if they're configured. Hidden from the TV home.
- **Settings (H5, H6):** per-person playback defaults (skip length, autoplay, subtitles on, preferred quality). TV settings are split into Playback and Display only.
- **Picture (P18, P19, P20, P24):** fit/fill/original; a quality picker that re-requests PlaybackInfo with a lower `MaxStreamingBitrate`; a friendly "This TV can't play this format, so it's being converted" note or an error naming the codec; and audio-description tracks labelled "AD".

## Build prompt
```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M10-health-settings.md

Bring downloads, health and settings into /nl/, reusing existing endpoints. Split into PRs
(downloads; status+admin; settings+picture). Tests + make check for each.
```
