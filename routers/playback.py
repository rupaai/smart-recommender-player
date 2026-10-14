# ruff: noqa: F821 -- endpoint annotations resolve from the injected app dependency namespace.
"""Playback API routes and endpoint handlers."""

from fastapi import APIRouter

_ANNOTATION_NAMES = (
    "Any",
    "CancelWatchRequest",
    "Dict",
    "FinishWatchRequest",
    "NLPactBody",
    "NLProgressRequest",
    "NLShowPrefs",
    "NLSkipIntro",
    "NLSubtitleDownload",
    "NLTypeSession",
    "NLTypeText",
    "NLUnwatchBody",
    "Optional",
    "Request",
    "StartWatchRequest",
)
ROUTES = [
    ("GET", "/api/nl/play/{item_id}", "nl_play_info"),
    ("GET", "/api/nl/cast/config", "nl_cast_config"),
    ("GET", "/api/nl/cast/play/{item_id}", "nl_cast_play"),
    ("GET", "/api/nl/subtitles/{item_id}", "nl_subtitle_search"),
    ("POST", "/api/nl/subtitles/{item_id}", "nl_subtitle_download"),
    ("POST", "/api/nl/progress", "nl_progress"),
    ("POST", "/api/nl/played/{jellyfin_id}", "nl_mark_played"),
    ("DELETE", "/api/nl/played/{jellyfin_id}", "nl_mark_unplayed"),
    ("GET", "/api/nl/library", "nl_library"),
    ("GET", "/api/nl/home", "nl_home_data"),
    ("GET", "/api/nl/title/{item_id}", "nl_title_data"),
    ("GET", "/api/nl/title/{item_id}/extras", "nl_title_extras"),
    ("GET", "/api/nl/title/{item_id}/more", "nl_title_more"),
    ("GET", "/api/nl/show/{item_id}", "nl_show_data"),
    ("GET", "/api/nl/show/{item_id}/pact", "nl_show_pact_get"),
    ("PUT", "/api/nl/show/{item_id}/pact", "nl_show_pact_set"),
    ("POST", "/api/nl/unwatch/{item_id}", "nl_unwatch"),
    ("GET", "/api/nl/show/{item_id}/prefs", "nl_show_prefs_get"),
    ("PUT", "/api/nl/show/{item_id}/prefs", "nl_show_prefs_set"),
    ("GET", "/api/nl/show/{item_id}/skip-intro", "nl_show_skip_intro_get"),
    ("PUT", "/api/nl/show/{item_id}/skip-intro", "nl_show_skip_intro_set"),
    ("GET", "/api/nl/player-extras/{item_id}", "nl_player_extras"),
    ("GET", "/api/nl/next/{item_id}", "nl_next_episode"),
    ("GET", "/api/nl/search", "nl_search"),
    ("POST", "/api/nl/type/session", "nl_type_session"),
    ("GET", "/api/nl/type/{code}/poll", "nl_type_poll"),
    ("DELETE", "/api/nl/type/{code}", "nl_type_close"),
    ("POST", "/api/nl/type/{code}", "nl_type_send"),
    ("POST", "/api/watch/start", "start_watch"),
    ("POST", "/api/watch/finish", "finish_watch"),
    ("POST", "/api/watch/cancel", "cancel_watch"),
]


