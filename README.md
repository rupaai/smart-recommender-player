# 🍿 Smart Local Movie Hub & Recommender

A lightweight, locally hosted cinema companion and recommendation engine designed to run out-of-the-box on a **Raspberry Pi (4 or 5)**, Mac, or PC with zero cloud dependencies.

Features a **Mobile-First PWA**, a **Direct Search & Download Subsystem** for instant acquisition with live progress polling, an **Offline Recommender Engine** with mood and taste biasing, automated **Jellyfin file structuring**, and a completely decoupled **Offline Model Training Module** (`/training`).

---

## 🌟 Key Features

1. **Dual Discovery Modes:**
   - ⚡ **Direct Download Tab:** Search for *any* movie **or TV show** across verified public endpoints (YTS & Apibay), view resolution badges (`4K`, `1080p`, `720p`), file sizes, and seed counts. Sort by smallest size for rapid streaming or highest quality. A 🎬/📺 toggle switches between Movies and TV Shows everywhere.
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
├── config.yaml               # Target paths (e.g. media_folders: ["/Users/labibarupty/Movies"])
├── .env                      # TMDB and Jellyfin API keys and URLs
├── database.py               # SQLite schema & query methods
├── server.py                 # FastAPI application routes & background worker
├── services/
│   ├── __init__.py           # Package marker
│   ├── torrent_service.py    # Searcher, non-blocking downloader & Jellyfin organizer
│   └── recommender_service.py# Mood filtering, taste scoring, rating rules & ONNX adaptor
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
media_folders:
  - /Users/labibarupty/Movies   # Primary target for Jellyfin & downloads

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
- Choose your sorting preference:
  - **⚡ Smallest File (Fast Download):** Orders candidate torrents from smallest size upwards.
  - **💎 Highest Quality / Seeds:** Prioritizes 4K/2160p and 1080p releases with the highest seeders.
- Click **"⬇️ Direct Download"** on your preferred release.
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
- Click **"✨ Find Movie for Tonight"**. If a movie is not on disk yet, click **"🍿 Watch Tonight"** to automatically search torrents, download it, and notify you when ready.

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
| `GET` | `/api/status` | Jellyfin online status, LAN IP & catalogue stats |
| `GET` | `/api/moods` | Returns 8 mood mappings and sub-moods |
| `POST` | `/api/recommend` | Runs mood filtering + taste scoring + rating rules |
| `POST` | `/api/torrent/search` | Body: `{ query, sort_by_size, media_type }`. Searches YTS (movies) & Apibay (movies/TV) |
| `POST` | `/api/torrent/download` | Body: `{ magnet_link, movie_id, movie_title, media_type }`. Starts non-blocking download |
| `GET` | `/api/torrent/status/{movie_id}` | Live progress polled every 1s (percentage, speed, bytes) |
| `POST` | `/api/torrent/cancel` | Body: `{ movie_id }`. Cancels the download and purges partial staging files |
| `POST` | `/api/sync` | Scans media folders and enriches new titles via TMDB |
| `GET` | `/api/stream/{item_id}` | Returns Jellyfin streaming details |
| `GET` | `/api/watch/current` | Active watching session status |
| `POST` | `/api/watch/start` | Starts an active watch session |
| `POST` | `/api/watch/finish` | Completes watch session with 1-10 star rating & notes |
| `POST` | `/api/watch/cancel` | Cancels active watch session |
| `GET` | `/api/history` | Fetches watch history and learned genre/keyword profile |

---

## 📺 Jellyfin Setup (Local TV Streaming)

1. Install [Jellyfin](https://jellyfin.org/) on your Raspberry Pi, Mac, or server.
2. In the Jellyfin web dashboard, add a Movie Library pointing to your `media_folders` directory (e.g., `/Users/labibarupty/Movies`).
3. Set your Jellyfin API key and URL in `.env`.
4. When downloads complete, files are automatically formatted into Jellyfin's standard structure — Movies: `/Movies/Movie Title (Year)/Movie Title (Year).ext`, TV Shows: `/Shows/Show Title (Year)/Season XX/Show Title - SXXEYY.ext` — and Jellyfin is told to refresh. The new title appears on your TV screen within seconds!

---

## 📄 License

MIT License. Designed with privacy, zero cloud dependence, and efficiency in mind.