"""
Today's movie picks from what you've watched lately (services/wishmatch_movies/daily.py).

Nothing is typed. For each list (each person alone, and both of you together) mars:

1. takes what was watched in the last `days` days: Jellyfin's played marks with their dates (per person)
   plus titles marked seen or rated in the app in that time (`watch_history.watched_at`,
   `rating_details.updated_at`), with each person's reaction (loved ... not for me) when there is one;
2. turns them into a wish with the Wishmatch core (`wishmatch.recent`): the moods those titles agree on,
   the feelings most of them share, and as "avoid" what the ones you disliked had;
3. lets Wishmatch rank films for it as usual: both tastes, the MovieLens crowd, IMDb / Rotten Tomatoes
   quality, well-known titles only, nothing already seen (and nothing played lately);
4. names, for each pick, the recent title it is most like ("because you watched X").

The lists are made once a day (the first time anyone asks) and kept in `daily_picks.json` next to the
database, so a restart doesn't change them. No model is asked: it takes about a second and costs nothing.
Settings: `wishmatch.daily_picks` in recommender_rules.yaml.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from wishmatch import Request, closest, wish_from_recent

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "days": 30,             # what was watched in this many days counts
    "per_list": 12,         # picks per list
    "kinds": ["movie"],     # films only (Labiba asked for films)
    "file": "daily_picks.json",
}

CARD_FIELDS = ("id", "title", "release_year", "media_type", "poster_url", "overview", "genres", "runtime", "rating",
               "imdb_rating", "rt_score", "vibe", "taste_reasons", "wishmatch_score")


def parse_when(text: Any) -> Optional[datetime]:
    """Jellyfin ("2026-11-04T21:10:00.0000000Z") and SQLite ("2026-11-04 21:10:00") times, as naive UTC."""
    s = str(text or "").strip().replace("T", " ").rstrip("Z")
    if not s:
        return None
    s = s.split(".")[0][:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


class DailyPicks:
    """`wm` is WishmatchMovies. `played(profile_id)` returns Jellyfin's played titles for that person
    ([{media_item_id, played_at}], newest first) or None when Jellyfin can't say."""

    def __init__(self, wm: Any, played: Callable[[int], Optional[List[Dict[str, Any]]]],
                 today: Optional[Callable[[], date]] = None, now: Optional[Callable[[], datetime]] = None):
        self.wm = wm
        self.db = wm.db
        self.played = played
        self.today = today or date.today
        self.now = now or datetime.utcnow
        self._lock = threading.Lock()
        self._saved: Optional[Dict[str, Any]] = None

    @property
    def cfg(self) -> Dict[str, Any]:
        return {**DEFAULTS, **((self.wm.adapter.rules() or {}).get("daily_picks") or {})}

    # --- what was watched lately ------------------------------------------------
    def recent(self, profile_id: int) -> Tuple[Dict[int, float], bool]:
        """({title id: days ago}, jellyfin answered) for one person, most recent watch per title."""
        days = float(self.cfg["days"])
        now = self.now()
        out: Dict[int, float] = {}

        def add(item_id: Any, when: Any) -> None:
            at = parse_when(when)
            if at is None or item_id is None:
                return
            ago = max(0.0, (now - at).total_seconds() / 86400)
            if ago <= days and ago < out.get(int(item_id), 1e9):
                out[int(item_id)] = ago

        played = self.played(profile_id)
        for p in played or []:
            add(p.get("media_item_id"), p.get("played_at"))
        with self.db.get_connection() as conn:
            for r in conn.execute(
                "SELECT media_item_id, watched_at FROM watch_history WHERE profile_id = ? AND status = 'seen'",
                (profile_id,),
            ).fetchall():
                add(r[0], r[1])
            for r in conn.execute(
                "SELECT media_item_id, updated_at FROM rating_details WHERE profile_id = ?", (profile_id,),
            ).fetchall():
                add(r[0], r[1])
        return out, played is not None

    # --- one list ----------------------------------------------------------------
    def build(self, people: List[int], together: bool) -> Dict[str, Any]:
        """Picks for these people (the viewer first). Each card carries `because` (a recent title)."""
        cfg = self.cfg
        catalogue = self.wm.adapter.catalogue()
        settings = self.wm.engine.settings()
        watchers = people if together else people[:1]
        recent: Dict[int, float] = {}
        for pid in watchers:
            mine, _ = self.recent(pid)
            for iid, ago in mine.items():
                recent[iid] = min(ago, recent.get(iid, 1e9))
        reactions: Dict[int, List[float]] = {}
        for pid in watchers:
            for r in self.wm.adapter.reactions(pid):
                reactions.setdefault(int(r.item_id), []).append(float(r.score))
        rows: List[Tuple[Any, float, Optional[float]]] = []
        for iid, ago in recent.items():
            item = catalogue.get(iid)
            if item is None:
                continue
            scores = reactions.get(iid)
            # Together: the less keen of you decides how much a title counts.
            rows.append((item, ago, min(scores) if scores else None))
        base = {"people": people, "together": together, "recent_count": len(rows)}
        wish = wish_from_recent(rows, settings)
        if wish.is_empty():
            return {**base, "picks": [], "note": "Watch something first: picks come from what you watched lately."}
        request = Request(people=people, together=together, kinds=list(cfg["kinds"]) or None,
                          filters={"exclude_seen": True, "exclude_ids": list(recent)}, limit=int(cfg["per_list"]))
        picks = self.wm.engine.recommend(request, wish=wish)["picks"][: int(cfg["per_list"])]
        recent_items = [it for it, _, r in rows if r is None or r >= float(settings["recent"]["dislike_below"])]
        cards = []
        full = self.wm.rows(picks)
        titles = self.db.get_items_by_ids([it.id for it in recent_items]) if recent_items else {}
        for pick, row in zip(picks, full):
            card = {k: row.get(k) for k in CARD_FIELDS if k in row}
            near = closest(pick.item, recent_items)
            name = (titles.get(near) or {}).get("title") if near is not None else None
            card["because"] = {"id": near, "title": name} if name else None
            card["why"] = f"Because you watched {name}" if name else "From what you watched lately"
            cards.append(card)
        return {**base, "picks": cards, "wish": wish.as_dict()}

    # --- the day's lists -----------------------------------------------------------
    def _path(self) -> Optional[str]:
        db_path = str(getattr(self.db, "db_path", "") or "")
        name = str(self.cfg["file"])
        if os.path.isabs(name):
            return name
        if not db_path or db_path == ":memory:":
            return None
        return os.path.join(os.path.dirname(os.path.abspath(db_path)), name)

    def _load(self) -> Dict[str, Any]:
        if self._saved is None:
            self._saved = {}
            path = self._path()
            if path and os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        self._saved = json.load(f)
                except (OSError, ValueError):
                    self._saved = {}
        return self._saved

    def _save(self) -> None:
        path = self._path()
        if not path:
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._saved, f)
        os.replace(tmp, path)

    @staticmethod
    def key(people: List[int], together: bool) -> str:
        return "together:" + ",".join(str(p) for p in sorted(people)) if together else f"solo:{people[0]}"

    def today_list(self, people: List[int], together: bool, fresh: bool = False) -> Dict[str, Any]:
        """Today's list for these people: made the first time it is asked for each day."""
        if not self.cfg["enabled"]:
            return {"enabled": False, "picks": []}
        day = self.today().isoformat()
        k = self.key(people, together)
        with self._lock:
            saved = self._load()
            if saved.get("day") != day:
                saved.clear()
                saved.update(day=day, lists={})
            hit = saved["lists"].get(k)
            if hit is not None and not fresh:
                return {"enabled": True, "day": day, **hit}
        made = self.build(people, together)
        with self._lock:
            saved = self._load()
            if saved.get("day") == day:
                saved["lists"][k] = made
                try:
                    self._save()
                except OSError as e:
                    print(f"[DailyPicks] Could not keep today's picks ({e}); they're made again next time.", flush=True)
        return {"enabled": True, "day": day, **made}
