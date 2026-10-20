# 🍿 Smart Local Movie Hub & Recommender

A lightweight, locally hosted cinema companion and recommendation engine designed to run out-of-the-box on a **Raspberry Pi (4 or 5)**, Mac, or PC with zero cloud dependencies.

Features a **Mobile-First PWA**, a **Direct Search & Download Subsystem** for instant acquisition with live progress polling, an **Offline Recommender Engine** with mood and taste biasing, automated **Jellyfin file structuring**, and a completely decoupled **Offline Model Training Module** (`/training`). 

---

## 🌟 Key Features

1. **Dual Discovery Modes:**
   - ⚡ **Direct Download Tab:** Search for *any* movie **or TV show** across verified public endpoints (YTS & Apibay), view resolution badges (`4K`, `1080p`, `720p`), file sizes, and seed counts. Default **Best match** sort favours good quality with enough seeders to finish; **Smallest file** and **Best quality** are one tap away. A 🎬/📺 toggle switches between Movies and TV Shows everywhere.
   - 🍿 **Smart Mood Recommender:** Suggests films using mood-to-genre maps, runtime/era narrowing, personal taste scores learned from past ratings ($\ge 7.0$), and baseline rating rules ($\ge 6.0$). Query context with keywords like *"guilty pleasure"*, *"so bad it's good"*, or *"cringe"* automatically suppresses the rating floor!
2. **Non-Blocking Background Downloader:**
   - Downloads launch via `asyncio.create_task()` so HTTP API responses return immediately.
   - **Cancelable:** any in-flight download can be cancelled (`POST /api/torrent/cancel`) — the torrent is removed from the session and partial staging files are purged automatically.
   - Real-time in-place progress transformation powered by **1-second HTTP polling** on `/api/torrent/status/{movie_id}`.
   - Monitors download speed (MB/s), downloaded bytes, and percentage.
   - Automatically aborts fake/shortcut links ($< 500\text{ MB}$) and dead torrents ($0$ seeds).
3. **Automated Jellyfin Organization:**
   - Upon $100\%$ download completion, extracts the largest video file (`.mkv`, `.mp4`, `.avi`, `.mov`).
   - Cleans and relocates files into standard Jellyfin format:
     - Movies: `/Movies/Movie Title (Year)/Movie Title (Year).ext`
     - TV Shows: `/Shows/Show Title (Year)/Season XX/Show Title - SXXEYY.ext` (season/episode auto-detected from the release name).
   - Registers local sources directly into SQLite (`media_sources` table).
   - Automatically pings Jellyfin's `/Library/Refresh` API to rescan libraries.
4. **Standalone Decoupled Training Module (`/training`):**
   - Fine-tune `BAAI/bge-small-en-v1.5` on Apple Silicon (MPS GPU), CUDA, or CPU using triplet loss (`movie_triplets.json`).
   - One-command export to ONNX format (`model.onnx`) optimized for lightweight CPU inference on ARM64 / Raspberry Pi.
   - The main app functions seamlessly without any trained model; drop `model.onnx` into `models/` whenever you are ready!

---

## 🏗️ System Architecture & File Structure

```text
movie-app/
├── config.yaml               # media_folders roots + jellyfin_library (Movies/TV Shows dirs)
├── .env                      # TMDB and Jellyfin API keys and URLs
├── database.py               # SQLite schema & query methods
├── server.py                 # FastAPI application routes & background worker
├── services/
│   ├── __init__.py           # Package marker
│   ├── torrent_service.py    # Searcher, non-blocking downloader & Jellyfin organizer
│   ├── torrent_ranking.py    # "Best match" copy score (tunable in config.yaml)
│   ├── jellyfin_service.py   # JellyfinService: library auto-setup, refresh + visibility verify
│   └── recommender_service.py# Mood filtering, taste scoring, rating rules & ONNX adaptor
├── tests/
│   ├── test_organizer.py     # Offline organizer tests (uv run python tests/test_organizer.py)
│   └── test_searcher.py      # Offline Prowlarr adapter tests (uv run python tests/test_searcher.py)
├── utils/
│   ├── add_prowlarr_indexers.py  # Idempotent safe-indexer setup for Prowlarr
│   └── build_full_catalogue.py   # Full TMDB catalogue builder (10k movies + 10k TV, resumable)
├── static/
│   ├── app.js                # Frontend SPA controller & 1-second polling logic
│   ├── styles.css            # Dark mode mobile-first PWA styling
│   ├── sw.js                 # Service worker
│   └── manifest.json         # PWA installation manifest
├── templates/
│   └── index.html            # PWA layout with Recommender, Direct Download, & History tabs
└── training/                 # DECOUPLED TRAINING MODULE
    ├── README.md             # Easy step-by-step training guide
    ├── movie_triplets.json   # Sample triplet dataset (15 curated triplets)
    ├── train_mps.py          # PyTorch fine-tuning script (Mac MPS / CUDA / CPU)
    └── export_onnx.py        # ONNX conversion script
```

