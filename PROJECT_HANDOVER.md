# Project handover

Read this first in a fresh session instead of reading the whole repo. It says what the project is, where things live, what state each part is in, and what is still open. History (build logs, per-task changelogs, verification notes, old branch lists) is in `docs/HANDOVER_ARCHIVE.md`; look there only when you need the detail of how something was built.

**Keeping it current:** when you finish a feature, change its one line here (state, where it lives, what is still open) and update "Branches and open work". Do not add PR logs, dated changelogs or test-run notes; those belong in the PR. Section numbers are referenced from other docs (§10.6, §10.7, §10.8, §12, §13, §14), so keep them stable.

## Branches and open work
- `main`: has everything since PR #67/#68 (2026-10-13): the original app, the mood-based questions pop-up (§10.10) and the Northern Lights player and TV app M0-M10c (§14). Schema **v20**. Jellyfin stays the media engine behind the new player (§14). New work branches off `main`.
- `northern-lights-player-app`: finished (merged into `main`); can be deleted.
- Branch `ui-polish-dark` is unused; Labiba asked for it to be deleted on GitHub.
- `claude/project-thread-sx7sn7`: typed "no animation" (and other negated genre names) now hard-excludes the genre; PR open for Labiba. Variety and animation-share options are in `/mnt/project-files/recommender-variety/brainstorm.md`, waiting on her pick.
- Workflow: one branch and one PR per piece of work; Labiba merges. Push after every feature.

## 1. What this is
**Northern Lights** (earlier names: CouchCinema, Smart Movie Streamer, Movie Night): a self-hosted movie and TV app for Labiba (profile 1) and Gabriele, her boyfriend (profile 2). It finds and downloads titles, plays them through Jellyfin, keeps everything they have watched and rated, and learns their tastes to recommend what to watch, alone or together.
- **Server:** `mars`, a Dell OptiPlex 3060 (i5-9500T, 16 GB, 238 GB SSD), Ubuntu 24.04, rootless Docker, reached over Tailscale (`mars.tailc3278f.ts.net`, LAN 192.168.1.183). It also runs Home Assistant and Matter containers that our stack must never touch.
- **Mac:** trains models and runs the nightly pipeline (§13). The server runs no torch; it serves ONNX models and precomputed vectors.
- **Clients:** phone and laptop browsers, an Android TV app (`tv-app/`), Chromecast.

## 2. Where things live
- `server.py`: FastAPI app, every route. `database.py`: `DatabaseManager`, SQLite schema and migrations (`_migrate_vN_*`, gated by `PRAGMA user_version`).
- `services/`: recommender (`recommender_service.py`, `generative_retriever.py` TIGER, `taste_model.py` + `taste_runtime.py`, `verdicts.py`, `pairs.py`), vibe search and questions (`vibe_vocab.py`, `vibe_document.py`, `vibe_index.py`, `vibe_questions.py`, `vibe_popup.py`, `vibe_moods.py`, `mood_questions.py`, `mood_popup.py`), downloads (`torrent_service.py`, `torrent_ranking.py`, `disk_guard.py`), Jellyfin (`jellyfin_service.py`, `jellyfin_playback.py`, `device_profiles.py`), deployment (`admin_api.py`, `catalogue_patch.py`, `model_bundle.py`, `paths.py`), Northern Lights (`nl_*.py`, §14).
- `templates/index.html` + `static/app.js`, `styles.css`: the original app (tabs Discover / Download / Rate / Later / History). `templates/nl/`, `static/nl/`: Northern Lights web pages; `templates/nl/tv/`, `static/nl/tv/`: TV pages.
- `tv-app/`: Android TV app (Kotlin WebView + Media3). `training/`: offline training (TIGER, vibe index). `utils/`: backfills and maintenance scripts. `deploy/`: server scripts, compose, runbook. `ops/mac/`: Mac nightly job and admin client. `tests/`: script-style tests (+ `tests/js/` node checks, `tests/deploy/`, `tests/mac/`).
- Design docs: `docs/RECOMMENDER_ARCHITECTURE.md` (read before touching the recommender), `docs/RECOMMENDER_V2_DESIGN.md`, `docs/VIBE_QUESTION_BANK.md`, `docs/DEPLOYMENT_PLAN.md`, `docs/BUILD_GUIDE.md`, `docs/northern-lights/` (roadmap and one file per milestone), `PRODUCT.md`. Project plans also live in the project's shared files (`/mnt/project-files/`).

