# M2 Browse like a real app

**Features:** L1–L5, L7, P11, P12.
**Done when:** on a phone or laptop you can find and start any movie or episode at `/nl/` without opening Jellyfin's web UI.

## Screens
- **Home `/nl/`:** Continue Watching (L5, from Jellyfin `GET /Users/{uid}/Items/Resume`), then Movies and Shows rows, and a search box at the top. It replaces M1's bare grid.
- **Movies `/nl/movies` and Shows `/nl/shows`:** poster grids (L1). Cards show poster, title, year and runtime, plus one badge at most (e.g. "New", or a progress bar).
- **Details `/nl/title/{id}` (L3):** backdrop, title, year, runtime, genres, a short synopsis, and **Play / Resume** as the main button. Show and season info for TV.
- **Show `/nl/show/{id}` (L2):** a season picker, episode cards with watched ticks and progress, and a "Next up" button (Jellyfin `GET /Shows/NextUp?SeriesId=`).
- **Search (L4):** server-side `GET /api/nl/search?q=` over our catalogue (movies, shows, episodes). Results are labelled **Ready** or **Not in library yet** (L7). "Not in library" links to the existing download flow in the current app.

## Player additions
- **Next-episode card (P11):** 30 s before the end (or when credits start, once M7 adds segments), show "Next: S1·E4 Title" with a 10 s countdown and Cancel. It uses the show's next-episode lookup.
- **Autoplay per show (P12):** a toggle on the show page, stored server-side in a new `nl_show_prefs` table (schema bump, follow `database.py`'s migration pattern). Default on.

## Data notes
- Our DB is the source of truth for what's in the library (`media_items`, `media_sources`). Jellyfin gives ids, progress and NextUp. Map between them with M0's path lookup. Cache the mapping in a new `jellyfin_id` column on `media_sources` (schema bump), so we don't scan Jellyfin on every page.
- Images: use our stored TMDB poster and backdrop URLs. Fall back to Jellyfin's `/Items/{id}/Images/Primary` through the proxy (add `Items/{id}/Images/` to the allowlist, read-only GET).

## Checks
Tests for the new API routes (fake Jellyfin), the schema migration, and NextUp/Resume parsing. `make check`. Manual: browse to a show, play the next episode, watch the countdown, cancel it, and turn autoplay off.

## Build prompt
```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M02-browse.md

Build the M2 browse screens and API under /nl/ and /api/nl/, reusing M0/M1 code. Follow the
ticket look in PRODUCT.md for cards and pages; keep it uncluttered (poster, title, year,
runtime, one badge). Schema changes follow database.py's existing migration pattern and bump
the version once. Tests with fake Jellyfin; `make check`. Update handover and README; PR into
northern-lights-player-app.
```
