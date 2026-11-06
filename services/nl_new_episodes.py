"""
New episodes to download (services/nl_new_episodes.py), for the Northern Lights "New & popular" page.

Two lists, both built on the server (mars) so the TV pages only draw them:

- **Your shows**: shows you have files of, marked as seen, or saved for later, whose latest episode on TMDB
  came out in the last `recent_days` and that you don't have yet. An episode counts as "had" when the
  library ever had a file of it, even if the watched cleanup deleted it since.
- **Popular right now**: shows trending on TMDB this week that are still airing, well rated, not in a skipped
  genre (news, talk, reality...), and that fit your taste (Wishmatch's taste score, when the show is in the
  tagged catalogue).

Each card offers one download, the next episode in watching order: from S1E1 for a show you never had,
on from where your files are for one you have, the latest season for one you watched elsewhere. The season
picker lists every season and episode, so you can get any episode, or a whole finished season, yourself. The Download button asks the server to find copies ("Best match" ranking, same as the Download
tab), drop the ones for another episode or show, and start the best one. Copies are kept here and only a
short `copy_id` goes to the page: magnet links never leave the server.

Settings: `new_episodes:` in config.yaml. TMDB is free; nothing here spends money.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

IMAGE_BASE = "https://image.tmdb.org/t/p/"

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "recent_days": 45,          # the latest episode came out within this many days
    "your_shows_max": 60,       # how many of your shows are checked on TMDB (most recently active first)
    "skip_rated_below": 5,      # a show someone rated below this (1-10) is not "yours"
    "popular_pages": 2,         # TMDB trending pages read (20 shows each)
    "popular_max": 18,          # cards in Popular right now
    "min_rating": 7.0,          # TMDB rating floor for popular shows
    "min_votes": 100,           # and the votes behind it
    "min_taste": -0.15,         # a popular show we know your taste for must score at least this (-1..1)
    "taste_weight": 0.6,        # popular order: this much taste, the rest trending rank
    "skip_genres": ["News", "Talk", "Reality", "Soap", "Kids"],
    "cache_hours": 6,           # TMDB answers are reused this long
    "workers": 6,               # TMDB lookups at once
    "copies_max": 12,           # copies listed under "Choose another copy"
    "list_minutes": 15,         # a built page is reused this long per viewer
    "movies_max": 18,           # cards in Popular movies (and per service)
    "movie_min_days_out": 30,   # a trending film must have come out at least this long ago (cinema-only films have no copies)
    "watch_region": "US",       # the country whose streaming catalogues the service tabs show (TMDB, two letters)
    "provider_max": 18,         # cards per section on a service tab
    # The service tabs: TMDB watch-provider ids. Names are what the tab says.
    "providers": [
        {"key": "netflix", "name": "Netflix", "id": 8},
        {"key": "prime", "name": "Prime Video", "id": 9},
        {"key": "apple", "name": "Apple TV+", "id": 350},
        {"key": "disney", "name": "Disney+", "id": 337},
        {"key": "max", "name": "Max", "id": 1899},
        {"key": "hulu", "name": "Hulu", "id": 15},
        {"key": "paramount", "name": "Paramount+", "id": 531},
    ],
}

_SEASON_PACK_RE = re.compile(r"(?i)(?:\bs(\d{1,2})(?![\d]|\s*[-._ ]?\s*e\d)|\bseason[\s._-]*(\d{1,2})\b)")
_RANGE_RE = re.compile(r"(?i)\bs\d{1,2}\s*-\s*s?\d{1,2}\b|\bseasons?[\s._-]*\d{1,2}\s*-\s*\d{1,2}\b")
_EPISODE_RE = re.compile(r"(?i)(?<!\d)s(\d{1,2})e(\d{1,3})(?:\s*[-_. ]\s*(?:e|ep)?(\d{1,3}))?(?!\d)|(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)")
_WORD_RE = re.compile(r"[a-z0-9]+")
_NOISE = {"the", "a", "an", "and", "of"}


def settings_from(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`new_episodes:` from config.yaml laid over the defaults."""
    raw = (config or {}).get("new_episodes") or {}
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if v is not None})
    return out


# ---------------------------------------------------------------- pure helpers