## 3. Data model (SQLite)
- Catalogue: `catalogue_full.db` by default (`database_path` in `config.yaml`; `DATABASE_PATH` env overrides; `catalogue.db` is a small test catalogue). The server alone assigns `media_items.id` and never deletes items.
- Core tables: `media_items` (TMDB metadata, `UNIQUE(media_type, tmdb_id)`, `vote_count`, collection fields, vibe fields `tagline`, `vibe_tags`, `facets`, `vibe_cluster_id`, `season_count`), `media_sources` (files; a show has one row per episode; `jellyfin_id` cache), `profiles` (1 Labiba, 2 Gabriele), `watch_history` (per profile; `status` seen / not_seen, "not seen" carries no rating).
- Ratings: `rating_details` (verdict, chips, vibe-check answers, `taste_score` -1..+1, `inferred`), `rating_history`, `rating_pairs`, `season_ratings`. Feedback: `discover_sessions`, `discover_impressions`, `vibe_clusters`, `mood_tag_batches`. Lists: `watch_later`.
- Northern Lights (v12-v20): `nl_show_prefs`, `nl_person_prefs`, `nl_interest`, `nl_show_viewer_prefs`, `nl_hidden_titles`, `nl_seen_together`, `nl_pacts`, `nl_vetoes`, `nl_chooser`.
- Migrations are additive and idempotent. Tests that pin a schema number use "this version or newer".

## 4. Core subsystems
- **A. Search and download** (`torrent_service.py`): providers Prowlarr (optional, `PROWLARR_URL`/`PROWLARR_API_KEY`) then YTS then Apibay. Downloads run as asyncio tasks tracked in the in-memory `downloads_store`; finished files are organised into Jellyfin's layout (`Movie (Year)/…`, `Show (Year)/Season XX/Show - SxxEyy`) and registered in `media_sources`. Healthy Prowlarr indexers: YTS, ThePirateBay, NyaaSi, Knaben (EZTV unreachable; 1337x and friends need FlareSolverr, left out). Disk guard: `torrent.max_media_gb`, `torrent.min_free_gb`.
- **B. Recommender** (`recommender_service.py`): SQL hard filters, a Bayesian quality cut to 250, personal taste scoring, vibe-search boost, MMR to 50. `route_recommend()` chooses legacy or TIGER (`recommender.engine_mode`: fallback / legacy_only / generative_only; generative after 5 rated items). Profile modes me / partner / together; co-view tiers A/B/C.
- **C. Training** (`training/`): TIGER pipeline (`training/README.md` steps A-H, `retrain_tiger.sh`) and the vibe index (`build_vibe_index.py`). Needs torch: Mac only.
- **D. Jellyfin** (`jellyfin_service.py`): creates and repairs the Movies and Shows libraries, refreshes and checks visibility, diagnostics. The server boots fine when Jellyfin is down.
- **E. TIGER** (`generative_retriever.py`): semantic-ID generative retrieval, artifacts in `models/tiger/` (must be built from the same catalogue). The deployed model is still the old synthetic one until the Mac retrain runs.

## 5. Running and testing
- Run: `uv run python server.py` (or `uv run uvicorn server:app --host 0.0.0.0 --port 8000`), at `http://localhost:8000`. Northern Lights is under `/nl/` and `/nl/tv/`. Try changes on a COPY of the catalogue (`DATABASE_PATH=/tmp/copy.db`).
- `.env`: `TMDB_API_KEY`, `JELLYFIN_URL`, `JELLYFIN_API_KEY`, `JELLYFIN_USER_ID`, optional Prowlarr, `ADMIN_TOKEN`, `NL_CAST_SECRET`. `CONFIG_PATH` and `MODELS_DIR` env pick the config and models.
- **`make check`** before every PR: ruff, shellcheck, every `tests/test_*.py` run as its own script (with a temp config), and the deploy/Mac shell tests. CI runs the same.
- Optional extras: `uv run --extra llm …` for `utils/mood_tag.py`.

