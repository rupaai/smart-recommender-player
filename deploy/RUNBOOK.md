# Runbook: running couchcinema on `mars`

Steps marked **[Labiba]** need your hands. Everything else is a script. Background and
decisions are in `docs/DEPLOYMENT_PLAN.md`.

Two names used below: `mars` is the server (SSH as `gabstevens`, over Tailscale), and `deploy`
is the user the scripts create. `deploy` runs its own private Docker (rootless), so it can
only ever see our three containers, never Home Assistant or the Matter bridges.

## First-time setup

### 1. Get a runner token **[Labiba]**
GitHub → the repo → Settings → Actions → Runners → **New self-hosted runner** (Linux, x64).
Copy only the token from the `./config.sh ... --token XXXX` line. It expires after an hour, so
do this just before step 3.

### 2. Put the deploy folder on the server **[Labiba]**
From your Mac, in a terminal (after pulling `main`):

```
cd ~/codes/smart_movie_streamer
scp -r deploy gabstevens@mars:~/couchcinema-deploy
```

No GitHub login is needed on the server for this; the repo stays private.

### 3. Run the setup script **[Labiba]**
```
ssh gabstevens@mars
sudo bash ~/couchcinema-deploy/bootstrap.sh
```

- If it says packages are missing, it prints the one `apt-get install` line to run. Run it and
  start the script again (or add `--install-prereqs` and it does it for you).
- It asks for: the TMDB API key (from your Mac's `.env`), a Prowlarr API key (press Enter and
  it makes one), a healthchecks.io ping URL (press Enter to skip until you have one), and the
  runner token from step 1.
- At the end it prints the **ADMIN_TOKEN** and the **ntfy topic**. Save both.
- Running it again is harmless: it skips everything already done. To change one value later:
  `sudo bash ~/couchcinema-deploy/bootstrap.sh --set TMDB_API_KEY=newvalue`.

What it does, so there are no surprises:

1. Checks Docker, Compose, Tailscale and unattended-upgrades exist (it never installs them).
2. Creates the `deploy` user with its own rootless Docker. `deploy` is **not** in the `docker`
   or `sudo` group, and the script refuses to continue if it ever is.
3. Creates `/srv/couchcinema/...` and `$MEDIA_ROOT/{Movies,Shows,downloads}`.
4. Writes `/srv/couchcinema/.env` (mode 600) and gives Prowlarr the API key you chose.
5. Installs the GitHub runner as a service under `deploy`, label `mars`.
6. Starts Jellyfin and Prowlarr. The app itself starts with your first deploy (there is no
   image to run until CI has built one).

### 4. Copy your data across **[Labiba]**
Stop the app on your Mac first, so nothing is writing to the database. From your Mac:

```
scp ~/codes/smart_movie_streamer/catalogue_full.db gabstevens@mars:~/
rsync -av ~/codes/smart_movie_streamer/models/ gabstevens@mars:~/models-in/
rsync -av ~/path/to/your/films/ gabstevens@mars:~/films-in/      # repeat for shows, see below
```

Then on `mars`:

```
sudo bash ~/couchcinema-deploy/import-data.sh db ~/catalogue_full.db
sudo bash ~/couchcinema-deploy/import-data.sh models ~/models-in
sudo bash ~/couchcinema-deploy/import-data.sh media ~/films-in Movies      # or Shows
```

- `db` checks the file is a real catalogue, shows the row counts, and refuses to overwrite a
  database that is already on the server (the server owns your data from now on).
- `media` moves the files (not copies) and refuses to go past the 100GB cap.
- The Mac's copy is only for development from here. You both rate and watch through the server.

Once the app is deployed, the Phase 0 check should be re-run on the real database: open
`http://mars:8000/healthz` and make sure it says `"ok": true`.

### 5. First deploy **[Labiba]**
GitHub → Actions → **Deploy** → Run workflow → branch `main` → Run. When it goes green, open
`http://mars:8000`.

### 6. Jellyfin's first-run wizard **[Labiba]**
1. Open `http://mars:8096` and make an admin account.
2. Add libraries: **Movies** → folder `/media/Movies`; **Shows** → folder `/media/Shows`.
3. Dashboard → Playback → Transcoding: hardware acceleration **Intel QuickSync (QSV)**, and tick
   the decoding formats for H.264, HEVC and 10-bit HEVC. (If QSV shows an error, leave it on
   software for now and tell me; the app works either way.)
4. Dashboard → API Keys → **+** → name it `couchcinema` → copy the key.
5. Save it on the server:
   ```
   sudo bash ~/couchcinema-deploy/bootstrap.sh --set JELLYFIN_API_KEY=PASTE_KEY_HERE
   ```
