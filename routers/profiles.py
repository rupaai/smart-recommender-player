# ruff: noqa: F821 -- endpoint annotations resolve from the injected app dependency namespace.
"""Profiles API routes and endpoint handlers."""

from fastapi import APIRouter

_ANNOTATION_NAMES = (
    "Any",
    "Dict",
    "List",
    "NLHiddenBody",
    "NLInterestBody",
    "NLReactBody",
    "NLWatchlistAdd",
    "Optional",
    "PairAnswer",
    "RateRequest",
    "RenameProfileRequest",
    "Request",
    "SeasonRateRequest",
    "WatchLaterRequest",
)
ROUTES = [
    ("GET", "/api/nl/viewer", "nl_viewer_info"),
    ("GET", "/api/nl/prefs", "nl_prefs_get"),
    ("PUT", "/api/nl/prefs", "nl_prefs_put"),
    ("GET", "/api/nl/react/{item_id}", "nl_react_get"),
    ("POST", "/api/nl/react/{item_id}", "nl_react_post"),
    ("GET", "/api/nl/hidden/{item_id}", "nl_hidden_get"),
    ("PUT", "/api/nl/hidden/{item_id}", "nl_hidden_set"),
    ("GET", "/api/nl/watchlist", "nl_watchlist"),
    ("GET", "/api/nl/watchlist/state", "nl_watchlist_state"),
    ("POST", "/api/nl/watchlist", "nl_watchlist_add"),
    ("DELETE", "/api/nl/watchlist/{item_id}", "nl_watchlist_remove"),
    ("PUT", "/api/nl/interest/{item_id}", "nl_interest_set"),
    ("GET", "/api/history", "get_history"),
    ("GET", "/api/watch-later", "get_watch_later"),
    ("POST", "/api/watch-later", "add_watch_later"),
    ("DELETE", "/api/watch-later/{media_item_id}", "remove_watch_later"),
    ("POST", "/api/watch-later/{media_item_id}/done", "done_watch_later"),
    ("POST", "/api/watch-later/{media_item_id}/undo", "undo_watch_later"),
    ("GET", "/api/profiles", "get_profiles"),
    ("PUT", "/api/profiles/{profile_id}", "rename_profile"),
    ("GET", "/api/ratings/genres", "list_rating_genres"),
    ("GET", "/api/ratings/queue", "get_rating_queue"),
    ("POST", "/api/ratings", "rate_item"),
    ("GET", "/api/ratings/vocab", "get_rating_vocab"),
    ("POST", "/api/ratings/season", "rate_season"),
    ("GET", "/api/ratings/details/{item_id}", "get_rating_details"),
    ("GET", "/api/ratings/pairs", "get_rating_pairs"),
    ("POST", "/api/ratings/pairs", "answer_rating_pair"),
    ("GET", "/api/ratings/inferred-queue", "get_inferred_rating_queue"),
    ("GET", "/api/ratings/summary/{item_id}", "get_rating_summary"),
    ("GET", "/api/ratings/co-view", "get_co_view_history"),
]