## 6. Rules and conventions
- **Never spend Anthropic API money** without first giving Labiba the exact Mac command and the expected cost. Mood tagging ships off.
- Claude's sandbox cannot reach TMDB, Hugging Face, Google Maven or the user's Jellyfin, so anything needing them is a command for Labiba to run on the Mac.
- On the server, edit `deploy/config.server.yaml` in the repo, not on `mars`: every deploy copies it over `/srv/couchcinema/config.server.yaml`.
- The browser, TV app and Chromecast never see the Jellyfin API key, Jellyfin user ids, magnet links or file paths. All Jellyfin calls go through the server; `/nl/jf/` is a strict allowlist proxy (videos, subtitles, trickplay, artwork). Do not widen it without a test.
- UI: the original app uses the light "ticket" look (paper ground, white ticket cards, roll colours as roles, condensed Archivo, aurora only as holographic foil). Northern Lights pages share that look; TV pages are dark. Every new feature must work on phone (390 px), laptop and TV (D-pad focus, `NLFocus`), with no horizontal scroll.
- Missing data degrades, never 500s: no Jellyfin, no TMDB, no model, no people configured each have a fallback.
- Write plain, short user-facing text.

## 7. Recommender: known open problems
- **Same ~50 films every time:** SQL keeps the 250 best-rated titles (Bayesian TMDB score), unwatched leaves ~190, and only the top 50 are ever shown. The questions path never shuffles, and `/api/recommend` shuffles only untargeted requests. On Labiba's Mac copy (2026-10-13), 11 different mood answers gave just 88 distinct films. Nothing suppresses titles already shown (`discover_impressions` is logged, never read).
- **Animation share:** about 9% of the catalogue rated 6+, 16% of the quality pool, and up to half the top 10 after a "light" answer (6 of 30 moods weight Animation; "Grown-up" still adds a little animation weight). Taste does not cause it today because it saturates (below), but un-capping it would: her Animation weight is her 3rd biggest after the 2026-10-09 ratings.
- Generative engine ignores query exclusions, rating floor and diversity; the genre filter needs 20+ matches. Queries with a typed "no <genre>" are routed to legacy.
- Taste bonus saturates at +2.0 (genre weights are raw sums, so with ~85 ratings almost every film hits the cap and taste stops ranking anything) and has no negative signal; `rank_candidates_profile_aware` overwrites `relevance_boost` when content words exist.
- Concept boost is unbounded per generic term; `exclude_ids` filters only the current page; Tier A/B boosts are unreachable in Discover; genre chips widen instead of narrow; era/runtime filters silently fall back; the variety shuffle runs after MMR.
- Origin parser leftovers: duplicate `chinese`/`portuguese` keys, Kollywood/Tollywood swapped, Nigerian = Yoruba only.
- `finish_active_watch` in the old app writes `watch_history` without `profile_id` (Northern Lights finishes go to the right profiles).
- Dead ONNX branch in `recommender_service.py` (reads a nonexistent `embedding` column).

## 8. Recommender v2: decisions that still bind
Train on the Mac, serve on `mars`. Vibe search = embeddings + clusters. LLM mood tags with Claude Haiku via the Batch API, 200-title pilot first, then ask before the full run. Questions are about mood, never "pick a film"; soft scores (deprioritise, never remove).

## 9. UI and name
The app is **Northern Lights**. The original app's look is the ticket look (§6). `DESIGN.md` is still to be written from it. The variable font `static/fonts/Archivo-Variable.ttf` is committed; the CSS also asks for a `.woff2` that does not exist (a harmless 404).

