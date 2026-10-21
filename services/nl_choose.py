"""
Northern Lights choosing for two (services/nl_choose.py), M9b

  W6  "Choose for us": mood, genre and time available in, one big pick out (Play / Another);
  W7  the weekday and time of day feed the recommender request (a default mood and length when
      the people did not pick one), reusing the recommender's own moods (/api/moods);
  W8  veto: "Not tonight" hides a title until tomorrow 05:00 local time, "Never" hides it for that
      person (the home row and Choose for us leave vetoed titles out);
  W9  rotating chooser: the app remembers who chose last and says "Your turn, <name>", or picks at
      random from the titles both said yes to (the M6 interest answers).

No new recommender and no new rating system: the pick comes from `RecommenderService.recommend` in
co-view mode exactly like the M6 row (same `only_ids` library limit, same yes lift, same reasons,
same fallback when the recommender cannot run), with the mood, genre and length passed through.

Everything that decides something is a plain function at the top (dicts and datetimes in, plain values
out), so it is tested without a server. With fewer than two people configured the turn-taking
(W9) is switched off; the rest works for the one viewer.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from services.nl_browse import play_href
from services.nl_people import Person, Viewer
from services.recommender_rules import RULES
from services.nl_picks import (
    FALLBACK_MIN_RATING,
    POOL_SIZE,
    PicksError,
    build_anchors,
    build_reason,
    both_said_yes,
    fallback_pool,
    genre_set,
    parse_max_runtime,
    passed_by_any,
    rerank,
    runtime_ok,
    utc_str,
    viewer_keys,
)
from services.nl_together import together_possible

TIMES = (
    {"minutes": 90, "label": "Under 90 min"},
    {"minutes": 120, "label": "Under 2 h"},
    {"minutes": 180, "label": "Under 3 h"},
)
VETO_KINDS = ("tonight", "never")
VETO_HOUR = 5                  # "Not tonight" lasts until the next 05:00, local time
MAX_EXCLUDE = 60               # how many titles "Another" remembers
GENRE_CHIPS = 10
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# ---------------------------------------------------------------- time (W7, W8)


def clean_tz(value: Any) -> Optional[int]:
    """The browser's UTC offset in minutes east of UTC (-840 to 840) from a request value; None for junk."""
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return minutes if -840 <= minutes <= 840 else None


def local_time(now_utc: datetime, tz_minutes: Optional[int] = None, tz_name: Optional[str] = None) -> datetime:
    """The wall-clock time (naive) for the person: from the browser's offset if it sent one, else the
    configured time zone name, else UTC."""
    if tz_minutes is not None:
        return (now_utc + timedelta(minutes=tz_minutes)).replace(tzinfo=None)
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return now_utc.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)
        except Exception:  # noqa: BLE001 - a bad name falls back to UTC rather than breaking choosing
            pass
    return now_utc.replace(tzinfo=None)


def veto_until(now_utc: datetime, tz_minutes: Optional[int] = None, tz_name: Optional[str] = None) -> datetime:
    """When "Not tonight" ends, as a UTC moment: the next 05:00 on the person's own clock, strictly after
    now. Evening or afternoon: tomorrow 05:00. After midnight but before 05:00 it is still tonight, so it
    ends at 05:00 that morning."""
    local = local_time(now_utc, tz_minutes, tz_name)
    end_local = local.replace(hour=VETO_HOUR, minute=0, second=0, microsecond=0)
    if end_local <= local:
        end_local += timedelta(days=1)
    if tz_minutes is not None:
        return (end_local - timedelta(minutes=tz_minutes)).replace(tzinfo=timezone.utc)
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return end_local.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            pass
    return end_local.replace(tzinfo=timezone.utc)