6. Find your Jellyfin user id and save that too:
   ```
   curl -s -H 'Authorization: MediaBrowser Token="PASTE_KEY_HERE"' http://localhost:8096/Users \
     | python3 -c 'import json,sys; [print(u["Name"], u["Id"]) for u in json.load(sys.stdin)]'
   sudo bash ~/couchcinema-deploy/bootstrap.sh --set JELLYFIN_USER_ID=PASTE_ID_HERE
   ```

### 6b. A second Jellyfin user for "Who's watching" **[Labiba]** (optional, Northern Lights M4)
Skip this and the player works as before with the single user from step 6. To give the two of you separate progress, languages and a "Both of us" button:
1. Jellyfin → Dashboard → Users → **+**: add a user for your partner (not an administrator; enable access to the Movies and Shows libraries; the password is only for Jellyfin's own apps, Northern Lights never asks for it).
2. Print both ids (same command as step 6, it lists every user):
   ```
   curl -s -H 'Authorization: MediaBrowser Token="PASTE_KEY_HERE"' http://localhost:8096/Users \
     | python3 -c 'import json,sys; [print(u["Name"], u["Id"]) for u in json.load(sys.stdin)]'
   ```
3. In the repo, edit `deploy/config.server.yaml` (it has the block commented out): uncomment `northern_lights:` and fill in the two ids and the names you want on the screen (the partner's name is whatever you type; nothing is hard-coded). `profile_id` 1 and 2 are the two profiles of the Rate tab. A user *name* works in place of the id. Commit and deploy as usual: every deploy copies that file over `/srv/couchcinema/config.server.yaml`, so editing the copy on `mars` would be undone by the next deploy. (Jellyfin user ids are not secrets.)
4. After the deploy, open `http://mars:8000/nl/`: you get "Who's watching?". Check once: play something as "Both of us" for a minute, then look in Jellyfin (Dashboard → Users → each user → Continue Watching, or each user's own web page) that **both** users show it.
To go back to one viewer, comment the block out again.

### 6c. Casting to a Chromecast **[Labiba]** (optional, Northern Lights M8)
Skip this and the player has no Cast button. Casting works from **Chrome on Android or a computer**; iPhones and Safari cannot cast from a web page. The phone becomes the remote (play, pause, skip, seek, audio and subtitles, next episode, stop), and when you stop, the player offers "Continue here from 42:10".

1. **Open the app over HTTPS on the phone.** Google's Cast code refuses to run on a plain `http://` page (only `https://` and `localhost` count). Pick one:
   - Tailscale (easiest): on `mars` run `sudo tailscale serve --bg --https=443 http://localhost:8000` (switch on HTTPS certificates under Tailscale admin → DNS first). Then use `https://mars.<your-tailnet>.ts.net/nl/` on the phone. Run `tailscale serve status` to see the exact address.
   - Or give the server a certificate for its LAN name (for example Caddy or mkcert in front of port 8000) and use that address.
   Without HTTPS the Cast button simply never appears.
2. **Tell the app where the Chromecast reaches the server.** The Chromecast is **not on Tailscale**, so it needs the server's home-network address, not the `ts.net` one. Find it (`ip -4 addr` on `mars`, or your router's device list; give `mars` a fixed address in the router so it does not change). In `deploy/config.server.yaml` uncomment `northern_lights:` (it is already open if you did step 6b, then just add the line) and set:
   ```
   northern_lights:
     cast_base_url: http://192.168.1.50:8000
   ```
   Open a PR, merge it and run **Deploy**. Check from the phone (on the same Wi-Fi): `http://192.168.1.50:8000/healthz` opens.
3. **Set the token secret.** The Chromecast gets stream links that carry a signed token valid for 6 hours and good for one title only. Make the secret once:
   ```
   sudo bash ~/couchcinema-deploy/bootstrap.sh --set NL_CAST_SECRET=$(openssl rand -hex 24)
   ```
   Without it the app makes a random secret each time it starts, so casts that are running stop when the app restarts or is redeployed (start casting again). Changing the secret has the same effect. The secret is never shown in the app and is unrelated to the Jellyfin key.
4. **Try it.** Open a title in Chrome on the phone (HTTPS address), tap the Cast icon in the player controls, pick the Chromecast. With no further setup the picture comes from Google's **Default Media Receiver** (plain video, no Northern Lights screen).
5. **Optional: the Northern Lights receiver screen** (backdrop, title, episode, aurora progress bar). Google wants a one-time **$5** developer registration:
   1. https://cast.google.com/publish → pay the $5 → **Add new application** → **Custom receiver**.
   2. Receiver application URL: `http://192.168.1.50:8000/nl/cast/receiver` (the same LAN address as above; if the console insists on `https`, put the LAN certificate from step 1 in front and use that address, and set `cast_base_url` to the same `https://` address so the artwork is not blocked as mixed content).
   3. Register your Chromecast's serial number under **Add new device** (an unpublished app only runs on registered devices; it takes up to 15 minutes to start working). You can leave the app unpublished.
   4. Copy the 8-character **Application ID** into the config next to `cast_base_url`: `cast_receiver_app_id: ABCD1234`, then deploy again.
   Preview the screen in any browser: `/nl/cast/receiver?preview=1&title=Alpha&episode=S1%20E2&progress=0.4`.

What the Chromecast can play: H.264 video with AAC or MP3 audio (an mp4 that already is that plays directly; everything else is converted by Jellyfin to H.264/AAC stereo HLS, which uses the server's CPU/QSV). Picture subtitles are burned in; text subtitles are sent as WebVTT. HEVC and surround sound are converted, never passed through.

If it does not work: no Cast icon means the page is not HTTPS, the browser is not Chrome, or no Chromecast answered on the same network. The Chromecast showing "can't play" usually means `cast_base_url` is not reachable from the Chromecast (test it from another device on the Wi-Fi) or the cast session is older than 6 hours.

**Phone keyboard for TV search (Northern Lights M8b).** Nothing to set up. On the TV's Search page there is a code and a QR; scan it (or open the same address plus `/nl/type` and type the code) on a phone on the same network and what you type appears on the TV. Codes last 10 minutes and are forgotten when the app restarts (the TV makes a new one by itself). If the phone cannot open the page, it cannot reach the address the TV page uses.

### 7. Prowlarr indexers **[Labiba]**
Open `http://mars:9696`, set a username and password when it asks, and add your indexers (the
new Prowlarr starts empty; your Mac's indexers do not come across). The app already has the
API key.

### 8. Alerts **[Labiba]**
Two layers, so you hear about a broken app and also about a dead machine.

1. **Phones (ntfy):** install the free **ntfy** app on both phones and subscribe to the topic
   bootstrap printed (it is also `NTFY_TOPIC` in `/srv/couchcinema/.env`). Bootstrap sends a
   test message; if it arrives, alerts work. You get messages for: a finished or failed
   deploy, the app / Jellyfin / Prowlarr being down for 10 minutes (and back again), and
   media or the disk passing 85% (checked daily).
2. **Dead-man's switch (healthchecks.io):** the server pings a URL every 5 minutes while the app
   is healthy. If the pings stop, healthchecks.io emails you, even if the whole machine is off.
   Make a free account, add a check named `mars`, set **Period 5 minutes** and **Grace 10
   minutes**, add your email as the notification, and copy the ping URL. Then:
   ```
   sudo bash ~/couchcinema-deploy/bootstrap.sh --set HEALTHCHECKS_URL=https://hc-ping.com/YOUR-UUID
   ```
   (Or paste it when bootstrap asks.)

To test it, as `deploy` (`sudo -iu deploy`) stop the app, wait ten minutes for the phone
alert, then start it again; the "back" message follows within five minutes:

```
docker compose -p couchcinema --project-directory /srv/couchcinema -f /srv/couchcinema/compose.yaml stop app
docker compose -p couchcinema --project-directory /srv/couchcinema -f /srv/couchcinema/compose.yaml start app
```

## Where things live

```
/srv/couchcinema/
  compose.yaml, config.server.yaml   copied from the repo by each deploy; change them by PR
  .env                               secrets (mode 600); change with bootstrap.sh --set
  data/catalogue_full.db             your database, always on the internal drive
  models/releases/<id>/, models/current
  backups/pre-deploy/                database snapshots taken before each deploy (newest 7)
  bin/                               helper scripts the cron jobs use
  jellyfin/, prowlarr/               their settings
$MEDIA_ROOT (/srv/media)/Movies, Shows, downloads
~deploy/actions-runner             the GitHub runner
```

Looking around as `deploy`: `sudo -iu deploy`, then `docker ps` shows only our containers.

## Deploying

**[Labiba]** GitHub → Actions → **Deploy** → Run workflow. Leave `ref` as `main` and run it.

What happens next, so you know what to expect:

1. The `verify` job checks that the commit has a green CI run and a built image. Only commits
   that reached `main` get an image. If either is missing, the job stops there and nothing on
   `mars` changes.
2. The `deploy` job runs on `mars`. It:
   - copies `compose.yaml`, `config.server.yaml` and the helper scripts to `/srv/couchcinema`;
   - snapshots the database into `/srv/couchcinema/backups/pre-deploy/` (the newest 7 are kept);
   - pulls the new image and starts it;
   - waits up to 90 seconds for `http://localhost:8000/healthz` to report the new commit.
3. On success, a "Deployed" notification goes to your phones (ntfy).

A deploy refuses to run if there is no database in `/srv/couchcinema/data` yet. Copy it across
first (see "Copy your data across").

## Rolling back

**Automatic.** If the health check fails, `deploy.sh` puts back the previous image tag and the
previous `compose.yaml` and `config.server.yaml`. It prints the last 100 log lines from the app.
The Deploy run goes red and your phones get "Deploy FAILED".

**Manual** (for example, a bug found later):

**[Labiba]** GitHub → Actions → **Deploy** → Run workflow. In `ref`, type the full commit SHA of
the older version. Copy it from the commits list. That commit must have been on `main`, so it
has an image.

To see which tag is running now, read `/srv/couchcinema/deployed_tag` (as `deploy`).

Rolling back the code does not roll back the database. If the database is the problem, see the
next section.

## Restoring a database snapshot

Only do this if a migration or a bug damaged data. Restoring a snapshot loses everything rated
or watched since that snapshot was taken.

Each snapshot is named `<UTC timestamp>-before-<tag>.db` and lives in
`/srv/couchcinema/backups/pre-deploy/`. It is the database as it was just before that tag was
deployed.

**[Labiba]** Open a shell on the server as `deploy`:

```
ssh gabstevens@mars
sudo -iu deploy
```

Then, as `deploy`:

1. Stop the app:
   ```
   docker compose -p couchcinema --project-directory /srv/couchcinema -f /srv/couchcinema/compose.yaml stop app
   ```
2. Keep the current database by moving it aside:
   ```
   mv /srv/couchcinema/data/catalogue_full.db /srv/couchcinema/data/catalogue_full.db.broken
   ```
   If `catalogue_full.db-wal` or `catalogue_full.db-shm` exist next to it, move them aside the
   same way.
3. Copy the snapshot you chose into place and lock down its permissions:
   ```
   cp /srv/couchcinema/backups/pre-deploy/<chosen file> /srv/couchcinema/data/catalogue_full.db
   chmod 600 /srv/couchcinema/data/catalogue_full.db
   ```
4. Start the app on the version that matches the snapshot. You have two options:
   - If the running version is a problem too, run the deploy workflow for the older commit first
     (see "Rolling back").
   - If the running version is fine, start the app on the current tag:
     ```
     IMAGE_TAG=$(cat /srv/couchcinema/deployed_tag) docker compose -p couchcinema --project-directory /srv/couchcinema -f /srv/couchcinema/compose.yaml up -d app
     ```
5. Check that `http://mars:8000/healthz` says `"ok": true`.

The nightly Mac backup, once it exists, is a second place to restore from.

## Reading logs and checking on things

Open a shell as `deploy` first (`ssh gabstevens@mars`, then `sudo -iu deploy`).

- **Which containers are running:** `docker ps` shows only our containers.
- **App logs** (follow them live):
  ```
  docker compose -p couchcinema --project-directory /srv/couchcinema -f /srv/couchcinema/compose.yaml logs --tail 200 -f app
  ```
  Use `jellyfin` or `prowlarr` in place of `app` for their logs.
- **Health:**
  ```
  curl -s http://localhost:8000/healthz
  ```
  It shows `ok`, `git_sha`, `model_bundle` and `db_schema`.

The GitHub runner runs as a service on `mars`. From `gabstevens`:

```
sudo journalctl -u actions.runner.rupaai-smart-recommender-player.mars -n 100
sudo systemctl restart actions.runner.rupaai-smart-recommender-player.mars
```

**Changing a secret.** **[Labiba]** Run this on `mars` as `gabstevens`. It restarts only the app:

```
sudo bash ~/couchcinema-deploy/bootstrap.sh --set KEY=value
```

**Changing `config.server.yaml`** (for example, the 100GB media cap, `torrent.max_media_gb`):

1. **[Labiba]** Edit `deploy/config.server.yaml` in the repo and open a PR.
2. **[Labiba]** Merge the PR, then run **Deploy** (see "Deploying").

Do not edit `config.server.yaml` on the server. The next deploy overwrites it.
