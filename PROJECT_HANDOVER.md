# 🤖 MASTER AI HANDOVER & ARCHITECTURE SPECIFICATION
> **Document Purpose:** Hand this document to any AI model or engineer to instantly onboard them on the codebase, architecture, conventions, and status so they can seamlessly pick up development without losing context.

---

## 1. PROJECT ESSENCE & HARDWARE BUDGET

- **Product:** CouchCinema / Smart Movie Streamer — a self-hosted, lightweight personal cinema companion and recommender running on a **Raspberry Pi (4 or 5)**, Mac, or Linux/PC with **zero mandatory cloud dependencies**.
- **Target Footprint:**
  - Peak RAM: $< 1.2\text{ GB}$ (FastAPI ~60MB, SQLite ~20MB, Libtorrent ~80MB).
  - Storage: Embedded SQLite (`catalogue.db`) + local media folders.
- **Dual Core Discovery Pipelines:**
  1. **Direct Torrent Search & Acquisition:** Direct search across YTS & Apibay with live 1s polling, cancelable background downloads, quality/size sorting, and automated Jellyfin organization.
  2. **Smart Mood Recommender:** Candidate filtering, runtime/era/genre narrowing, taste scoring from user watch history ($\ge 7.0$), rating rules ($\ge 6.0$, with guilty-pleasure suppression), and optional ONNX vector embedding drop-in.
  3. **Decoupled Training Module (`/training`):** Off-device fine-tuning of `bge-small-en-v1.5` on Mac MPS GPU / Colab, exported to ONNX for optional ARM64 inference.

---

## 2. REPOSITORY FILE STRUCTURE

```text
smart_movie_streamer/
├── config.yaml               # Target paths (media_folders), port config, player settings
├── .env                      # TMDB_API_KEY, JELLYFIN_URL, JELLYFIN_API_KEY, JELLYFIN_USER_ID
├── database.py               # SQLite DatabaseManager (media_items, media_sources, watch_history)
├── server.py                 # FastAPI web application routes, static mounting, background tasks
├── services/                 # MODULAR CORE SERVICES
│   ├── __init__.py
│   ├── torrent_service.py    # TorrentSearcher, TorrentDownloader, download store, Jellyfin file organizer
│   └── recommender_service.py# Mood mappings, taste scoring, rating suppression, ONNX drop-in
├── scanner.py                # Local filesystem media scanner & filename parsing
├── enricher.py               # TMDB metadata enricher
├── jellyfin.py               # JellyfinRefresher (Library/Refresh API)
├── ws_manager.py             # WebSocket connection manager
├── templates/
│   └── index.html            # PWA shell (Recommender, Direct Download, History tabs, player modal)
├── static/
│   ├── app.js                # Frontend SPA controller, direct search, 1s HTTP polling, rating modal
│   ├── styles.css            # Dark mode responsive cinema styling, badges, progress bars
│   ├── sw.js                 # Service worker for offline caching
│   └── manifest.json         # PWA installation manifest
├── training/                 # DECOUPLED OFFLINE TRAINING MODULE
│   ├── README.md             # 4-step execution guide
│   ├── movie_triplets.json   # 15 curated anchor/positive/negative triplets
│   ├── train_mps.py          # PyTorch fine-tuner (auto-detects Apple Silicon MPS, CUDA, CPU)
│   └── export_onnx.py        # ONNX model exporter with CPU optimization
├── utils/                    # Developer maintenance tools
│   ├── view_db.py            # SQLite database inspector
│   └── virtual_updater.py    # Media source updater
├── sample_library/           # Realistic 0-byte dummy video files for instant zero-download tests
└── catalogue.db              # SQLite catalogue database (generated)
```

---

## 3. DATABASE SCHEMA & DATA MODEL (`catalogue.db`)

Managed by `database.py` via `DatabaseManager`:

### `media_items`
Canonical metadata for movies and TV shows:
- `id` (INTEGER PRIMARY KEY)
- `media_type` (TEXT: `'movie'` or `'tv'`, default `'movie'`)
- `tmdb_id` (INTEGER UNIQUE)
- `title` (TEXT NOT NULL)
- `original_title` (TEXT)
- `release_year` (INTEGER)
- `overview` (TEXT)
- `genres` (TEXT: comma-separated names, e.g. `"Action, Sci-Fi"`)
- `genre_ids` (TEXT: comma-separated TMDB IDs)
- `rating` (REAL: TMDB rating 0.0 - 10.0)
- `runtime` (INTEGER: duration in minutes)
- `poster_url` (TEXT: TMDB poster URL)
- `tier2_keywords` (TEXT: cached TMDB keywords/themes)
- `tier2_reviews_summary` (TEXT: cached public review consensus excerpt)
- `tier2_profiled_at` (TIMESTAMP)

