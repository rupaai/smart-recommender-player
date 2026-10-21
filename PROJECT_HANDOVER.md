# Project handover

Read this first in a fresh session instead of reading the whole repo. It says what the project is, where things live, what state each part is in, and what is still open. History (build logs, per-task changelogs, verification notes, old branch lists) is in `docs/HANDOVER_ARCHIVE.md`; look there only when you need the detail of how something was built.

**Keeping it current:** when you finish a feature, change its one line here (state, where it lives, what is still open) and update "Branches and open work". Do not add PR logs, dated changelogs or test-run notes; those belong in the PR. Section numbers are referenced from other docs (§10.6, §10.7, §10.8, §12, §13, §14), so keep them stable.

## Branches and open work
- `main`: has everything since PR #67/#68 (2026-10-13), PR #70 (2026-10-14, Discover variety, §10.11), and PR #79 (2026-10-20, Homelab deployment): the original app, the mood-based questions pop-up (§10.10) and the Northern Lights player and TV app M0-M10c (§14). Schema **v20**. Jellyfin stays the media engine behind the new player (§14). New work branches off `main`.
- `northern-lights-player-app`: finished (merged into `main`); can be deleted.
- Branch `ui-polish-dark` is unused; Labiba asked for it to be deleted on GitHub.
- `claude/recommender-cleanup-44uxk8`: recommender cleanup (2026-10-21): every word list, mood card, place and weight moved to `recommender_rules.yaml`; typed genres filter; foreign titles gated; typed requests never go to TIGER. Open PR, waiting for Labiba.
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
- `tv-app/`: Android TV app (Kotlin WebView + Media3). `training/`: offline training (TIGER, vibe index). `utils/`: backfills and maintenance scripts. `homelab.yaml`, `homelab/config.yaml`: how Homelab runs the app. `ops/mac/`: Mac training run (Train button) and admin client. `tests/`: script-style tests (+ `tests/js/` node checks, `tests/mac/`).
- Design docs: `docs/RECOMMENDER_ARCHITECTURE.md` (read before touching the recommender), `docs/RECOMMENDER_V2_DESIGN.md`, `docs/VIBE_QUESTION_BANK.md`, `docs/casting.md`, `docs/northern-lights/` (roadmap and one file per milestone), `PRODUCT.md`. Project plans also live in the project's shared files (`/mnt/project-files/`).

## 3. Data model (SQLite)
- Catalogue: `catalogue_full.db` by default (`database_path` in `config.yaml`; `DATABASE_PATH` env overrides; `catalogue.db` is a small test catalogue). The server alone assigns `media_items.id` and never deletes items.
- Core tables: `media_items` (TMDB metadata, `UNIQUE(media_type, tmdb_id)`, `vote_count`, collection fields, vibe fields `tagline`, `vibe_tags`, `facets`, `vibe_cluster_id`, `season_count`), `media_sources` (files; a show has one row per episode; `jellyfin_id` cache), `profiles` (1 Labiba, 2 Gabriele), `watch_history` (per profile; `status` seen / not_seen, "not seen" carries no rating).
- Ratings: `rating_details` (verdict, chips, vibe-check answers, `taste_score` -1..+1, `inferred`), `rating_history`, `rating_pairs`, `season_ratings`. Feedback: `discover_sessions`, `discover_impressions`, `vibe_clusters`, `mood_tag_batches`. Lists: `watch_later`.
- Northern Lights (v12-v20): `nl_show_prefs`, `nl_person_prefs`, `nl_interest`, `nl_show_viewer_prefs`, `nl_hidden_titles`, `nl_seen_together`, `nl_pacts`, `nl_vetoes`, `nl_chooser`.
- Migrations are additive and idempotent. Tests that pin a schema number use "this version or newer".