---

## 🚀 Quick Start Guide

### Step 1: Clone & Install Dependencies

Using `uv` (recommended for ultra-fast setup) or standard `pip`:

```bash
# Clone the repository
git clone https://github.com/your-username/smart_movie_streamer.git
cd smart_movie_streamer

# Create a virtual environment
uv venv  # or: python3 -m venv .venv
source .venv/bin/activate

# Install application dependencies
uv pip install -e .  # or: pip install -r requirements.txt
```

> **Requirements:** Python 3.10+ and standard build tools. `libtorrent` is installed via pre-built wheels.

---

### Step 2: Configure Environment (`.env` and `config.yaml`)

#### 1. `.env` File
Create or update your `.env` file in the root directory:

```env
# TMDB API Key (Free from https://www.themoviedb.org/settings/api)
TMDB_API_KEY=your_tmdb_api_key_here

# Jellyfin Integration (Optional - enables automatic TV library refresh)
JELLYFIN_URL=http://localhost:8096/
JELLYFIN_API_KEY=your_jellyfin_api_key
JELLYFIN_USER_ID=your_jellyfin_user_id
```

#### 2. `config.yaml`
Specify your local media folders where movies should be saved and organized:

```yaml
media_folders:                      # scanner scans both roots
  - /Users/labibarupty/Media/Movies
  - /Users/labibarupty/Media/Shows

# Dedicated Jellyfin libraries (auto-created at server startup)
jellyfin_library:
  movie_dir: /Users/labibarupty/Media/Movies
  tv_dir: /Users/labibarupty/Media/Shows
  movies_library_name: Movies
  tv_library_name: TV Shows
  realtime_monitor: true

region: GB
database_path: catalogue.db

recommender:
  default_top_n: 5
  exclude_watched: true
  enable_tier2_profiling: true

torrent:
  min_port: 6881
  max_port: 6891
  download_dir: ./downloads      # Staging folder for in-progress pieces
```

> **Optional — Prowlarr aggregated search:** set `PROWLARR_URL` and `PROWLARR_API_KEY` in `.env` to search all your Prowlarr indexers (YTS, ThePirateBay, NyaaSi, Knaben…) with automatic fallback to YTS/Apibay when Prowlarr is offline or misconfigured. Run `uv run python utils/add_prowlarr_indexers.py` once to install the safe indexer set (no Cloudflare bypass, no accounts; each indexer is live-tested and failures are disabled, never deleted). Results are torrent-only, de-duplicated by info-hash, and merged into one clean sorted list.

### Optional — Full TMDB Catalogue (~10,000 movies + 10,000 TV)

The default `catalogue.db` is a small test catalogue (42 items). Build the full one with:

```bash
uv run python utils/build_full_catalogue.py                 # 10k movies + 10k TV + runtimes (resumable)
uv run python utils/build_full_catalogue.py --max-pages 50  # quick partial build
```

- Movies and TV are stored separately (`UNIQUE(media_type, tmdb_id)` — required because TMDB movie and TV id spaces overlap), popularity-sorted, adult content excluded, unrated junk filtered by vote count.
- **Plug it in:** add `DATABASE_PATH=catalogue_full.db` to `.env`. **Unplug:** remove the line — you're back on the test catalogue instantly. Your watch history / taste profile is copied into the full DB at build time, so ratings follow you across switches.
- Fully resumable: re-run any time; existing rows are skipped and only missing runtimes are fetched.

---