## 10. Recommender v2 features (state)
- **10.1 Mood tags + TMDB vibe backfill:** `utils/backfill_tmdb_vibe.py` (free) and `utils/mood_tag.py` (costs money) are built and tested offline. **Not run yet** on the real catalogue.
- **10.2 Search to rate:** done (`services/catalogue_search.py`, Rate tab search, TMDB import).
- **10.3 Feedback logging:** done (`discover_sessions`, `discover_impressions`, outcomes shown < opened < dismissed < downloaded < played < rated).
- **10.4 Vibe documents:** done (`services/vibe_document.py`).
- **10.5 Vibe engine:** done (`training/build_vibe_index.py` on the Mac, `services/vibe_index.py` serves). Real-model build depends on the Mac run; rebuild after backfills.
- **10.6 Questions pop-up:** done. It runs on the mood map (`MoodSession`, §10.10) and opens on every search while "Ask me a few questions first" is on; the old dial-based `PopupSession` is still in the repo. If it never opens, the serving catalogue has no facets or mood tags (`GET /api/vibe/status` shows `mood_data`).
- **10.7 Verdict ratings:** done. Five verdicts (rewatch, loved, liked, meh, not for me), chips, vibe-check questions, inferred verdicts for old 1-10 ratings, "Sort out my 7s".
- **10.8 Taste model** (`facet_fit`, `cluster_affinity`): built, **off by default** (`recommender.taste_model.enabled`). The offline check did not beat the current ordering; turn on after more confirmed verdicts and re-run `utils/eval_taste_model.py`.
- **10.8b "This or that" pairs:** done (`services/pairs.py`, Bradley-Terry nudge). Ranking does not read the pair nudge yet.
- **10.9 Per-season TV verdicts:** done. `utils/backfill_season_counts.py` TMDB pass still to run on the Mac.
- **10.10 Mood map:** 30 night moods in 6 groups (`vibe_moods.py`), entropy question picker (`mood_questions.py`, `mood_popup.py`), ranking by mood mix (`recommender.mood_weight`, 3.0), "Tonight: …" line in results. Weights are first guesses; tune by playing it. No learning across nights yet.
- **Not built:** Phase 5 learning (weight fitting, `p(option)`, `usefulness(q)`), co-view predictor (movies only), Watch later as a ranking signal.

## 11. Download tab and "Best match" sort
Done. Downloads start only from the Download tab ("Choose a copy" on Discover jumps there with the catalogue link). `services/torrent_ranking.py` scores quality × seeder health × size fit × scope match; tuning numbers in `config.yaml` `torrent_ranking:`. Numbers were tuned on sample lists only; tune from real results.

## 12. Watch later
Done (schema v11). One shared list, one row per person per title (`watch_later`), "I'm …" switch in the header, bookmark on Discover / Rate / search, the Later tab, auto-done on rating or finishing. Northern Lights M6 adds "Just me" rows and yes / maybe / pass answers (§14).

## 13. Deployment
Live on `mars`. Docker Compose stack in `/srv/couchcinema` (app + Jellyfin + Prowlarr) under a `deploy` user with rootless Docker. CI (`.github/workflows/ci.yml`) runs `make check` and pushes `ghcr.io/rupaai/smart-recommender-player:sha-<sha>` (also for `northern-lights-player-app`). Deploy is the manual **Deploy** workflow on the self-hosted runner, with health check and automatic rollback; deploying `main` puts Northern Lights on `mars`. The image job pulls BuildKit from the AWS mirror and falls back to Docker Hub when the mirror says `toomanyrequests`. Alerts via ntfy and healthchecks.io. Admin API under `/admin` (Bearer `ADMIN_TOKEN`) for exports, catalogue patches and model bundles. Mac nightly job (`ops/mac/nightly.sh`, launchd 03:30): backup, catalogue refresh, TIGER retrain, vibe index build, eval gate, publish bundle. Full steps: `deploy/RUNBOOK.md`, `ops/mac/README.md`, `docs/BUILD_GUIDE.md`, `docs/DEPLOYMENT_PLAN.md`.
- Known gap: titles added by the catalogue refresh get no TIGER semantic code until the prepare / RQ-VAE steps are re-run by hand.