## 4. Core subsystems
- **A. Search and download** (`torrent_service.py`): providers Prowlarr (optional, `PROWLARR_URL`/`PROWLARR_API_KEY`) then YTS then Apibay. Downloads run as asyncio tasks tracked in the in-memory `downloads_store`; finished files are organised into Jellyfin's layout (`Movie (Year)/…`, `Show (Year)/Season XX/Show - SxxEyy`) and registered in `media_sources`. Healthy Prowlarr indexers: YTS, ThePirateBay, NyaaSi, Knaben (EZTV unreachable; 1337x and friends need FlareSolverr, left out). Disk guard: `torrent.max_media_gb`, `torrent.min_free_gb`.
- **B. Recommender** (`recommender_service.py`): SQL hard filters, a Bayesian quality cut (`shortlist_size`), personal taste scoring, the request's genres and words, vibe-search boost, MMR (`final_pool`). `route_recommend()` chooses legacy or TIGER (`recommender.engine_mode`: fallback / legacy_only / generative_only; generative after 5 rated items, and only for requests with no typed words). Profile modes me / partner / together; co-view tiers A/B/C.
- **B1. Recommender rules** (`recommender_rules.yaml`, loaded by `services/recommender_rules.py`, `RECOMMENDER_RULES_PATH` overrides): the only place for word lists and personal choices. Genre names typed ("comedy") are a hard filter, "no X" a hard exclusion; genre hints ("laugh", "scary") pull hard (+2) and add that genre's best titles to the shortlist; tones ("relaxing", "cozy") pull softly and exclude what nothing else asks for; "less X" pushes down. Typed genres beat the mood card, whose genres then only pull. Foreign gate: a non-English title (`home_languages: [en]`) shows only at TMDB 7.8+ with 2,000+ votes (shows 500+), unless the request names a place or language, says "foreign", or ranks the library (`only_ids`). Also holds concept themes, mood cards, places, Choose-for-us defaults, profile mix, tiers, memory, taste and spread numbers. Funnel sizes, engine mode and the taste-model switch stay in `config.yaml`.
- **C. Training** (`training/`): TIGER pipeline (`training/README.md` steps A-H, `retrain_tiger.sh`) and the vibe index (`build_vibe_index.py`). Needs torch: Mac only.
- **D. Jellyfin** (`jellyfin_service.py`): creates and repairs the Movies and Shows libraries, refreshes and checks visibility, diagnostics. The server boots fine when Jellyfin is down.
- **E. TIGER** (`generative_retriever.py`): semantic-ID generative retrieval, artifacts in `models/tiger/` (must be built from the same catalogue). The deployed model is still the old synthetic one until the Mac retrain runs.

## 5. Running and testing
- Run: `uv run python server.py` (or `uv run uvicorn server:app --host 0.0.0.0 --port 8000`), at `http://localhost:8000`. Northern Lights is under `/nl/` and `/nl/tv/`. Try changes on a COPY of the catalogue (`DATABASE_PATH=/tmp/copy.db`).
- `.env`: `TMDB_API_KEY`, `JELLYFIN_URL`, `JELLYFIN_API_KEY`, `JELLYFIN_USER_ID`, optional Prowlarr, `ADMIN_TOKEN`, optional `NL_CAST_SECRET` (else the app keeps one in `nl_cast_secret` next to the database). `CONFIG_PATH` and `MODELS_DIR` env pick the config and models.
- **`make check`** before every PR: ruff, shellcheck, every `tests/test_*.py` run as its own script (with a temp config), and the Mac shell tests. CI runs the same.
- Optional extras: `uv run --extra llm …` for `utils/mood_tag.py`.

## 6. Rules and conventions
- **Never spend Anthropic API money** without first giving Labiba the exact Mac command and the expected cost. Mood tagging ships off.
- Claude's sandbox cannot reach TMDB, Hugging Face, Google Maven or the user's Jellyfin, so anything needing them is a command for Labiba to run on the Mac.
- On the server, edit `homelab/config.yaml` in the repo, not on `mars`: it is baked into the image.
- The browser, TV app and Chromecast never see the Jellyfin API key, Jellyfin user ids, magnet links or file paths. All Jellyfin calls go through the server; `/nl/jf/` is a strict allowlist proxy (videos, subtitles, trickplay, artwork). Do not widen it without a test.
- UI: the original app uses the light "ticket" look (paper ground, white ticket cards, roll colours as roles, condensed Archivo, aurora only as holographic foil). Northern Lights pages share that look; TV pages are dark. Every new feature must work on phone (390 px), laptop and TV (D-pad focus, `NLFocus`), with no horizontal scroll.
- Missing data degrades, never 500s: no Jellyfin, no TMDB, no model, no people configured each have a fallback.
- Write plain, short user-facing text.