def build_router(dependencies):
    """Build the profiles router with live app dependencies."""
    globals().update({name: dependencies[name] for name in _ANNOTATION_NAMES})
    router = APIRouter(tags=["profiles"])

    async def nl_viewer_info(request: "Request") -> "Dict[str, Any]":
        """Who is watching: the choices for the who's-watching screen, the chosen viewer (null = not
        chosen yet) and the recommender profiles their ratings go to. Never carries Jellyfin ids."""
        viewer = dependencies["_nl_viewer"](request)
        return {
            "configured": dependencies["nl_people"].configured,
            "choices": dependencies["nl_people"].choices(),
            "viewer": viewer.public() if viewer else None,
            "profile_ids": viewer.profile_ids if viewer else [],
        }

    async def nl_prefs_get(request: "Request") -> "Dict[str, Any]":
        """The current viewer's language and subtitle prefs (audio language, subtitle language, subtitles on
        by default, the subtitle look, the subtitle search language). Per person, not per device."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        return await dependencies["asyncio"].to_thread(
            dependencies["_nl_prefs_reply"], viewer
        )

    async def nl_prefs_put(
        request: "Request", body: "Dict[str, Any]" = dependencies["Body"](...)
    ) -> "Dict[str, Any]":
        """Updates the fields sent (null / "" clears a language); the rest stay. 400 on a bad value."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        try:
            fields = dependencies["clean_prefs"](body)
        except ValueError as e:
            raise dependencies["HTTPException"](status_code=400, detail=str(e))
        await dependencies["asyncio"].to_thread(
            dependencies["db"].set_person_prefs, viewer.key, fields
        )
        return await dependencies["asyncio"].to_thread(
            dependencies["_nl_prefs_reply"], viewer
        )

    async def nl_react_get(item_id: "int", request: "Request") -> "Dict[str, Any]":
        """W10: should the player ask "How was it?" now. `ask` is false for an unknown title, a viewer with no
        profile, or when everyone at the screen already gave this title a verdict of their own."""
        viewer = dependencies["_nl_viewer_or_default"](request)

        def work() -> "Dict[str, Any]":
            item = dependencies["db"].get_item_by_id(item_id)
            profiles = dependencies["_react_profiles"](viewer)
            asked = bool(item and profiles) and (
                not all(
                    (
                        dependencies["nl_reactions"].has_own_verdict(
                            dependencies["db"].get_rating_details(item_id, pid)
                        )
                        for pid in profiles
                    )
                )
            )
            return {
                "ask": asked,
                "text": dependencies["nl_reactions"].question(
                    item["title"], viewer.is_both
                )
                if item
                else "",
                "options": [
                    {"id": r["id"], "label": r["label"]}
                    for r in dependencies["nl_reactions"].REACTIONS
                ],
            }

        return await dependencies["asyncio"].to_thread(work)

    async def nl_react_post(
        item_id: "int", body: "NLReactBody", request: "Request"
    ) -> "Dict[str, Any]":
        """W10: records the answer as a verdict for the profile(s) at the screen ("Both of us" rates for both)."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        verdict = dependencies["nl_reactions"].verdict_for(body.reaction)
        if verdict is None:
            raise dependencies["HTTPException"](
                status_code=400, detail="Unknown reaction."
            )
        profiles = dependencies["_react_profiles"](viewer)
        if not profiles:
            raise dependencies["HTTPException"](
                status_code=400, detail="No profile to rate for."
            )

        def work() -> "bool":
            if not dependencies["db"].get_item_by_id(item_id):
                return False
            for pid in profiles:
                dependencies["db"].rate_item(
                    item_id, profile_id=pid, status="seen", verdict=verdict
                )
            return True

        if not await dependencies["asyncio"].to_thread(work):
            raise dependencies["HTTPException"](
                status_code=404, detail="That title isn't in the library."
            )
        return {"success": True, "verdict": verdict, "profiles": len(profiles)}

    async def nl_hidden_get(item_id: "int", request: "Request") -> "Dict[str, Any]":
        """Has this viewer hidden the title from the home rows?"""
        viewer = dependencies["_nl_viewer_or_default"](request)
        return {
            "hidden": item_id
            in await dependencies["asyncio"].to_thread(
                dependencies["db"].get_hidden_ids, viewer.key
            )
        }

    async def nl_hidden_set(
        item_id: "int", body: "NLHiddenBody", request: "Request"
    ) -> "Dict[str, Any]":
        """ "Hide from home" / "Show on home again" for the person at the screen ("Both of us" has its own list)."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        try:
            value = await dependencies["asyncio"].to_thread(
                dependencies["db"].set_hidden, viewer.key, item_id, body.hidden
            )
        except ValueError as e:
            raise dependencies["HTTPException"](status_code=404, detail=str(e))
        return {"hidden": value}

    async def nl_watchlist(
        request: "Request", max_runtime: "Optional[str]" = None
    ) -> "Dict[str, Any]":
        """The watchlist for the viewer: titles both said yes to first, then one yes, then the rest, passed
        last. `max_runtime` (minutes, 90 or 120 in the chips) hides longer movies and shows."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        return await dependencies["asyncio"].to_thread(
            dependencies["nl_picks"].watchlist,
            viewer,
            dependencies["parse_max_runtime"](max_runtime),
        )

    async def nl_watchlist_state(request: "Request") -> "Dict[str, Any]":
        """What a card needs to paint its watchlist button and yes / maybe / pass: listed titles and each
        person's answers, plus who the people are and whether "Just me" is offered."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        return await dependencies["asyncio"].to_thread(
            dependencies["nl_picks"].state, viewer
        )

    async def nl_watchlist_add(
        body: "NLWatchlistAdd", request: "Request"
    ) -> "Dict[str, Any]":
        """Adds a title: shared by default (credited to everyone at the screen), or scope "me" for Just me."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        try:
            return await dependencies["asyncio"].to_thread(
                dependencies["nl_picks"].add, viewer, body.media_item_id, body.scope
            )
        except dependencies["PicksError"] as e:
            raise dependencies["_picks_error"](e) from e

    async def nl_watchlist_remove(
        item_id: "int", request: "Request"
    ) -> "Dict[str, Any]":
        """Takes a title off the watchlist (the shared list for both, plus the viewer's own Just me)."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        return await dependencies["asyncio"].to_thread(
            dependencies["nl_picks"].remove, viewer, item_id
        )

    async def nl_interest_set(
        item_id: "int", body: "NLInterestBody", request: "Request"
    ) -> "Dict[str, Any]":
        """One person's yes / maybe / pass on a title (state null clears it)."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        try:
            return await dependencies["asyncio"].to_thread(
                dependencies["nl_picks"].set_interest,
                viewer,
                item_id,
                body.state,
                body.person,
            )
        except dependencies["PicksError"] as e:
            raise dependencies["_picks_error"](e) from e

    async def get_history() -> "Dict[str, Any]":
        """Retrieves watch history and learned taste profile."""
        history = dependencies["db"].get_watch_history(limit=50)
        taste = dependencies["db"].get_user_taste_profile(min_rating=7.0)
        return {"history": history, "taste_profile": taste}

    async def get_watch_later(
        include_done: "bool" = False,
        media_type: "Optional[str]" = None,
        added_by: "Optional[int]" = None,
    ) -> "Dict[str, Any]":
        """The shared list, one entry per title, newest add first."""
        items = await dependencies["asyncio"].to_thread(
            dependencies["db"].get_watch_later,
            include_done=include_done,
            media_type=media_type or None,
            added_by=added_by,
        )
        return {"items": items, "count": len(items)}

    async def add_watch_later(req: "WatchLaterRequest") -> "Dict[str, Any]":
        """Puts a title on the list for one person; returns the grouped entry."""
        try:
            return await dependencies["asyncio"].to_thread(
                dependencies["db"].add_to_watch_later, req.media_item_id, req.profile_id
            )
        except ValueError as e:
            raise dependencies["HTTPException"](status_code=400, detail=str(e)) from e

    async def remove_watch_later(
        media_item_id: "int", profile_id: "int"
    ) -> "Dict[str, Any]":
        """Takes one person's add off the list."""
        removed = await dependencies["asyncio"].to_thread(
            dependencies["db"].remove_from_watch_later, media_item_id, profile_id
        )
        return {"removed": bool(removed)}

    async def done_watch_later(media_item_id: "int") -> "Dict[str, Any]":
        """Marks a title on the list as watched (the Later tab's "Watched it")."""
        n = await dependencies["asyncio"].to_thread(
            dependencies["db"].mark_watch_later_done, media_item_id, "watched"
        )
        return {"done": n}

    async def undo_watch_later(media_item_id: "int") -> "Dict[str, Any]":
        """Brings a title marked done back onto the list."""
        n = await dependencies["asyncio"].to_thread(
            dependencies["db"].undo_watch_later_done, media_item_id
        )
        return {"reopened": n}

    async def get_profiles() -> "List[Dict[str, Any]]":
        """Lists the configured co-view rating profiles with live stats."""
        return dependencies["db"].get_profiles()

    async def rename_profile(
        profile_id: "int", req: "RenameProfileRequest"
    ) -> "Dict[str, Any]":
        """Renames a rating profile. 400 on unknown id or a duplicate name."""
        if not dependencies["db"].update_profile_name(profile_id, req.name):
            raise dependencies["HTTPException"](
                status_code=400, detail="Unknown profile id or duplicate name."
            )
        return {"ok": True, "profiles": dependencies["db"].get_profiles()}

    async def list_rating_genres():
        """Genres worth offering as rating-queue chips.

        Derived from the catalogue rather than hardcoded in the UI: the old fixed
        list of 9 included "Documentary", which only 7 films in the catalogue
        carry — clicking it produced a 1–2 card queue that felt broken. Anything
        below the floor is omitted, and the count lets the UI show how deep each
        genre actually goes.
        """
        with dependencies["db"].get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT genres FROM media_items WHERE genres IS NOT NULL AND genres != ''"
            ).fetchall()
        counts = {}
        for (raw,) in rows:
            for g in (raw or "").split(","):
                name = g.strip()
                if name:
                    counts[name] = counts.get(name, 0) + 1
        floor = 20
        ranked = sorted(
            ((name, n) for name, n in counts.items() if n >= floor),
            key=lambda kv: (-kv[1], kv[0]),
        )
        return {"genres": [{"name": name, "count": n} for name, n in ranked]}

    async def get_rating_queue(
        profile_id: "int" = 1,
        media_type: "str" = "movie",
        limit: "int" = dependencies["Query"](default=25, ge=1, le=100),
        genres: "Optional[str]" = dependencies["Query"](
            default=None, description="Comma-separated genre filter"
        ),
        seed: "Optional[int]" = dependencies["Query"](
            default=None, description="Rotation seed; omit to use a 10-min bucket"
        ),
        balanced: "bool" = dependencies["Query"](
            default=False,
            description="Walk the genre wheel — best for building a taste profile fast",
        ),
    ) -> "Dict[str, Any]":
        """Next items for this profile to rate, highest-value first.

        Each profile rates independently — this is a to-do list *for the active
        profile*, not a list of their existing ratings. `genres` narrows to a slice
        (the UI's genre chips); `seed` rotates which films come up; `balanced` walks
        the genre wheel so a short rating session still covers many genres.
        """
        genre_list = (
            [g.strip() for g in genres.split(",") if g.strip()]
            if isinstance(genres, str) and genres
            else None
        )
        seed_value = seed if isinstance(seed, int) else None
        balanced_value = balanced if isinstance(balanced, bool) else False
        items = await dependencies["asyncio"].to_thread(
            dependencies["db"].get_rating_queue,
            profile_id,
            media_type,
            limit,
            genre_list,
            seed_value,
            balanced_value,
        )
        other_id = 2 if profile_id == 1 else 1
        for item in items:
            summary = dependencies["db"].get_rating_summary(item["id"])
            item["co_view"] = {
                "tier": summary["tier"],
                "tier_reason": summary["tier_reason"],
                "combined_score": summary["combined_score"],
                "disagreement": summary["disagreement"],
                "verdicts": summary["verdicts"],
            }
            item["other_profile_id"] = other_id
        dependencies["_with_on_list"](items)
        return {"profile_id": profile_id, "media_type": media_type, "items": items}

    async def rate_item(req: "RateRequest") -> "Dict[str, Any]":
        """Records one profile's verdict on one item (upsert).

        `status="not_seen"` means "we haven't watched this" — stored as its own
        state with no rating attached, never as a zero or a low score.
        """
        try:
            summary = await dependencies["asyncio"].to_thread(
                dependencies["db"].rate_item,
                req.media_item_id,
                req.profile_id,
                req.user_rating,
                req.status,
                req.notes,
                req.verdict,
                req.did_not_finish,
                req.worked,
                req.didnt,
                req.vibe_check,
            )
        except ValueError as e:
            raise dependencies["HTTPException"](status_code=400, detail=str(e)) from e
        if req.status == "seen":
            logged = (
                dependencies["vd"].legacy_rating(req.verdict, req.did_not_finish)
                if req.verdict
                else req.user_rating
            )
            if logged:
                dependencies["_log_outcome"](req.media_item_id, "rated", logged)
        return summary

    async def get_rating_vocab() -> "Dict[str, Any]":
        """The verdicts and "why" chips the Rate card shows, from one place."""
        return {
            "verdicts": [
                {"id": k, **{"label": v["label"]}}
                for k, v in dependencies["vd"].VERDICTS.items()
            ],
            "extreme": list(dependencies["vd"].EXTREME_VERDICTS),
            "chips": dependencies["vd"].CHIPS,
            "season_extra": dependencies["vd"].SEASON_DID_NOT_FINISH,
        }

    async def rate_season(req: "SeasonRateRequest") -> "Dict[str, Any]":
        """One optional per-season verdict on a TV show (upsert; empty clears it).

        A confirmed show verdict is never changed by this. Without one, the show
        gets a verdict derived from its seasons, marked inferred.
        """
        try:
            return await dependencies["asyncio"].to_thread(
                dependencies["db"].rate_season,
                req.media_item_id,
                req.profile_id,
                req.season_number,
                req.verdict,
                req.did_not_finish,
            )
        except ValueError as e:
            raise dependencies["HTTPException"](status_code=400, detail=str(e)) from e

    async def get_rating_details(
        item_id: "int", profile_id: "int" = 1
    ) -> "Dict[str, Any]":
        """This person's verdict and answers for one title, plus the vibe-check
        questions to show for it."""
        details = await dependencies["asyncio"].to_thread(
            dependencies["db"].get_rating_details, item_id, profile_id
        )
        questions = await dependencies["asyncio"].to_thread(
            dependencies["db"].get_vibe_check_questions, item_id
        )
        seasons = await dependencies["asyncio"].to_thread(
            dependencies["db"].get_season_details, item_id, profile_id
        )
        return {
            "media_item_id": item_id,
            "profile_id": profile_id,
            "details": details,
            "questions": questions,
            "seasons": seasons,
        }

    async def get_rating_pairs(
        profile_id: "int" = 1,
        media_type: "str" = "movie",
        limit: "int" = dependencies["Query"](default=10, ge=1, le=30),
        seed: "Optional[int]" = dependencies["Query"](default=None),
    ) -> "Dict[str, Any]":
        """A "This or that" round: pairs of films this person has seen, same genre."""
        seed_value = seed if isinstance(seed, int) else None
        return await dependencies["asyncio"].to_thread(
            dependencies["db"].get_pair_round, profile_id, media_type, limit, seed_value
        )

    async def answer_rating_pair(req: "PairAnswer") -> "Dict[str, Any]":
        """One "This or that" answer. Skip is client-side: nothing is stored."""
        try:
            return await dependencies["asyncio"].to_thread(
                dependencies["db"].record_pair_answer,
                req.profile_id,
                req.a_id,
                req.b_id,
                req.choice,
            )
        except ValueError as exc:
            raise dependencies["HTTPException"](status_code=400, detail=str(exc))

    async def get_inferred_rating_queue(
        profile_id: "int" = 1,
        media_type: "str" = "movie",
        limit: "int" = dependencies["Query"](default=100, ge=1, le=300),
    ) -> "Dict[str, Any]":
        """ "Sort out my 7s": old 1-10 ratings that only have a guessed verdict."""
        items = await dependencies["asyncio"].to_thread(
            dependencies["db"].get_inferred_rating_queue, profile_id, media_type, limit
        )
        for item in items:
            dependencies["_with_co_view"](item)
        dependencies["_with_on_list"](items)
        return {"profile_id": profile_id, "media_type": media_type, "items": items}

    async def get_rating_summary(item_id: "int") -> "Dict[str, Any]":
        """Both profiles' verdicts on one item, the combined score, and its tier."""
        return dependencies["db"].get_rating_summary(item_id)

    async def get_co_view_history(min_liked: "float" = 7.0) -> "Dict[str, Any]":
        """Films BOTH profiles rated >= min_liked — the virtual-user sequence.

        This is what a `together` recommendation is grounded in; it stays empty
        until enough films have been rated by both.
        """
        items = dependencies["db"].get_co_view_history(min_liked=min_liked)
        return {
            "count": len(items),
            "min_liked": min_liked,
            "items": items,
            "note": "Both profiles have liked these films — the shared taste signal."
            if items
            else "Nothing rated by both profiles yet. Rate together to build this.",
        }

    _handlers = {
        "nl_viewer_info": nl_viewer_info,
        "nl_prefs_get": nl_prefs_get,
        "nl_prefs_put": nl_prefs_put,
        "nl_react_get": nl_react_get,
        "nl_react_post": nl_react_post,
        "nl_hidden_get": nl_hidden_get,
        "nl_hidden_set": nl_hidden_set,
        "nl_watchlist": nl_watchlist,
        "nl_watchlist_state": nl_watchlist_state,
        "nl_watchlist_add": nl_watchlist_add,
        "nl_watchlist_remove": nl_watchlist_remove,
        "nl_interest_set": nl_interest_set,
        "get_history": get_history,
        "get_watch_later": get_watch_later,
        "add_watch_later": add_watch_later,
        "remove_watch_later": remove_watch_later,
        "done_watch_later": done_watch_later,
        "undo_watch_later": undo_watch_later,
        "get_profiles": get_profiles,
        "rename_profile": rename_profile,
        "list_rating_genres": list_rating_genres,
        "get_rating_queue": get_rating_queue,
        "rate_item": rate_item,
        "get_rating_vocab": get_rating_vocab,
        "rate_season": rate_season,
        "get_rating_details": get_rating_details,
        "get_rating_pairs": get_rating_pairs,
        "answer_rating_pair": answer_rating_pair,
        "get_inferred_rating_queue": get_inferred_rating_queue,
        "get_rating_summary": get_rating_summary,
        "get_co_view_history": get_co_view_history,
    }
    for method, path, handler_name, *rest in ROUTES:
        options = dict(rest[0]) if rest else {}
        if options.get("response_class") == "HTMLResponse":
            options["response_class"] = dependencies["HTMLResponse"]
        router.add_api_route(path, _handlers[handler_name], methods=[method], **options)
    for name, handler in _handlers.items():
        dependencies[name] = handler
    return router