## 14. Northern Lights player and TV app
Our own player in front of Jellyfin. Plans and feature ids (P, L, U, W, C, T, H, D) are in `docs/northern-lights/`. Pages: web `/nl/…`, TV `/nl/tv/…`, player `/nl/play/{id}` (`?source=` for an episode, `?tv=1` for the remote).
- **M0-M1 Player** (merged): `jellyfin_playback.py`, `device_profiles.py` (browser profile from probed codecs), `static/nl/player-core.js` (the only file touching hls.js, the `<video>` or the native bridge), `player.js`. Audio and subtitle menus, resume, skip-length chip, progress reports.
- **M2 Browse** (merged): `nl_browse.py`, home with Continue Watching, Movies, Shows, title and show pages, search, next-episode card and per-show autoplay. Titles whose files are gone are hidden.
- **M3 TV** (merged): TV pages with `static/nl/tv/focus.js`; Android app in `tv-app/` (WebView in front of a headless Media3 player, bridge `window.NorthernLightsNative`, now **v4**); APK built by `.github/workflows/tv-app.yml`, debug-signed with the committed keystore.
- **M5 Subtitles** (merged): search and download through Jellyfin's OpenSubtitles plugin, appearance (size, background, position), delay (web only).
- **M4 Who's watching** (merged): people in `northern_lights.people` config, cookie `nl_viewer` (a person or "Both of us"), per-person Jellyfin user, progress written for each viewer, per-person prefs (`/api/nl/prefs`), finishes rated to the right profiles.
- **M6 Picks for two** (merged, `nl_picks.py`): watchlist with shared and "Just me" titles, yes / maybe / pass, a "For both of us" row from the existing recommender, runtime chips.
- **M7a Player polish** (merged, `nl_player_extras.py`): trickplay thumbnails, chapters, skip intro / credits, phone gestures, speed.
- **M7b Browse polish** (merged, `nl_polish_b.py`): Recently added / watched, Unwatched only, hide from home, cast and crew, trailer, tech details, More like this.
- **M8 Casting**: M8a (merged, `nl_cast.py`): Chromecast sender, receiver page, cast profile, signed stream tokens, hand-back to the phone. M8b (merged, `nl_type.py`): type TV searches on the phone via a code and QR.
- **M9a Together** (merged, `nl_together.py`): seen-together marks, watch-together pact, mark unwatched for one person.
- **M9b Choosing** (merged, `nl_choose.py`): Choose for us, weekday and time-of-day defaults, Not tonight / Never vetoes, rotating chooser.
- **M9c Reactions and spoilers** (merged, `nl_reactions.py`): "How was it?" after a film or a show's last episode (writes verdicts), spoiler-hidden episode lists.
- **M10a Downloads** (merged, `nl_downloads.py`): download states, plain-words errors, Retry.
- **M10b Status and admin** (merged, `nl_health.py`): header status (Jellyfin, disk, queue), web-only `/nl/admin`.
- **M10c Settings and picture** (merged, `nl_settings.py`, `nl_picture.py`): per-person skip length, autoplay, quality cap, picture size; conversion notes; audio-description labels.
- Nothing in M0-M10 has been checked against a real Jellyfin with two users, a real TV running the current APK, or a real Chromecast; each milestone was checked in headless Chromium with a fake Jellyfin.

## 15. Waiting on Labiba (by hand)
- **Mac commands:** `uv run python utils/backfill_collections.py`, `utils/backfill_tmdb_vibe.py`, `utils/backfill_season_counts.py`, then `training/build_vibe_index.py` (or install the nightly job, which retrains and rebuilds). Mood-tag pilot only after the cost is confirmed.
- **Jellyfin:** install the OpenSubtitles plugin and sign in (M5); create Gabriele's Jellyfin user and put both people in `deploy/config.server.yaml` (M4); enable trickplay and run Generate Trickplay Images, install Intro Skipper and run segment detection (M7a).
- **Casting (optional):** HTTPS for the phone (`tailscale serve`), `northern_lights.cast_base_url` = mars's LAN address, `NL_CAST_SECRET`; optional custom receiver app id (`deploy/RUNBOOK.md` step 6c).
- **Optional config:** admin tool links (`northern_lights.tools`), `northern_lights.timezone`.
- **GitHub:** delete `ui-polish-dark`; branch protection on `main`.
- **Media:** one-time move of old show folders to `~/Media/Shows`; confirm new downloads show on the TV.

## 16. Check first after the next deploy
1. With two people configured, play something as "Both of us" for a minute, then check both Jellyfin users show it in Continue Watching (if not, look at `JellyfinPlayback.report_progress`).
2. Install the latest TV APK and check: picture behind the transparent page, Back, subtitles, speed, skip-intro focus with the remote.
3. Play an mp4 and an mkv, switch audio, a text and a picture subtitle, leave and resume, phone layout.
4. `GET /api/nl/picks` reports `engine: recommender` (else the image lacks the model and the row falls back).