### Step 3: Run the Application Server

Start the FastAPI application:

```bash
python3 server.py
# Or with uv:
uv run python server.py
```

Console output will display:
```
=================================================================
🎬 SMART MOVIE STREAMER PWA SERVER RUNNING
📱 Access on your phone:   http://192.168.1.XX:8000
💻 Access on your laptop:  http://localhost:8000
📺 Jellyfin Server:        http://192.168.1.XX:8096
=================================================================
```

---

### Step 4: Open on Your Phone / Laptop

1. Open `http://<your-lan-ip>:8000` on your mobile browser (Safari on iOS or Chrome on Android).
2. Tap **"Add to Home Screen"** or **"Install App"** to install it as a standalone native PWA.

---

## 📱 How to Use the App

### 1. Direct Search & Download Tab (⚡ Direct Download)
- Click the **"⚡ Direct Download"** tab.
- Type any movie name into the search bar (e.g., `Dune 2024`, `Inception`, `Mad Max`).
- Choose your sorting preference (default **Best match**):
  - **✨ Best match:** one combined score = quality × seeder health × size fit. Good quality with healthy seeders comes first; huge files with few seeders and tiny low-quality ones drop down. For TV, searching a season (`The Bear S02`) ranks season packs first and searching an episode (`S02E06`) ranks episodes first. Tune the numbers under `torrent_ranking:` in `config.yaml`.
  - **⚡ Smallest File (Fast Download):** Orders candidate torrents from smallest size upwards.
  - **💎 Highest Quality / Seeds:** Prioritizes 4K/2160p and 1080p releases with the highest seeders.
- Each result shows its quality, size and seeders (plus "Season pack" and size per episode for TV). Click **Download** on your preferred release.
- Downloads only ever start here. On a Discover card, **Choose a copy** opens this tab with the title already searched; the copy you pick stays linked to that film, so it is logged as downloaded and filed under the catalogue title.
- **In-Place Progress Bar:** The download button immediately transforms into a real-time progress bar polling every 1 second, showing:
  - Percentage completed (`64.2%`)
  - Download speed (`4.8 MB/s`)
  - Downloaded vs. total file size (`1.2 GB / 1.85 GB`)
  - Organizing step when extracting for Jellyfin
- **🎬 Ready to Watch:** When complete, the progress bar turns into a green **"Ready to Watch"** button that directly launches playback!

### 2. Smart Mood Recommender Tab (🍿 Recommender)
- Select from 8 curated moods:
  - 😊 *Happy / Feel-Good* (Comedy, Family, Animation)
  - 😢 *Sad / Emotional* (Drama, Romance)
  - 😱 *Thrilling / On the Edge* (Action, Thriller, Adventure)
  - 👻 *Scary / Spooky* (Horror, Mystery)
  - ❤️ *Romantic* (Romance)
  - 🤯 *Mind-Bending / Thoughtful* (Sci-Fi, Mystery)
  - 😌 *Relaxing / Chill* (Documentary, Animation)
  - 🎲 *Surprise Me!* (Wildcard)
- Apply era filters (*Modern 2015+*, *2000s*, *Classic Pre-2000*) and runtime filters (*< 105m*, *105-140m*, *> 140m*).
- **Guilty-Pleasure Keyword Detection:** Typing phrases like *"cringe"*, *"so bad it's good"*, or *"guilty pleasure"* automatically suppresses the $\ge 6.0$ rating floor so you can discover fun B-movies.
- Click **"✨ Find Movie for Tonight"**. If a movie is not on disk yet, click **"Choose a copy"** to open the Download tab with it already searched, then pick the copy you want.

### 3. Taste Learning & Watch History (⭐ Taste & History)
- When finished watching a film, tap **"Finished Watching ✨"** on the active watch banner.
- Rate the movie from 1 to 10 stars and optionally speak or write personal notes.
- The recommender calculates a **Taste Bonus** (up to $+2.0$) by evaluating genre and keyword affinities from all your ratings $\ge 7.0$:
  $$\text{Final Recommendation Score} = \text{Base Rating} + \text{Taste Bonus}$$

---

## 🧠 Standalone Training Module (`/training`)