def parse_day(value: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def episode_code(season: int, episode: Optional[int] = None) -> str:
    return f"S{int(season):02d}" + (f"E{int(episode):02d}" if episode else "")


def nice_day(day: Optional[date], today: date) -> str:
    """'today', 'yesterday', '3 Nov'."""
    if day is None:
        return ""
    if day == today:
        return "today"
    if day == today - timedelta(days=1):
        return "yesterday"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return f"{day.day} {day.strftime('%b')}"


def season_episode_count(details: Dict[str, Any], season: int) -> Optional[int]:
    for s in details.get("seasons") or []:
        if s.get("season_number") == season and s.get("episode_count"):
            return int(s["episode_count"])
    return None


def aired_episodes(details: Dict[str, Any], today: Optional[date] = None) -> List[Tuple[int, int]]:
    """Every (season, episode) that has aired, in order: whole seasons before the latest one, and the
    latest season up to TMDB's last aired episode. Specials (season 0) are left out."""
    last = details.get("last_episode_to_air") or {}
    last_season = int(last.get("season_number") or 0)
    upto = int(last.get("episode_number") or 0)
    out: List[Tuple[int, int]] = []
    for s in sorted(details.get("seasons") or [], key=lambda x: int(x.get("season_number") or 0)):
        n = int(s.get("season_number") or 0)
        if n < 1 or n > last_season:
            continue
        count = upto if n == last_season else int(s.get("episode_count") or 0)
        out.extend((n, e) for e in range(1, count + 1))
    if not out and last_season >= 1:
        out = [(last_season, e) for e in range(1, upto + 1)]
    return out


START_MODES = ("beginning", "continue", "latest")


def next_in_order(aired: List[Tuple[int, int]], have: Set[Tuple[int, int]], start: str) -> List[Tuple[int, int]]:
    """The aired episodes still to get, in watching order.

    beginning: from S1E1 (a show you have never had).
    continue:  from the first episode you ever had onwards (you have files, so watch on from there).
    latest:    the latest season only (you watched it somewhere else and have no files)."""
    if start == "continue" and have:
        first_had = min(have)
        todo = [x for x in aired if x >= first_had and x not in have]
    elif start == "latest" and aired:
        season = aired[-1][0]
        todo = [x for x in aired if x[0] == season and x not in have]
    else:
        todo = [x for x in aired if x not in have]
    return todo


def latest_offer(details: Dict[str, Any], have: Set[Tuple[int, int]], today: date, recent_days: int,
                 start: str = "latest") -> Optional[Dict[str, Any]]:
    """What to offer for a show: None when nothing came out recently or there is nothing to get.

    `have` is the (season, episode) pairs the library has (or had). The offer is always one episode, the
    next one in watching order (see next_in_order), so you can start watching while the rest come. A whole
    season is only downloaded when you pick it in the season list."""
    last = details.get("last_episode_to_air") or {}
    aired_day = parse_day(last.get("air_date"))
    season = int(last.get("season_number") or 0)
    upto = int(last.get("episode_number") or 0)
    if not aired_day or season < 1 or upto < 1 or (today - aired_day).days > int(recent_days) or aired_day > today:
        return None
    todo = next_in_order(aired_episodes(details), have, start)
    if not todo:
        return None
    nxt = details.get("next_episode_to_air") or {}
    next_day = parse_day(nxt.get("air_date"))
    count = season_episode_count(details, season)
    finished = (not nxt or int(nxt.get("season_number") or 0) != season) and (count is None or upto >= count)
    first_s, first_e = todo[0]
    out: Dict[str, Any] = {
        "season": first_s,
        "episode": first_e,
        "kind": "episode",
        "start": start,
        "label": f"Get {episode_code(first_s, first_e)}",
        "missing": [e for s, e in todo if s == season],
        "to_get": len(todo),
        "latest": {"season": season, "episode": upto, "name": last.get("name") or "", "air_date": str(aired_day)},
        "season_finished": finished,
        "next": ({"season": int(nxt.get("season_number") or 0), "episode": int(nxt.get("episode_number") or 0),
                  "air_date": str(next_day)} if next_day else None),
    }
    if finished:
        line = f"Season {season} finished {nice_day(aired_day, today)}"
    else:
        line = f"{episode_code(season, upto)} out {nice_day(aired_day, today)}"
        if next_day:
            line += f" · next {nice_day(next_day, today)}"
    if start == "beginning" and (first_s, first_e) != (season, upto):
        line += f" · start from {episode_code(first_s, first_e)}"
    elif len(todo) > 1:
        line += f" · {len(todo)} to watch"
    out["status_line"] = line
    return out


def season_list(details: Dict[str, Any], have: Set[Tuple[int, int]]) -> List[Dict[str, Any]]:
    """The show's seasons for the season picker: how many episodes aired, how many you have."""
    aired = aired_episodes(details)
    out = []
    for s in sorted(details.get("seasons") or [], key=lambda x: int(x.get("season_number") or 0)):
        n = int(s.get("season_number") or 0)
        if n < 1:
            continue
        on_air = sum(1 for x in aired if x[0] == n)
        out.append({"season": n, "name": s.get("name") or f"Season {n}", "episodes": int(s.get("episode_count") or 0),
                    "aired": on_air, "have": sum(1 for x in have if x[0] == n),
                    "air_date": s.get("air_date"), "finished": on_air > 0 and on_air >= int(s.get("episode_count") or 0)})
    return out


def episode_list(season_data: Dict[str, Any], season: int, have: Set[Tuple[int, int]], today: date) -> List[Dict[str, Any]]:
    """One season's episodes for the picker: name, air date, aired yet, already had."""
    out = []
    for ep in season_data.get("episodes") or []:
        n = int(ep.get("episode_number") or 0)
        if n < 1:
            continue
        day = parse_day(ep.get("air_date"))
        out.append({"season": season, "episode": n, "code": episode_code(season, n), "name": ep.get("name") or "",
                    "air_date": str(day) if day else None, "when": nice_day(day, today), "aired": bool(day and day <= today),
                    "have": (season, n) in have, "runtime": ep.get("runtime")})
    return out


def name_words(text: str) -> Set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _NOISE}


