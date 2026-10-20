# Casting to a Chromecast and the phone keyboard (optional)
Skip this and the player has no Cast button. Casting works from **Chrome on Android or a computer**; iPhones and Safari cannot cast from a web page. The phone becomes the remote (play, pause, skip, seek, audio and subtitles, next episode, stop), and when you stop, the player offers "Continue here from 42:10".

1. **Open the app over HTTPS on the phone.** Google's Cast code refuses to run on a plain `http://` page (only `https://` and `localhost` count). Pick one:
   - Tailscale (easiest): on `mars` run `sudo tailscale serve --bg --https=443 http://localhost:8000` (switch on HTTPS certificates under Tailscale admin → DNS first). Then use `https://mars.<your-tailnet>.ts.net/nl/` on the phone. Run `tailscale serve status` to see the exact address.
   - Or give the server a certificate for its LAN name (for example Caddy or mkcert in front of port 8000) and use that address.
   Without HTTPS the Cast button simply never appears.
2. **Tell the app where the Chromecast reaches the server.** The Chromecast is **not on Tailscale**, so it needs the server's home-network address, not the `ts.net` one. Find it (`ip -4 addr` on `mars`, or your router's device list; give `mars` a fixed address in the router so it does not change). In `homelab/config.yaml` add `cast_base_url` under `northern_lights:` (the `people` block is already there):
   ```
   northern_lights:
     cast_base_url: http://192.168.1.50:8000
   ```
   Merge the change, then promote the app in Homelab. Check from the phone (on the same Wi-Fi): `http://192.168.1.50:8000/healthz` opens.
3. **Set the token secret.** The Chromecast gets stream links that carry a signed token valid for 6 hours and good for one title only. In the Homelab companion open the app, then **Secrets**, and set `NL_CAST_SECRET` to a long random text (for example the output of `openssl rand -hex 24`), then promote.
   Without it the app makes a random secret each time it starts, so casts that are running stop when the app restarts or is promoted (start casting again). Changing the secret has the same effect. The secret is never shown in the app and is unrelated to the Jellyfin key.
4. **Try it.** Open a title in Chrome on the phone (HTTPS address), tap the Cast icon in the player controls, pick the Chromecast. With no further setup the picture comes from Google's **Default Media Receiver** (plain video, no Northern Lights screen).
5. **Optional: the Northern Lights receiver screen** (backdrop, title, episode, aurora progress bar). Google wants a one-time **$5** developer registration:
   1. https://cast.google.com/publish → pay the $5 → **Add new application** → **Custom receiver**.
   2. Receiver application URL: `http://192.168.1.50:8000/nl/cast/receiver` (the same LAN address as above; if the console insists on `https`, put the LAN certificate from step 1 in front and use that address, and set `cast_base_url` to the same `https://` address so the artwork is not blocked as mixed content).
   3. Register your Chromecast's serial number under **Add new device** (an unpublished app only runs on registered devices; it takes up to 15 minutes to start working). You can leave the app unpublished.
   4. Copy the 8-character **Application ID** into the config next to `cast_base_url`: `cast_receiver_app_id: ABCD1234`, then promote again.
   Preview the screen in any browser: `/nl/cast/receiver?preview=1&title=Alpha&episode=S1%20E2&progress=0.4`.

What the Chromecast can play: H.264 video with AAC or MP3 audio (an mp4 that already is that plays directly; everything else is converted by Jellyfin to H.264/AAC stereo HLS, which uses the server's CPU/QSV). Picture subtitles are burned in; text subtitles are sent as WebVTT. HEVC and surround sound are converted, never passed through.

If it does not work: no Cast icon means the page is not HTTPS, the browser is not Chrome, or no Chromecast answered on the same network. The Chromecast showing "can't play" usually means `cast_base_url` is not reachable from the Chromecast (test it from another device on the Wi-Fi) or the cast session is older than 6 hours.

**Phone keyboard for TV search (Northern Lights M8b).** Nothing to set up. On the TV's Search page there is a code and a QR; scan it (or open the same address plus `/nl/type` and type the code) on a phone on the same network and what you type appears on the TV. Codes last 10 minutes and are forgotten when the app restarts (the TV makes a new one by itself). If the phone cannot open the page, it cannot reach the address the TV page uses.