### `media_sources`
Physical media files associated with items:
- `id` (INTEGER PRIMARY KEY)
- `media_item_id` (INTEGER REFERENCES media_items(id) ON DELETE CASCADE)
- `source_type` (TEXT: `'local'` or `'virtual'`)
- `file_path` (TEXT UNIQUE NOT NULL: absolute path)
- `parsed_title` (TEXT)
- `parsed_year` (INTEGER)
- `season_number` (INTEGER: NULL for movies, e.g. 1 for S01)
- `episode_number` (INTEGER: NULL for movies, e.g. 2 for E02)
- `file_size` (INTEGER: bytes)

### `watch_history`
User review impressions fueling the taste profile:
- `id` (INTEGER PRIMARY KEY)
- `media_item_id` (INTEGER REFERENCES media_items(id) ON DELETE CASCADE)
- `watched_at` (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
- `user_rating` (REAL: 1.0 to 10.0 star rating)
- `notes` (TEXT: user review notes)

### Key Database Methods
- `get_user_taste_profile(min_rating=7.0)`: Computes frequency maps of preferred genres and keywords weighted by user star ratings.
- `get_or_create_media_item(title, release_year, media_type)`: Ensures an arbitrary search term is registered with a canonical ID before downloading.
- `get_candidates(...)`: Uses `LEFT JOIN media_sources` to retrieve media matching genre and watch status.

---

## 4. SUBSYSTEM SPECIFICATIONS

### A. Direct Search & Download (`services/torrent_service.py`)
1. **Searcher:**
   - Primary: YTS official API (`https://yts.mx/api/v2/list_movies.json`).
   - Fallback: Apibay (`https://apibay.org/q.php`).
   - Filtering: Drops dead torrents ($0$ seeds) and shortcut files ($< 500\text{ MB}$).
   - Sorting: `sort_by_size=True` (smallest size first) or `sort_by_size=False` (4K/1080p high quality + seeds first).
   - Inferred quality badge: `4K/2160p`, `1080p`, `720p`, `Compact`.
2. **Downloader:**
   - Non-blocking execution via `asyncio.create_task()`.
   - Global in-memory dictionary `downloads_store[movie_id]` tracking percentage, download speed, total size, downloaded size, and status (`connecting`, `downloading`, `organizing`, `completed`, `cancelled`, `error`).
   - Supports download cancellation and cleanup.
3. **Jellyfin Auto-Organization:**
   - **Movies:** `/Movies/Movie Title (Year)/Movie Title (Year).ext`
   - **TV Shows:** `/Shows/Show Title (Year)/Season XX/Show Title - SXXEYY.ext`
   - Moves largest video file (`.mkv`, `.mp4`, `.avi`, `.mov`, `.webm`), updates SQLite `media_sources`, and invokes Jellyfin `/Library/Refresh`.

### B. Recommender Engine (`services/recommender_service.py`)
1. **Stage 1 (Hard Filtering):** Filter SQLite catalogue by genre, era (Modern 2015+, 2000s, Classic), runtime (<105m, 105-140m, >140m), media type (`movie` or `tv`).
2. **Baseline Rating Rule & Guilty-Pleasure Suppression:**
   - Default rating threshold: $\ge 6.0$.
   - If query contains keywords (*"guilty pleasure"*, *"so bad it's good"*, *"cringe"*, *"b-movie"*, etc.), the threshold is suppressed to $0.0$.
3. **Stage 2 (Taste Scoring):**
   - Matches candidate genres and keywords against user taste profile ($\ge 7.0$ ratings).
   - Bonus calculation: $\text{Final Score} = \text{Base Rating} + \text{Taste Bonus}$ (capped at $+2.0$).
4. **Optional ONNX Adaptor:**
   - Checks for `models/model.onnx`. If present, loads via `onnxruntime` for vector re-ranking. Falls back to SQL scoring if absent.

### C. Standalone Training Module (`/training`)
- Fully decoupled from app runtime.
- `movie_triplets.json`: 15 curated triplets.
- `train_mps.py`: Fine-tunes `BAAI/bge-small-en-v1.5` on Apple Silicon MPS, CUDA, or CPU with `MultipleNegativesRankingLoss`.
- `export_onnx.py`: Exports to `model.onnx` optimized for ARM64 CPU.
- Deploy: `cp training/model.onnx models/model.onnx`.

---

## 5. RUNNING THE APP

```bash
# Using uv:
uv run python server.py

# Using python:
python3 server.py
```
App runs at `http://localhost:8000` (or `http://<lan-ip>:8000` on mobile).
