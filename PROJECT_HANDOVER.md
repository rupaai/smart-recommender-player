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
│   ├── torrent_service.py    # TorrentSearcher (+ optional ProwlarrSearcher), TorrentDownloader, organizer
│   ├── jellyfin_service.py   # JellyfinService: library auto-setup, refresh + visibility polling, diagnostics
│   ├── recommender_service.py# Mood mappings, taste scoring, rating suppression, ONNX drop-in
│   └── generative_retriever.py # TIGER generative retrieval engine (parallel; feature-flag routed)
├── scanner.py                # Local filesystem media scanner & filename parsing
├── enricher.py               # TMDB metadata enricher
├── tests/
│   ├── test_organizer.py     # Offline organizer & parser tests (uv run python tests/test_organizer.py)
│   └── test_searcher.py      # Offline Prowlarr adapter tests (uv run python tests/test_searcher.py)
├── templates/
│   └── index.html            # PWA shell (Recommender, Direct Download, History tabs, player modal)
├── static/
│   ├── app.js                # Frontend SPA controller, direct search, 1s HTTP polling, rating modal
│   ├── styles.css            # Dark mode responsive cinema styling, badges, progress bars
│   ├── sw.js                 # Service worker for offline caching
│   └── manifest.json         # PWA installation manifest
├── training/                 # DECOUPLED OFFLINE TRAINING MODULE
│   ├── README.md             # Execution guides (embedding fine-tune + TIGER pipeline Steps A–H)
│   ├── movie_triplets.json   # 15 curated anchor/positive/negative triplets
│   ├── train_mps.py          # PyTorch fine-tuner (auto-detects Apple Silicon MPS, CUDA, CPU)
│   ├── export_onnx.py        # ONNX model exporter with CPU optimization
│   ├── prepare_semantic_data.py # TIGER Stage A: catalog text corpus -> bge embeddings
│   ├── train_rqvae.py        # TIGER Stage B: RQ-VAE -> 3-level semantic IDs (K=256, D=64)
│   ├── bootstrap_sequences.py   # TIGER Stage D: watch-history / MovieLens / synthetic sequences
│   ├── train_tiger_predictor.py # TIGER Stage C: causal Transformer over semantic ID triplets
│   └── export_tiger_onnx.py  # TIGER Stage F: ONNX export + INT8 quantize + prefix trie + gates
├── utils/                    # Developer maintenance tools
│   ├── view_db.py            # SQLite database inspector
│   ├── virtual_updater.py    # Media source updater
│   ├── add_prowlarr_indexers.py  # Idempotent safe-indexer setup for Prowlarr
│   ├── build_full_catalogue.py   # Full TMDB catalogue builder (10k movies + 10k TV, resumable)
│   └── backfill_vote_counts.py   # Resumable TMDB vote-count backfill for the recommender quality-cut funnel
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
- `tmdb_id` (INTEGER, `UNIQUE(media_type, tmdb_id)` — migrated from global UNIQUE via schema v1 because TMDB movie and TV id spaces overlap)
- `title` (TEXT NOT NULL)
- `original_title` (TEXT)
- `release_year` (INTEGER)
- `overview` (TEXT)
- `genres` (TEXT: comma-separated names, e.g. `"Action, Sci-Fi"`)
- `genre_ids` (TEXT: comma-separated TMDB IDs)
- `rating` (REAL: TMDB rating 0.0 - 10.0)
- `vote_count` (INTEGER: TMDB vote count — added by schema v2 as an idempotent `ALTER TABLE`. 0 = not backfilled yet, -1 = confirmed missing on TMDB. Powers the recommender's SQL quality-cut funnel; backfilled by `utils/backfill_vote_counts.py`)
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
3. **Jellyfin Auto-Organization (pure `organize_media_files()` + async verify):**
   - **Movies:** `<movie_target>/Movie Title (Year)/Movie Title (Year).ext`, subtitles beside it as `Movie Title (Year).en.srt` (+`.forced`/`.sdh` flags preserved).
   - **TV Shows:** `<tv_target>/Show Title (Year)/Season XX/Show Title - SXXEYY.ext` for **every** video file (season packs); multi-episode files → `SXXEYY-EZZ`; subtitles paired by the SxxEyy tag parsed from their own filename; unpairable subs logged and preserved (never deleted).
   - Split roots from `jellyfin_library` config (`movie_dir` / `tv_dir`, legacy fallback `media_folders[0]`).
   - Updates SQLite `media_sources` for every organized movie/episode; removes the staging dir once emptied.
   - Afterwards a non-blocking async poller (`_verify_in_jellyfin`, `asyncio.to_thread`) triggers `/Library/Refresh`, polls `GET /Items` until the title is visible, and updates `downloads_store[id]["jellyfin_visible"]` (+ `jellyfin_hint` on failure).

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

**Large-catalogue candidate funnel (added later on 2026-10-02):**
1. **Funnel 1 — hard narrowing (SQL):** media type, mood/genre chips (TV aliases expanded), era, runtime bounds, watch status, rating floor.
2. **Funnel 2 — quality cut (SQL):** `DatabaseManager.get_top_candidates(...)` orders by the Bayesian formula `(votes/(votes+1000))*rating + (1000/(votes+1000))*6.5` computed **in SQL** using real `vote_count`, cut to `recommender.shortlist_size` (250). Items without vote counts yet score a flat 6.5 → graceful rating/year fallback until the backfill runs.
3. **Funnel 3 — personal scoring (Python, unchanged logic):** taste bonus + free-text query matching over the ~250-item shortlist only (~50× less Python work than scoring the full catalogue; Pi-friendly).
4. **Funnel 4 — diversity:** MMR pool capped at `recommender.final_pool` (50) → pagination serves from this pool.
5. **`variety` (opt-in, default False; server passes True):** seeded shuffle rotating every 10 minutes — repeat requests surface different picks while pagination stays coherent within a bucket. Both engines accept the flag.
6. **`utils/backfill_vote_counts.py`:** resumable threaded TMDB vote-count backfill (fills `vote_count`, refreshes `rating`; confirmed 404s marked -1 and skipped on re-runs). Run: `uv run python utils/backfill_vote_counts.py` (supports `DATABASE_PATH` override).

### C. Standalone Training Module (`/training`)
- Fully decoupled from app runtime.
- `movie_triplets.json`: 15 curated triplets.
- `train_mps.py`: Fine-tunes `BAAI/bge-small-en-v1.5` on Apple Silicon MPS, CUDA, or CPU with `MultipleNegativesRankingLoss`.
- `export_onnx.py`: Exports to `model.onnx` optimized for ARM64 CPU.
- Deploy: `cp training/model.onnx models/model.onnx`.

### D. Jellyfin Service (`services/jellyfin_service.py`)
- `JellyfinService(config)` (legacy alias `JellyfinRefresher` kept for compatibility):
  - `ensure_libraries()`: idempotent, additive-only creation of dedicated typed "Movies" (`movies`) and "TV Shows" (`tvshows`) libraries from `jellyfin_library` config, with realtime monitoring. If a named library exists but lacks the path, it is added via `POST /Library/VirtualFolders/Paths?name=<name>` body `{"Path": dir}`; on version-specific endpoint mismatches it logs a clear warning and continues — never deletes anything. Returns `{created, updated, skipped}`.
  - `refresh_and_verify(title, media_type, timeout_s=120)`: `POST /Library/Refresh`, then polls `GET /Items?searchTerm=<title>&Recursive=true&IncludeItemTypes=Movie,Series&Limit=1` every 5s → `{"visible": bool, "hint": str|None}` (hints: missing library paths, realtime monitor off, 4K/HEVC transcode note).
  - `diagnostics()`: snapshot for `GET /api/jellyfin/status` (server online, libraries with types + locations, `.env` presence). Never raises.
- `server.py` calls `ensure_libraries()` in a startup hook wrapped in try/except → the server boots fine when Jellyfin is offline.

### F. TIGER Generative Retrieval Engine (`services/generative_retriever.py`)
- **Parallel engine** implementing TIGER (arXiv:2305.05065): catalog text → bge embeddings → RQ-VAE semantic IDs `[c1,c2,c3]` (K=256, D=64; colliding tuples get a 4th leaf token) → causal Transformer sequence predictor → ONNX INT8 → prefix trie lookup (0% hallucinated items).
- **Router** `route_recommend()` in the same module, wired into `POST /api/recommend`: `recommender.engine_mode` in `config.yaml` = `fallback` (default; generative when ready + history ≥ `generative.history_threshold`, else legacy) | `legacy_only` (kill-switch) | `generative_only` (legacy still catches failures). Every response carries `engine: "generative" | "legacy"`.
- `GenerativeRetriever` loads artifacts lazily from `models/tiger/` (`predictor_int8.onnx`, `semantic_ids.json`, `prefix_trie.json`, `item_map.json`, `config.json`); missing artifacts → engine disabled, legacy serves. Also validates item-ID overlap with the active DB and warns on 0% overlap (artifacts must be built from the same catalogue as `DATABASE_PATH`).
- Payload shape identical to legacy (`total_results/page/page_size/has_more/items`) plus `generative: {history_len, predicted_tuples}`; items additionally carry `semantic_id`, `generative_rank`.
- Legacy `RecommenderService` is untouched — it is only ever called as-is via the router.
- Offline tests: `uv run python tests/test_generative_retriever.py` (trie, beam decode, sequence conversion, router modes, artifact-missing readiness).
- Training runbook: `training/README.md` Steps A–H; artifacts verified: INT8 0.69 MB (<15 MB gate), 0.15 ms CPU (<5 ms gate), trie covers 14,397 items.
- **Context awareness + variety (added later on 2026-10-02):** `POST /api/recommend` now resolves mood genres server-side and passes them to both engines. The generative engine applies a strong genre boost (+1.5) with genre hard-filter fallback (needs ≥8 matches, else boost-only), TV genre aliases, free-text query boosts (+0.4/word, exposed as `query_boost`), era/duration filters, and genre-matched SQL backfill of the candidate pool (scored without the model boost) when a mood narrows the request. Beam widened (width 64, candidates-per-level 64, `top_k: 40`) so the predictor explores far more tuples. Both engines accept `variety` (same 10-minute seeded rotation as legacy).
- **Cold-start gate:** `recommender.generative.history_threshold: 5` — the legacy funnel engine serves until 5+ rated items exist, then generative takes over automatically. UI badge on each result card shows the active engine ("🎲 Picks for You" = legacy, "🧠 Learning Your Taste" = generative).

### E. Search Providers (`services/torrent_service.py`)
- `TorrentSearcher.search()` provider chain: **Prowlarr → YTS → Apibay** (Prowlarr first only when configured; failures fall through silently with a warning).
- `ProwlarrSearcher`: optional, enabled by `PROWLARR_URL` / `PROWLARR_API_KEY` in `.env`. Calls `GET /api/v1/search?query=…&type=search&categories={2000|5000}&limit=100`, maps results into the standard result dict (`name/quality/size_bytes/seeds/peers/magnet/media_type`) so the frontend is unchanged. Torrent-protocol only (NZBs dropped). Prowlarr's `magnetUrl` is its own proxied grab link (or a redirect to the site magnet) — kept as-is and converted into a real magnet on demand by `torrent_url_to_magnet()` (follows redirects to `magnet:`, otherwise parses the `.torrent` with the built-in bencode encoder/decoder — zero dependencies). Dedup by info-hash or title+size; seed counts are NOT filtered (indexers like NyaaSi report 0 for live torrents — the downloader aborts dead/fake torrents itself). Any failure (unreachable / 401 / HTTP error / malformed JSON) → warning + `[]` → automatic fallback. Unconfigured → byte-identical legacy behavior.
- `utils/add_prowlarr_indexers.py`: idempotent safe-indexer installer (YTS, EZTV, ThePirateBay, NyaaSi, Knaben). Echoes the schema definition back on `POST /api/v1/indexer` (**requires `appProfileId` = 1 "Standard"** — a schema echo without it fails validation with 400), live-tests each with per-indexer queries (**NyaaSi is anime-only → use anime test queries**), auto-disables failures (never deletes).
- Healthy set verified 2026-10-01: **YTS, ThePirateBay, NyaaSi, Knaben** (Knaben alone ≈ 70–95 results per query). **EZTV excluded:** its site is unreachable from this network at add-time (Prowlarr 400 "Unable to connect to indexer").
- Future indexers (deliberately kept out): 1337x, TorrentGalaxy, TheRARBG require FlareSolverr (headless Chrome — large RAM cost on a Pi); SolidTorrents is absent from this Prowlarr version's schema.

---

## 5. RUNNING THE APP

```bash
# Using uv:
uv run python server.py

# Using python:
python3 server.py
```
App runs at `http://localhost:8000` (or `http://<lan-ip>:8000` on mobile).

---

## 6. CHANGELOG & VERIFICATION CHECKLIST (2026-10-02)

### Changelog (later update — same day)
- **`/api/moods` 500 fixed:** the endpoint read a non-existent `vibe` key from `MOOD_MAPPINGS`. Endpoint is now defensive (`info.get("vibe", "")`) and `MOOD_MAPPINGS` gained vibe descriptions (additive metadata only — ranking behavior unchanged).
- **Frontend render fix:** `app.js` treated the paginated recommend payload as an array (`movies.forEach` TypeError in the browser → "Failed to load recommendations" despite clean 200s server-side). Now renders `data.items` and passes `data.engine`.
- **Full catalogue is the default:** `config.yaml database_path: catalogue_full.db` — the TIGER artifacts (14,397 semantic IDs) match it (coverage 100%). `DATABASE_PATH` env override still switches catalogues per launch. `server.py` prints `[DB] active catalogue: <path> (<source>)` at startup so DB/artifact mismatches are immediately visible.
- **Vote-count quality funnel + variety + cold-start gate:** schema v2 (`media_items.vote_count`), `get_top_candidates` SQL funnel (shortlist 250), MMR final pool 50, opt-in `variety` (10-minute seeded rotation) wired through both engines, `history_threshold: 5` cold-start gate, engine badge in the UI, and `utils/backfill_vote_counts.py`. Details in Sections 3/B/F above.
### Schema v3: Multi-profile co-view ratings (added later on 2026-10-03)
The recommender's core problem was **signal, not architecture**: 2 ratings in `watch_history`. Two people share one catalogue, so `watch_history` needed to know *who* rated.
- **`profiles`** table (`id`, `name` UNIQUE, `rating_count`, `mean_rating`) — seeds 2 renameable profiles (`Me`, `Partner`).
- **`watch_history.profile_id`** — who rated. Existing rows migrate to profile 1 with ratings preserved.
- **`watch_history.status`** — `'seen'` | `'not_seen'`. "Never saw it" is a real answer stored with **no rating attached** (never a 0 or a 1); it's what makes the Tier B recommendation computable ("he saw it, you haven't" vs "neither has").
- Indexes: `idx_watch_history_profile_item` (unique, upsert target), `idx_watch_history_item` (co-view grouping).
- **Migration note:** SQLite rejects `ALTER TABLE ... ADD COLUMN ... REFERENCES ... NOT NULL DEFAULT 1` ("Cannot add a REFERENCES column with non-NULL default value"), so `profile_id` is added bare then backfilled. Idempotent via `PRAGMA user_version = 3`.

**Co-view tiers** (`get_rating_summary`): **A** both rated ≥7.0 · **B** one rated ≥7.0 and the other hasn't seen it · **C** exploration, or both saw it and disagreed (a disagreement is *not* Tier B).

**Combined score:** Bayesian-shrunk weighted mean per rater — `adj = (n·mean + 10·6.5)/(n+10)`, weight `w = n/(n+10)`, combined = `Σ(w·adj)/Σw` over raters who saw it. A 4/10 from someone with 5 ratings can't outvote an 8/10 from someone with 200.

**Rating queue** (`get_rating_queue`) priority: in-library but unrated by this profile → rated by the other profile → unseen high-rated. Leads with films already on disk, turning watch events into taste signal.

**New DB methods:** `get_profiles`, `update_profile_name`, `rate_item`, `get_rating_summary`, `get_rating_queue`, `get_co_view_history`.
**New API:** `GET /api/profiles`, `PUT /api/profiles/{id}`, `GET /api/ratings/queue`, `POST /api/ratings`, `GET /api/ratings/summary/{id}`, `GET /api/ratings/co-view`.
**New frontend:** **💞 Rate Together** tab — one card at a time, profile switcher, 1–10 stars, **"👁️ We haven't seen it"**, skip, keyboard shortcuts (`1`–`0`, `N`, `S`), co-view summary of films both profiles liked.

### Bugs fixed (2026-10-03)
1. **Genre filter never reached SQL** (`database.py` `get_top_candidates`) — the filter ran as a Python loop *after* the query, so every mood request pulled the whole ~8,041-row catalogue (~2.1 MB with overviews) into Python to cut to 250. Now a SQL `LIKE` predicate. **91 ms → 17 ms**, results verified byte-identical across 6 genre combinations.
   - *Subtle:* genres are stored comma+**space** separated (`"Crime, Mystery, Thriller"`). The padded-comma token test needs spaces stripped on **both** sides or `%,mystery,%` never matches `", mystery,"` — this silently dropped ~84% of matches on the first attempt.
2. **`movies.csv` join missing in `bootstrap_sequences.movielens_sequences()`** — it read `row["title"]`, but `ratings.csv` has no title column (`userId,movieId,rating,timestamp`). The function could never run; it `KeyError`'d on row 1. Now joins `movies.csv` by `movieId` (auto-detected as a sibling file). Verified: **0 → 576 sequences**, 63,294/100,836 rows matched (62.6%).
3. **`rate_item` read its own uncommitted write** — called `get_rating_summary()` (a second connection) inside the `with` block, so SQLite returned the pre-write state and the new verdict was silently missing. Moved the read after the commit.
4. **Co-view tier misclassified disagreements** — one person liking a film the other had *seen and disliked* returned Tier B ("he'd recommend this to you"). Now Tier C.
5. `MOVIE_LENS` data location: `https://files.grouplens.org/datasets/movielens/ml-latest-small.zip` (the bare `ratings.csv` URLs 404; files ship zipped). Measured: 1.95M usable co-rated pairs after matching to the catalogue.
- **Note:** IMDb `title.ratings.tsv.gz` covers TV but is `tconst,averageRating,numVotes` — aggregate per-title means with **no userId**, so it cannot express agreement between people. There is no public per-user TV ratings dataset; any future co-view predictor is movies-only.
- **PENDING VERIFICATION (not yet executed — session terminal wedged):** `python3 -m py_compile database.py server.py services/recommender_service.py services/generative_retriever.py utils/backfill_vote_counts.py`, `uv run python tests/test_generative_retriever.py`, server restart + live checks (`/api/moods` 200, `POST /api/recommend` → `engine` field + badge data), and the vote-count backfill run.

### Changelog
- **TIGER generative retrieval engine (parallel, feature-flagged):** new `services/generative_retriever.py` (`GenerativeRetriever` + `route_recommend` router) implementing TIGER semantic-ID generative retrieval beside the untouched legacy recommender; `POST /api/recommend` responses now carry `engine: "generative" | "legacy"`. Config: `recommender.engine_mode` (`fallback` default | `legacy_only` | `generative_only`) + `recommender.generative.*` knobs in `config.yaml`.
- **TIGER training pipeline added to `/training`:** `prepare_semantic_data.py` (Stage A: catalog text corpus → bge-small embeddings, reuses `fine_tuned_embedder/` when present), `train_rqvae.py` (Stage B: RQ-VAE 3×256×64 semantic IDs + collision leaf tokens), `bootstrap_sequences.py` (Stage D: watch-history / MovieLens / synthetic genre co-watch sequences), `train_tiger_predictor.py` (Stage C: causal Transformer, 3×256 codebook embedding table, per-level CE loss), `export_tiger_onnx.py` (Stage F: ONNX export → dynamic INT8 → <15MB/<5ms gates → prefix trie → end-to-end smoke test). Full runbook in `training/README.md` Steps A–H.
- **TIGER artifacts deployed & live-verified:** `models/tiger/` (INT8 0.69 MB, 0.15 ms CPU, trie covers 14,397 full-catalogue items); generative engine returns real scored items (`engine: "generative"`) when history ≥ threshold; short history / artifact-missing / DB-mismatch all route to legacy automatically. Load-time DB↔artifact coverage check logs a loud warning on 0% overlap.
- **Additive DB method:** `DatabaseManager.get_watch_history_chronological()` (oldest-first history for sequence modeling; no existing queries changed).
- **Dependencies:** `onnxruntime>=1.17.0,<1.24` added to `pyproject.toml` (capped: 1.24+ dropped Python 3.10 wheels). Training-only deps documented in `training/README.md` (torch, sentence-transformers, onnx, onnxruntime, onnxscript).
- **Tests:** new offline suite `tests/test_generative_retriever.py` (trie build/lookup/collisions, beam decode, history→semantic sequence, insufficient-history signal, router modes, artifact-missing readiness).
- **Jellyfin service modularized:** `jellyfin.py` removed → `services/jellyfin_service.py` (`JellyfinService`, legacy alias `JellyfinRefresher`).
- **Split media roots:** `config.yaml` now has `media_folders: [~/Media/Movies, ~/Media/Shows]` + a `jellyfin_library` block (movie_dir / tv_dir / library names / realtime_monitor), with legacy fallback to `media_folders[0]`.
- **Organizer rewritten:** pure module-level `organize_media_files()` handles movies + season packs + multi-episode files + subtitle pairing (`_parse_sxxeyy`, `_parse_subtitle_tags`), registers every episode in `media_sources`, logs 4K/HEVC transcode hints, and removes the staging dir once emptied (non-media leftovers preserved, never deleted).
- **Async Jellyfin verification:** `_verify_in_jellyfin` poller (non-blocking, `asyncio.to_thread`) replaces the old sync `JellyfinRefresher` call inside the download loop; `downloads_store[id]["jellyfin_visible"]` (+ `jellyfin_hint`).
- **Server:** fault-tolerant startup `ensure_libraries()` + `GET /api/jellyfin/status`.
- **Docs:** stale `ws_manager.py` row removed (file does not exist); README Jellyfin setup rewritten.
- **Prowlarr aggregated search (optional):** `ProwlarrSearcher` adapter + Prowlarr→YTS→Apibay fallback chain; `utils/add_prowlarr_indexers.py` installs the safe indexer set; healthy set = YTS, ThePirateBay, NyaaSi, Knaben (EZTV unreachable from this network — documented for later with the FlareSolverr-requiring indexers).
- **Full catalogue support:** schema v1 migration (`tmdb_id UNIQUE` → `UNIQUE(media_type, tmdb_id)`, `PRAGMA user_version`-gated, idempotent, FK-preserving rebuild), `upsert_catalogue_items()` bulk insert, `utils/build_full_catalogue.py` (10k movies + 10k TV via TMDB discover, resumable, threaded runtime details pass, watch-history carry-over), `DATABASE_PATH` env override in `server.py` for one-line plug/unplug (`catalogue.db` = test catalogue, `catalogue_full.db` = full).

### Verification checklist
- [x] Schema v3 migration verified on **both** `catalogue.db` and `catalogue_full.db`: history rows preserved (2 → 2, ratings intact, landed on profile 1), `PRAGMA foreign_key_check` clean, idempotent on reopen
- [x] `uv run python tests/test_ratings.py` — 45 checks pass (migration, not_seen semantics, tiers A/B/C, upsert, shrinkage, queue priority, co-view history, validation, SQL genre matching)
- [x] Genre-in-SQL fix verified **byte-identical** to the old Python filter across 6 genre combinations incl. multi-word ("Science Fiction") and TV aliases ("Sci-Fi & Fantasy"); `Se7en` (top-scoring) now correctly included
- [x] `bootstrap_sequences.movielens_sequences()` verified on the real dataset: 0 → **576 sequences**, 63,294/100,836 rows matched (62.6%)
- [x] Live API check against the real 14,398-item catalogue: all 6 new endpoints correct (queue, rate 9.0 → tier B, not_seen → tier B, summary, co-view count 0, rename, HTTP 400 on unknown item + duplicate name)
- [x] `POST /api/recommend` regression — still returns `engine: "legacy"`, 10 items
- [x] Frontend wiring verified: all 7 `onclick` handlers defined, all 9 element IDs present in `index.html`, `node --check static/app.js` clean
- [x] No regressions: `test_organizer.py`, `test_searcher.py`, `test_generative_retriever.py` all still pass
- [x] `python3 -m py_compile database.py server.py training/bootstrap_sequences.py`
- [x] `training/data/` added to `.gitignore` (4.1 MB download, not source)
- [ ] **NOT YET BUILT — Phase 2:** profile-aware recommendation scoring (`profile=me|partner|together`), tier boosts (A:+1.5, B:+0.8), profile-aware `exclude_watched`, profile selector on Discover, co-view badges on cards
- [ ] **NOT YET BUILT:** `MOOD_MAPPINGS` still maps Horror to `["Horror","Mystery"]` (Mystery is crime → Scary returns *Mystic River*, *High and Low*); silent `or candidates` filter fallback (`recommender_service.py:302-312`); variety shuffle runs *after* MMR so pool composition never varies (`:326`)
- [ ] **NOT YET BUILT:** dead ONNX branch (`recommender_service.py:97-126,184-188` — reads a nonexistent `embedding` column, needs an unexported `models/model.onnx`, imports uninstalled `transformers`); `tier2_keywords` backfill (0% populated, no util exists)
- [ ] **DEFERRED — Phase 5:** co-view predictor (1.95M MovieLens pairs available; ~1 new module + 1 training run). Movies only — no public per-user TV ratings data exists.
- [x] `uv run python -m py_compile services/generative_retriever.py services/recommender_service.py server.py database.py training/prepare_semantic_data.py training/train_rqvae.py training/bootstrap_sequences.py training/train_tiger_predictor.py training/export_tiger_onnx.py tests/test_generative_retriever.py`
- [x] `uv run python tests/test_generative_retriever.py` — all offline TIGER checks pass (trie, beam, sequences, router, missing artifacts)
- [x] Full TIGER pipeline live-run on `catalogue_full.db` (scratch venv): Stage A (14,397 × 384 embeddings), Stage B (2,822 unique tuples, 1,254 collision groups leaf-disambiguated, codebook utilization ~0.5), Stage D (500 synthetic sequences), Stage C (loss 4.46→3.45), Stage F gates (0.69 MB / 0.15 ms) + trie smoke test
- [x] Runtime live test: `engine: "legacy"` fallback with history < threshold; `engine: "generative"` with forced threshold returns real scored items; `generative_only` + TV media type OK; DB-mismatch case routes to legacy with loud warning
- [x] `uv run python tests/test_organizer.py` — all offline checks pass
- [x] `uv run python tests/test_searcher.py` — all offline checks pass
- [x] `uv run python -m py_compile services/jellyfin_service.py services/torrent_service.py server.py tests/test_organizer.py`
- [x] `uv run python tests/test_organizer.py` — all offline checks pass (parsers, movie+subs, season pack, multi-episode, no-video edge case)
- [x] `uv run python tests/test_searcher.py` — all offline checks pass (Prowlarr mapping, NZB/no-magnet/dead filtering, dedup, categories, unconfigured path)
- [x] Prowlarr live-verified: 4 safe indexers enabled and test-searched (Knaben 70–95 hits/query); adapter returns merged torrent-only results through `TorrentSearcher.search()`
- [x] Schema migration v1 verified on `catalogue.db` (42 sources + 2 history rows preserved, 0 FK violations, idempotent reopen)
- [x] `DATABASE_PATH` plug/unplug verified (`catalogue_full.db` ↔ `catalogue.db`)
- [ ] `catalogue_full.db` runtime details pass finishing in background (`tail -f /tmp/fullcat2.log`; re-run `uv run python utils/build_full_catalogue.py --media-types tv` if interrupted — fully resumable). Final counts: 9,993 movies (≥200 votes), 4,404 TV (≥100 votes — TMDB's full qualifying set), 2 history rows carried over
- [x] Server boots with `ensure_libraries()` on startup; `GET /api/jellyfin/status` returns diagnostics
- [ ] Manual: one-time move of existing show folders to `~/Media/Shows` (command in README)
- [ ] Manual: confirm new downloads appear on the TV client (4K/HEVC may need transcoding enabled)

---

## 7. RECOMMENDER REVIEW (2026-10-08)

**Full architecture + flow diagrams: `docs/RECOMMENDER_ARCHITECTURE.md`. Read that before touching the recommender.**

Note: commits after 2026-10-03 (profile-aware scoring, query-intent filtering, origin-of-cinema filtering + `origin_country`/`original_language` columns (schema v4), concept/premise matching via `THEMES`, download resume/ETA) were never recorded in this handover. The architecture doc reflects the code as of commit `9beb8e3`.

**Live state observed:** `catalogue_full.db` = 36,447 movies / 14,050 TV. `watch_history` = 71 `seen` + 220 `not_seen`, **all profile 1** (Partner has zero ratings). `tier2_keywords` 0 % populated. The router sends almost every mood/chip request to the **generative** engine (history threshold is counted over all rows, including `not_seen`).

### Fixed 2026-10-08
- **Series order (both engines):** schema v5 adds `media_items.collection_id / collection_name / collection_order / collection_checked`. `apply_series_order()` in `recommender_service.py` swaps a sequel for the earliest entry neither profile has *seen*, keeps one film per series, and adds `series_note` / `series_position` / `replaced_sequel_title` (UI badge 📚). Before TMDB data exists, it falls back to sequel titles (`DatabaseManager.parse_sequel_title`), guarded by shared genre and an earlier bare-title film. **To do once, from your own terminal:** `uv run python utils/backfill_collections.py` (resumable; `--limit N` for a partial run). TMDB is not reachable from Claude's sandbox.
- **Generative history:** `GenerativeRetriever.get_history_item_ids(profile=…)` → `DatabaseManager.get_recent_liked_history()`: seen only, rating ≥ `generative.history_min_rating` (6.0, new config key) or unrated, newest 30, per profile (`me` / `partner` / both). Profile ids come from `get_profiles()` (lowest = me) instead of hard-coded 1/2.
- **`exclude_watched` SQL** (`get_candidates`, `get_top_candidates`) now excludes only `status='seen'`. "We haven't seen it" answers no longer hide films.
- Tests: new `tests/test_series.py` (18 checks). `test_ratings.py` passes except 3 groups that need uvicorn/libtorrent, which weren't installable in the review sandbox.

**Open bugs (prioritised):**
1. ~~No franchise/collection awareness~~ (fixed above, pending backfill run). Original note: No franchise/collection awareness → sequels recommended before earlier entries (both engines). Needs TMDB `belongs_to_collection` (id + order) stored on `media_items`, then a "replace with earliest unseen entry" pass.
2. ~~TIGER history~~ (fixed above). Original note: TIGER history (`get_history_item_ids` → `get_watch_history_chronological(limit=100)` ASC) uses the **oldest** 100 rows, includes `not_seen`, ignores rating value, mixes profiles. Generative profile IDs are hard-coded 1/2.
3. Generative engine ignores query exclusions, rating floor and diversity. Genre filter only applies with ≥20 matches. Scary→Mystery leaks comedies.
4. Taste bonus saturates at the 2.0 cap and has no negative signal (ratings < 7 are ignored).
5. `rank_candidates_profile_aware` overwrites `relevance_boost` (wiping the intent-genre bonus) when content words exist. The line is also duplicated.
6. Concept SQL boost: unbounded +3.0 per generic term. The second "wider" query repeats the first. "smart" alone triggers concept mode.
7. `parse_origin_intent` substring matching ("indiana jones"→Indian, "polished"→Polish). Duplicate dict keys `chinese`/`portuguese`. Kollywood/Tollywood swapped. Nigerian = Yoruba only.
8. Negation window is 1 token ("not complicated" adds Horror/Thriller/Crime/War).
9. `server.py` `exclude_ids` filters only the current page and forces `has_more=False`.
10. Tier A/B boosts unreachable in Discover (all seen films are dropped before scoring). Partner mode also drops films only profile 1 has seen.
11. Genre chips widen (union with mood + query genres) instead of narrowing.
12. Era/runtime `or candidates` silent fallback. `medium` duration is unhandled in Python.
13. Variety: generative always shuffles. Legacy shuffles after MMR (same 50, reordered).

### TIGER retraining fixes (2026-10-08)
- `training/bootstrap_sequences.py`: real history = seen + rated ≥ `--min-rating` (6) only, repeated `--history-weight` (20)×. New `--no-synthetic`. Default DB is now `catalogue_full.db` (the one the semantic IDs come from). Verified locally: 70 liked films + 601 MovieLens sequences.
- `training/train_tiger_predictor.py`: `make_training_examples` slid only over the last `max_seq_len-1` items of each sequence. Now it covers the whole sequence: **4,721 → 30,359 training examples** on the same data.
- `training/retrain_tiger.sh`: one command (Steps D→G, backs up `models/tiger` to `models/tiger.bak`). Needs torch, so run it on the Mac (Claude's sandbox has no torch and no PyPI). **Not yet run**, so the deployed model is still the old synthetic one.

## 8. NEXT: Recommender v2 design
`docs/RECOMMENDER_V2_DESIGN.md`: search-to-rate, vibe search (text → embedding over plot + TMDB keywords + reviews + LLM mood tags), vibe clusters, facet dials, a skippable questions pop-up, feedback logging, learning loops, a 6-phase build order and a user prep checklist (incl. a `training/vibe_eval.csv` golden set).
- **Decisions 2026-10-08:** train on the Mac, serve on the Pi (INT8 ONNX encoder, precomputed vectors, no torch on the Pi). The questions pop-up appears automatically, asks up to ~6 questions, and only asks questions that would change the top 10 (§3 "meaningful" rules). Vibe search = embeddings + clusters. **LLM mood tags: yes**, Claude Haiku 5.5 via the Batch API, 200-film pilot first, then ask the user before the full run. Subtitles and the boyfriend's profile are out of scope for now.
- **Question bank swapped in (2026-10-08):** `docs/VIBE_QUESTION_BANK.md` (33 indirect "guess the vibe" questions in 9 groups, adaptive picker, short set of 10) replaces the placeholder Q1–Q8 in V2 §3. Its §4 adds dials (tension, ending_valence, humour, strangeness, cringe; gore, fantasy, talkiness, spectacle), content flags, setting/palette tags and closed "feels like" (37) / "good for" (16) vocabularies. V2 §2.5 and §4 now reference them.
- **Rule for API spend:** never spend Anthropic API money without giving the user the exact Mac command and expected cost first.

---

## 9. UI REDESIGN — TWO LOOKS ON TRIAL (2026-10-08)

**Status:** the frontend was redesigned. Two light looks ship side by side behind a header switch, and the user will pick one. **Next step once she picks:** delete the other look's block at the end of `static/styles.css` and its token set, remove the `.look-switch` from `templates/index.html` and `setLook`/`syncLookSwitch` from `app.js`, then write `DESIGN.md` from the result.

- **Looks:** `<html data-look="ticket|clean">`, set before first paint by an inline script in `<head>` (from `?look=` or `localStorage.look`).
  - **ticket:** paper ground `#e8e6e1`, white ticket cards. Each pick's footer is a perforated, notched stub showing "No. 01" and printed on the tangerine stock (mint once it's playable). The Rate card's stub holds both people's verdicts. Ticket roll colours are roles: tangerine = act, butter = selected, rose = together, mint = ready, sky = learned taste. Display type is condensed Archivo.
  - **clean:** Apple-style `#f5f5f7` greys, one blue accent `#2453d6`, system type.
- **Font:** run `sh utils/fetch_fonts.sh` once. It saves `static/fonts/Archivo-Variable.ttf`, and commit that file. The sandbox couldn't download it. Until it's there, the ticket look falls back to wide system lettering (Avenir Next Condensed on Apple devices), and its condensed display type isn't finished.
- **Placeholder name "Movie Night"** replaces "CouchCinema" (the user wants a new name; still undecided). The popcorn mark is gone.
- **Structure (shared by both looks):**
  - Tabs: Discover / Download / Rate / History. A top bar on desktop, a fixed bottom bar at ≤640px. URL hash (`#rate`) restores the tab.
  - Discover order: Movies|TV and whose-taste segmented controls → describe box → mood tiles (name + API `vibe`; emoji stripped from mood names) → "More filters" `<details>` with a live summary of active filters and "Reset all filters" → sticky CTA.
  - Results: one header naming the engine ("Learning your taste" / "Picked by rating & genre") instead of a badge on every card. Skeletons while loading, an error state with retry, and a scroll to results.
  - Reason badges say why each title is there.
- **Bugs fixed on the way:**
  - The "From" origin chips never loaded on first visit.
  - Movies/TV toggle and "Reset" had no CSS.
  - The progress-bar cancel button was unstyled.
  - Posters used dead `via.placeholder.com` (now a typographic fallback).
  - Titles were interpolated unescaped into `innerHTML` and inline `onclick` (now `escapeHtml` + `jsArg()`).
  - A failed rating still advanced the Rate card, silently losing it (`postRating` now returns success).
  - The feedback sheet's star selector also selected the Rate tab's stars.
  - The rate keyboard shortcuts fired with a sheet open.
  - `alert()` for voice is now a toast, and the mic is hidden when unsupported.
  - The page now allows pinch-zoom.
- **Accessibility:** real `<button>` mood tiles, `aria-pressed` on every toggle, tab roles, `:focus-visible` rings, labelled inputs, Esc / backdrop close on sheets, focus return, `prefers-reduced-motion`, 16px inputs on phones (no iOS zoom).
- **Design records:** `PRODUCT.md` (product truth) and `.impeccable/surfaces/templates-index-html.md` (direction contract). `DESIGN.md` is deliberately not written until a look is chosen.
- **Verified against a mock API only** (FastAPI deps couldn't install in the sandbox): both looks at 390px and 1280px, every tab, loading / downloading / error states, and `node --check static/app.js`. An independent design review passed 7 of 8 fixes; the open item is the font above. **Not yet checked against the real server.**

---

## 10. RECOMMENDER V2 BUILD LOG (from 2026-10-08)

Work happens on branch `claude/project-thread-ig7qqo` (PR into `main`). The light redesign on `main` is the chosen UI (2026-10-08); ignore the `ui-polish-dark` branch. Of its two looks, the user keeps "ticket" (2026-10-08); "clean" is being removed in another thread, so build new UI in the ticket look.

### 10.1 Mood tags (LLM) + TMDB vibe backfill
- **Schema v6** (`_migrate_v6_vibe`): `media_items.tagline`, `tier2_checked` (0 / 1 / -1 = 404), `vibe_tags` (JSON), `vibe_tagged_at`, `facets` (JSON), `vibe_cluster_id`; new table `mood_tag_batches` (batch id, item ids, status, usage, real cost).
- **`services/vibe_vocab.py`:** the single source for the 14 dials (with 0/5/10 rubric films and anchor sentences for the free fallback), 11 content flags, 37 "feels", 16 "good for", setting / season / time-of-day / palette / story-era lists. Everything vibe-related imports from here.
- **`utils/backfill_tmdb_vibe.py`** (free, TMDB): one call per title, `/{movie|tv}/{id}?append_to_response=keywords,reviews`, fills `tier2_keywords`, `tier2_reviews_summary` (up to 3 cleaned snippets, rated + short first) and `tagline`. Resumable, most-voted first. `uv run python utils/backfill_tmdb_vibe.py [--limit N]`.
- **`utils/mood_tag.py`** (costs money): Claude Haiku 5.5 via the Message Batches API, `thinking` disabled, effort low, JSON-schema structured output, cached system prompt (rubric). Commands: `pilot` (200 titles: ~35 % films you rated, 35 % popular across genres, 15 % series, rest long-tail), `run`, `collect` (resume after Ctrl-C), `status`, `show "<title>"`, `preview` (prints one request + estimate, no API call). Every submitting command prints an estimate and needs a typed `yes`. Real cost is computed from each result's `usage` and stored per batch; reports go to `reports/` (gitignored). Needs `uv run --extra llm ...` (new optional dependency `anthropic`).
- **Prices used** (Batch API, Haiku 5.5, prompts < 100k tokens, checked 2026-10-08): input $0.05/MTok, cache write $0.0625, cache read $0.005, output $0.25.
- **Estimate:** pilot ≈ $0.02–0.04. Whole catalogue (~50k titles) ≈ $4–5 if the cached rubric hits, ≈ $9 worst case. The pilot replaces this with a measured per-title cost.
- **Status:** written and tested offline with a fake Batch client (`tests/test_mood_tag.py`, `tests/test_vibe_backfill.py`). **Not run yet** (the sandbox has no API key and can't reach TMDB). Order on the Mac: backfill (free) → pilot → user approves → `run`.

### 10.2 Search to rate (Rate tab)
- **UI:** a search box at the top of the Rate tab (`#rateSearchInput`, an ARIA combobox). 250 ms debounce, ↑/↓ + Enter, Esc closes. Results show poster, year, Film/TV and your own verdict ("You: 8/10", "Not seen"). Picking one puts it on the card in front of the queue (`openSearchedTitle`, `item.fromSearch`), progress reads "From your search", and the card says "You gave it 8/10. Change it?" when you already rated it. The last row, "Not here? Search TMDB for …", searches TMDB; picking a TMDB result adds it to the catalogue and opens it.
- **API:** `GET /api/catalogue/search?q=&media_type=all|movie|tv&limit=10&profile_id=` (items carry `my_verdict` and `co_view`), `GET /api/catalogue/tmdb-search?q=` (needs `TMDB_API_KEY`; items carry `catalogue_id` if already present), `POST /api/catalogue/tmdb-import {media_type, tmdb_id}`.
- **`services/catalogue_search.py`:** in-memory index of normalised titles (accents, case, punctuation, `&`→and, "spiderman" = "spider man"), title + original title. Rank: exact → starts-with → word-start → contains → all words, then vote count. Rebuilds itself when `COUNT(*)`/`MAX(id)` of `media_items` changes.
- **Verified:** `tests/test_catalogue_search.py`, plus the real server on a copy of `catalogue.db` driven by Playwright in both looks (390 px and 1280 px): search → pick → rate 8 → search again shows "You: 8/10" → card offers to change it; no JS errors. TMDB import isn't exercised live (TMDB is blocked in the sandbox).

### 10.3 Feedback logging (Phase 1)
- **Schema v7** (`_migrate_v7_feedback`): `discover_sessions` (one row per Discover search: profile, media type, query text, request JSON, engine, weekday, hour, and room for pop-up answers / asked order / rules / belief), `discover_impressions` (session, item, rank, score parts JSON, outcome, rating; unique per session+item), `vibe_clusters` (filled in Phase 3).
- **Outcomes**, weakest to strongest: shown < opened < dismissed < downloaded < played < rated. An outcome never downgrades. It is credited to the latest impression of that title within 7 days (`ATTRIBUTION_DAYS`).
- **Where they come from:** `/api/recommend` creates a session (page 2+ reuses it via `session_id`) and logs every returned card with its rank and score parts. "opened" comes from opening the synopsis. "dismissed" comes from the new x button on Discover cards. "downloaded" comes from `/api/torrent/download`. "played" comes from `/api/watch/start`. "rated" comes from `/api/ratings` (seen + rating) and the finish-and-rate flow.
- **API:** `POST /api/feedback {media_item_id, outcome, session_id?, rating?}`, `GET /api/feedback/stats`. Logging failures never break a recommendation (try/except).
- **Known bug (pre-existing, not fixed):** `finish_active_watch` inserts `watch_history` without `profile_id`, so ratings from the "finished, rate it" flow land with a NULL profile.
- **Verified:** `tests/test_feedback.py`, plus the live server with Playwright in both looks: dismiss fades the card and logs it, page 2 continues the ranks, watch/start marks "played".

### 10.4 Vibe documents (Phase 2)
- **`services/vibe_document.py`:** `build_document(item, notes)` makes one paragraph per title: title (year)[, series], tagline, overview (≤700 chars), genres, up to 20 TMDB keywords, then the mood part from `vibe_tags` (vibe sentence, feels, the dial labels that sit clearly at one end (≤0.25 or ≥0.75), good for, setting), then review snippets and the profiles' `watch_history.notes`. Clipped at 1,900 characters (bge-small reads ~512 tokens). Mood goes before reviews so clipping never drops it. `iter_documents(conn)` yields `(id, document)` for the whole catalogue; Phase 3 embeds these.
- **CLI:** `uv run python -m services.vibe_document --stats` (coverage of overview / tagline / keywords / reviews / mood tags) and `uv run python -m services.vibe_document "Paddington 2"` (print one document).
- Documents aren't stored; they're rebuilt when embedding, so re-running the backfill or the tagger just means re-embedding.
- **Verified:** `tests/test_vibe_document.py`.

### 10.5 Vibe engine (Phase 3)
- **Build, on the Mac (free, local):** `uv run --with torch --with sentence-transformers --with onnx --with onnxscript python training/build_vibe_index.py`. Steps: vibe documents → bge-small-en-v1.5 vectors (`models/vibe/item_vectors.npy` float16 + `item_ids.json`); facets for the 14 dials into `media_items.facets` (LLM dial when tagged, else the anchor projection mapped to its catalogue percentile; `source` = llm / anchor / mixed); spherical k-means (k = √(n/4), 80–150) into `vibe_clusters` + `media_items.vibe_cluster_id`, named by the most typical keywords / feels / genres; ONNX query encoder (INT8 if it stays ≥ 0.97 cosine to the full model, else fp32) + `tokenizer/tokenizer.json`. `--skip-embed` reuses the vectors (facets + clusters only), `--skip-export` skips the encoder, `--k N` sets the cluster count.
- **Rebuild** after the backfill or the full mood-tag run (both change the documents). `models/` is gitignored: copy `models/vibe/` to the Pi, and the catalogue too, because facets and clusters live in the DB.
- **Serving (`services/vibe_index.py`, Pi):** loads the files lazily, needs only numpy + onnxruntime + `tokenizers` (new dependency). `encode_query` (bge query prefix, CLS pooling, 64 tokens), `top`, `similarities`, `facets_for` (same dial projection for any text, for the Phase 4 pop-up). "like <film>" (`like_phrases`, `resolve_like_titles`: exact or starts-with title match, ≥ 50 votes) pulls the query vector halfway to that film, and the named film is left out of the results. No files or a failed load → `is_ready()` False and everything behaves as before.
- **Ranking:** when the index is ready and the request has text, `route_recommend` sends it to the legacy engine (`vibe_routed_to`). `RecommenderService.recommend` adds the 400 nearest titles (`VIBE_SHORTLIST`) to the SQL shortlist through the same hard filters (`get_top_candidates(only_ids=...)`), then adds `VIBE_WEIGHT` (3.0) × similarity rescaled over the pool (25th percentile → 0, best → 1). Items carry `vibe_similarity` / `vibe_boost`, and impressions log them plus `vibe_cluster_id`. A vibe-ranked list counts as targeted, so the variety shuffle is off. Response has `vibe: {used, like_titles}`. `GET /api/vibe/status`.
- **Parser fixes:** origin phrases match whole words only (+ plural), so "indiana jones", "polished" and "animated" no longer trigger Indian / Polish / anime. Negation looks back up to 3 words and stops at but/and/or ("no really scary" excludes Horror; "not scary but funny" keeps Comedy). "not complicated" / "not simple" keep their exclusions instead of adding Horror/Thriller/Crime/War. Open bugs 7 and 8 above are fixed apart from the duplicate dict keys, Kollywood/Tollywood and Nigerian notes.
- **Not done yet (later phases):** facet_fit (needs pop-up answers, Phase 4), cluster affinity and learned weights (Phase 5), MMR on vibe vectors.
- **Verified:** `tests/test_vibe_index.py` (fake index, no model). The build script ran end to end in the sandbox on `catalogue.db` with a tiny random BERT standing in for bge-small (Hugging Face is blocked here), and the live server loaded the result, routed a typed request to the vibe ranking and logged the vibe parts. **Not yet run with the real model**: that's the Mac command above.