def copy_matches(name: str, title: str, season: int, episode: Optional[int]) -> bool:
    """Is this release the asked-for episode (or the asked-for single season pack) of this show?"""
    words = name_words(title)
    head = name.lower()
    m = _EPISODE_RE.search(head)
    cut = head[: m.start()] if m else head
    pm = _SEASON_PACK_RE.search(cut) if not m else None
    if pm:
        cut = cut[: pm.start()]
    if words and not words <= name_words(cut):
        return False
    if episode:
        if not m:
            return False
        s = int(m.group(1) or m.group(4))
        e = int(m.group(2) or m.group(5))
        e2 = int(m.group(3)) if m.group(3) else e
        return s == season and e <= episode <= e2
    if m or _RANGE_RE.search(head):
        return False
    pm = _SEASON_PACK_RE.search(head)
    return bool(pm) and int(pm.group(1) or pm.group(2)) == season


def card_base(details: Dict[str, Any], media_item_id: Optional[int], in_library: bool) -> Dict[str, Any]:
    first = parse_day(details.get("first_air_date"))
    networks = [n.get("name") for n in details.get("networks") or [] if n.get("name")]
    return {
        "kind": "tv",
        "tmdb_id": int(details["id"]),
        "media_item_id": media_item_id,
        "title": details.get("name") or details.get("original_name") or "",
        "year": first.year if first else None,
        "overview": details.get("overview") or "",
        "rating": round(float(details.get("vote_average") or 0.0), 1),
        "vote_count": int(details.get("vote_count") or 0),
        "genres": [g.get("name") for g in details.get("genres") or [] if g.get("name")],
        "network": networks[0] if networks else None,
        "poster_url": f"{IMAGE_BASE}w342{details['poster_path']}" if details.get("poster_path") else None,
        "backdrop_url": f"{IMAGE_BASE}w780{details['backdrop_path']}" if details.get("backdrop_path") else None,
        "in_library": in_library,
        "href": f"/nl/show/{media_item_id}" if (media_item_id and in_library) else None,
    }


def movie_card(details: Dict[str, Any], media_item_id: Optional[int], today: date) -> Dict[str, Any]:
    """A film's card: same fields as a show's, with a movie offer ("Get movie")."""
    out_day = parse_day(details.get("release_date"))
    genres = [g.get("name") for g in details.get("genres") or [] if g.get("name")]
    runtime = details.get("runtime")
    line = f"Out {nice_day(out_day, today)}" if out_day and (today - out_day).days < 120 else (str(out_day.year) if out_day else "")
    if runtime:
        line += f" · {runtime // 60} h {runtime % 60} min" if runtime >= 60 else f" · {runtime} min"
    return {
        "kind": "movie",
        "tmdb_id": int(details["id"]),
        "media_item_id": media_item_id,
        "title": details.get("title") or details.get("original_title") or "",
        "year": out_day.year if out_day else None,
        "overview": details.get("overview") or "",
        "rating": round(float(details.get("vote_average") or 0.0), 1),
        "vote_count": int(details.get("vote_count") or 0),
        "genres": genres,
        "network": None,
        "poster_url": f"{IMAGE_BASE}w342{details['poster_path']}" if details.get("poster_path") else None,
        "backdrop_url": f"{IMAGE_BASE}w780{details['backdrop_path']}" if details.get("backdrop_path") else None,
        "in_library": False,
        "href": None,
        "offer": {"kind": "movie", "season": None, "episode": None, "label": "Get movie", "status_line": line.strip(" ·")},
    }


