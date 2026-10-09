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

### 7. Prowlarr indexers **[Labiba]**
Open `http://mars:9696`, set a username and password when it asks, and add your indexers (the
new Prowlarr starts empty; your Mac's indexers do not come across). The app already has the
API key.

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
