"""
Northern Lights picks for two (services/nl_picks.py), M6

Three things for the /nl/ pages, built on what already exists (no new recommender):

- the watchlist: the shared Watch later list (`watch_later`, schema v11) with a "Just me" option
  (`watch_later.owner_profile_id`, v14) and a yes / maybe / pass per person per title
  (`nl_interest`, v14). Titles both people said yes to come first;
- "For both of us" (or "Picks for you" when there is only one person): the existing recommender
  in co-view mode, limited to titles that are in the library, with what the people said yes to
  laid over its scores, and one line saying why each title is there;
- the runtime filter (chips "Under 90 min" and "Under 2 h") on both lists.

Reasons are built only from real data: the people's own ratings, their yes answers, genres and
the catalogue rating. If the recommender cannot run (heavy ML dependencies are optional on the
server) the row falls back to "both said yes" titles plus well rated unwatched ones, with reasons
that make no claim about the recommender.

Everything that decides something (runtime filter, ordering, reasons, the fallback) is a plain
function at the top that takes dicts, so it is tested without a server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services.nl_people import Person, Viewer

STATES = ("yes", "maybe", "pass")
RUNTIME_CHIPS = (
    {"minutes": 90, "label": "Under 90 min"},
    {"minutes": 120, "label": "Under 2 h"},
)
LOVED = 8.0                 # a rating at or above this is "loved" in a reason
LIKED_GENRE_RATING = 7      # the recommender's taste profile counts ratings of 7 and up
PICKS_LIMIT = 12
POOL_SIZE = 50
FALLBACK_MIN_RATING = 7.0
YES_BOTH_BOOST = 2.0        # both said yes: a strong positive co-view signal
YES_ONE_BOOST = 0.6
YES_GENRE_BOOST = 0.25      # per genre shared with a title both said yes to ...
YES_GENRE_CAP = 0.75        # ... up to this much


class PicksError(Exception):
    """A request that cannot be done; `status` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- pure helpers


def viewer_keys(viewer: Viewer) -> List[str]:
    """The person keys a viewer's vetoes are filed under: each person at the screen, or the implicit key."""
    return [p.id for p in viewer.people] or [viewer.key]


def utc_str(moment: Optional[datetime] = None) -> str:
    """A UTC moment as 'YYYY-MM-DD HH:MM:SS' (what the database stores and compares)."""
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_max_runtime(value: Any) -> Optional[int]:
    """The runtime filter in minutes from a query value ("90", "120"); None for no filter or junk."""
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return minutes if 20 <= minutes <= 600 else None


def runtime_ok(runtime: Any, max_minutes: Optional[int]) -> bool:
    """True when a title fits the filter. A title with no known runtime (a show, a movie the catalogue
    has no length for) cannot be promised to fit, so it is left out while a filter is on."""
    if not max_minutes:
        return True
    try:
        minutes = float(runtime or 0)
    except (TypeError, ValueError):
        return False
    return 0 < minutes <= max_minutes


def genre_set(genres: Any) -> Set[str]:
    """Lowercased genre names from "Action, Thriller" (or a list)."""
    if isinstance(genres, (list, tuple, set)):
        parts = genres
    else:
        parts = str(genres or "").split(",")
    return {str(g).strip().lower() for g in parts if str(g).strip()}


def pretty_genres(names: Iterable[str]) -> str:
    """"sci-fi", "drama" -> "Sci-Fi and Drama" (at most three, in a stable order)."""
    shown = [("-".join(w.capitalize() for w in n.split("-"))) for n in sorted(names)][:3]
    if len(shown) <= 1:
        return "".join(shown)
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def join_names(names: Sequence[str]) -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


@dataclass
class Ctx:
    """Who is on screen and who the row is for."""

    viewer: Viewer
    persons: List[Dict[str, Any]]            # everyone configured: {id, name, profile_id}
    mine: List[int]                          # profile ids at the screen
    pair: List[int]                          # profile ids the row is for (the first two people)
    names: Dict[int, str] = field(default_factory=dict)

    @property
    def solo(self) -> bool:
        """One person only (or nobody configured): "Picks for you"."""
        return len(self.pair) < 2

    @property
    def is_both(self) -> bool:
        return self.viewer.is_both

    @property
    def title(self) -> str:
        return "Picks for you" if self.solo else "For both of us"

    def name_of(self, profile_id: int) -> str:
        return self.names.get(profile_id, "They")