def movie_copy_matches(name: str, title: str, year: Optional[int]) -> bool:
    """Is this release the film: every title word before the year, the right year (a year either side for
    festival vs release dates), and no episode tag."""
    if _EPISODE_RE.search(name.lower()):
        return False
    m = re.search(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", name)
    head = name[: m.start()] if m else name
    words = name_words(title)
    if words and not words <= name_words(head):
        return False
    if year and m and abs(int(m.group(1)) - int(year)) > 1:
        return False
    return True


def popular_order(cards: List[Dict[str, Any]], taste_weight: float) -> List[Dict[str, Any]]:
    """Blend trending rank (first = 1.0) with taste (-1..1 mapped to 0..1); unknown taste counts as neutral."""
    n = max(1, len(cards))
    place = {id(c): i for i, c in enumerate(sorted(cards, key=lambda c: c["_rank"]))}

    def key(c: Dict[str, Any]) -> float:
        trend = 1.0 - place[id(c)] / n
        taste = c.get("taste")
        fit = 0.5 if taste is None else (taste + 1.0) / 2.0
        return -(taste_weight * fit + (1.0 - taste_weight) * trend)

    return sorted(cards, key=key)


def wishmatch_taste(engine: Any, ids: List[int], people: List[int], together: bool) -> Dict[int, float]:
    """Wishmatch's taste score (-1..1) of these titles for these people (the viewer first), for the titles
    that have mood tags. Same maths as a Wishmatch search with no wish: each person's taste, then combined."""
    from wishmatch.profiles import Population, combine
    catalogue = engine.adapter.catalogue()
    items = [catalogue[i] for i in ids if i in catalogue]
    if not items or not people:
        return {}
    settings = engine.settings()
    pop = Population.of(catalogue.values())
    tastes = engine.tastes(people, pop, settings)
    return {it.id: combine([t.score(it, pop)[0] for t in tastes], together, settings) for it in items}


# ---------------------------------------------------------------- the service


class NewEpisodes:
    """`tmdb_get(path, params)` returns TMDB JSON or None. `taste(ids, profile_ids)` returns {media_item_id:
    -1..1} for the titles it knows. `searcher` is the TorrentSearcher. `foreign_gate(row)` says whether a
    non-English title may show (recommender_rules.yaml foreign_titles)."""

    def __init__(self, db: Any, tmdb_get: Callable[..., Optional[Dict[str, Any]]], config: Optional[Dict[str, Any]] = None,
                 taste: Optional[Callable[[List[int], List[int]], Dict[int, float]]] = None,
                 searcher: Any = None, foreign_gate: Optional[Callable[[Dict[str, Any]], bool]] = None,
                 today: Optional[Callable[[], date]] = None, clock: Callable[[], float] = time.time):
        self.db = db
        self.tmdb_get = tmdb_get
        self.cfg = settings_from(config)
        self.taste = taste
        self.searcher = searcher
        self.foreign_gate = foreign_gate
        self.today = today or date.today
        self.clock = clock
        self._details: Dict[int, Tuple[float, Optional[Dict[str, Any]]]] = {}
        self._trending: Tuple[float, List[int]] = (0.0, [])
        self._copies: Dict[str, Dict[str, Any]] = {}
        self._built: Dict[Tuple[int, ...], Tuple[float, Dict[str, Any]]] = {}
        self._seasons: Dict[Tuple[int, int], Tuple[float, Dict[str, Any]]] = {}
        self._lists: Dict[Any, Tuple[float, List[int]]] = {}
        self._lock = threading.Lock()

    # --- TMDB, cached ---------------------------------------------------------
    def _fresh(self, at: float) -> bool:
        return self.clock() - at < float(self.cfg["cache_hours"]) * 3600

    def details(self, tmdb_id: int, kind: str = "tv") -> Optional[Dict[str, Any]]:
        key: Any = int(tmdb_id) if kind == "tv" else ("movie", int(tmdb_id))
        with self._lock:
            hit = self._details.get(key)
        if hit and self._fresh(hit[0]):
            return hit[1]
        data = self.tmdb_get(f"/{'tv' if kind == 'tv' else 'movie'}/{int(tmdb_id)}")
        if data is not None or not hit:
            with self._lock:
                self._details[key] = (self.clock(), data)
            return data
        return hit[1]   # TMDB down: the last answer is better than nothing

    def _many(self, ids: Iterable[int], kind: str = "tv") -> Dict[int, Optional[Dict[str, Any]]]:
        ids = list(dict.fromkeys(int(i) for i in ids))
        if not ids:
            return {}
        with ThreadPoolExecutor(max_workers=max(1, int(self.cfg["workers"]))) as pool:
            return dict(zip(ids, pool.map(lambda i: self.details(i, kind), ids)))

    def _cached_list(self, path: str, params: Dict[str, Any], pages: int = 1) -> List[int]:
        """TMDB ids from a list call (trending, discover), cached like the details."""
        key = (path, tuple(sorted(params.items())), pages)
        with self._lock:
            hit = self._lists.get(key)
        if hit and self._fresh(hit[0]):
            return hit[1]
        out: List[int] = []
        for page in range(1, pages + 1):
            data = self.tmdb_get(path, {**params, "page": page})
            if not data:
                break
            out.extend(int(r["id"]) for r in data.get("results") or [] if r.get("id"))
        if out:
            with self._lock:
                self._lists[key] = (self.clock(), list(dict.fromkeys(out)))
            return self._lists[key][1]
        return hit[1] if hit else []

    def trending_ids(self) -> List[int]:
        at, ids = self._trending
        if ids and self._fresh(at):
            return ids
        out: List[int] = []
        for page in range(1, int(self.cfg["popular_pages"]) + 1):
            data = self.tmdb_get("/trending/tv/week", {"page": page})
            if not data:
                break
            out.extend(int(r["id"]) for r in data.get("results") or [] if r.get("id"))
        if out:
            self._trending = (self.clock(), list(dict.fromkeys(out)))
            return self._trending[1]
        return ids

    # --- the database ---------------------------------------------------------
    def your_shows(self, profile_ids: List[int]) -> List[Dict[str, Any]]:
        """Shows with files, seen by one of these people, or on their Watch later list; most recent first."""
        marks = ",".join("?" * len(profile_ids)) or "NULL"
        floor = float(self.cfg["skip_rated_below"])
        sql = f"""
            SELECT mi.id, mi.tmdb_id, mi.title,
                   EXISTS(SELECT 1 FROM media_sources ms WHERE ms.media_item_id = mi.id) AS in_library,
                   EXISTS(SELECT 1 FROM watch_history wh WHERE wh.media_item_id = mi.id AND wh.status = 'seen'
                          AND wh.profile_id IN ({marks})) AS seen,
                   MAX(COALESCE((SELECT MAX(ms.created_at) FROM media_sources ms WHERE ms.media_item_id = mi.id), ''),
                       COALESCE((SELECT MAX(wh.watched_at) FROM watch_history wh WHERE wh.media_item_id = mi.id
                                 AND wh.profile_id IN ({marks})), ''),
                       COALESCE((SELECT MAX(wl.added_at) FROM watch_later wl WHERE wl.media_item_id = mi.id
                                 AND wl.profile_id IN ({marks})), '')) AS active_at
            FROM media_items mi
            WHERE mi.media_type = 'tv' AND mi.tmdb_id IS NOT NULL
              AND (EXISTS(SELECT 1 FROM media_sources ms WHERE ms.media_item_id = mi.id)
                   OR EXISTS(SELECT 1 FROM watch_history wh WHERE wh.media_item_id = mi.id AND wh.status = 'seen'
                             AND wh.profile_id IN ({marks}))
                   OR EXISTS(SELECT 1 FROM watch_later wl WHERE wl.media_item_id = mi.id AND wl.done_at IS NULL
                             AND wl.profile_id IN ({marks})))
              AND NOT EXISTS(SELECT 1 FROM watch_history wh WHERE wh.media_item_id = mi.id
                             AND wh.profile_id IN ({marks}) AND wh.user_rating IS NOT NULL AND wh.user_rating < ?)
            ORDER BY active_at DESC
            LIMIT ?
        """
        params = [*profile_ids] * 6 + [floor, int(self.cfg["your_shows_max"])]
        with self.db.get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def had_episodes(self, media_item_id: int) -> Set[Tuple[int, int]]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT season_number, episode_number FROM media_sources WHERE media_item_id = ? "
                "AND season_number IS NOT NULL AND episode_number IS NOT NULL", (media_item_id,)).fetchall()
        return {(int(r[0]), int(r[1])) for r in rows}

    def _catalogue_ids(self, tmdb_ids: List[int], kind: str = "tv") -> Dict[int, Tuple[int, bool]]:
        if not tmdb_ids:
            return {}
        marks = ",".join("?" * len(tmdb_ids))
        with self.db.get_connection() as conn:
            rows = conn.execute(
                f"SELECT mi.tmdb_id, mi.id, EXISTS(SELECT 1 FROM media_sources ms WHERE ms.media_item_id = mi.id) "
                f"FROM media_items mi WHERE mi.media_type = ? AND mi.tmdb_id IN ({marks})",
                ["tv" if kind == "tv" else "movie", *tmdb_ids]).fetchall()
        return {int(r[0]): (int(r[1]), bool(r[2])) for r in rows}

    # --- the lists ------------------------------------------------------------
    def providers(self) -> List[Dict[str, Any]]:
        """The service tabs (key and name), as configured."""
        out = []
        for p in self.cfg.get("providers") or []:
            if isinstance(p, dict) and p.get("key") and p.get("id"):
                out.append({"key": str(p["key"]), "name": str(p.get("name") or p["key"]), "id": int(p["id"])})
        return out

    def lists(self, profile_ids: List[int], fresh: bool = False, provider: Optional[str] = None) -> Dict[str, Any]:
        """The lists for these people, reused for `list_minutes` (a download clears it). With no provider:
        your shows, popular shows and popular movies. With a provider (a tab's key): what's popular on that
        service, shows and movies."""
        key = (tuple(profile_ids), provider or "")
        with self._lock:
            hit = self._built.get(key)
        if hit and not fresh and self.clock() - hit[0] < float(self.cfg["list_minutes"]) * 60:
            return hit[1]
        if provider:
            out = self._build_provider(profile_ids, provider)
        else:
            out = self._build(profile_ids)
        out["providers"] = [{"key": p["key"], "name": p["name"]} for p in self.providers()]
        out["region"] = str(self.cfg.get("watch_region") or "")
        with self._lock:
            self._built[key] = (self.clock(), out)
        return out

    def forget_lists(self) -> None:
        with self._lock:
            self._built.clear()

    # --- shared filters -------------------------------------------------------
    def _passes(self, d: Dict[str, Any]) -> bool:
        """Well rated, enough votes, not a skipped genre, and through the foreign-title bar."""
        cfg = self.cfg
        if float(d.get("vote_average") or 0) < float(cfg["min_rating"]) or int(d.get("vote_count") or 0) < int(cfg["min_votes"]):
            return False
        skip = {str(g).lower() for g in cfg.get("skip_genres") or []}
        if {str(g.get("name") or "").lower() for g in d.get("genres") or []} & skip:
            return False
        if self.foreign_gate and not self.foreign_gate({"original_language": d.get("original_language"),
                                                        "rating": d.get("vote_average"), "vote_count": d.get("vote_count")}):
            return False
        return True

    def _taste_filter(self, cards: List[Dict[str, Any]], profile_ids: List[int], kind: str, limit: int,
                      fit_why: Optional[str]) -> List[Dict[str, Any]]:
        """Drop what clearly doesn't fit your taste, order by taste and rank, keep `limit`."""
        if self.taste and cards:
            ids = [c["media_item_id"] for c in cards if c["media_item_id"]]
            try:
                scores = self.taste(ids, profile_ids) if ids else {}
            except Exception as e:  # noqa: BLE001 - taste is a bonus; the list works without it
                print(f"[NewEpisodes] taste scores failed ({e}); popularity order only.", flush=True)
                scores = {}
            floor = float(self.cfg["min_taste"])
            kept = []
            for c in cards:
                sc = scores.get(c["media_item_id"]) if c["media_item_id"] else None
                if sc is not None and sc < floor:
                    continue
                c["taste"] = None if sc is None else round(float(sc), 2)
                if fit_why and sc is not None and sc > 0.15:
                    c["why"] = fit_why
                kept.append(c)
            cards = kept
        cards = popular_order(cards, float(self.cfg["taste_weight"]))[:limit]
        for c in cards:
            c.pop("_rank", None)
        return cards

    def _seen_movies(self, profile_ids: List[int]) -> Set[int]:
        """TMDB ids of films one of these people saw, or that are on the server."""
        marks = ",".join("?" * len(profile_ids)) or "NULL"
        with self.db.get_connection() as conn:
            rows = conn.execute(
                f"SELECT mi.tmdb_id FROM media_items mi WHERE mi.media_type = 'movie' AND mi.tmdb_id IS NOT NULL AND ("
                f"EXISTS(SELECT 1 FROM media_sources ms WHERE ms.media_item_id = mi.id) OR "
                f"EXISTS(SELECT 1 FROM watch_history wh WHERE wh.media_item_id = mi.id AND wh.status = 'seen' "
                f"AND wh.profile_id IN ({marks})))", list(profile_ids)).fetchall()
        return {int(r[0]) for r in rows}

    def _movie_cards(self, tmdb_ids: List[int], profile_ids: List[int], why: str, min_days: int) -> List[Dict[str, Any]]:
        today = self.today()
        seen = self._seen_movies(profile_ids)
        ids = [t for t in tmdb_ids if t not in seen]
        details = self._many(ids, "movie")
        known = self._catalogue_ids(ids, "movie")
        cards = []
        for rank, tmdb_id in enumerate(ids):
            d = details.get(tmdb_id)
            if not d or not self._passes(d):
                continue
            out_day = parse_day(d.get("release_date"))
            if not out_day or (today - out_day).days < int(min_days):
                continue
            card = movie_card(d, known.get(tmdb_id, (None, False))[0], today)
            card.update(why=why, _rank=rank, taste=None)
            cards.append(card)
        return cards

    def _show_cards(self, tmdb_ids: List[int], profile_ids: List[int], why: str, recent_days: int,
                    skip: Set[int]) -> List[Dict[str, Any]]:
        today = self.today()
        ids = [t for t in tmdb_ids if t not in skip]
        details = self._many(ids)
        known = self._catalogue_ids(ids)
        cards = []
        for rank, tmdb_id in enumerate(ids):
            d = details.get(tmdb_id)
            if not d or not self._passes(d):
                continue
            item_id, in_lib = known.get(tmdb_id, (None, False))
            have = self.had_episodes(item_id) if item_id else set()
            offer = latest_offer(d, have, today, recent_days, "continue" if have else "beginning")
            if not offer:
                continue
            card = card_base(d, item_id, in_lib)
            card.update(offer=offer, why=why, _rank=rank, taste=None)
            cards.append(card)
        return cards

    # --- the lists ------------------------------------------------------------
    def _build(self, profile_ids: List[int]) -> Dict[str, Any]:
        cfg = self.cfg
        if not cfg.get("enabled", True):
            return {"enabled": False, "yours": [], "popular": [], "movies": [], "tmdb": True}
        today = self.today()
        mine = self.your_shows(profile_ids)
        trending = self.trending_ids()
        details = self._many([r["tmdb_id"] for r in mine] + trending[: int(cfg["popular_pages"]) * 20])
        tmdb_ok = any(v is not None for v in details.values()) or not details

        yours: List[Dict[str, Any]] = []
        mine_tmdb = set()
        for row in mine:
            mine_tmdb.add(int(row["tmdb_id"]))
            d = details.get(int(row["tmdb_id"]))
            if not d:
                continue
            have = self.had_episodes(row["id"])
            start = "continue" if have else ("latest" if row.get("seen") else "beginning")
            offer = latest_offer(d, have, today, int(cfg["recent_days"]), start)
            if offer:
                card = card_base(d, row["id"], bool(row["in_library"]))
                card.update(offer=offer, why="On your server" if row["in_library"] else
                            ("You watch this" if row.get("seen") else "On your Watch later list"))
                yours.append(card)
        yours.sort(key=lambda c: c["offer"]["latest"]["air_date"], reverse=True)

        popular = self._show_cards(trending, profile_ids, "Trending this week", int(cfg["recent_days"]), mine_tmdb)
        popular = self._taste_filter(popular, profile_ids, "tv", int(cfg["popular_max"]), "Trending, and your kind of show")
        movie_ids = self._cached_list("/trending/movie/week", {}, int(cfg["popular_pages"]))
        movies = self._movie_cards(movie_ids, profile_ids, "Trending this week", int(cfg["movie_min_days_out"]))
        movies = self._taste_filter(movies, profile_ids, "movie", int(cfg["movies_max"]), "Trending, and your kind of film")
        return {"enabled": True, "yours": yours, "popular": popular, "movies": movies, "tmdb": tmdb_ok}

    def _build_provider(self, profile_ids: List[int], key: str) -> Dict[str, Any]:
        """Popular shows and films on one streaming service in `watch_region` (TMDB discover)."""
        cfg = self.cfg
        prov = next((p for p in self.providers() if p["key"] == key), None)
        if prov is None:
            raise LookupError("There is no tab for that service.")
        if not cfg.get("enabled", True):
            return {"enabled": False, "provider": prov["name"], "shows": [], "movies": [], "tmdb": True}
        params = {"with_watch_providers": prov["id"], "watch_region": str(cfg.get("watch_region") or "US"),
                  "sort_by": "popularity.desc", "vote_average.gte": cfg["min_rating"], "vote_count.gte": cfg["min_votes"]}
        show_ids = self._cached_list("/discover/tv", params, int(cfg["popular_pages"]))
        movie_ids = self._cached_list("/discover/movie", params, int(cfg["popular_pages"]))
        why = f"Popular on {prov['name']}"
        # Any aired show counts here (not only ones with a new episode): it's what's on the service.
        shows = self._show_cards(show_ids, profile_ids, why, 365 * 100, set())
        shows = self._taste_filter(shows, profile_ids, "tv", int(cfg["provider_max"]), f"{why}, and your kind of show")
        movies = self._movie_cards(movie_ids, profile_ids, why, 0)
        movies = self._taste_filter(movies, profile_ids, "movie", int(cfg["provider_max"]), f"{why}, and your kind of film")
        return {"enabled": True, "provider": prov["name"], "shows": shows, "movies": movies,
                "tmdb": bool(show_ids or movie_ids)}

    # --- the season picker ----------------------------------------------------
    def _have_for(self, tmdb_id: int) -> Set[Tuple[int, int]]:
        found = self._catalogue_ids([int(tmdb_id)]).get(int(tmdb_id))
        return self.had_episodes(found[0]) if found else set()

    def seasons(self, tmdb_id: int) -> Dict[str, Any]:
        """The show's seasons (aired and had counts) so you can pick one yourself."""
        d = self.details(tmdb_id)
        if not d:
            raise LookupError("TMDB doesn't know that show (or TMDB can't be reached right now).")
        return {"tmdb_id": int(tmdb_id), "title": d.get("name") or "", "seasons": season_list(d, self._have_for(tmdb_id))}

    def season(self, tmdb_id: int, season: int) -> Dict[str, Any]:
        """One season's episodes; each aired one you don't have can be downloaded on its own."""
        key = (int(tmdb_id), int(season))
        with self._lock:
            hit = self._seasons.get(key)
        if hit and self._fresh(hit[0]):
            data = hit[1]
        else:
            data = self.tmdb_get(f"/tv/{int(tmdb_id)}/season/{int(season)}")
            if data is None and hit:
                data = hit[1]
            elif data is not None:
                with self._lock:
                    self._seasons[key] = (self.clock(), data)
        if not data:
            raise LookupError("TMDB doesn't have that season (or TMDB can't be reached right now).")
        episodes = episode_list(data, int(season), self._have_for(tmdb_id), self.today())
        aired = [e for e in episodes if e["aired"]]
        return {"tmdb_id": int(tmdb_id), "season": int(season), "name": data.get("name") or f"Season {season}",
                "episodes": episodes, "can_get_season": bool(aired) and len(aired) == len(episodes)
                and any(not e["have"] for e in aired)}

    # --- downloading ----------------------------------------------------------
    def _title(self, tmdb_id: int, kind: str = "tv") -> str:
        d = self.details(tmdb_id, kind)
        if not d:
            what = "show" if kind == "tv" else "film"
            raise LookupError(f"TMDB doesn't know that {what} (or TMDB can't be reached right now).")
        return (d.get("name") or d.get("original_name") or "") if kind == "tv" else (d.get("title") or d.get("original_title") or "")

    def copies(self, tmdb_id: int, season: Optional[int] = None, episode: Optional[int] = None,
               kind: str = "tv") -> List[Dict[str, Any]]:
        """Copies of this episode (or season) of this show, or of this film, best first. Each has a
        `copy_id`; the magnet link stays here."""
        if self.searcher is None:
            raise RuntimeError("Downloading isn't set up on this server.")
        title = self._title(tmdb_id, kind)
        if kind == "movie":
            d = self.details(tmdb_id, "movie") or {}
            day = parse_day(d.get("release_date"))
            year = day.year if day else None
            results = self.searcher.search(f"{title} {year or ''}".strip(), media_type="movie", sort="best") or []
            matched = [r for r in results if r.get("magnet") and movie_copy_matches(r.get("name") or "", title, year)]
        else:
            if not season:
                raise LookupError("Which season?")
            query = f"{title} {episode_code(season, episode)}"
            results = self.searcher.search(query, media_type="tv", sort="best") or []
            matched = [r for r in results if r.get("magnet") and copy_matches(r.get("name") or "", title, season, episode)]
        out = []
        now = self.clock()
        with self._lock:
            for key in [k for k, v in self._copies.items() if now - v["at"] > 3600]:
                del self._copies[key]
            for r in matched[: int(self.cfg["copies_max"])]:
                copy_id = uuid.uuid4().hex[:16]
                self._copies[copy_id] = {"at": now, "magnet": r["magnet"], "name": r.get("name") or "", "kind": kind,
                                         "tmdb_id": int(tmdb_id), "season": season, "episode": episode}
                out.append({"copy_id": copy_id, "name": r.get("name") or "", "quality": r.get("quality"),
                            "size": r.get("size_display") or "", "seeds": int(r.get("seeds") or 0),
                            "score": r.get("score")})
        return out

    def catalogue_item(self, tmdb_id: int, kind: str = "tv") -> int:
        """Our id for the show or film, adding it to the catalogue from TMDB when it isn't there yet."""
        from services.catalogue_search import tmdb_details_to_row
        found = self._catalogue_ids([int(tmdb_id)], kind).get(int(tmdb_id))
        if found:
            return found[0]
        d = self.details(tmdb_id, kind)
        if not d:
            raise LookupError("TMDB doesn't know that title (or TMDB can't be reached right now).")
        self.db.upsert_catalogue_items([tmdb_details_to_row(d, kind)])
        found = self._catalogue_ids([int(tmdb_id)], kind).get(int(tmdb_id))
        if not found:
            raise LookupError("Could not add the title to the catalogue.")
        return found[0]

    def pick(self, tmdb_id: int, season: Optional[int] = None, episode: Optional[int] = None,
             copy_id: Optional[str] = None, kind: str = "tv") -> Dict[str, Any]:
        """The copy to download: the chosen one, else the best match. Returns magnet, title, our id, kind."""
        if copy_id:
            with self._lock:
                chosen = self._copies.get(copy_id)
            if not chosen or chosen["tmdb_id"] != int(tmdb_id) or chosen.get("kind", "tv") != kind:
                raise LookupError("That copy is no longer in the list. Open the copies again.")
        else:
            found = self.copies(tmdb_id, season, episode, kind)
            if not found:
                if kind == "movie":
                    raise LookupError("No copy of this film found yet. Films often show up once they're out to stream or buy.")
                what = f"episode {episode_code(season, episode)}" if episode else f"season {season}"
                raise LookupError(f"No copy of {what} found yet. New episodes often show up a few hours after they air.")
            with self._lock:
                chosen = self._copies[found[0]["copy_id"]]
        title = self._title(tmdb_id, kind)
        item_id = self.catalogue_item(tmdb_id, kind)
        return {"magnet": chosen["magnet"], "release": chosen["name"], "title": title, "media_item_id": item_id, "kind": kind}
