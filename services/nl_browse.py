"""
Northern Lights browse (services/nl_browse.py), M2

Everything the /nl/ browse pages need, built from two sources:
- our database says what is in the library (media_items, media_sources);
- Jellyfin says what was watched: Resume, NextUp and a show's Episodes (UserData).

Jellyfin items are mapped back to ours by file Path -> media_sources.file_path. The Jellyfin
id of each file is cached in media_sources.jellyfin_id. If Jellyfin is down every page still
builds from our database, just without progress, ticks and Continue Watching.

The pure functions at the top (parse_*, progress_of, ...) take plain dicts so they can be
tested without a server. Nothing here returns an API key; artwork URLs point at our proxy.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from services.jellyfin_playback import (
    PROXY_PREFIX,
    JellyfinPlayback,
    JellyfinUnavailable,
    valid_jellyfin_id,
)

_TICKS = 10_000_000
NEW_DAYS = 14
NEW_MAX = 6          # at most this many "New" badges per list (a first scan must not mark everything)
HOME_ROW = 18
RECENT_ROW = 18        # Recently Added / Recently Watched on the home page
PLAYED_FETCH = 2000    # how many played items to read from Jellyfin (newest first) per person
DOWNLOAD_HREF = "/#direct"   # the existing app's Download tab (static/app.js switchTab('direct'))


# ---------------------------------------------------------------- pure helpers


def image_url(jf_id: Optional[str], kind: str = "Primary", width: int = 400) -> Optional[str]:
    """Our proxy URL for a Jellyfin image, or None when the id is not usable."""
    if not jf_id or not valid_jellyfin_id(str(jf_id)) or kind not in ("Primary", "Backdrop", "Thumb"):
        return None
    return f"{PROXY_PREFIX}Items/{jf_id}/Images/{kind}?maxWidth={int(width)}&quality=80"


def progress_of(item: Dict[str, Any]) -> Dict[str, Any]:
    """Watched state from a Jellyfin item's UserData: {played, progress (0-100), position_seconds}."""
    data = item.get("UserData") or {}
    ticks = int(data.get("PlaybackPositionTicks") or 0)
    run = int(item.get("RunTimeTicks") or 0)
    if run > 0:
        pct = int(round(100.0 * ticks / run))
    else:
        pct = int(round(float(data.get("PlayedPercentage") or 0)))
    played = bool(data.get("Played"))
    return {
        "played": played,
        "progress": 0 if played else max(0, min(100, pct)),
        "position_seconds": 0.0 if played else ticks / _TICKS,
    }


def episode_code(season: Optional[int], episode: Optional[int]) -> str:
    bits = []
    if season is not None:
        bits.append(f"S{season}")
    if episode is not None:
        bits.append(f"E{episode}")
    return " · ".join(bits)


def runtime_minutes(item: Dict[str, Any]) -> Optional[int]:
    ticks = int(item.get("RunTimeTicks") or 0)
    return int(round(ticks / _TICKS / 60)) if ticks else None


def play_href(media_item_id: int, source_id: Optional[int] = None, resume: Optional[bool] = None) -> str:
    href = f"/nl/play/{media_item_id}"
    qs = []
    if source_id is not None:
        qs.append(f"source={source_id}")
    if resume is not None:
        qs.append("resume=1" if resume else "resume=0")
    return href + ("?" + "&".join(qs) if qs else "")