## 7. Recommender: known open problems
- Variety (§10.11) only remembers Discover's own impressions. Rows like "For both of us" don't log impressions, so memory doesn't rotate them.
- Genre spread shelves are fixed in `SPREAD_GENRES`. Animation and Family are exempt only when the request or mood answers ask for them.
- Generative engine ignores rating floor and diversity; the genre filter needs 20+ matches. Every typed request is routed to legacy (it can't read words), so TIGER only serves no-words requests (plus the foreign gate).
- Taste: `taste_formula: average` replaced the capped sum (§10.11). The `sum` path and `get_profile_taste_profile` are still used by the rating queue and the cold-start path. The taste model (§10.8) is still off; turning it on is the next step for "driven by our taste" once more verdicts exist.
- SQL concept boost is unbounded per generic term; `exclude_ids` filters only the current page; Tier A/B boosts are unreachable in Discover; era/runtime filters silently fall back; the variety shuffle runs after MMR.
- `shortlist_size` went from 1,500 to 500 on 2026-10-20; with no genre in the request the quality cut holds few of any one genre. Hinted genres now add their own shortlist, but a vague request still sees mostly the top-rated 500.
- The hint and tone word lists are first guesses; extend them in `recommender_rules.yaml` when a word does nothing.
- The legacy `/api/watch/finish` path relies on SQLite's default `profile_id = 1`; make the single-viewer profile explicit/configurable as part of the portability work.

## 8. Recommender v2: decisions that still bind
Train on the Mac, serve on `mars`. Vibe search = embeddings + clusters. LLM mood tags with Claude Haiku via the Batch API, 200-title pilot first, then ask before the full run. Questions are about mood, never "pick a film"; soft scores (deprioritise, never remove).

## 9. UI and name
The app is **Northern Lights**. The original app's look is the ticket look (§6). `DESIGN.md` is still to be written from it. The variable font `static/fonts/Archivo-Variable.ttf` is committed; the CSS also asks for a `.woff2` that does not exist (a harmless 404).

## 10. Recommender v2 features (state)
- **10.1 Mood tags + TMDB vibe backfill:** `utils/backfill_tmdb_vibe.py` (free) and `utils/mood_tag.py` (costs money). **Run on 2026-10-08 on the Mac's `catalogue_full.db`:** 48,260 titles tagged by Haiku for $5.30 (pilot + 5 batches, see `mood_tag_batches`, `reports/mood_tag_run.md`); `facets` there come from those tags. Not yet confirmed that `mars`'s database has them (`GET /api/vibe/status`). Only the questions pop-up, the off-by-default taste model and the vibe index read them; typed searches do not use them yet.
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
- **10.11 Variety and animation balance** (2026-10-14, PR #70): the candidate pool is now capped at 500 and up to 200 are shown (`shortlist_size`, `final_pool`); mood re-ranking can only reorder the candidates in that capped pool. Discover remembers what it showed: a title shown and not opened loses 1 point per showing, fading over 14 days and capped at 3. "Not this one" is pushed out for 30 days, and a series shares the penalty (`_apply_memory`, `get_shown_memory`). Genre spread allows at most 2 of any 10 picks from one shelf (animation, horror, documentary, music, war, western, family) unless asked (`apply_genre_spread`). "Grown-up" pushes animation and family down (option `avoid`), and animation weight is cut in Cosy, Brain off, Adventure and Background. Taste is averaged (`build_taste_affinity`): how much more you liked a genre than your average, shrunk, then averaged over the film's genres, with keywords added. Leave-one-out on Labiba's 85 ratings gives rank correlation +0.15, against 0 for the old capped sum (`utils/eval_taste_formula.py`). The signal is weak, so re-check as ratings grow. On her Mac copy, the same search on 6 nights gave 60 different top-10 films (10 before), and animation is at most 2 in any top 10.
- **Not built:** Phase 5 learning (weight fitting, `p(option)`, `usefulness(q)`), co-view predictor (movies only), Watch later as a ranking signal.

## 11. Download tab and "Best match" sort
Done. Downloads start only from the Download tab ("Choose a copy" on Discover jumps there with the catalogue link). `services/torrent_ranking.py` scores quality × seeder health × size fit × scope match; tuning numbers in `config.yaml` `torrent_ranking:`. Numbers were tuned on sample lists only; tune from real results.
Each result's download slot is keyed by the torrent's info hash (`downloadSlotId` in `static/app.js`), never by its position in the list, so re-sorting or searching again keeps progress, Cancel and Play on the copy that was picked; the same torrent listed twice is drawn once (`tests/js/direct_download_slots_check.js`).

## 12. Watch later
Done (schema v11). One shared list, one row per person per title (`watch_later`), "I'm …" switch in the header, bookmark on Discover / Rate / search, the Later tab, auto-done on rating or finishing. Northern Lights M6 adds "Just me" rows and yes / maybe / pass answers (§14).

## 13. Deployment
Live on `mars` under Homelab (`homelab.yaml`; Jellyfin and Prowlarr are Homelab services). CI (`.github/workflows/ci.yml`) runs `make check` and pushes a linux/amd64 image `ghcr.io/rupaai/smart-recommender-player:sha-<sha>` for every push to `main`; Homelab puts it on dev, and prod waits for Promote. Prod also answers on port 8000 (the TV app's and the Mac's address). Secrets (`TMDB_API_KEY`, `JELLYFIN_API_KEY`, ...) are set in the Homelab companion; `ADMIN_TOKEN` is made by Homelab (`generate_secrets`; copy it from Apps -> Northern Lights -> Secrets), and the cast secret is kept by the app in `/data`. Running downloads are stored in SQLite and start again after a restart. The old couchcinema deploy scripts are gone; README.md has the steps to remove what is left on `mars`. Admin API under `/admin` (Bearer `ADMIN_TOKEN`) for exports, catalogue patches and model bundles. Mac training run, no schedule (Labiba's choice, 2026-10-19: the Mac is off at night): the **Train CouchCinema** button (`ops/mac/train-now.sh`, put in `~/Applications` by `ops/mac/install.sh`, which also removes the old launchd job) checks `/healthz`, then runs `ops/mac/nightly.sh`: backup, catalogue refresh, TIGER retrain, vibe index build, eval gate, publish bundle; catalogue refresh when `~/.couchcinema/last-catalogue` is a week old. Full steps: `ops/mac/README.md`.
- Known gap: titles added by the catalogue refresh get no TIGER semantic code until the prepare / RQ-VAE steps are re-run by hand.

## 14. Northern Lights player and TV app
Our own player in front of Jellyfin. Plans and feature ids (P, L, U, W, C, T, H, D) are in `docs/northern-lights/`. Pages: web `/nl/…`, TV `/nl/tv/…`, player `/nl/play/{id}` (`?source=` for an episode, `?tv=1` for the remote).
- **M0-M1 Player** (merged): `jellyfin_playback.py`, `device_profiles.py` (browser profile from probed codecs), `static/nl/player-core.js` (the only file touching hls.js, the `<video>` or the native bridge), `player.js`. Audio and subtitle menus, resume, skip-length chip, progress reports.
- **M2 Browse** (merged): `nl_browse.py`, home with Continue Watching, Movies, Shows, title and show pages, search, next-episode card and per-show autoplay. Titles whose files are gone are hidden.
- **M3 TV** (merged): TV pages with `static/nl/tv/focus.js`; the TV bar is Home, Downloads, Search, with the person as a round initial in the top-right corner (Watchlist and Choose pages still exist at `/nl/tv/watchlist`, `/nl/tv/choose` but are not in the bar); Android app in `tv-app/` (WebView in front of a headless Media3 player, bridge `window.NorthernLightsNative`, now **v4**); APK built by `.github/workflows/tv-app.yml`, debug-signed with the committed keystore.
- **M5 Subtitles** (merged): search and download through Jellyfin's OpenSubtitles plugin, appearance (size, background, position), delay (web only). Text subtitle URLs always end in `Stream.vtt` (Jellyfin converts; the TV app only reads WebVTT, so `.srt` DeliveryUrls showed nothing). TV: subtitle menus and the finder pause the film while open; "off" hides Media3's subtitle view instead of re-selecting tracks; text tracks are fetched once 8 s after a stream starts so Jellyfin has them extracted. The finder is a right-hand sheet on the TV (cleaned release names, one plain meta line, no Close button: Back closes).
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
- **Train button (after PR #78 merges):** on the Mac, `git pull && bash ops/mac/install.sh` (adds the button, removes the 03:30 job), then press Train CouchCinema once; nothing trained since 2026-10-10. Pause the healthchecks.io nightly check if one exists.
- **Mac commands:** `uv run python utils/backfill_collections.py`, `utils/backfill_tmdb_vibe.py`, `utils/backfill_season_counts.py`, then `training/build_vibe_index.py` (or install the nightly job, which retrains and rebuilds). Mood-tag pilot only after the cost is confirmed.
- **Jellyfin:** install the OpenSubtitles plugin and sign in (M5); create Gabriele's Jellyfin user and put both people in `homelab/config.yaml` (M4); enable trickplay and run Generate Trickplay Images, install Intro Skipper and run segment detection (M7a).
- **Casting (optional):** HTTPS for the phone (`tailscale serve`), `northern_lights.cast_base_url` = mars's LAN address; optional custom receiver app id (`docs/casting.md`).
- **Optional config:** admin tool links (`northern_lights.tools`), `northern_lights.timezone`.
- **GitHub:** delete `ui-polish-dark`; branch protection on `main`.
- **Media:** one-time move of old show folders to `~/Media/Shows`; confirm new downloads show on the TV.

## 16. Check first after the next deploy
1. With two people configured, play something as "Both of us" for a minute, then check both Jellyfin users show it in Continue Watching (if not, look at `JellyfinPlayback.report_progress`).
2. Install the latest TV APK and check: picture behind the transparent page, Back, subtitles, speed, skip-intro focus with the remote.
3. Play an mp4 and an mkv, switch audio, a text and a picture subtitle, leave and resume, phone layout.
4. `GET /api/nl/picks` reports `engine: recommender` (else the image lacks the model and the row falls back).