def build_router(dependencies):
    """Build the playback router with live app dependencies."""
    globals().update({name: dependencies[name] for name in _ANNOTATION_NAMES})
    router = APIRouter(tags=["playback"])

    async def nl_play_info(
        request: "Request",
        item_id: "int",
        audio: "Optional[int]" = dependencies["Query"](
            None, ge=0, description="Jellyfin audio stream index"
        ),
        subtitle: "Optional[int]" = dependencies["Query"](
            None,
            ge=-1,
            description="Jellyfin subtitle stream index to burn in; -1 = none",
        ),
        start: "float" = dependencies["Query"](
            0.0, ge=0, description="Start position in seconds"
        ),
        caps: "Optional[str]" = dependencies["Query"](
            None,
            max_length=300,
            description="Codecs this browser can play, e.g. v=h264.hevc;a=aac;h=h264.aac",
        ),
        source: "Optional[int]" = dependencies["Query"](
            None,
            ge=1,
            description="media_sources.id: which episode of a show (default: the first)",
        ),
        client: "Optional[str]" = dependencies["Query"](
            None,
            pattern="^(tv)$",
            description="tv = the Android TV app (TV profile; caps ignored)",
        ),
        max_kbps: "Optional[int]" = dependencies["Query"](
            None,
            ge=0,
            le=200000,
            description="Quality for this viewing: 8000, 4000, 2000 or 1000 kbit/s; 0 = Auto; absent = the person's default",
        ),
    ) -> "Dict[str, Any]":
        """How to play one movie or episode: a proxied stream URL and its kind (file or hls),
        its audio and subtitle tracks, and where the viewer left off. For the viewer (who's watching):
        default tracks from their language prefs, and Resume from their own progress (the further of the
        two for "Both of us", with a note when they are in different places)."""
        return await dependencies["_nl_play"](
            request, item_id, audio, subtitle, start, caps, source, client, max_kbps
        )

    async def nl_cast_config() -> "Dict[str, Any]":
        """Whether casting is set up (cast_base_url configured) and which receiver app the sender launches."""
        return dependencies["nl_cast"].public()

    async def nl_cast_play(
        request: "Request",
        item_id: "int",
        audio: "Optional[int]" = dependencies["Query"](
            None, ge=0, description="Jellyfin audio stream index"
        ),
        subtitle: "Optional[int]" = dependencies["Query"](
            None,
            ge=-1,
            description="Jellyfin subtitle stream index to burn in; -1 = none",
        ),
        start: "float" = dependencies["Query"](
            0.0, ge=0, description="Start position in seconds"
        ),
        source: "Optional[int]" = dependencies["Query"](
            None, ge=1, description="media_sources.id: which episode of a show"
        ),
    ) -> "Dict[str, Any]":
        """/api/nl/play for a Chromecast: the cast profile (H.264/AAC), every URL absolute on the LAN
        (northern_lights.cast_base_url) and carrying a 6-hour token for this one item (?t=), so the
        Chromecast needs no cookies. Adds a `cast` block (content type, backdrop, poster). No Jellyfin key."""
        if not dependencies["nl_cast"].enabled:
            raise dependencies["HTTPException"](
                status_code=503,
                detail="Casting isn't set up: set northern_lights.cast_base_url to this server's LAN address.",
            )
        info = await dependencies["_nl_play"](
            request, item_id, audio, subtitle, start, None, source, "cast"
        )
        return dependencies["cast_payload"](
            info, dependencies["nl_cast"], dependencies["nl_cast_tokens"]
        )

    async def nl_subtitle_search(
        item_id: "int",
        language: "str" = dependencies["Query"](
            "eng",
            pattern="^[a-z]{3}$",
            description="Three-letter ISO 639-2 code, e.g. eng",
        ),
        source: "Optional[int]" = dependencies["Query"](
            None, ge=1, description="media_sources.id: which episode of a show"
        ),
    ) -> "Any":
        """Search OpenSubtitles (through Jellyfin's plugin) for subtitles of one title or episode.
        Results are best first (file-hash matches, then most downloaded). The Jellyfin key stays here."""
        item = dependencies["_nl_subtitle_item"](item_id, source)
        try:
            found = await dependencies["asyncio"].to_thread(
                dependencies["jellyfin_playback"].search_subtitles, item, language
            )
        except (
            dependencies["SubtitleError"],
            dependencies["ItemNotFound"],
            dependencies["JellyfinUnavailable"],
        ) as e:
            return dependencies["_nl_subtitle_failure"](e)
        found["code"] = "ok" if found["results"] else "no_results"
        if not found["results"]:
            found["message"] = f"No subtitles found for {found['language_name']}."
        return found

    async def nl_subtitle_download(
        item_id: "int",
        req: "NLSubtitleDownload",
        source: "Optional[int]" = dependencies["Query"](None, ge=1),
    ) -> "Any":
        """Have Jellyfin download one of the searched subtitles and save it with the file. The page then
        re-reads /api/nl/play for the new track."""
        if not dependencies["valid_subtitle_id"](req.subtitle_id):
            return dependencies["JSONResponse"](
                status_code=400,
                content={
                    "detail": "That subtitle id isn't valid.",
                    "code": "bad_request",
                },
            )
        item = dependencies["_nl_subtitle_item"](item_id, source)
        try:
            await dependencies["asyncio"].to_thread(
                dependencies["jellyfin_playback"].download_subtitle,
                item,
                req.subtitle_id,
            )
        except (
            dependencies["SubtitleError"],
            dependencies["ItemNotFound"],
            dependencies["JellyfinUnavailable"],
        ) as e:
            return dependencies["_nl_subtitle_failure"](e)
        return {"success": True}

    async def nl_progress(
        req: "NLProgressRequest", request: "Request"
    ) -> "Dict[str, Any]":
        """Tells Jellyfin where the viewer is (start, progress, stop) so it can offer 'Resume'.
        Each person's progress goes to their own Jellyfin user; "Both of us" reports to both."""
        if not dependencies["valid_jellyfin_id"](req.jellyfin_id):
            raise dependencies["HTTPException"](status_code=400, detail="Bad item id.")
        viewer = dependencies["_nl_viewer_or_default"](request)
        try:
            await dependencies["asyncio"].to_thread(
                dependencies["_report_to_users"],
                viewer.jellyfin_users,
                lambda user: dependencies["jellyfin_playback"].report_progress(
                    req.event,
                    req.jellyfin_id,
                    req.play_session_id,
                    req.media_source_id,
                    req.position_seconds,
                    req.paused,
                    req.play_method,
                    **{"user": user} if user else {},
                ),
            )
        except dependencies["JellyfinUnavailable"] as e:
            raise dependencies["HTTPException"](status_code=503, detail=str(e))
        if (
            req.event == "stop"
            and req.media_item_id
            and viewer.is_both
            and dependencies["nl_together"].together_possible(
                dependencies["nl_people"].people
            )
            and dependencies["nl_together"].is_finished(
                req.position_seconds, req.duration_seconds, req.ended
            )
        ):
            await dependencies["asyncio"].to_thread(
                dependencies["db"].mark_seen_together, req.media_item_id, req.source_id
            )
        return {"success": True}

    async def nl_mark_played(
        jellyfin_id: "str", request: "Request"
    ) -> "Dict[str, Any]":
        """Mark a title as watched in Jellyfin (for both users when "Both of us")."""
        return await dependencies["_nl_set_played"](jellyfin_id, True, request)

    async def nl_mark_unplayed(
        jellyfin_id: "str", request: "Request"
    ) -> "Dict[str, Any]":
        """Mark a title as not watched in Jellyfin."""
        return await dependencies["_nl_set_played"](jellyfin_id, False, request)

    async def nl_library(
        request: "Request",
        type: "str" = dependencies["Query"]("movie", pattern="^(movie|tv)$"),
        unwatched: "bool" = dependencies["Query"](
            False, description="only titles nobody at the screen has watched"
        ),
    ) -> "Dict[str, Any]":
        """Cards for every movie or show that has a file. Movies partly watched carry a progress
        bar, the latest additions a "New" badge. `unwatched` (M7b) keeps the titles nobody at the screen has
        watched; `unwatched_applied` says whether Jellyfin could tell. Works with Jellyfin down (no progress then)."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        return await dependencies["asyncio"].to_thread(
            dependencies["_as_user"],
            viewer.jellyfin_users[0],
            dependencies["nl_browse"].library_page,
            type,
            viewer.jellyfin_users,
            unwatched,
        )

    async def nl_home_data(request: "Request") -> "Dict[str, Any]":
        """Home: Continue Watching (Jellyfin Resume of the viewer), Recently Added, Recently Watched and the
        newest movies and shows. Titles this viewer hid (M7b) are left out of every row except Continue Watching."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        hidden = await dependencies["asyncio"].to_thread(
            dependencies["db"].get_hidden_ids, viewer.key
        )
        return await dependencies["asyncio"].to_thread(
            dependencies["_as_user"],
            viewer.jellyfin_users[0],
            dependencies["nl_browse"].home,
            viewer.jellyfin_users,
            hidden,
        )

    async def nl_title_data(item_id: "int", request: "Request") -> "Dict[str, Any]":
        viewer = dependencies["_nl_viewer_or_default"](request)
        data = await dependencies["asyncio"].to_thread(
            dependencies["_as_user"],
            viewer.jellyfin_users[0],
            dependencies["nl_browse"].title,
            item_id,
            viewer.jellyfin_users,
        )
        if not data:
            raise dependencies["HTTPException"](
                status_code=404, detail="That title isn't in the library."
            )
        data["hidden"] = item_id in await dependencies["asyncio"].to_thread(
            dependencies["db"].get_hidden_ids, viewer.key
        )
        together = dependencies["nl_together"].together_possible(
            dependencies["nl_people"].people
        )
        data["together"] = bool(
            together
            and data.get("media_type") != "tv"
            and (
                0
                in await dependencies["asyncio"].to_thread(
                    dependencies["db"].get_seen_together, item_id
                )
            )
        )
        return data

    async def nl_title_extras(item_id: "int", request: "Request") -> "Dict[str, Any]":
        """Cast & crew, the Trailer (a YouTube key + address, never a TMDB or Jellyfin key) and Tech details.
        Each part is just empty when its source (Jellyfin, TMDB) is away; only an unknown title is an error."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        data = await dependencies["asyncio"].to_thread(
            dependencies["_as_user"],
            viewer.jellyfin_users[0],
            dependencies["nl_extras"].title_extras,
            item_id,
        )
        if not data:
            raise dependencies["HTTPException"](
                status_code=404, detail="That title isn't in the library."
            )
        return data

    async def nl_title_more(item_id: "int", request: "Request") -> "Dict[str, Any]":
        """ "More like this": library titles close to this one (TMDB similar, the recommender's vibe index, shared
        genres). `items` may be empty; it degrades part by part and never fails for a missing source."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        data = await dependencies["asyncio"].to_thread(
            dependencies["_as_user"],
            viewer.jellyfin_users[0],
            dependencies["nl_extras"].more_like_this,
            item_id,
        )
        if data is None:
            raise dependencies["HTTPException"](
                status_code=404, detail="That title isn't in the library."
            )
        return data

    async def nl_show_data(item_id: "int", request: "Request") -> "Dict[str, Any]":
        """A show's seasons and episodes with watched state, and its Next up episode (the viewer's;
        for "Both of us" the first person's)."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        data = await dependencies["asyncio"].to_thread(
            dependencies["_as_user"],
            viewer.jellyfin_users[0],
            dependencies["nl_browse"].show,
            item_id,
        )
        if not data:
            raise dependencies["HTTPException"](
                status_code=404, detail="That show isn't in the library."
            )
        await dependencies["asyncio"].to_thread(
            dependencies["_add_together_to_show"], data, item_id, viewer
        )
        data["autoplay"] = await dependencies["asyncio"].to_thread(
            dependencies["_autoplay_for"], item_id, viewer, data.get("autoplay")
        )
        return data

    async def nl_show_pact_get(item_id: "int") -> "Dict[str, Any]":
        """Is "We watch this together" on for this show? Only offered with two or more people configured."""
        possible = dependencies["nl_together"].together_possible(
            dependencies["nl_people"].people
        )
        return {
            "available": possible,
            "together": bool(
                possible
                and await dependencies["asyncio"].to_thread(
                    dependencies["db"].get_pact, item_id
                )
            ),
        }

    async def nl_show_pact_set(item_id: "int", body: "NLPactBody") -> "Dict[str, Any]":
        """Switches the watch-together pact for a show on or off (it belongs to the pair, not to one person)."""
        if not dependencies["nl_together"].together_possible(
            dependencies["nl_people"].people
        ):
            raise dependencies["HTTPException"](
                status_code=400,
                detail="Watching together needs two people in the config.",
            )
        try:
            value = await dependencies["asyncio"].to_thread(
                dependencies["db"].set_pact, item_id, body.together
            )
        except ValueError as e:
            raise dependencies["HTTPException"](status_code=404, detail=str(e))
        return {"available": True, "together": value}

    async def nl_unwatch(
        item_id: "int", body: "NLUnwatchBody", request: "Request"
    ) -> "Dict[str, Any]":
        """U6, "Mark unwatched for me": resets the played flag and resume position of ONE person's Jellyfin
        user for an episode or movie. On "Both of us" `person` says whose; nobody else's data is touched. The
        together mark of that title goes too, since it is no longer seen by both."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        if viewer.implicit:
            person_user = None
            person_id = None
        else:
            wanted = (body.person or "").strip().lower() or (
                viewer.primary.id if not viewer.is_both else ""
            )
            if not wanted:
                raise dependencies["HTTPException"](
                    status_code=400, detail="Say whose watched state to reset."
                )
            person = next((p for p in viewer.people if p.id == wanted), None)
            if person is None:
                raise dependencies["HTTPException"](
                    status_code=403 if dependencies["nl_people"].get(wanted) else 400,
                    detail="That person isn't at the screen.",
                )
            person_user, person_id = (person.jellyfin_user_id, person.id)
        item = dependencies["_nl_subtitle_item"](item_id, body.source_id)
        try:
            jf_id = await dependencies["asyncio"].to_thread(
                dependencies["jellyfin_playback"]._subtitle_item_id, item
            )
            await dependencies["asyncio"].to_thread(
                dependencies["_as_user"],
                person_user,
                dependencies["jellyfin_playback"].reset_user_data,
                jf_id,
            )
        except dependencies["ItemNotFound"] as e:
            raise dependencies["HTTPException"](status_code=404, detail=str(e))
        except dependencies["JellyfinUnavailable"] as e:
            raise dependencies["HTTPException"](status_code=503, detail=str(e))
        await dependencies["asyncio"].to_thread(
            dependencies["db"].clear_seen_together,
            item_id,
            item["sources"][0]["id"] if item.get("media_type") == "tv" else 0,
        )
        return {"success": True, "person": person_id}

    async def nl_show_prefs_get(item_id: "int") -> "Dict[str, Any]":
        return {
            "autoplay": await dependencies["asyncio"].to_thread(
                dependencies["db"].get_show_autoplay, item_id
            )
        }

    async def nl_show_prefs_set(
        item_id: "int", prefs: "NLShowPrefs"
    ) -> "Dict[str, Any]":
        """Autoplay of the next episode, per show (on by default)."""
        try:
            value = await dependencies["asyncio"].to_thread(
                dependencies["db"].set_show_autoplay, item_id, prefs.autoplay
            )
        except ValueError as e:
            raise dependencies["HTTPException"](status_code=404, detail=str(e))
        return {"autoplay": value}

    async def nl_show_skip_intro_get(
        item_id: "int", request: "Request"
    ) -> "Dict[str, Any]":
        """Does this viewer always skip this show's intro? (per viewer, off by default)"""
        viewer = dependencies["_nl_viewer_or_default"](request)
        return {
            "skip_intro": await dependencies["asyncio"].to_thread(
                dependencies["db"].get_show_skip_intro, item_id, viewer.key
            )
        }

    async def nl_show_skip_intro_set(
        item_id: "int", body: "NLSkipIntro", request: "Request"
    ) -> "Dict[str, Any]":
        """The per-show "always skip intro" switch of the person at the screen (Both of us has its own)."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        try:
            value = await dependencies["asyncio"].to_thread(
                dependencies["db"].set_show_skip_intro,
                item_id,
                viewer.key,
                body.skip_intro,
            )
        except ValueError as e:
            raise dependencies["HTTPException"](status_code=404, detail=str(e))
        return {"skip_intro": value}

    async def nl_player_extras(
        request: "Request",
        item_id: "int",
        source: "Optional[int]" = dependencies["Query"](
            None, ge=1, description="media_sources.id: which episode of a show"
        ),
    ) -> "Dict[str, Any]":
        """What the player draws over the picture, all read here (the Jellyfin key never leaves):
        `trickplay` (seek-bar thumbnail sheets: geometry + a /nl/jf/ URL template, or null), `chapters`
        ([{start_seconds, name}]) and `segments` ([{type intro|outro, start_seconds, end_seconds}], from
        Jellyfin's MediaSegments, e.g. the Intro Skipper plugin). `skip_intro`: this viewer's switch for the show.
        Jellyfin down, an old Jellyfin or a missing plugin only leave the parts empty; never an error."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        item = dependencies["_nl_subtitle_item"](item_id, source)
        extras = await dependencies["asyncio"].to_thread(
            dependencies["_as_user"],
            viewer.jellyfin_users[0],
            dependencies["_player_extras_for"],
            item,
        )
        is_show = item.get("media_type") == "tv"
        extras["skip_intro"] = bool(
            is_show
            and await dependencies["asyncio"].to_thread(
                dependencies["db"].get_show_skip_intro, item_id, viewer.key
            )
        )
        extras["is_show"] = is_show
        return extras

    async def nl_next_episode(
        request: "Request",
        item_id: "int",
        source: "int" = dependencies["Query"](..., ge=1),
    ) -> "Dict[str, Any]":
        """The episode after `source` for the player's next-episode card (has_next false at the end)."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        data = await dependencies["asyncio"].to_thread(
            dependencies["_as_user"],
            viewer.jellyfin_users[0],
            dependencies["nl_browse"].next_episode,
            item_id,
            source,
        )
        if isinstance(data, dict) and "autoplay" in data:
            data["autoplay"] = await dependencies["asyncio"].to_thread(
                dependencies["_autoplay_for"], item_id, viewer, data["autoplay"]
            )
        return data

    async def nl_search(
        request: "Request",
        q: "str" = dependencies["Query"]("", max_length=100),
        limit: "int" = dependencies["Query"](20, ge=1, le=40),
    ) -> "Dict[str, Any]":
        """Movies, shows and episodes matching the text. Each result is Ready (in the library,
        with a link) or Not in library yet (with a link to the Download tab of the main app)."""
        viewer = dependencies["_nl_viewer_or_default"](request)
        results = await dependencies["asyncio"].to_thread(
            dependencies["_as_user"],
            viewer.jellyfin_users[0],
            dependencies["nl_browse"].search,
            q,
            limit,
        )
        return {"query": q, "results": results}

    async def nl_type_session(
        request: "Request",
        body: "NLTypeSession" = dependencies["Body"](
            default_factory=dependencies["NLTypeSession"]
        ),
    ) -> "Any":
        """The TV search page asks for a typing code. Returns {code, key, expires_in}: the code goes on the screen (and in
        the QR), the key stays on the TV (it is how only that TV reads what the phone types). With the old key the old
        session is replaced: one session per TV."""
        try:
            return dependencies["nl_type"].create(
                dependencies["_client_address"](request), body.previous_key
            )
        except dependencies["TypingError"] as e:
            return dependencies["_type_error"](e)

    async def nl_type_poll(
        code: "str",
        request: "Request",
        after: "int" = dependencies["Query"](0, ge=0),
        x_nl_type_key: "Optional[str]" = dependencies["Header"](None),
    ) -> "Any":
        """The TV reads the latest phone text. Needs the session key (header X-NL-Type-Key)."""
        try:
            return dependencies["nl_type"].poll(
                code, x_nl_type_key, dependencies["_client_address"](request), after
            )
        except dependencies["TypingError"] as e:
            return dependencies["_type_error"](e)

    async def nl_type_close(
        code: "str",
        request: "Request",
        x_nl_type_key: "Optional[str]" = dependencies["Header"](None),
    ) -> "Any":
        """The TV leaves the search page: its code stops working."""
        try:
            dependencies["nl_type"].close(
                code, x_nl_type_key, dependencies["_client_address"](request)
            )
        except dependencies["TypingError"] as e:
            return dependencies["_type_error"](e)
        return {"ok": True}

    async def nl_type_send(
        code: "str", body: "NLTypeText", request: "Request"
    ) -> "Any":
        """The phone's text for the TV (the whole text each time, so a missed update heals itself). 404 once the code is
        gone, 422 for more than 100 characters, 429 when going too fast."""
        try:
            return dependencies["nl_type"].send(
                code, body.text, dependencies["_client_address"](request), body.submit
            )
        except dependencies["TypingError"] as e:
            return dependencies["_type_error"](e)

    async def start_watch(req: "StartWatchRequest") -> "Dict[str, Any]":
        """Begins watching a movie session."""
        session_id = dependencies["db"].start_active_watch(req.media_item_id)
        dependencies["_log_outcome"](req.media_item_id, "played")
        active = dependencies["db"].get_active_watch()
        return {"success": True, "session_id": session_id, "active": active}

    async def finish_watch(
        req: "FinishWatchRequest", request: "Request"
    ) -> "Dict[str, Any]":
        """Completes watch session with star rating & review notes, updating taste profile.
        With who's-watching set up, the watch and rating are logged for the viewer's recommender
        profile(s) ("Both of us" = both) instead of always landing on profile 1."""
        with dependencies["db"].get_connection() as conn:
            row = conn.execute(
                "SELECT media_item_id FROM active_watch_sessions WHERE id = ?;",
                (req.session_id,),
            ).fetchone()
        viewer = (
            dependencies["_nl_viewer"](request)
            if dependencies["nl_people"].configured
            else None
        )
        profile_ids = req.profile_ids or (viewer.profile_ids if viewer else [])
        if profile_ids:
            try:
                done = dependencies["db"].mark_watch_done_for_profiles(
                    req.session_id, profile_ids, req.user_rating, req.notes
                )
            except ValueError as e:
                raise dependencies["HTTPException"](status_code=400, detail=str(e))
            record_id = req.session_id if done else None
        else:
            record_id = dependencies["db"].finish_active_watch(
                session_id=req.session_id, user_rating=req.user_rating, notes=req.notes
            )
        if not record_id:
            raise dependencies["HTTPException"](
                status_code=404, detail="Active session not found."
            )
        if req.user_rating and row:
            dependencies["_log_outcome"](row["media_item_id"], "rated", req.user_rating)
        if row:
            try:
                dependencies["db"].mark_watch_later_done(
                    row["media_item_id"], "watched"
                )
            except Exception as e:
                print(
                    f"[WatchLater] could not mark {row['media_item_id']} done: {e}",
                    flush=True,
                )
        return {"success": True, "record_id": record_id}

    async def cancel_watch(req: "CancelWatchRequest") -> "Dict[str, Any]":
        """Cancels an active watch session."""
        dependencies["db"].cancel_active_watch(req.session_id)
        return {"success": True}

    _handlers = {
        "nl_play_info": nl_play_info,
        "nl_cast_config": nl_cast_config,
        "nl_cast_play": nl_cast_play,
        "nl_subtitle_search": nl_subtitle_search,
        "nl_subtitle_download": nl_subtitle_download,
        "nl_progress": nl_progress,
        "nl_mark_played": nl_mark_played,
        "nl_mark_unplayed": nl_mark_unplayed,
        "nl_library": nl_library,
        "nl_home_data": nl_home_data,
        "nl_title_data": nl_title_data,
        "nl_title_extras": nl_title_extras,
        "nl_title_more": nl_title_more,
        "nl_show_data": nl_show_data,
        "nl_show_pact_get": nl_show_pact_get,
        "nl_show_pact_set": nl_show_pact_set,
        "nl_unwatch": nl_unwatch,
        "nl_show_prefs_get": nl_show_prefs_get,
        "nl_show_prefs_set": nl_show_prefs_set,
        "nl_show_skip_intro_get": nl_show_skip_intro_get,
        "nl_show_skip_intro_set": nl_show_skip_intro_set,
        "nl_player_extras": nl_player_extras,
        "nl_next_episode": nl_next_episode,
        "nl_search": nl_search,
        "nl_type_session": nl_type_session,
        "nl_type_poll": nl_type_poll,
        "nl_type_close": nl_type_close,
        "nl_type_send": nl_type_send,
        "start_watch": start_watch,
        "finish_watch": finish_watch,
        "cancel_watch": cancel_watch,
    }
    for method, path, handler_name, *rest in ROUTES:
        options = dict(rest[0]) if rest else {}
        if options.get("response_class") == "HTMLResponse":
            options["response_class"] = dependencies["HTMLResponse"]
        router.add_api_route(path, _handlers[handler_name], methods=[method], **options)
    for name, handler in _handlers.items():
        dependencies[name] = handler
    return router
