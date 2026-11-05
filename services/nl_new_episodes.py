"""
New episodes to download (services/nl_new_episodes.py), for the Northern Lights "New & popular" page.

Two lists, both built on the server (mars) so the TV pages only draw them:

- **Your shows**: shows you have files of, marked as seen, or saved for later, whose latest episode on TMDB
  came out in the last `recent_days` and that you don't have yet. An episode counts as "had" when the
  library ever had a file of it, even if the watched cleanup deleted it since.
- **Popular right now**: shows trending on TMDB this week that are still airing, well rated, not in a skipped
  genre (news, talk, reality...), and that fit your taste (Wishmatch's taste score, when the show is in the
  tagged catalogue).

Each card offers one download: the whole season once it has finished airing, else the first episode you are
missing. The Download button asks the server to find copies ("Best match" ranking, same as the Download
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


def latest_offer(details: Dict[str, Any], have: Set[Tuple[int, int]], today: date, recent_days: int) -> Optional[Dict[str, Any]]:
    """What to offer for a show: None when nothing came out recently or you have it all.

    `have` is the (season, episode) pairs the library has (or had). The latest season is the one TMDB's
    last aired episode belongs to. A season that finished airing is offered whole when two or more of its
    episodes are missing; otherwise the first missing episode is offered."""
    last = details.get("last_episode_to_air") or {}
    aired = parse_day(last.get("air_date"))
    season = int(last.get("season_number") or 0)
    upto = int(last.get("episode_number") or 0)
    if not aired or season < 1 or upto < 1 or (today - aired).days > int(recent_days) or aired > today:
        return None
    missing = [e for e in range(1, upto + 1) if (season, e) not in have]
    if not missing:
        return None
    nxt = details.get("next_episode_to_air") or {}
    next_day = parse_day(nxt.get("air_date"))
    count = season_episode_count(details, season)
    finished = (not nxt or int(nxt.get("season_number") or 0) != season) and (count is None or upto >= count)
    whole = finished and len(missing) >= 2
    first = missing[0]
    out: Dict[str, Any] = {
        "season": season,
        "episode": None if whole else first,
        "kind": "season" if whole else "episode",
        "label": f"Get season {season}" if whole else f"Get {episode_code(season, first)}",
        "missing": missing,
        "latest": {"season": season, "episode": upto, "name": last.get("name") or "", "air_date": str(aired)},
        "season_finished": finished,
        "next": ({"season": int(nxt.get("season_number") or 0), "episode": int(nxt.get("episode_number") or 0),
                  "air_date": str(next_day)} if next_day else None),
    }
    if finished:
        line = f"Season {season} finished {nice_day(aired, today)}"
    else:
        line = f"{episode_code(season, upto)} out {nice_day(aired, today)}"
        if next_day:
            line += f" · next {nice_day(next_day, today)}"
    if len(missing) > 1 and not whole:
        line += f" · {len(missing)} new for you"
    out["status_line"] = line
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
        self._lock = threading.Lock()

    # --- TMDB, cached ---------------------------------------------------------
    def _fresh(self, at: float) -> bool:
        return self.clock() - at < float(self.cfg["cache_hours"]) * 3600

    def details(self, tmdb_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            hit = self._details.get(int(tmdb_id))
        if hit and self._fresh(hit[0]):
            return hit[1]
        data = self.tmdb_get(f"/tv/{int(tmdb_id)}")
        if data is not None or not hit:
            with self._lock:
                self._details[int(tmdb_id)] = (self.clock(), data)
            return data
        return hit[1]   # TMDB down: the last answer is better than nothing

    def _many(self, ids: Iterable[int]) -> Dict[int, Optional[Dict[str, Any]]]:
        ids = list(dict.fromkeys(int(i) for i in ids))
        if not ids:
            return {}
        with ThreadPoolExecutor(max_workers=max(1, int(self.cfg["workers"]))) as pool:
            return dict(zip(ids, pool.map(self.details, ids)))

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
        params = [*profile_ids] * 5 + [floor, int(self.cfg["your_shows_max"])]
        with self.db.get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def had_episodes(self, media_item_id: int) -> Set[Tuple[int, int]]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT season_number, episode_number FROM media_sources WHERE media_item_id = ? "
                "AND season_number IS NOT NULL AND episode_number IS NOT NULL", (media_item_id,)).fetchall()
        return {(int(r[0]), int(r[1])) for r in rows}

    def _catalogue_ids(self, tmdb_ids: List[int]) -> Dict[int, Tuple[int, bool]]:
        if not tmdb_ids:
            return {}
        marks = ",".join("?" * len(tmdb_ids))
        with self.db.get_connection() as conn:
            rows = conn.execute(
                f"SELECT mi.tmdb_id, mi.id, EXISTS(SELECT 1 FROM media_sources ms WHERE ms.media_item_id = mi.id) "
                f"FROM media_items mi WHERE mi.media_type = 'tv' AND mi.tmdb_id IN ({marks})", tmdb_ids).fetchall()
        return {int(r[0]): (int(r[1]), bool(r[2])) for r in rows}

    # --- the lists ------------------------------------------------------------
    def lists(self, profile_ids: List[int], fresh: bool = False) -> Dict[str, Any]:
        """Both lists for these people, reused for `list_minutes` (a download clears it)."""
        key = tuple(profile_ids)
        with self._lock:
            hit = self._built.get(key)
        if hit and not fresh and self.clock() - hit[0] < float(self.cfg["list_minutes"]) * 60:
            return hit[1]
        out = self._build(profile_ids)
        with self._lock:
            self._built[key] = (self.clock(), out)
        return out

    def forget_lists(self) -> None:
        with self._lock:
            self._built.clear()

    def _build(self, profile_ids: List[int]) -> Dict[str, Any]:
        cfg = self.cfg
        if not cfg.get("enabled", True):
            return {"enabled": False, "yours": [], "popular": [], "tmdb": True}
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
            offer = latest_offer(d, self.had_episodes(row["id"]), today, int(cfg["recent_days"]))
            if offer:
                card = card_base(d, row["id"], bool(row["in_library"]))
                card.update(offer=offer, why="You watch this" if not row["in_library"] else "On your server")
                yours.append(card)
        yours.sort(key=lambda c: c["offer"]["latest"]["air_date"], reverse=True)

        skip = {str(g).lower() for g in cfg.get("skip_genres") or []}
        known = self._catalogue_ids([t for t in trending if t not in mine_tmdb])
        popular: List[Dict[str, Any]] = []
        for rank, tmdb_id in enumerate(t for t in trending if t not in mine_tmdb):
            d = details.get(tmdb_id)
            if not d:
                continue
            if float(d.get("vote_average") or 0) < float(cfg["min_rating"]) or int(d.get("vote_count") or 0) < int(cfg["min_votes"]):
                continue
            genres = {str(g.get("name") or "").lower() for g in d.get("genres") or []}
            if genres & skip:
                continue
            if self.foreign_gate and not self.foreign_gate({"original_language": d.get("original_language"),
                                                            "rating": d.get("vote_average"), "vote_count": d.get("vote_count")}):
                continue
            offer = latest_offer(d, set(), today, int(cfg["recent_days"]))
            if not offer:
                continue
            item_id, in_lib = known.get(tmdb_id, (None, False))
            card = card_base(d, item_id, in_lib)
            card.update(offer=offer, why="Trending this week", _rank=rank, taste=None)
            popular.append(card)
        if self.taste and popular:
            ids = [c["media_item_id"] for c in popular if c["media_item_id"]]
            try:
                scores = self.taste(ids, profile_ids) if ids else {}
            except Exception as e:  # noqa: BLE001 - taste is a bonus; the list works without it
                print(f"[NewEpisodes] taste scores failed ({e}); trending order only.", flush=True)
                scores = {}
            floor = float(cfg["min_taste"])
            kept = []
            for c in popular:
                s = scores.get(c["media_item_id"]) if c["media_item_id"] else None
                if s is not None and s < floor:
                    continue
                c["taste"] = None if s is None else round(float(s), 2)
                if s is not None and s > 0.15:
                    c["why"] = "Trending, and your kind of show"
                kept.append(c)
            popular = kept
        popular = popular_order(popular, float(cfg["taste_weight"]))[: int(cfg["popular_max"])]
        for c in popular:
            c.pop("_rank", None)
        return {"enabled": True, "yours": yours, "popular": popular, "tmdb": tmdb_ok}

    # --- downloading ----------------------------------------------------------
    def _title(self, tmdb_id: int) -> str:
        d = self.details(tmdb_id)
        if not d:
            raise LookupError("TMDB doesn't know that show (or TMDB can't be reached right now).")
        return d.get("name") or d.get("original_name") or ""

    def copies(self, tmdb_id: int, season: int, episode: Optional[int] = None) -> List[Dict[str, Any]]:
        """Copies of this episode (or season) of this show, best first. Each has a `copy_id`; the magnet
        link stays here."""
        if self.searcher is None:
            raise RuntimeError("Downloading isn't set up on this server.")
        title = self._title(tmdb_id)
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
                self._copies[copy_id] = {"at": now, "magnet": r["magnet"], "name": r.get("name") or "",
                                         "tmdb_id": int(tmdb_id), "season": season, "episode": episode}
                out.append({"copy_id": copy_id, "name": r.get("name") or "", "quality": r.get("quality"),
                            "size": r.get("size_display") or "", "seeds": int(r.get("seeds") or 0),
                            "score": r.get("score")})
        return out

    def catalogue_item(self, tmdb_id: int) -> int:
        """Our id for the show, adding it to the catalogue from TMDB when it isn't there yet."""
        from services.catalogue_search import tmdb_details_to_row
        found = self._catalogue_ids([int(tmdb_id)]).get(int(tmdb_id))
        if found:
            return found[0]
        d = self.details(tmdb_id)
        if not d:
            raise LookupError("TMDB doesn't know that show (or TMDB can't be reached right now).")
        self.db.upsert_catalogue_items([tmdb_details_to_row(d, "tv")])
        found = self._catalogue_ids([int(tmdb_id)]).get(int(tmdb_id))
        if not found:
            raise LookupError("Could not add the show to the catalogue.")
        return found[0]

    def pick(self, tmdb_id: int, season: int, episode: Optional[int] = None,
             copy_id: Optional[str] = None) -> Dict[str, Any]:
        """The copy to download: the chosen one, else the best match. Returns magnet, show title, our id."""
        chosen = None
        if copy_id:
            with self._lock:
                chosen = self._copies.get(copy_id)
            if not chosen or chosen["tmdb_id"] != int(tmdb_id):
                raise LookupError("That copy is no longer in the list. Open the copies again.")
            title = self._title(tmdb_id)
        else:
            found = self.copies(tmdb_id, season, episode)
            if not found:
                what = f"episode {episode_code(season, episode)}" if episode else f"season {season}"
                raise LookupError(f"No copy of {what} found yet. New episodes often show up a few hours after they air.")
            with self._lock:
                chosen = self._copies[found[0]["copy_id"]]
            title = self._title(tmdb_id)
        item_id = self.catalogue_item(tmdb_id)
        return {"magnet": chosen["magnet"], "release": chosen["name"], "title": title, "media_item_id": item_id}