def build_ctx(configured_people: Sequence[Person], viewer: Viewer) -> Ctx:
    """The context for a viewer. A person without a profile_id in config gets 1 for the first person
    and 2 for the second (the two seeded recommender profiles)."""
    if not configured_people:
        persons = [{"id": viewer.key, "name": "You", "profile_id": 1}]
        return Ctx(viewer=viewer, persons=persons, mine=[1], pair=[1], names={1: "You"})
    persons = [
        {"id": p.id, "name": p.name, "profile_id": p.profile_id if p.profile_id is not None else i + 1}
        for i, p in enumerate(configured_people)
    ]
    by_id = {p["id"]: p for p in persons}
    mine = [by_id[p.id]["profile_id"] for p in viewer.people if p.id in by_id]
    pair = [p["profile_id"] for p in persons[:2]]
    return Ctx(viewer=viewer, persons=persons, mine=mine or pair[:1], pair=pair, names={p["profile_id"]: p["name"] for p in persons})


def personal_visible(ctx: Ctx) -> List[int]:
    """Whose "Just me" rows this screen may show: the one person's own. Both of us shows the shared
    list only (two people on one sofa should not see what one of them keeps to themselves)."""
    return [] if ctx.is_both else list(ctx.mine)


def interest_view(ctx: Ctx, states: Dict[int, str]) -> List[Dict[str, Any]]:
    """Each person's answer on a title: [{person, name, state (yes|maybe|pass|None), mine}]."""
    mine = set(ctx.mine)
    return [
        {"person": p["id"], "name": p["name"], "state": states.get(p["profile_id"]), "mine": p["profile_id"] in mine}
        for p in ctx.persons[:2] if (not ctx.solo or p["profile_id"] in mine)
    ]


def both_said_yes(ctx: Ctx, states: Dict[int, str]) -> bool:
    """Every person of the pair said yes. Needs two people; alone it is never true."""
    return not ctx.solo and all(states.get(p) == "yes" for p in ctx.pair)


def passed_by_any(ctx: Ctx, states: Dict[int, str]) -> bool:
    return any(states.get(p) == "pass" for p in ctx.pair)


def watchlist_rank(ctx: Ctx, states: Dict[int, str]) -> int:
    """0 both said yes, 1 someone said yes, 2 nothing said yet (or maybe), 3 someone passed.
    The list is sorted by this; inside a tier the newest add stays first."""
    if both_said_yes(ctx, states):
        return 0
    if passed_by_any(ctx, states):
        return 3
    if any(states.get(p) == "yes" for p in ctx.pair):
        return 1
    return 2


def rerank(pool: List[Dict[str, Any]], ctx: Ctx, interest: Dict[int, Dict[int, str]], genres_of_yes: Set[str]) -> List[Dict[str, Any]]:
    """Lays what the people said yes to over the recommender's scores. Titles both said yes to get the
    biggest lift, titles one said yes to a smaller one, and a few points for sharing genres with titles
    both said yes to. Titles anyone passed on are dropped. Returns a new list, best first."""
    out = []
    for item in pool:
        iid = item.get("id")
        states = interest.get(iid, {})
        if passed_by_any(ctx, states):
            continue
        score = float(item.get("recommendation_score") or item.get("rating") or 0.0)
        if both_said_yes(ctx, states):
            score += YES_BOTH_BOOST
        elif any(states.get(p) == "yes" for p in ctx.pair):
            score += YES_ONE_BOOST
        shared = len(genre_set(item.get("genres")) & genres_of_yes)
        score += min(YES_GENRE_CAP, YES_GENRE_BOOST * shared)
        copy = dict(item)
        copy["_pick_score"] = score
        out.append(copy)
    out.sort(key=lambda i: (-i["_pick_score"], str(i.get("title") or "")))
    return out


