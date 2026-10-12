# M8 Casting

**Features:** C1–C4, T6.

## Design
- **Cast sender (C1):** add the Google Cast Web Sender SDK to the web player (Chrome on Android, desktop Chrome). It requires a secure context, so phones must open the app over HTTPS (`tailscale serve`, or a LAN certificate). Note that in the runbook.
- **Receiver (C2):** a custom Web Receiver page at `/nl/cast/receiver` (register an app id in the Google Cast developer console, a one-time $5 fee; until then use the Default Media Receiver). It shows the backdrop, title, episode, progress bar and the Northern Lights foil.
- **Stream for Cast (S2):** a `CAST_PROFILE` (H.264/AAC HLS, or direct mp4). The URL handed to the Chromecast must be the **LAN** address of mars (the Chromecast isn't on Tailscale) and must work without cookies. Use a short-lived signed token in the URL (`/nl/jf/...?t=`, validated by the proxy, expiring after 6 hours).
- **Phone remote (C3):** while casting, the phone player becomes a remote with play/pause, skip, seek, tracks, next episode and stop. Progress keeps reporting to Jellyfin from the server side.
- **Hand-back (C4):** on disconnect, the phone offers "Continue here from 42:10".
- **Phone keyboard for TV search (T6):** the TV search screen shows a short code/QR. Opening it on the phone sends the typed text to the TV through a small server-side channel (polling or SSE).

## Build prompt
```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M08-casting.md

Add Google Cast sending, a custom receiver page, a cast device profile and signed short-lived
stream tokens (never the Jellyfin key). Two PRs into northern-lights-player-app: casting, then
the phone-as-keyboard helper. Document the HTTPS requirement and receiver registration.
```
