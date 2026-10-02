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
│   └── build_full_catalogue.py   # Full TMDB catalogue builder (10k movies + 10k TV, resumable)
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