def daypart(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _default_for(anchor_day: int, part: str, rules: Sequence[Dict[str, Any]]) -> Tuple[int, Optional[str]]:
    """The first `choose_defaults` rule (recommender_rules.yaml) matching the day and part of day.
    `when: daytime` = morning or afternoon; `days` lists mon..sun. No match: Surprise Me, any length."""
    for rule in rules or []:
        when = rule.get("when")
        if when == "daytime" and part not in ("morning", "afternoon"):
            continue
        if when and when != "daytime" and when != part:
            continue
        days = [str(d).lower()[:3] for d in rule.get("days") or []]
        if days and _DAYS[anchor_day] not in days:
            continue
        return int(rule.get("mood", 8)), rule.get("duration")
    return 8, None


def context_for(local: datetime, moods: Dict[int, Dict[str, Any]],
                rules: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """What the weekday and the time of day suggest, as part of the recommender request (W7):
    a default mood id (one of the recommender's own, /api/moods) and a length preference
    ("short" = under 105 min) used only when the people did not pick a mood or a time.
    The rules are `choose_defaults` in recommender_rules.yaml.
    """
    weekday = local.weekday()          # 0 Monday
    part = daypart(local.hour)
    # After midnight still belongs to the evening before.
    anchor_day = (weekday - 1) % 7 if (part == "night" and local.hour < 5) else weekday
    mood, duration = _default_for(anchor_day, part, RULES["choose_defaults"] if rules is None else rules)
    info = moods.get(mood) or moods.get(8) or {}
    return {
        "weekday": WEEKDAYS[weekday],
        "daypart": part,
        "label": f"{WEEKDAYS[anchor_day] if part == 'night' and local.hour < 5 else WEEKDAYS[weekday]} {part}",
        "mood_id": mood if mood in moods else 8,
        "mood_name": clean_mood_name(info.get("name", "")),
        "duration": duration,
    }


def clean_mood_name(name: str) -> str:
    """"😊 Happy / Feel-Good" -> "Happy / Feel-Good" (the leading emoji is dropped)."""
    i = 0
    while i < len(name) and not name[i].isalnum():
        i += 1
    return name[i:].strip()


# ---------------------------------------------------------------- turn taking (W9)


def next_chooser(people: Sequence[Person], last_person_id: Optional[str]) -> Optional[Person]:
    """Who chooses next: the person after the last chooser, in the order of the config (the first person
    when nobody has chosen yet or the last chooser is no longer configured)."""
    if not people:
        return None
    ids = [p.id for p in people]
    if last_person_id in ids:
        return people[(ids.index(last_person_id) + 1) % len(people)]
    return people[0]


def turn_info(viewer: Viewer, people: Sequence[Person], last_person_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The "Your turn, <name>" line, or None with fewer than two people. On "Both of us" it is always
    "Your turn, <the next chooser>"; one person gets "Your turn, <them>" when it is theirs and
    "<name>'s turn to choose" when it is the other's."""
    if not together_possible(people):
        return None
    nxt = next_chooser(people, last_person_id)
    mine = bool(viewer.is_both or (viewer.primary and viewer.primary.id == nxt.id))
    last = next((p for p in people if p.id == last_person_id), None)
    return {
        "person": nxt.id,
        "name": nxt.name,
        "mine": mine,
        "text": f"Your turn, {nxt.name}" if mine else f"{nxt.name}’s turn to choose",
        "last": last.name if last else None,
    }


# ---------------------------------------------------------------- picking (W6)


def mood_genres(moods: Dict[int, Dict[str, Any]], mood_id: Optional[int]) -> Set[str]:
    return genre_set((moods.get(mood_id) or {}).get("genres") or []) if mood_id else set()


def fits_genres(item_genres: Any, wanted: Set[str]) -> bool:
    """No wish means anything fits; otherwise the title shares at least one wanted genre. "Sci-Fi" and
    "Science Fiction" are the same genre."""
    if not wanted:
        return True
    have = {"science fiction" if g == "sci-fi" else g for g in genre_set(item_genres)}
    want = {"science fiction" if g == "sci-fi" else g for g in wanted}
    return bool(have & want)


class NLChoose:
    """Choose for us, vetoes and the rotating chooser. `picks` is the NLPicks (library, recommender,
    cards, reasons), `people` the configured People (or a function returning them), `moods` the
    recommender's MOOD_MAPPINGS, `tz_name` the optional `northern_lights.timezone` used when the browser
    sends no offset, `rng` and `clock` exist so tests can fix the random choice and the time."""

    def __init__(self, db: Any, picks: Any, people: Any, moods: Dict[int, Dict[str, Any]], tz_name: Optional[str] = None,
                 rng: Optional[random.Random] = None, clock: Any = None):
        self.db = db
        self.picks = picks
        self._people = people
        self.moods = moods
        self.tz_name = tz_name or None
        self.rng = rng or random.Random()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def people(self) -> Any:
        return self._people() if callable(self._people) else self._people

    def now(self) -> datetime:
        return self.clock()

    # ------------------------------------------------------------ options

    def options(self, viewer: Viewer, tz: Optional[int] = None) -> Dict[str, Any]:
        """What the Choose page offers: the recommender's moods, the shelf's genres, the time chips, what the
        weekday and time suggest, whose turn it is and how many titles both said yes to."""
        ctx = self.picks.ctx(viewer)
        rows = self.picks.browse._on_disk("movie")
        counts: Dict[str, int] = {}
        for item in self.db.get_items_by_ids([r["id"] for r in rows]).values():
            for g in sorted(genre_set(item.get("genres"))):
                counts[g] = counts.get(g, 0) + 1
        genres = [("-".join(w.capitalize() for w in g.split("-"))) for g, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:GENRE_CHIPS]]
        local = local_time(self.now(), tz, self.tz_name)
        ids, interest, _seen = self._candidates(viewer, ctx, None, set())
        return {
            "moods": [{"id": i, "label": clean_mood_name(m.get("name", "")), "vibe": m.get("vibe", "")} for i, m in self.moods.items()],
            "genres": genres,
            "times": [dict(t) for t in TIMES],
            "context": context_for(local, self.moods),
            "turn": turn_info(viewer, self.people.people, (self.db.get_last_chooser() or {}).get("person_id")),
            "yeses": len(self._yes_ids(ctx, ids, interest)),
            "solo": ctx.solo,
            "together": together_possible(self.people.people),
            "people": [{"id": p.id, "name": p.name} for p in viewer.people],
        }

    # ----------------------------------------------------------- candidates

    def _vetoed(self, viewer: Viewer) -> Set[int]:
        return self.db.get_vetoed_ids(viewer_keys(viewer), utc_str(self.now()))

    def _candidates(self, viewer: Viewer, ctx: Any, max_minutes: Optional[int], exclude: Set[int]):
        """Movies on the shelf nobody at the pair has seen, that fit the length, that nobody passed on,
        hid from home or vetoed, minus the ones "Another" already showed."""
        rows = {r["id"]: r for r in self.picks.browse._on_disk("movie")}
        ids = [i for i, r in rows.items() if runtime_ok(r.get("runtime"), max_minutes) and i not in exclude]
        interest = self.db.get_interest(ids)
        blocked = self.db.get_hidden_ids(viewer.key) | self._vetoed(viewer)
        seen = self.db.get_seen_item_ids([p["profile_id"] for p in ctx.persons])
        ids = [i for i in ids if i not in blocked and i not in seen and not passed_by_any(ctx, interest.get(i, {}))]
        return ids, interest, seen

    @staticmethod
    def _yes_ids(ctx: Any, ids: List[int], interest: Dict[int, Dict[int, str]]) -> List[int]:
        """Titles both said yes to; with one person, the titles that person said yes to."""
        if ctx.solo:
            me = ctx.mine[0] if ctx.mine else None
            return [i for i in ids if me is not None and interest.get(i, {}).get(me) == "yes"]
        return [i for i in ids if both_said_yes(ctx, interest.get(i, {}))]

    # --------------------------------------------------------------- choose

    def choose(
        self,
        viewer: Viewer,
        mood: Optional[int] = None,
        genre: Optional[str] = None,
        max_minutes: Any = None,
        exclude: Optional[Sequence[int]] = None,
        mode: str = "pick",
        tz: Optional[int] = None,
    ) -> Dict[str, Any]:
        """One big pick. `mood` / `genre` / `max_minutes` are the people's wishes (hard filters); without a
        mood or a length the weekday and time of day fill in (W7). `exclude` is what "Another" has shown.
        mode "yeses": a random title from the ones both said yes to."""
        if mode not in ("pick", "yeses"):
            raise PicksError("mode must be pick or yeses.")
        if mood is not None and mood not in self.moods:
            raise PicksError("That mood isn't one of the recommender's moods.")
        minutes = parse_max_runtime(max_minutes)
        gone = {int(i) for i in (exclude or [])[:MAX_EXCLUDE] if isinstance(i, int)}
        ctx = self.picks.ctx(viewer)
        ids, interest, seen = self._candidates(viewer, ctx, minutes, gone)
        local = local_time(self.now(), tz, self.tz_name)
        context = context_for(local, self.moods)
        wanted: Set[str] = {genre.strip().lower()} if genre and genre.strip() else mood_genres(self.moods, mood)
        items = self.db.get_items_by_ids(ids)
        ids = [i for i in ids if fits_genres(items.get(i, {}).get("genres"), wanted)]
        base: Dict[str, Any] = {
            "item": None, "context": context, "engine": "none", "note": "", "turn": self._turn(viewer), "remaining": 0,
            "used": {"mood_id": mood if mood is not None else context["mood_id"], "mood_chosen": mood is not None,
                     "max_minutes": minutes, "genre": genre or None},
        }
        if not ids:
            base["note"] = "Nothing on your shelf fits that. Try fewer wishes." if (wanted or minutes or gone) else \
                "Nothing on your shelf to choose from. Add something from the Download tab of the main app."
            return base

        if mode == "yeses":
            yes = self._yes_ids(ctx, ids, interest)
            if not yes:
                base["note"] = "No title you both said yes to is waiting on the shelf." if not ctx.solo else \
                    "No title you said yes to is waiting on the shelf."
                return base
            pick = self.rng.choice(sorted(yes))
            base.update(engine="yeses", remaining=len(yes) - 1)
            base["item"] = self._card(viewer, ctx, pick, interest, items)
            return base

        engine = "recommender"
        pool: List[Dict[str, Any]] = []
        try:
            pool = self._recommend(ctx, ids, mood if mood is not None else context["mood_id"], genre, minutes, context)
        except Exception as e:  # noqa: BLE001 - the recommender is optional on the server
            print(f"[NLChoose] recommender unavailable, using the fallback: {e}", flush=True)
        keep = set(ids)
        pool = [p for p in pool if p.get("id") in keep and fits_genres(p.get("genres"), wanted)]
        if not pool:
            engine = "fallback"
            pool = fallback_pool(items, ids, seen) or fallback_pool(items, ids, seen, min_rating=0.0)
        # what the people said yes to lifts titles (and a title both said yes to may join an empty pool)
        yes_ids = self._yes_ids(ctx, ids, interest) if not ctx.solo else []
        in_pool = {p.get("id") for p in pool}
        pool = pool + [dict(items[i], recommendation_score=float(items[i].get("rating") or 0)) for i in yes_ids if i not in in_pool and i in items]
        yes_items = self.db.get_items_by_ids(yes_ids) if yes_ids else {}
        genres_of_yes: Set[str] = set()
        for it in yes_items.values():
            genres_of_yes |= genre_set(it.get("genres"))
        ranked = rerank(pool, ctx, interest, genres_of_yes)
        if not ranked:
            base["note"] = "Nothing on your shelf fits that. Try fewer wishes."
            return base
        top = ranked[0]
        base.update(engine=engine, remaining=len({r.get("id") for r in ranked}) - 1)
        base["item"] = self._card(viewer, ctx, top["id"], interest, items, extra=top, yes_items=list(yes_items.values()))
        return base

    def _recommend(self, ctx: Any, ids: List[int], mood_id: int, genre: Optional[str], minutes: Optional[int],
                   context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The existing recommender in co-view mode with the mood, genre and length (W7: the day's own mood and
        length when nobody picked), limited to the shelf like the M6 row."""
        tastes = self.picks._tastes()
        profile_ids = sorted(tastes.keys()) or [1, 2]
        if ctx.is_both:
            mode = "together"
        else:
            mode = "me" if (not ctx.mine or ctx.mine[0] == profile_ids[0]) else "partner"
        res = self.picks.recommender.recommend(
            mood_id=mood_id,
            genres_override=[genre] if genre else None,
            duration_preference=None if minutes else context.get("duration"),
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

    def _card(self, viewer: Viewer, ctx: Any, item_id: int, interest: Dict[int, Dict[str, Any]], items: Dict[int, Dict[str, Any]],
              extra: Optional[Dict[str, Any]] = None, yes_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        row = next((r for r in self.picks.browse._on_disk("movie") if r["id"] == item_id), None) or {"id": item_id, "title": "", "media_type": "movie"}
        base = items.get(item_id) or {}
        card = self.picks.browse._card(row, None)
        genres = (extra or base).get("genres") or base.get("genres")
        card["genres"] = [g.strip() for g in str(genres or "").split(",") if g.strip()]
        card["overview"] = base.get("overview") or ""
        tastes = self.picks._tastes()
        anchors = build_anchors(
            ctx, self.db.get_profile_ratings(ctx.pair),
            [{"title": it["title"], "genres": it.get("genres")} for it in (yes_items or [])],
        )
        card["reason"] = build_reason({**base, **(extra or {})}, ctx, interest.get(item_id, {}), anchors, tastes)
        card["both_yes"] = both_said_yes(ctx, interest.get(item_id, {}))
        card["play_href"] = play_href(item_id)
        return card

    def _turn(self, viewer: Viewer) -> Optional[Dict[str, Any]]:
        return turn_info(viewer, self.people.people, (self.db.get_last_chooser() or {}).get("person_id"))

    # ------------------------------------------------------------ the chooser (W9)

    def chosen(self, viewer: Viewer, media_item_id: int) -> Dict[str, Any]:
        """Records who chose (the person at the screen; on "Both of us" the one whose turn it was), when the
        pick is played. With fewer than two people there is no turn to keep."""
        if not self.db.get_items_by_ids([media_item_id]):
            raise PicksError("That title isn't in the catalogue.", 404)
        people = self.people.people
        if not together_possible(people):
            return {"turn": None}
        last = (self.db.get_last_chooser() or {}).get("person_id")
        who = next_chooser(people, last) if viewer.is_both else viewer.primary
        if who is not None:
            self.db.set_last_chooser(who.id, media_item_id)
        return {"turn": self._turn(viewer)}

    # ---------------------------------------------------------------- vetoes (W8)

    def _veto_person(self, viewer: Viewer, person: Optional[str]) -> str:
        """Whose "Never" this is: the one person at the screen; on "Both of us" the request must say which."""
        if viewer.implicit:
            return viewer.key
        wanted = (person or "").strip().lower() or ("" if viewer.is_both else viewer.primary.id)
        if not wanted:
            raise PicksError("Say whose “never” this is.")
        if wanted not in [p.id for p in viewer.people]:
            raise PicksError("That person isn't at the screen.", 403)
        return wanted

    def veto(self, viewer: Viewer, media_item_id: int, kind: str, person: Optional[str] = None, tz: Optional[int] = None) -> Dict[str, Any]:
        """"Not tonight": hidden for whoever is at the screen until the next 05:00 on the person's clock.
        "Never": hidden for that person for good (undo from their page)."""
        if kind not in VETO_KINDS:
            raise PicksError("kind must be tonight or never.")
        try:
            if kind == "tonight":
                until = utc_str(veto_until(self.now(), tz, self.tz_name))
                self.db.set_veto("*", media_item_id, "tonight", until)
                return {"success": True, "kind": kind, "until": until}
            key = self._veto_person(viewer, person)
            self.db.set_veto(key, media_item_id, "never")
        except ValueError as e:
            raise PicksError(str(e), 404) from e
        return {"success": True, "kind": kind, "person": key}

    def unveto(self, viewer: Viewer, media_item_id: int, kind: str, person: Optional[str] = None) -> Dict[str, Any]:
        if kind not in VETO_KINDS:
            raise PicksError("kind must be tonight or never.")
        key = "*" if kind == "tonight" else self._veto_person(viewer, person)
        return {"success": True, "removed": self.db.clear_veto(key, media_item_id), "kind": kind}

    def vetoes(self, viewer: Viewer) -> Dict[str, Any]:
        """The active vetoes for this screen: each person's "never" list and what is off the table tonight."""
        names = {p.id: p.name for p in viewer.people}
        out = []
        for r in self.db.get_vetoes(viewer_keys(viewer), utc_str(self.now())):
            out.append({
                "media_item_id": r["media_item_id"], "title": r["title"], "year": r["year"], "kind": r["kind"],
                "person": None if r["person_key"] == "*" else r["person_key"],
                "person_name": names.get(r["person_key"]),
                "until": r["until"],
            })
        return {"items": out, "both": viewer.is_both}