def fallback_pool(items: Dict[int, Dict[str, Any]], ids: Iterable[int], seen: Set[int], min_rating: float = FALLBACK_MIN_RATING) -> List[Dict[str, Any]]:
    """What the row shows when the recommender cannot run: well rated titles on the shelf that nobody
    has seen, best first (the catalogue rating shrunk toward 6.5 by its vote count)."""
    out = []
    for iid in ids:
        it = items.get(iid)
        if not it or iid in seen:
            continue
        rating = float(it.get("rating") or 0.0)
        if rating < min_rating:
            continue
        votes = max(0, int(it.get("vote_count") or 0))
        weighted = (votes / (votes + 200.0)) * rating + (200.0 / (votes + 200.0)) * 6.5
        out.append({**it, "recommendation_score": round(weighted, 2)})
    out.sort(key=lambda i: (-i["recommendation_score"], str(i.get("title") or "")))
    return out


def build_anchors(ctx: Ctx, ratings: List[Dict[str, Any]], yes_titles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Titles a pick can be compared with, strongest first:
    - both_rated: the whole pair rated it 8 or more (alone: you rated it 8 or more, kind "rated");
    - both_yes: the pair both said yes to it;
    - one_rated: one of the pair rated it 8 or more.
    Each: {kind, title, genres (set), floor (int), by [profile ids]}."""
    by_item: Dict[int, Dict[int, Dict[str, Any]]] = {}
    for r in ratings:
        by_item.setdefault(r["media_item_id"], {})[r["profile_id"]] = r
    anchors: List[Dict[str, Any]] = []
    for _, people in by_item.items():
        first = next(iter(people.values()))
        genres = genre_set(first.get("genres"))
        if not genres:
            continue
        if all(p in people and float(people[p]["rating"]) >= LOVED for p in ctx.pair):
            floor = int(min(float(people[p]["rating"]) for p in ctx.pair))
            anchors.append({"kind": "rated" if ctx.solo else "both_rated", "title": first["title"], "genres": genres, "floor": floor, "by": list(ctx.pair)})
            continue
        if not ctx.solo:
            for p in ctx.pair:
                if p in people and float(people[p]["rating"]) >= LOVED:
                    anchors.append({"kind": "one_rated", "title": first["title"], "genres": genres, "floor": int(float(people[p]["rating"])), "by": [p]})
    for t in yes_titles:
        genres = genre_set(t.get("genres"))
        if genres:
            anchors.append({"kind": "both_yes", "title": t["title"], "genres": genres, "floor": 0, "by": list(ctx.pair)})
    order = {"both_rated": 0, "rated": 0, "both_yes": 1, "one_rated": 2}
    anchors.sort(key=lambda a: (order.get(a["kind"], 9), a["title"]))
    return anchors


def build_reason(
    item: Dict[str, Any], ctx: Ctx, interest: Dict[int, str], anchors: List[Dict[str, Any]], tastes: Dict[int, Dict[str, Any]]
) -> str:
    """One line saying why a title is in the row. Never empty, never invented: each branch quotes data
    we hold (a yes, a rating of 8 or more, a genre the people rate 7 or more, the catalogue rating)."""
    names = [ctx.name_of(p) for p in ctx.pair]
    # 1. what they said yes to
    if both_said_yes(ctx, interest):
        return f"{join_names(names)} both said yes"
    said_yes = [ctx.name_of(p) for p in ctx.pair if interest.get(p) == "yes"]
    if said_yes:
        who = said_yes[0]
        return "You said yes" if who == "You" else f"{who} said yes"
    # 2. a title they loved, with the genres it shares
    mine = genre_set(item.get("genres"))
    need = min(2, len(mine))
    best: Optional[Tuple[Tuple[int, int], Dict[str, Any], Set[str]]] = None
    order = {"both_rated": 0, "rated": 0, "both_yes": 1, "one_rated": 2}
    for a in anchors:
        shared = mine & a["genres"]
        if need and len(shared) >= need and a["title"] != item.get("title"):
            key = (order.get(a["kind"], 9), -len(shared))
            if best is None or key < best[0]:
                best = (key, a, shared)
    if best:
        _, a, shared = best
        g = pretty_genres(shared)
        if a["kind"] == "both_rated":
            return f"You both loved {a['title']} (rated {a['floor']}+), and this is {g} too"
        if a["kind"] == "rated":
            return f"You loved {a['title']} (rated {a['floor']}+), and this is {g} too"
        if a["kind"] == "both_yes":
            return f"You both said yes to {a['title']}, and this is {g} too"
        who = ctx.name_of(a["by"][0])
        return f"{who} loved {a['title']} (rated {a['floor']}+), and this is {g} too"
    # 3. genres they rate well
    liked: Dict[int, Set[str]] = {}
    for p in ctx.pair:
        t = tastes.get(p) or {}
        if int(t.get("rating_count") or 0) > 0:
            liked[p] = {str(g).strip().lower() for g in (t.get("preferred_genres") or {}) if str(g).strip()}
    common = mine & set.intersection(*liked.values()) if len(liked) == len(ctx.pair) and liked else set()
    if common:
        return f"Matches {pretty_genres(common)}, which {'you' if ctx.solo else 'you both'} usually rate {LIKED_GENRE_RATING} or higher"
    for p, lg in liked.items():
        shared = mine & lg
        if shared:
            who = ctx.name_of(p)
            g = pretty_genres(shared)
            if who == "You":
                return f"Matches {g}, which you usually rate {LIKED_GENRE_RATING} or higher"
            return f"Matches {g}, which {who} usually rates {LIKED_GENRE_RATING} or higher"
    # 4. the catalogue rating, or the plain truth
    rating = float(item.get("rating") or 0.0)
    if rating > 0:
        return f"Rated {rating:.1f} on TMDB"
    return "On your shelf, and you haven't rated it" if ctx.solo else "On your shelf, and neither of you has rated it"


# ---------------------------------------------------------------- service


class NLPicks:
    """The watchlist, interest and the picks row. `browse` is the NLBrowse (library, cards),
    `recommender` the RecommenderService, `people` the configured People (or a function returning
    them, so a reloaded configuration is picked up)."""

    def __init__(self, db: Any, browse: Any, recommender: Any, people: Any):
        self.db = db
        self.browse = browse
        self.recommender = recommender
        self._people = people
        self.clock = None   # tests fix the time of day vetoes are judged by

    @property
    def people(self) -> Any:
        return self._people() if callable(self._people) else self._people

    # ------------------------------------------------------------ context

    def ctx(self, viewer: Viewer) -> Ctx:
        return build_ctx(self.people.people, viewer)

    @staticmethod
    def _chips() -> List[Dict[str, Any]]:
        return [dict(c) for c in RUNTIME_CHIPS]

    # ------------------------------------------------------- watchlist state

    def state(self, viewer: Viewer) -> Dict[str, Any]:
        """Everything a card needs to paint its "+ Watchlist" and interest controls: which titles are
        listed (and whether "Just me"), each person's answers, who the people are."""
        ctx = self.ctx(viewer)
        entries = self.db.get_watch_later(include_done=False, personal_of=personal_visible(ctx))
        interest = self.db.get_interest()
        listed = {str(e["media_item_id"]): {"personal": bool(e["personal"])} for e in entries}
        answers = {
            str(iid): interest_view(ctx, states) for iid, states in interest.items()
        }
        return {
            "people": [{"id": p["id"], "name": p["name"], "mine": p["profile_id"] in ctx.mine} for p in ctx.persons[:2]],
            "both": ctx.is_both,
            "solo": ctx.solo,
            "can_personal": self.people.configured and not ctx.is_both and not ctx.solo,
            "listed": listed,
            "interest": answers,
        }

    def _check_title(self, media_item_id: int) -> None:
        if not self.db.get_items_by_ids([media_item_id]):
            raise PicksError("That title isn't in the catalogue.", 404)

    def add(self, viewer: Viewer, media_item_id: int, scope: str = "shared") -> Dict[str, Any]:
        """Puts a title on the watchlist: shared (credited to everyone at the screen) or "Just me"."""
        ctx = self.ctx(viewer)
        if scope not in ("shared", "me"):
            raise PicksError("scope must be shared or me.")
        self._check_title(media_item_id)
        if scope == "me":
            if not self.people.configured or ctx.is_both or ctx.solo or len(ctx.mine) != 1:
                raise PicksError("Just me needs one person chosen at the who's-watching screen.")
            self.db.add_to_watch_later(media_item_id, ctx.mine[0], owner_profile_id=ctx.mine[0])
        else:
            for profile_id in ctx.mine:
                self.db.add_to_watch_later(media_item_id, profile_id)
        return self.item_state(viewer, media_item_id)

    def remove(self, viewer: Viewer, media_item_id: int) -> Dict[str, Any]:
        ctx = self.ctx(viewer)
        removed = self.db.remove_from_watchlist(media_item_id, ctx.mine)
        out = self.item_state(viewer, media_item_id)
        out["removed"] = bool(removed)
        return out

    def item_state(self, viewer: Viewer, media_item_id: int) -> Dict[str, Any]:
        ctx = self.ctx(viewer)
        entries = [e for e in self.db.get_watch_later(include_done=False, personal_of=personal_visible(ctx))
                   if e["media_item_id"] == media_item_id]
        states = self.db.get_interest([media_item_id]).get(media_item_id, {})
        return {
            "media_item_id": media_item_id,
            "listed": bool(entries),
            "personal": bool(entries and entries[0]["personal"]),
            "interest": interest_view(ctx, states),
        }

    def set_interest(self, viewer: Viewer, media_item_id: int, state: Optional[str], person_id: Optional[str] = None) -> Dict[str, Any]:
        """Records one person's yes / maybe / pass (None clears it). Alone it is you; on "Both of us"
        the request must say which of the two."""
        ctx = self.ctx(viewer)
        if state is not None and state not in STATES:
            raise PicksError("state must be yes, maybe or pass.")
        chosen = [p for p in ctx.persons if p["profile_id"] in ctx.mine]
        if person_id:
            chosen = [p for p in chosen if p["id"] == person_id]
            if not chosen:
                raise PicksError("That person is not at the screen.", 403)
        elif len(chosen) != 1:
            raise PicksError("Say whose answer this is (person).")
        self._check_title(media_item_id)
        self.db.set_interest(media_item_id, chosen[0]["profile_id"], state)
        return self.item_state(viewer, media_item_id)

    # --------------------------------------------------------------- watchlist

    def watchlist(self, viewer: Viewer, max_minutes: Optional[int] = None) -> Dict[str, Any]:
        """The watchlist cards: both-said-yes first, then someone-said-yes, then the rest, passed last.
        Titles that are not in the library yet are listed too (with a link to find a copy)."""
        ctx = self.ctx(viewer)
        entries = self.db.get_watch_later(include_done=False, personal_of=personal_visible(ctx))
        interest = self.db.get_interest([e["media_item_id"] for e in entries])
        exists = self.browse.pb.file_exists
        cards = []
        for e in entries:
            if not runtime_ok(e.get("runtime") if e.get("media_type") != "tv" else None, max_minutes):
                continue
            states = interest.get(e["media_item_id"], {})
            is_show = e.get("media_type") == "tv"
            ready = bool(e.get("file_path")) and exists(e["file_path"])
            both = both_said_yes(ctx, states)
            cards.append({
                "id": e["media_item_id"],
                "media_type": e.get("media_type"),
                "title": e["title"],
                "year": e.get("release_year"),
                "runtime": None if is_show else e.get("runtime"),
                "poster_url": e.get("poster_url"),
                "genres": [g.strip() for g in str(e.get("genres") or "").split(",") if g.strip()],
                "ready": ready,
                "href": f"/nl/{'show' if is_show else 'title'}/{e['media_item_id']}" if ready else None,
                "download_href": None if ready else "/#direct",
                "personal": bool(e["personal"]),
                "added_by": [ctx.names.get(a["profile_id"], a["name"]) for a in e["added_by"]],
                "interest": interest_view(ctx, states),
                "both_yes": both,
                "rank": watchlist_rank(ctx, states),
            })
        cards.sort(key=lambda c: c["rank"])   # stable: newest add stays first inside a tier
        return {
            "title": "Watchlist",
            "solo": ctx.solo,
            "max_minutes": max_minutes,
            "chips": self._chips(),
            "items": cards,
            "count": len(cards),
        }

    # ------------------------------------------------------------------ picks

    def picks(self, viewer: Viewer, max_minutes: Optional[int] = None, limit: int = PICKS_LIMIT) -> Dict[str, Any]:
        """The home row. Movies on the shelf that nobody in the pair has seen, ranked by the
        recommender in co-view mode, with each person's yes laid over the scores."""
        ctx = self.ctx(viewer)
        rows = {r["id"]: r for r in self.browse._on_disk("movie")}
        ids = [i for i, r in rows.items() if runtime_ok(r.get("runtime"), max_minutes)]
        interest = self.db.get_interest(ids)
        hidden = self.db.get_hidden_ids(viewer.key)   # M7b: "Hide from home" also keeps a title out of this row
        hidden = hidden | self.db.get_vetoed_ids(viewer_keys(viewer), utc_str(self.clock() if self.clock else None))   # M9b: "Not tonight" and "Never"
        ids = [i for i in ids if i not in hidden and not passed_by_any(ctx, interest.get(i, {}))]
        out: Dict[str, Any] = {
            "title": ctx.title, "solo": ctx.solo, "max_minutes": max_minutes, "chips": self._chips(),
            "engine": "recommender", "note": "", "items": [],
        }
        if not ids:
            out["engine"] = "none"
            return out

        tastes = self._tastes()
        pool: List[Dict[str, Any]] = []
        try:
            pool = self._recommend(ctx, ids, tastes)
        except Exception as e:  # noqa: BLE001 - the recommender is optional on the server; the row must not 500
            print(f"[NLPicks] recommender unavailable, using the fallback: {e}", flush=True)
        pool = [p for p in pool if p.get("id") in rows and p.get("id") not in hidden]
        if not pool:
            out["engine"] = "fallback"
            seen = self.db.get_seen_item_ids([p["profile_id"] for p in ctx.persons])
            pool = fallback_pool(self.db.get_items_by_ids(ids), ids, seen)

        # The people's yes answers: titles both said yes to join the row even when the recommender
        # did not surface them (unwatched, on the shelf, nobody passed), and their genres lift others.
        yes_ids = [i for i in ids if both_said_yes(ctx, interest.get(i, {}))]
        in_pool = {p.get("id") for p in pool}
        if yes_ids:
            seen_any = self.db.get_seen_item_ids([p["profile_id"] for p in ctx.persons])
            extra = self.db.get_items_by_ids([i for i in yes_ids if i not in in_pool and i not in seen_any])
            pool = pool + list(extra.values())
        yes_items = self.db.get_items_by_ids(yes_ids) if yes_ids else {}
        genres_of_yes: Set[str] = set()
        for it in yes_items.values():
            genres_of_yes |= genre_set(it.get("genres"))
        ranked = rerank(pool, ctx, interest, genres_of_yes)

        anchors = build_anchors(
            ctx,
            self.db.get_profile_ratings(ctx.pair),
            [{"title": it["title"], "genres": it.get("genres")} for it in yes_items.values()],
        )
        seen_titles: Set[int] = set()
        for it in ranked:
            iid = it.get("id")
            if iid in seen_titles or iid not in rows or iid in hidden:
                continue
            seen_titles.add(iid)
            card = self.browse._card(rows[iid], None)
            card["genres"] = [g.strip() for g in str(it.get("genres") or "").split(",") if g.strip()]
            card["reason"] = build_reason(it, ctx, interest.get(iid, {}), anchors, tastes)
            card["both_yes"] = both_said_yes(ctx, interest.get(iid, {}))
            out["items"].append(card)
            if len(out["items"]) >= limit:
                break
        out["note"] = self._note(ctx, out["engine"])
        return out

    @staticmethod
    def _note(ctx: Ctx, engine: str) -> str:
        if engine == "fallback":
            return "Ranked by rating and what you've said yes to. The recommender isn't running right now."
        who = "your" if ctx.solo else "both of your"
        return f"On your shelf and unwatched, ranked from {who} ratings and what you've said yes to."

    def _tastes(self) -> Dict[int, Dict[str, Any]]:
        try:
            return self.db.get_all_profile_tastes()
        except Exception as e:  # noqa: BLE001
            print(f"[NLPicks] taste lookup failed: {e}", flush=True)
            return {}

    def _recommend(self, ctx: Ctx, ids: List[int], tastes: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The existing recommender, in co-view mode for the people at the screen. Both of us = the
        'together' blend; one person = their seat ('me' is the lowest profile id, 'partner' the next)."""
        profile_ids = sorted(tastes.keys()) or [1, 2]
        if ctx.is_both:
            mode = "together"
        else:
            mode = "me" if (not ctx.mine or ctx.mine[0] == profile_ids[0]) else "partner"
        res = self.recommender.recommend(
            media_type="movie",
            exclude_watched=True,
            profile=mode,
            page=1,
            page_size=POOL_SIZE,
            return_pool=True,
            only_ids=ids,
            variety=False,
        )
        return list(res.get("pool") or [])
