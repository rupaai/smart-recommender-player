"""
Northern Lights "Get it": find a film or show and download it, starting from the title, not from a copy.

Labiba (2026-11-14): "I should be able to choose what I want to download, usually I go for the best one if it
has decent seed, all the TV seasons of a show should be in one page." Search shows real TMDB titles with their
year, so a download is always filed under the title you picked (never under a copy's own name). A show is one
page with every season: how many episodes are out, how many are on the server, and a way to get the rest. The
copies, the best pick and the download itself are the New & popular ones (services/nl_new_episodes.py), so
this file only adds the search and the title page. TMDB only (free). Pages: static/nl/get-it.js.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.nl_new_episodes import IMAGE_BASE, card_base, movie_card, parse_day, season_list

SEARCH_MAX = 12


def season_state(s: Dict[str, Any]) -> str:
    """'complete' (every aired episode is here), 'partial', 'none' (nothing here) or 'upcoming' (nothing out)."""
    if not s.get("aired"):
        return "upcoming"
    have = int(s.get("have") or 0)
    if have >= int(s["aired"]):
        return "complete"
    return "partial" if have else "none"


def season_line(s: Dict[str, Any]) -> str:
    """What a season row says, in plain words."""
    state = season_state(s)
    aired, have, total = int(s.get("aired") or 0), int(s.get("have") or 0), int(s.get("episodes") or 0)
    if state == "upcoming":
        return "Not out yet"
    if state == "complete":
        return f"On the server · {have} of {aired}"
    out = f"{aired} of {total} out" if aired < total else f"{aired} episodes"
    if state == "partial":
        return f"On the server · {have} of {aired} · {aired - have} missing"
    return f"Not on the server · {out}"


class GetIt:
    """`ne` is the NewEpisodes service (TMDB cache, catalogue lookups, copies and the download pick)."""

    def __init__(self, ne: Any):
        self.ne = ne

    def search(self, query: str, kind: str = "all") -> Dict[str, Any]:
        """Films and shows on TMDB that match the text, with what's already on the server."""
        query = (query or "").strip()
        if len(query) < 2:
            return {"query": query, "results": []}
        data = self.ne.tmdb_get("/search/multi", {"query": query, "include_adult": "false"})
        if data is None:
            raise RuntimeError("TMDB can't be reached right now, so titles can't be looked up.")
        rows: List[Dict[str, Any]] = []
        for r in data.get("results") or []:
            mtype = r.get("media_type")
            if mtype not in ("movie", "tv") or (kind in ("movie", "tv") and mtype != kind):
                continue
            day = parse_day(r.get("release_date") or r.get("first_air_date"))
            rows.append({"kind": mtype, "tmdb_id": int(r["id"]), "title": r.get("title") or r.get("name") or "",
                         "year": day.year if day else None,
                         "poster_url": f"{IMAGE_BASE}w342{r['poster_path']}" if r.get("poster_path") else None,
                         "overview": r.get("overview") or "", "rating": round(float(r.get("vote_average") or 0.0), 1),
                         "vote_count": int(r.get("vote_count") or 0)})
            if len(rows) >= SEARCH_MAX:
                break
        for kind_ in ("movie", "tv"):
            ids = [x["tmdb_id"] for x in rows if x["kind"] == kind_]
            known = self.ne._catalogue_ids(ids, kind_)
            for x in rows:
                if x["kind"] != kind_:
                    continue
                found = known.get(x["tmdb_id"])
                x["media_item_id"] = found[0] if found else None
                x["in_library"] = bool(found and found[1])
                x["have_label"] = self._have_label(x)
        # Two titles with the same name: the year tells them apart, so say so.
        names: Dict[str, int] = {}
        for x in rows:
            names[x["title"].lower()] = names.get(x["title"].lower(), 0) + 1
        for x in rows:
            x["same_name"] = names[x["title"].lower()] > 1
        return {"query": query, "results": rows}

    def _have_label(self, x: Dict[str, Any]) -> str:
        if not x.get("in_library"):
            return "Not here"
        if x["kind"] == "movie":
            return "On the server"
        seasons = {s for s, _ in self.ne.had_episodes(x["media_item_id"])}
        return f"{len(seasons)} season{'s' if len(seasons) != 1 else ''} here" if seasons else "On the server"

    def title(self, kind: str, tmdb_id: int) -> Dict[str, Any]:
        """The title page: a film with its download, or a show with every season on one page."""
        d = self.ne.details(tmdb_id, kind)
        if not d:
            what = "show" if kind == "tv" else "film"
            raise LookupError(f"TMDB doesn't know that {what} (or TMDB can't be reached right now).")
        found = self.ne._catalogue_ids([int(tmdb_id)], kind).get(int(tmdb_id))
        media_item_id, in_library = (found[0], found[1]) if found else (None, False)
        if kind == "movie":
            card = movie_card(d, media_item_id, self.ne.today())
            card["href"] = f"/nl/title/{media_item_id}" if (media_item_id and in_library) else None
            return {**card, "kind": "movie", "in_library": in_library}
        card = card_base(d, media_item_id, in_library)
        have = self.ne.had_episodes(media_item_id) if media_item_id else set()
        seasons = []
        for s in season_list(d, have):
            state = season_state(s)
            seasons.append({**s, "state": state, "line": season_line(s),
                            # A season pack only once every episode is out; until then, single episodes.
                            "can_get_season": state in ("none", "partial") and bool(s.get("finished")),
                            "missing": max(0, int(s.get("aired") or 0) - int(s.get("have") or 0))})
        return {**card, "kind": "tv", "seasons": seasons,
                "season_count": len(seasons), "complete_seasons": sum(1 for s in seasons if s["state"] == "complete")}

    def for_item(self, media_item_id: int) -> Dict[str, Any]:
        """Where "Find a copy" on a catalogue title goes: its TMDB page here, or a search for its name."""
        with self.ne.db.get_connection() as conn:
            row = conn.execute("SELECT media_type, tmdb_id, title FROM media_items WHERE id = ?", (int(media_item_id),)).fetchone()
        if not row:
            raise LookupError("That title isn't in the catalogue.")
        kind = "tv" if row[0] == "tv" else "movie"
        if row[1]:
            return {"kind": kind, "tmdb_id": int(row[1]), "title": row[2]}
        return {"kind": kind, "tmdb_id": None, "query": row[2] or "", "title": row[2]}


def get_href(media_item_id: Optional[int]) -> str:
    """The Get it link for a catalogue title (what "Find a copy" opens)."""
    return f"/nl/get?item={int(media_item_id)}" if media_item_id else "/nl/get"