The `/training` folder is completely decoupled from the main application. You can train an embedding model on your Mac (using Apple Silicon GPU) or on Google Colab, and export it as an ONNX model for optional semantic matching on the Pi.

### Step 1: Install Training Dependencies

```bash
cd training
pip install sentence-transformers torch onnxruntime
```

### Step 2: Inspect or Customize Triplet Data (`movie_triplets.json`)
The included dataset provides 15 curated triplets in standard `(anchor, positive, negative)` format:

```json
[
  {
    "anchor": "Atmospheric slow-burn cosmic horror with beautiful cinematography",
    "positive": "Annihilation (2018). Themes: cosmic dread, self-destruction, gorgeous visuals, slow pacing. Quality Score: 8.5/10",
    "negative": "Alien Convergence (2017). Themes: cheap CGI monster, fast action, cliché plot. Quality Score: 3.2/10"
  }
]
```

### Step 3: Run Fine-Tuning

```bash
python train_mps.py
```
- Auto-detects Apple Silicon (`mps`), CUDA (`cuda`), or fallback (`cpu`).
- Fine-tunes `BAAI/bge-small-en-v1.5` for 4 epochs using `MultipleNegativesRankingLoss`.
- Saves the tuned model to `./fine_tuned_embedder/`.

### Step 4: Export to ONNX

```bash
python export_onnx.py
```
- Exports the PyTorch transformer into `model.onnx` (~50 MB) optimized for fast CPU inference on ARM64 (Raspberry Pi).

### Step 5: Deploy to the Application (Optional)

```bash
mkdir -p ../models
cp model.onnx ../models/model.onnx
```

> **Drop-in Adaptor:** When `models/model.onnx` is present, `services/recommender_service.py` automatically enables dense vector re-ranking. If absent, the app falls back to the standard SQLite + Taste-Scoring pipeline with zero errors.

---

## 🔌 REST API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Serves the mobile PWA frontend |
| `GET` | `/api/status` | Jellyfin online status, Jellyfin address & catalogue stats |
| `GET` | `/api/jellyfin/status` | Jellyfin diagnostics: server online, libraries (types + locations), env config |
| `GET` | `/api/moods` | Returns 8 mood mappings and sub-moods |
| `POST` | `/api/recommend` | Runs mood filtering + taste scoring + rating rules |
| `POST` | `/api/torrent/search` | Body: `{ query, sort ('best' \| 'smallest' \| 'quality'), media_type }` (`sort_by_size` still accepted). Searches YTS (movies) & Apibay (movies/TV); results carry `score`, `seed_health`, `is_pack` |
| `POST` | `/api/torrent/download` | Body: `{ magnet_link, movie_id, movie_title, media_type }`. Starts non-blocking download |
| `GET` | `/api/torrent/status/{movie_id}` | Live progress polled every 1s (percentage, speed, bytes) |
| `POST` | `/api/torrent/cancel` | Body: `{ movie_id }`. Cancels the download and purges partial staging files |
| `GET` | `/api/stream/{item_id}` | Returns Jellyfin streaming details |
| `GET` | `/api/watch/current` | Active watching session status |
| `POST` | `/api/watch/start` | Starts an active watch session |
| `POST` | `/api/watch/finish` | Completes watch session with 1-10 star rating & notes |
| `POST` | `/api/watch/cancel` | Cancels active watch session |
| `GET` | `/api/history` | Fetches watch history and learned genre/keyword profile |

---

## 📺 Jellyfin Setup (Local TV Streaming)

