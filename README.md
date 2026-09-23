# Smart Movie Streamer & Recommender

A modular Home Streaming & Movie Recommender System. Scans local media collections, enriches records with canonical TMDB metadata, provides an interactive mood-driven recommendation engine with dynamic Tier 2 profiling and taste learning, and outputs media links ready for streaming to your TV via Jellyfin.

---

## Architecture Overview

The system is built on a decoupled, source-agnostic architecture centered around an SQLite database (`catalogue.db`):

```
[ Media Directories / Storage ]
              │
              ▼
   Block 1: Media Scanner (scanner.py)
              │
              ▼
   Block 3: Catalogue Hub (database.py) ◄─── Block 2: Metadata Enricher (enricher.py)
     ├── media_items (canonical info & cached profiles)     (fetches TMDB metadata)
     ├── media_sources (physical files, streams, episodes)
     └── watch_history (watched dates, ratings, notes)
              ▲
              │
   Block 4: Recommender Engine (recommender.py)
     ├── Mood & Genre Filter
     ├── Interactive Narrowing (Era & Runtime)
     ├── Dynamic Tier 2 Profiling (TMDB keywords & review highlights, cached to SQLite)
     ├── Taste Biasing (boosts movies similar to your highly rated past watches)
     └── Watch Logger & Jellyfin Playback Links
              │
              ▼
   Block 5: Media Serving (Jellyfin — external)
     Pointed at the same media folders to stream directly to your TCL Google TV.
```

---

## Normalized Database Schema

- **`media_items`**: Canonical title, TMDB ID, release year, overview, genres, ratings, runtime, poster URL, and cached Tier 2 profiles (`tier2_keywords`, `tier2_reviews_summary`, `tier2_profiled_at`).
- **`media_sources`**: Physical file paths, stream URIs, media type (`movie` or `tv`), season and episode numbers, and file sizes. *Cleanly supports multi-episode TV shows and multiple file copies.*
- **`watch_history`**: Logged watch events, personal 1–10 user ratings, and impressions.

---

## Quick Start (with `uv`)

### 1. Setup Environment
```bash
# Create virtual environment and install dependencies
uv venv
uv pip install -e .
```

### 2. Configure TMDB API Key
Ensure your `.env` contains your TMDB API Key:
```env
TMDB_API_KEY=your_key_here
```

### 3. Generate Sample Mock Library (Zero-Download Testing)
If you don't have video files on hand yet, generate a realistic sample library in seconds:
```bash
uv run python scanner.py --create-dummy-library ./sample_library
```

### 4. Scan Media Folders
```bash
uv run python scanner.py
```

### 5. Enrich with TMDB Metadata
```bash
uv run python enricher.py
```

### 6. Run the Movie Recommender
```bash
uv run python recommender.py
```

---

## Day-to-Day Usage

### Interactive Movie Night
```bash
uv run python recommender.py
```
1. Pick your mood (e.g. *Mind-Bending*, *Happy*, *Thrilling*, *Scary*, *Chill*).
2. Optionally narrow by era (Modern, 2000s, Classic) and runtime.
3. The engine fetches keywords and public review consensus on the fly (and caches them for instant future runs).
4. Review your personalized recommendations and open the file in your Jellyfin TV app.
5. Log the movie as watched to boost similar movies next time!

### Non-Interactive / Scripted Recommendations
```bash
# Mood 6 (Mind-Bending), modern era only, top 3
uv run python recommender.py --mood 6 --era modern --top-n 3

# View your watch history and personal ratings
uv run python recommender.py --history
```

---

## TV Streaming Setup (Block 5: Jellyfin)

1. Install Jellyfin on your machine / server.
2. In the Jellyfin dashboard, add a Movie / TV library pointed to your media folder (e.g., `./sample_library` or your media drive).
3. On your **TCL Google TV**, open the Google Play Store and install the **Jellyfin** app.
4. Connect to your Jellyfin server IP on your home Wi-Fi.
5. Use the recommender to decide what to watch; play it directly on your TV.

---

## Migration from Laptop to Dedicated Server

Because all configuration is externalized:
1. Copy the project folder (including `catalogue.db`) to your server.
2. Update the `media_folders` paths in `config.yaml` to point to the server's storage mount.
3. Run `uv run python scanner.py` to index the new paths.