def parse_added(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def mark_new(items: List[Dict[str, Any]], now: Optional[datetime] = None) -> None:
    """Sets item["new"] on the most recently added titles of the last NEW_DAYS days (at most NEW_MAX)."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=NEW_DAYS)
    dated = [(parse_added(i.get("added_at")), i) for i in items]
    recent = sorted(((d, i) for d, i in dated if d and d >= cutoff), key=lambda p: p[0], reverse=True)
    for _, item in recent[:NEW_MAX]:
        item["new"] = True


def parse_resume(data: Dict[str, Any], by_path: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Jellyfin's Items/Resume reply -> Continue Watching cards for files we have.

    One card per title (a show shows once, with its most recently played episode). Items whose
    Path we do not know are dropped. Each card has `source_id`, `jellyfin_id` for caching."""
    cards: List[Dict[str, Any]] = []
    seen = set()
    for it in (data or {}).get("Items") or []:
        src = by_path.get(it.get("Path") or "")
        if not src or src["media_item_id"] in seen:
            continue
        st = progress_of(it)
        if st["played"] or (st["progress"] <= 0 and st["position_seconds"] <= 0):
            continue
        seen.add(src["media_item_id"])
        episode = it.get("Type") == "Episode" or src.get("media_type") == "tv"
        jf_id = it.get("Id")
        if episode:
            sub = episode_code(src.get("season_number"), src.get("episode_number"))
            if it.get("Name"):
                sub = (sub + " · " if sub else "") + it["Name"]
            image = image_url(jf_id, "Primary", 480) if (it.get("ImageTags") or {}).get("Primary") else None
        else:
            sub = str(src["year"]) if src.get("year") else ""
            image = image_url(jf_id, "Backdrop", 480) if it.get("BackdropImageTags") else None
        cards.append({
            "media_item_id": src["media_item_id"],
            "source_id": src["source_id"],
            "kind": "episode" if episode else "movie",
            "title": src["title"],
            "subtitle": sub,
            "poster_url": src.get("poster_url"),
            "image": image,
            "progress": st["progress"],
            "resume_seconds": st["position_seconds"],
            "jellyfin_id": jf_id,
            "href": play_href(src["media_item_id"], src["source_id"] if episode else None, resume=True),
            "title_href": f"/nl/{'show' if episode else 'title'}/{src['media_item_id']}",
        })
    return cards


def parse_episodes(data: Dict[str, Any], by_path: Dict[str, Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Jellyfin's Shows/{id}/Episodes reply -> {our source id: state} for episodes we have."""
    out: Dict[int, Dict[str, Any]] = {}
    for it in (data or {}).get("Items") or []:
        src = by_path.get(it.get("Path") or "")
        if not src:
            continue
        st = progress_of(it)
        jf_id = it.get("Id")
        out[src["source_id"]] = {
            **st,
            "jellyfin_id": jf_id,
            "name": it.get("Name") or "",
            "overview": it.get("Overview") or "",
            "runtime_minutes": runtime_minutes(it),
            "image": image_url(jf_id, "Primary", 480) if (it.get("ImageTags") or {}).get("Primary") else None,
        }
    return out


def parse_next_up(data: Dict[str, Any], by_path: Dict[str, Dict[str, Any]]) -> Optional[int]:
    """Jellyfin's Shows/NextUp reply -> our source id of the first episode we have, or None."""
    for it in (data or {}).get("Items") or []:
        src = by_path.get(it.get("Path") or "")
        if src:
            return src["source_id"]
    return None


def parse_played(data: Dict[str, Any], by_path: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Jellyfin's played items (newest first) -> [{media_item_id, played_at}], one entry per title of ours
    (a show appears once, at its most recently played episode). Items whose Path we do not know are dropped."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for it in (data or {}).get("Items") or []:
        src = by_path.get(it.get("Path") or "")
        if not src or src["media_item_id"] in seen:
            continue
        if not (it.get("UserData") or {}).get("Played"):
            continue
        seen.add(src["media_item_id"])
        out.append({"media_item_id": src["media_item_id"], "played_at": str((it.get("UserData") or {}).get("LastPlayedDate") or "")})
    return out


def merge_played(lists: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Several people's played lists -> one: a title any of them played, at the latest time, newest first."""
    latest: Dict[int, str] = {}
    for lst in lists:
        for e in lst:
            if e["media_item_id"] not in latest or e["played_at"] > latest[e["media_item_id"]]:
                latest[e["media_item_id"]] = e["played_at"]
    return [{"media_item_id": i, "played_at": t} for i, t in sorted(latest.items(), key=lambda kv: kv[1], reverse=True)]


def season_label(number: int) -> str:
    return "Specials" if number == 0 else f"Season {number}"


# --------------------------------------------------------------------- service


class NLBrowse:
    def __init__(self, db: Any, playback: JellyfinPlayback, catalogue_search: Any = None):
        self.db = db
        self.pb = playback
        self.catalogue = catalogue_search
        self._series: Dict[int, str] = {}   # our show id -> Jellyfin series id
        self._lock = threading.Lock()

    # ------------------------------------------------------------ jellyfin calls

    def _uid(self) -> str:
        return self.pb._user_id()

    def _ids_for(self, sources: List[Dict[str, Any]]) -> Dict[int, str]:
        """{source id: Jellyfin id}: the cached column first, then one library scan (throttled)."""
        found = {s["id"]: s["jellyfin_id"] for s in sources if s.get("jellyfin_id")}
        missing = [s for s in sources if not s.get("jellyfin_id")]
        if missing:
            try:
                known = self.pb.known_ids(s["file_path"] for s in missing)
            except JellyfinUnavailable:
                known = {}
            fresh = {s["id"]: known[s["file_path"]] for s in missing if s["file_path"] in known}
            if fresh:
                self.db.set_jellyfin_ids(fresh)
                found.update(fresh)
        return found

    def _by_path(self, paths: List[str]) -> Dict[str, Dict[str, Any]]:
        return self.db.get_sources_by_paths(paths)

    def _remember(self, by_path: Dict[str, Dict[str, Any]], items: List[Dict[str, Any]]) -> None:
        """Keeps media_sources.jellyfin_id in step with what Jellyfin just reported."""
        changed = {}
        for it in items:
            src = by_path.get(it.get("Path") or "")
            if src and it.get("Id") and src.get("jellyfin_id") != it["Id"]:
                changed[src["source_id"]] = it["Id"]
        if changed:
            self.db.set_jellyfin_ids(changed)

    def resume_cards(self, users: Optional[List[Optional[str]]] = None) -> List[Dict[str, Any]]:
        """Continue Watching. [] when Jellyfin is not available. With several `users` ("Both of us",
        M4) the lists are merged: a title either of them is part way through shows once, at the
        further position. With none given, the current Jellyfin user (see JellyfinPlayback.as_user)."""
        if not users or len(users) < 2:
            return self._resume_cards_one()
        merged: Dict[int, Dict[str, Any]] = {}
        for u in users:
            with self.pb.as_user(u):
                cards = self._resume_cards_one()
            for c in cards:
                old = merged.get(c["media_item_id"])
                if old is None or c["resume_seconds"] > old["resume_seconds"]:
                    merged[c["media_item_id"]] = c
        return list(merged.values())

    def _resume_cards_one(self) -> List[Dict[str, Any]]:
        try:
            data = self.pb._request(
                "GET", f"/Users/{self._uid()}/Items/Resume",
                params={"MediaTypes": "Video", "Fields": "Path", "Limit": 24},
            )
        except JellyfinUnavailable:
            return []
        # Jellyfin keeps files it has not rescanned yet; a moved or deleted file is not resumable.
        items = [i for i in (data or {}).get("Items") or [] if i.get("Path") and self.pb.file_exists(i["Path"])]
        by_path = self._by_path([i.get("Path") for i in items])
        self._remember(by_path, items)
        return parse_resume({"Items": items}, by_path)

    def series_id(self, item: Dict[str, Any]) -> Optional[str]:
        """Jellyfin's series id for one of our shows (from any episode's SeriesId; remembered)."""
        with self._lock:
            if item["id"] in self._series:
                return self._series[item["id"]]
        sources = item["sources"]
        ids = self._ids_for(sources[:5])
        for s in sources[:5]:
            jf_id = ids.get(s["id"])
            if not jf_id:
                continue
            data = self.pb._request("GET", f"/Users/{self._uid()}/Items/{jf_id}") or {}
            if data.get("SeriesId"):
                with self._lock:
                    self._series[item["id"]] = data["SeriesId"]
                return data["SeriesId"]
        return None

    def episode_states(self, item: Dict[str, Any], series_id: str) -> Dict[int, Dict[str, Any]]:
        data = self.pb._request(
            "GET", f"/Shows/{series_id}/Episodes", params={"UserId": self._uid(), "Fields": "Path,Overview"},
        )
        items = (data or {}).get("Items") or []
        by_path = {s["file_path"]: {"source_id": s["id"], "jellyfin_id": s.get("jellyfin_id")} for s in item["sources"]}
        self._remember(by_path, items)
        return parse_episodes(data, by_path)

    def next_up_source(self, item: Dict[str, Any], series_id: str) -> Optional[int]:
        data = self.pb._request(
            "GET", "/Shows/NextUp",
            params={"SeriesId": series_id, "UserId": self._uid(), "Fields": "Path", "Limit": 1},
        )
        by_path = {s["file_path"]: {"source_id": s["id"]} for s in item["sources"]}
        return parse_next_up(data, by_path)

    # ------------------------------------------------------------------ lists

    def _movie_fallback(self, row: Dict[str, Any]) -> Optional[str]:
        return image_url(row.get("jellyfin_id"), "Primary", 400) if row.get("media_type") == "movie" else None

    def _on_disk(self, media_type: str) -> List[Dict[str, Any]]:
        """Library rows with at least one source file still on disk. Files that were moved or
        deleted keep their database rows (history, ratings) but should not show up as playable."""
        exists = self.pb.file_exists
        return [r for r in self.db.get_library_items(media_type) if any(exists(p) for p in r.get("file_paths") or [])]

    def library(
        self, media_type: str, limit: Optional[int] = None, with_progress: bool = True,
        users: Optional[List[Optional[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """Cards for movies or shows. Movies carry a progress bar when partly watched, else
        "New" for the latest additions. One badge at most."""
        rows = self._on_disk(media_type)
        mark_new(rows)
        progress: Dict[int, int] = {}
        if with_progress and media_type == "movie":
            progress = {c["media_item_id"]: c["progress"] for c in self.resume_cards(users) if c["kind"] == "movie"}
        if limit:
            rows = sorted(rows, key=lambda r: r.get("added_at") or "", reverse=True)[:limit]
        return [self._card(r, progress.get(r["id"])) for r in rows]

    def _card(self, row: Dict[str, Any], progress: Optional[int]) -> Dict[str, Any]:
        is_show = row.get("media_type") == "tv"
        badge = None
        if progress:
            badge = "Resume"
        elif row.get("new"):
            badge = "New"
        return {
            "id": row["id"],
            "media_type": row.get("media_type"),
            "title": row["title"],
            "year": row.get("year"),
            "runtime": None if is_show else row.get("runtime"),
            "seasons": row.get("seasons") if is_show else None,
            "poster_url": row.get("poster_url"),
            "image_fallback": self._movie_fallback(row),
            "badge": badge,
            "progress": progress or None,
            "together": self.db.get_together_counts().get(row["id"], 0) > 0,   # M9a: seen together
            "href": f"/nl/{'show' if is_show else 'title'}/{row['id']}",
        }

    # ------------------------------------------------------- watched state (M7b)

    def _played_one(self) -> List[Dict[str, Any]]:
        data = self.pb._request(
            "GET", f"/Users/{self._uid()}/Items",
            params={"Filters": "IsPlayed", "IncludeItemTypes": "Movie,Episode", "Recursive": "true", "Fields": "Path",
                    "SortBy": "DatePlayed", "SortOrder": "Descending", "Limit": PLAYED_FETCH, "EnableUserData": "true"},
        )
        items = [i for i in (data or {}).get("Items") or [] if i.get("Path")]
        return parse_played({"Items": items}, self._by_path([i["Path"] for i in items]))

    def played_titles(self, users: Optional[List[Optional[str]]] = None) -> Optional[List[Dict[str, Any]]]:
        """Titles of ours that were watched, newest first. For several people (Both of us) a title
        counts when either watched it. None when Jellyfin cannot tell (down, refused): callers then show
        everything rather than guess."""
        lists: List[List[Dict[str, Any]]] = []
        try:
            for u in (users or [None]):
                with self.pb.as_user(u):
                    lists.append(self._played_one())
        except JellyfinUnavailable:
            return None
        return lists[0] if len(lists) == 1 else merge_played(lists)

    def library_page(
        self, media_type: str, users: Optional[List[Optional[str]]] = None, unwatched: bool = False
    ) -> Dict[str, Any]:
        """The Movies / Shows page: every card, or only titles nobody at the screen has watched
        (`unwatched`). `unwatched_applied` is False when the filter was asked for but Jellyfin could not
        say what was watched, so the page can say so instead of showing a wrong list."""
        cards = self.library(media_type, None, True, users)
        applied = False
        if unwatched:
            played = self.played_titles(users)
            if played is not None:
                seen = {e["media_item_id"] for e in played}
                cards = [c for c in cards if c["id"] not in seen]
                applied = True
        return {"items": cards, "unwatched": bool(unwatched), "unwatched_applied": applied}

    def home(self, users: Optional[List[Optional[str]]] = None, hidden: Optional[Any] = None) -> Dict[str, Any]:
        hidden = set(hidden or ())
        cont = self.resume_cards(users)
        progress = {c["media_item_id"]: c["progress"] for c in cont if c["kind"] == "movie"}
        out: Dict[str, Any] = {"continue_watching": cont}
        pool: Dict[int, Dict[str, Any]] = {}
        for key, mtype in (("movies", "movie"), ("shows", "tv")):
            rows = self._on_disk(mtype)
            mark_new(rows)
            total = len(rows)
            rows = sorted(rows, key=lambda r: r.get("added_at") or "", reverse=True)
            for r in rows:
                pool[r["id"]] = r
            shown = [r for r in rows if r["id"] not in hidden][:HOME_ROW]
            out[key] = [self._card(r, progress.get(r["id"])) for r in shown]
            out[key + "_total"] = total
        # Recently Added: newest files first, movies and shows together.
        newest = sorted((r for r in pool.values() if r["id"] not in hidden), key=lambda r: r.get("added_at") or "", reverse=True)
        out["recently_added"] = [self._card(r, progress.get(r["id"])) for r in newest[:RECENT_ROW]]
        # Recently Watched: what Jellyfin says was played last; empty when it cannot say.
        played = self.played_titles(users) or []
        out["recently_watched"] = [
            self._card(pool[e["media_item_id"]], None) for e in played
            if e["media_item_id"] in pool and e["media_item_id"] not in hidden
        ][:RECENT_ROW]
        return out

    # ---------------------------------------------------------------- details

    def title(self, item_id: int, users: Optional[List[Optional[str]]] = None) -> Optional[Dict[str, Any]]:
        item = self.db.get_nl_title(item_id)
        if not item:
            return None
        is_show = item["media_type"] == "tv"
        genres = [g.strip() for g in (item.get("genres") or "").split(",") if g.strip()]
        base: Dict[str, Any] = {
            "id": item["id"],
            "media_type": item["media_type"],
            "title": item["title"],
            "year": item.get("release_year"),
            "runtime": None if is_show else item.get("runtime"),
            "genres": genres,
            "overview": item.get("overview") or "",
            "poster_url": item.get("poster_url"),
            "backdrop": None,
            "image_fallback": None,
            "jellyfin": False,
            "played": False,
            "progress": 0,
            "resume_seconds": 0,
        }
        if is_show:
            seasons: Dict[int, int] = {}
            for s in item["sources"]:
                n = s.get("season_number") or 0
                seasons[n] = seasons.get(n, 0) + 1
            base["seasons"] = [{"number": n, "label": season_label(n), "episodes": c} for n, c in sorted(seasons.items())]
            base["episode_count"] = len(item["sources"])
            base["show_href"] = f"/nl/show/{item['id']}"
            try:
                series = self.series_id(item)
            except JellyfinUnavailable:
                series = None
            if series:
                base["backdrop"] = image_url(series, "Backdrop", 1280)
                base["jellyfin"] = True
            base["play_href"] = play_href(item["id"], item["sources"][0]["id"])
            return base
        src = item["sources"][0]
        base["play_href"] = play_href(item["id"])
        try:
            jf_id = self._ids_for([src]).get(src["id"])
        except JellyfinUnavailable:
            jf_id = None
        if jf_id:
            base["backdrop"] = image_url(jf_id, "Backdrop", 1280)
            base["image_fallback"] = image_url(jf_id, "Primary", 500)
            details = self._further_details(jf_id, users)
            if details:
                st = progress_of(details)
                base.update(jellyfin=True, played=st["played"], progress=st["progress"], resume_seconds=st["position_seconds"])
                base["jellyfin_id"] = jf_id
        base["can_resume"] = base["resume_seconds"] > 30 and not base["played"]
        base["resume_href"] = play_href(item["id"], resume=True)
        base["restart_href"] = play_href(item["id"], resume=False)
        return base

    def _further_details(self, jf_id: str, users: Optional[List[Optional[str]]]) -> Dict[str, Any]:
        """The Jellyfin item for the current user; for "Both of us" the one of the two who is further in."""
        if not users or len(users) < 2:
            return self.pb.item_details(jf_id)
        best: Dict[str, Any] = {}
        best_pos = -1.0
        for u in users:
            with self.pb.as_user(u):
                details = self.pb.item_details(jf_id)
            st = progress_of(details) if details else None
            pos = -1.0 if st is None else (st["position_seconds"] if not st["played"] else 0.0)
            if details and (pos > best_pos or not best):
                best, best_pos = details, pos
        return best

    def show(self, item_id: int) -> Optional[Dict[str, Any]]:
        """Seasons, episodes with watched state and the Next up episode for one show."""
        item = self.db.get_nl_title(item_id)
        if not item or item["media_type"] != "tv":
            return None
        sources = item["sources"]
        states: Dict[int, Dict[str, Any]] = {}
        series_id = None
        next_id: Optional[int] = None
        jellyfin = False
        try:
            series_id = self.series_id(item)
            if series_id:
                states = self.episode_states(item, series_id)
                next_id = self.next_up_source(item, series_id)
                jellyfin = True
        except JellyfinUnavailable:
            states, next_id, jellyfin = {}, None, False

        by_season: Dict[int, List[Dict[str, Any]]] = {}
        order: List[int] = []
        for s in sources:
            n = s.get("season_number") or 0
            st = states.get(s["id"]) or {}
            ep = s.get("episode_number")
            by_season.setdefault(n, []).append({
                "source_id": s["id"],
                "season": n,
                "episode": ep,
                "code": episode_code(n, ep),
                "name": st.get("name") or (f"Episode {ep}" if ep is not None else "Episode"),
                "overview": st.get("overview") or "",
                "runtime_minutes": st.get("runtime_minutes"),
                "image": st.get("image"),
                "played": bool(st.get("played")),
                "progress": st.get("progress", 0),
                "resume_seconds": st.get("position_seconds", 0),
                "href": play_href(item["id"], s["id"], resume=True if st.get("progress") else None),
            })
            order.append(s["id"])
        seasons = [
            {"number": n, "label": season_label(n), "episodes": by_season[n]} for n in sorted(by_season)
        ]
        flat = {e["source_id"]: e for sn in seasons for e in sn["episodes"]}

        next_up = None
        pick = next_id if next_id in flat else None
        all_watched = jellyfin and pick is None and bool(flat) and all(e["played"] for e in flat.values())
        if pick is None:  # nothing watched yet, all watched, or Jellyfin is away: the first episode we have
            pick = next(iter(flat)) if flat else None
        if pick is not None:
            e = flat[pick]
            started = e["progress"] > 0
            if all_watched:
                label = "Watch again"
            elif started:
                label = "Resume"
            elif e["played"] or any(x["played"] or x["progress"] for x in flat.values()):
                label = "Next up"
            else:
                label = "Play"
            next_up = {**e, "label": label, "text": f"{label} {e['code']}".strip()}

        with self._lock:
            series = self._series.get(item["id"])
        return {
            "id": item["id"],
            "title": item["title"],
            "year": item.get("release_year"),
            "overview": item.get("overview") or "",
            "genres": [g.strip() for g in (item.get("genres") or "").split(",") if g.strip()],
            "poster_url": item.get("poster_url"),
            "backdrop": image_url(series, "Backdrop", 1280) if series else None,
            "jellyfin": jellyfin,
            "autoplay": self.db.get_show_autoplay(item["id"]),
            "seasons": seasons,
            "next_up": next_up,
            "all_watched": bool(all_watched),
            "episode_count": len(flat),
        }

    def next_episode(self, item_id: int, source_id: int) -> Dict[str, Any]:
        """The episode after this one (season/episode order among our files) for the player's
        next-episode card, plus the show's autoplay setting. Names come from Jellyfin when it answers."""
        item = self.db.get_nl_title(item_id)
        if not item or item["media_type"] != "tv":
            return {"has_next": False, "autoplay": True}
        ids = [s["id"] for s in item["sources"]]
        if source_id not in ids or ids.index(source_id) + 1 >= len(ids):
            return {"has_next": False, "autoplay": self.db.get_show_autoplay(item_id)}
        nxt = item["sources"][ids.index(source_id) + 1]
        name = ""
        try:
            series_id = self.series_id(item)
            if series_id:
                name = (self.episode_states(item, series_id).get(nxt["id"]) or {}).get("name") or ""
        except JellyfinUnavailable:
            name = ""
        n = nxt.get("season_number") or 0
        return {
            "has_next": True,
            "autoplay": self.db.get_show_autoplay(item_id),
            "source_id": nxt["id"],
            "season": n,
            "episode": nxt.get("episode_number"),
            "code": episode_code(n, nxt.get("episode_number")),
            "name": name,
            "show_title": item["title"],
        }

    # ----------------------------------------------------------------- search

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Titles from the catalogue (movies and shows) labelled Ready or Not in library yet,
        then matching episodes from Jellyfin when it answers. Ready results come first."""
        query = (query or "").strip()
        if len(query) < 2 or self.catalogue is None:
            return []
        rows = self.catalogue.search(query, "all", 24)
        results: List[Dict[str, Any]] = []
        for r in rows:
            ready = bool(r.get("in_library"))
            is_show = r.get("media_type") == "tv"
            results.append({
                "kind": "show" if is_show else "movie",
                "id": r["id"],
                "title": r["title"],
                "year": r.get("release_year"),
                "poster_url": r.get("poster_url"),
                "ready": ready,
                "label": "Ready" if ready else "Not in library yet",
                "href": f"/nl/{'show' if is_show else 'title'}/{r['id']}" if ready else None,
                "download_href": None if ready else DOWNLOAD_HREF,
            })
        results.sort(key=lambda x: not x["ready"])  # stable: relevance is kept inside each group
        episodes = self._search_episodes(query)
        # Episodes sit after the ready titles and before the ones we do not have.
        ready_count = sum(1 for r in results if r["ready"])
        merged = results[:ready_count] + episodes + results[ready_count:]
        return merged[:limit]

    def _search_episodes(self, query: str) -> List[Dict[str, Any]]:
        try:
            data = self.pb._request(
                "GET", f"/Users/{self._uid()}/Items",
                params={"SearchTerm": query, "IncludeItemTypes": "Episode", "Recursive": "true",
                        "Fields": "Path", "Limit": 8},
            )
        except JellyfinUnavailable:
            return []
        items = (data or {}).get("Items") or []
        by_path = self._by_path([i.get("Path") for i in items])
        out = []
        for it in items:
            src = by_path.get(it.get("Path") or "")
            if not src:
                continue
            code = episode_code(src.get("season_number"), src.get("episode_number"))
            out.append({
                "kind": "episode",
                "id": src["source_id"],
                "title": it.get("Name") or src["title"],
                "subtitle": f"{src['title']} · {code}".strip(" ·"),
                "year": src.get("year"),
                "poster_url": src.get("poster_url"),
                "ready": True,
                "label": "Ready",
                "href": play_href(src["media_item_id"], src["source_id"]),
                "download_href": None,
            })
        return out

