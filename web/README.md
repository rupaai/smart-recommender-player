# web/: the HTTP layer

`server.py` only builds the app: it includes one router per file in `web/routes/`,
runs the start-up jobs (resume downloads, Wishmatch model, watched cleanup, Jellyfin
libraries) and starts uvicorn. The logic lives in `services/`; these files only turn
HTTP requests into calls on it.

| File | What it holds |
|---|---|
| `state.py` | The shared objects, built once: settings, database, Jellyfin, recommenders, downloads, Northern Lights services. Routes use `state.db`, `state.jellyfin_playback`, ... Swap one here (or in a test: `state.db = ...`) and every route sees it. |
| `helpers.py` | Helpers several route files share: who is watching (`_nl_viewer`), rendering a page (`_nl_page`), Jellyfin checks. |
| `models.py` | The request bodies (pydantic models). |
| `routes/core.py` | `/`, `/healthz`, `/api/status`, Jellyfin status and repair |
| `routes/discover.py` | `/api/recommend`, `/api/discover/*`, feedback, `/api/rec/*` (Wishmatch) |
| `routes/watch.py` | original app: watch start/finish, history, Watch later, profiles |
| `routes/ratings.py` | Rate tab: queue, verdicts, seasons, pairs, catalogue search, TMDB import |
| `routes/torrents.py` | `/api/torrent/*`: search, download, cancel, status |
| `routes/nl_viewer.py` | who's watching, per-person settings |
| `routes/nl_play.py` | playback, casting, subtitles, tracks, Jellyfin proxy `/nl/jf/`, progress |
| `routes/nl_library.py` | home, title and show pages' data, reactions, watched marks, search |
| `routes/nl_picks.py` | watchlist, yes/maybe/pass, For both of us, Choose for us, vetoes |
| `routes/nl_admin.py` | downloads list, header status, admin page |
| `routes/nl_pages.py` | HTML pages for phone (`/nl/`), TV (`/nl/tv/`), laptop (`/player/`) |
| `routes/nl_type.py` | phone keyboard for TV search, Chromecast receiver, player page |

**Adding a route:** put it in the file for its area (or a new file with `router = APIRouter()`,
then `app.include_router(...)` in `server.py`). A new top-level code folder must also be copied
in the `Dockerfile` (`tests/test_layout.py` checks this).