1. Install [Jellyfin](https://jellyfin.org/) on your Raspberry Pi, Mac, or server.
2. Set `JELLYFIN_URL`, `JELLYFIN_API_KEY` (and optionally `JELLYFIN_USER_ID`) in `.env`.
3. **That's it — libraries are auto-created.** On server startup the app idempotently ensures two dedicated typed libraries exist ("Movies" at `jellyfin_library.movie_dir`, "TV Shows" at `jellyfin_library.tv_dir`) with realtime monitoring enabled. Setup is strictly additive: nothing is ever deleted or renamed. If Jellyfin is offline, startup simply skips it and the server boots normally.
4. Check `GET /api/jellyfin/status` anytime for diagnostics: server online state, every library with its type + folder locations, and `.env` config presence.
5. When downloads complete, files are formatted into Jellyfin's standard structure — Movies: `Movies/Movie Title (Year)/Movie Title (Year).ext` with `Movie Title (Year).en.srt` subtitles beside it; TV Shows: `Shows/Show Title (Year)/Season XX/Show Title - SXXEYY.ext` (season packs: **every** episode organized; multi-episode files become `SXXEYY-EZZ`). The app then refreshes the library and **polls until the title is actually visible** (`✅ '<title>' is now visible in Jellyfin`), logging a diagnostic hint if it isn't.
6. **Who's watching (Northern Lights):** optionally create a second Jellyfin user and list both under `northern_lights.people` in `config.yaml` (see the commented block in it). The player then opens on "Who's watching? Labiba · <partner> · Both of us" (the choice is a plain cookie, no passwords): each person has their own Continue Watching and Resume, their own audio/subtitle language and subtitle look (`/nl/me`), and "Both of us" reports progress to both users and resumes from the further place. Without the config nothing changes.
7. **Picks for two (Northern Lights):** the home page has a "For both of us" row ("Picks for you" with one person) built by the existing recommender in co-view mode from what is in the library, with a one-line reason under each pick and the chips "Under 90 min" / "Under 2 h". Press the round **+** on any card, search result or details page to put a title on the shared **Watchlist** (`/nl/watchlist`, `/nl/tv/watchlist`); "Just me" keeps it private to one person. Each person answers Yes / Maybe / Pass per title; titles you both said yes to come first and lift similar picks. If the recommender's ML packages are not installed the row still works from ratings and your answers. Nothing to configure.
8. **Subtitles tip:** install the OpenSubtitles plugin (Dashboard → Plugins) and sign in to an opensubtitles.com account, so Jellyfin can auto-download missing `.srt` files **and so the Northern Lights player's "Find subtitles" works** (Subtitles menu in the player, or the button on a movie's page, web and TV: search by language, exact matches first, download, and the new track is selected; plus subtitle size/background/position, and on the web a +/- 0.25 s delay); local subtitles named `Title (Year).en.forced.srt` / `Title (Year).en.sdh.srt` are honored automatically.
9. **One-time manual migration** (existing show folders downloaded to the old single root):

   ```bash
   mkdir -p ~/Media/Shows && mv "/Users/labibarupty/Movies/<Show Name>"* /Users/labibarupty/Media/Shows/
   ```

   Movies already placed under `~/Media/Movies` need no action.

> **Known limitations:** the "add path to existing library" API shape varies across Jellyfin versions — on mismatch the app logs a clear warning and continues (never destructive); playback of 4K/HEVC releases depends on your TV client / Jellyfin transcoding settings (a hint is logged by the organizer).

---

## Removing the old setup on mars

The app now runs under Homelab. The old couchcinema stack, its health-check cron file and its GitHub runner may still be on `mars`. They hold the Jellyfin and Prowlarr ports, so remove them. Run these on `mars`, in this order (they are safe to run twice; "not found" messages are fine):

```bash
# 1. Stop and remove the old containers (they run in the deploy user's own Docker, not the system one).
#    Do this before promoting the version with port 8000: the old app holds port 8000 too.
U=$(id -u deploy); sudo -u deploy XDG_RUNTIME_DIR=/run/user/$U DOCKER_HOST=unix:///run/user/$U/docker.sock docker compose -p couchcinema -f /srv/couchcinema/compose.yaml down

# 2. Remove the old health-check cron file.
sudo rm -f /etc/cron.d/couchcinema

# 3. Stop and remove the old GitHub runner service.
sudo systemctl disable --now actions.runner.rupaai-smart-recommender-player.mars
sudo rm -f /etc/systemd/system/actions.runner.rupaai-smart-recommender-player.mars.service
sudo systemctl daemon-reload

# 4. Check that ports 8096 and 9696 are free for Homelab's Jellyfin and Prowlarr.
sudo ss -ltnp 'sport = :8096 or sport = :9696'
```

To also remove the runner from GitHub, delete it under the repository's Settings, Actions, Runners. `/srv/couchcinema` still holds the old data; keep it until you have checked that the move worked.

## 📄 License

MIT License. Designed with privacy, zero cloud dependence, and efficiency in mind.
