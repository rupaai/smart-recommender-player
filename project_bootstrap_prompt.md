# Project Bootstrap Prompt

Copy everything below the line and paste it as your first message in the new project.

---

## Prompt

I want to build a **Home Streaming & Movie Recommender System**. I have a personal catalogue of movies and TV shows stored locally on my laptop. I want a system that:

1. Scans my media folders and finds all my video files
2. Enriches each file with metadata (genre, rating, duration, synopsis) from TMDB
3. Stores everything in a local database
4. Lets me pick a mood and recommends movies **from my own library**
5. Streams the content to my **TCL Google TV** over my home network using **Jellyfin**

I'll start on my laptop for now but will migrate to a **dedicated server later** so it runs 24/7.

### Architecture

The system must be **modular** — split into 5 independent blocks that communicate through a shared SQLite database. I should be able to change, upgrade, or rebuild any single block without breaking the others.

```
Block 1: Media Scanner (scanner.py)
  - Walks configured media folder(s)
  - Finds video files (.mkv, .mp4, .avi, .mov, .m4v, .webm)
  - Parses movie title + year from filenames
  - Writes discovered files to the catalogue DB
  - Skips files already in the DB (matched by file path)

Block 2: Metadata Enricher (enricher.py)
  - Reads un-enriched rows from the DB (where tmdb_id IS NULL)
  - Searches TMDB by title + year, picks the best match
  - Fills in: official title, genres, rating, runtime, synopsis, poster URL
  - Has built-in rate limiting to respect TMDB API limits
  - Idempotent — safe to re-run any time

Block 3: Catalogue Database (database.py + catalogue.db)
  - SQLite — zero setup, single portable file
  - Central hub: Blocks 1 & 2 write to it, Block 4 reads from it
  - Schema:

    CREATE TABLE catalogue (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path     TEXT UNIQUE NOT NULL,
        parsed_title  TEXT NOT NULL,
        parsed_year   TEXT,
        tmdb_id       INTEGER,
        title         TEXT,
        overview      TEXT,
        genres        TEXT,           -- comma-separated genre names
        genre_ids     TEXT,           -- comma-separated TMDB genre IDs
        rating        REAL,           -- 0-10
        runtime       INTEGER,        -- minutes
        poster_url    TEXT,
        media_type    TEXT DEFAULT 'movie',  -- 'movie' or 'tv'
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

Block 4: Recommender Engine (recommender.py)
  - CLI interface (for now — could become a web UI later)
  - Asks the user their mood from these options:
      1. 😊 Happy / Feel-Good → Comedy, Family, Animation, Music
      2. 😢 Sad / Emotional → Drama, Romance
      3. 😱 Thrilling / On the Edge → Action, Thriller, Adventure
      4. 👻 Scary / Spooky → Horror
      5. ❤️  Romantic → Romance
      6. 🤯 Mind-Bending / Thoughtful → Sci-Fi, Mystery
      7. 😌 Relaxing / Chill → Documentary, Animation, Family
      8. 🎲 Surprise Me! → no genre filter
  - Queries the catalogue DB, filters by matching genres
  - Ranks by TMDB rating
  - Displays: title, year, rating, duration, genres, vibe/sentiment, synopsis

Block 5: Media Server (Jellyfin — NOT custom code)
  - Jellyfin runs on the same machine, pointed at the same media folders
  - TCL Google TV runs the Jellyfin app (from Google Play Store)
  - Completely independent from Blocks 1–4
  - The recommender tells you WHAT to watch; Jellyfin is HOW you watch it
```

### File Structure

```
project_root/
├── config.yaml          # Media folder paths, region, preferences
├── .env                 # TMDB_API_KEY=your_key_here
├── requirements.txt     # Python dependencies
├── database.py          # Block 3: DB helper module
├── scanner.py           # Block 1: Scan folders → DB
├── enricher.py          # Block 2: TMDB lookup → DB
├── recommender.py       # Block 4: Mood-based suggestions
└── catalogue.db         # Auto-created SQLite database
```

### Tech Stack
- **Python 3** for all scripts
- **SQLite** for the database
- **TMDB API v3** (free, key stored in `.env`, loaded via python-dotenv)
- **Jellyfin** for media serving to the TV
- **requests** + **python-dotenv** as Python dependencies

### Config (config.yaml)
Should support specifying one or more media folder paths and the region (default: GB).

### Day-to-Day Usage
```bash
# After adding new movies:
python scanner.py
python enricher.py

# Movie night:
python recommender.py
# → pick mood → get recommendations from own library → play on Jellyfin
```

### Migration: Laptop → Server
The design must be portable: copy the project folder + catalogue.db to a new machine, update folder paths in config.yaml, and everything works.

### TMDB API Key
I will add my TMDB API key to the `.env` file manually. The scripts should read it from there using python-dotenv and fail gracefully with a clear message if it's missing.

### Please build all 4 blocks (Block 5 is Jellyfin, not custom code). Start with Block 3 (database.py) since everything depends on it, then Block 1 (scanner.py), then Block 2 (enricher.py), then Block 4 (recommender.py).
