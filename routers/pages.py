# ruff: noqa: F821 -- endpoint annotations resolve from the injected app dependency namespace.
"""Pages API routes and endpoint handlers."""

from fastapi import APIRouter

_ANNOTATION_NAMES = ("Any", "Optional", "Request")
ROUTES = [
    ("GET", "/", "serve_index", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/who", "nl_who_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/who/{key}", "nl_who_choose"),
    ("GET", "/nl/tv/who", "nl_tv_who_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/tv/who/{key}", "nl_tv_who_choose"),
    ("GET", "/nl/", "nl_home", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/movies", "nl_movies_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/shows", "nl_shows_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/title/{item_id}", "nl_title_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/show/{item_id}", "nl_show_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/watchlist", "nl_watchlist_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/choose", "nl_choose_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/admin", "nl_admin_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/me", "nl_me_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/tv/", "nl_tv_home", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/tv/search", "nl_tv_search", {"response_class": "HTMLResponse"}),
    (
        "GET",
        "/nl/tv/title/{item_id}",
        "nl_tv_title",
        {"response_class": "HTMLResponse"},
    ),
    ("GET", "/nl/tv/show/{item_id}", "nl_tv_show", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/tv/watchlist", "nl_tv_watchlist", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/tv/choose", "nl_tv_choose", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/tv/me", "nl_tv_me", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/type", "nl_type_enter", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/type/{code}", "nl_type_page", {"response_class": "HTMLResponse"}),
    (
        "GET",
        "/nl/cast/receiver",
        "nl_cast_receiver",
        {"response_class": "HTMLResponse"},
    ),
    ("GET", "/nl/play/{item_id}", "nl_play_page", {"response_class": "HTMLResponse"}),
]


def build_router(dependencies):
    """Build the pages router with live app dependencies."""
    globals().update({name: dependencies[name] for name in _ANNOTATION_NAMES})
    router = APIRouter(tags=["pages"])

    async def serve_index(request: "Request") -> "Any":
        """Serves the mobile-first PWA interface."""
        local_ip = dependencies["get_local_ip"]()
        return dependencies["templates"].TemplateResponse(
            request=request,
            name="index.html",
            context={
                "local_ip": local_ip,
                "group_label": dependencies["nl_people"].group_label,
            },
        )

    async def nl_who_page(request: "Request", next: "Optional[str]" = None) -> "Any":
        """Who's watching? Without configured people there is nothing to choose: straight home."""
        if not dependencies["nl_people"].configured:
            return dependencies["RedirectResponse"]("/nl/", status_code=302)
        return dependencies["templates"].TemplateResponse(
            request=request,
            name="nl/who.html",
            context={
                "active": "",
                "choices": dependencies["nl_people"].choices(),
                "next": dependencies["safe_next"](next, "/nl/"),
                "viewer": None,
                "people_configured": True,
                "tv": False,
                "choose_base": "/nl/who/",
            },
        )

    async def nl_who_choose(key: "str", next: "Optional[str]" = None) -> "Any":
        return dependencies["_nl_choose"](key, next, tv=False)

    async def nl_tv_who_page(request: "Request", next: "Optional[str]" = None) -> "Any":
        if not dependencies["nl_people"].configured:
            return dependencies["RedirectResponse"]("/nl/tv/", status_code=302)
        return dependencies["templates"].TemplateResponse(
            request=request,
            name="nl/tv/who.html",
            context={
                "page": "who",
                "choices": dependencies["nl_people"].choices(),
                "next": dependencies["safe_next"](next, "/nl/tv/"),
                "viewer": None,
                "people_configured": True,
                "tv": True,
                "group_label": dependencies["nl_people"].group_label,
                "choose_base": "/nl/tv/who/",
            },
        )

    async def nl_tv_who_choose(key: "str", next: "Optional[str]" = None) -> "Any":
        return dependencies["_nl_choose"](key, next, tv=True)

    async def nl_home(request: "Request") -> "Any":
        return dependencies["_nl_page"](request, "nl/home.html", {"active": "home"})

    async def nl_movies_page(request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request,
            "nl/browse.html",
            {"active": "movies", "kind": "movie", "heading": "Movies"},
        )

    async def nl_shows_page(request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request,
            "nl/browse.html",
            {"active": "shows", "kind": "tv", "heading": "Shows"},
        )

    async def nl_title_page(item_id: "int", request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/title.html", {"active": "", "item_id": item_id}
        )

    async def nl_show_page(item_id: "int", request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/show.html", {"active": "shows", "item_id": item_id}
        )

    async def nl_watchlist_page(request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/watchlist.html", {"active": "watchlist"}
        )

    async def nl_choose_page(request: "Request") -> "Any":
        return dependencies["_nl_page"](request, "nl/choose.html", {"active": "choose"})

    async def nl_admin_page(request: "Request") -> "Any":
        """Web only: there is deliberately no TV version and no link from the TV pages."""
        return dependencies["_nl_page"](request, "nl/admin.html", {"active": "admin"})

    async def nl_me_page(request: "Request") -> "Any":
        """The viewer's own settings: their languages and subtitles, and who's watching."""
        return dependencies["_nl_page"](request, "nl/me.html", {"active": "me"})

    async def nl_tv_home(request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/tv/home.html", {"page": "home"}, tv=True
        )

    async def nl_tv_search(request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/tv/search.html", {"page": "search"}, tv=True
        )

    async def nl_tv_title(item_id: "int", request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/tv/title.html", {"page": "title", "item_id": item_id}, tv=True
        )

    async def nl_tv_show(item_id: "int", request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/tv/show.html", {"page": "show", "item_id": item_id}, tv=True
        )

    async def nl_tv_watchlist(request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/tv/watchlist.html", {"page": "watchlist"}, tv=True
        )

    async def nl_tv_choose(request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/tv/choose.html", {"page": "choose"}, tv=True
        )

    async def nl_tv_me(request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/tv/me.html", {"page": "me"}, tv=True
        )

    async def nl_type_enter(
        request: "Request",
        code: "Optional[str]" = dependencies["Query"](None, max_length=32),
    ) -> "Any":
        """The phone page without a code: type the 6 characters from the TV (a code in the address goes on to its page)."""
        if code is not None:
            norm = dependencies["normalise_code"](code)
            if norm:
                return dependencies["RedirectResponse"](
                    f"/nl/type/{norm}", status_code=302
                )
            return dependencies["_type_page"](
                request,
                None,
                "That code doesn't look right. It has 6 letters and digits.",
                400,
            )
        return dependencies["_type_page"](request, None)

    async def nl_type_page(code: "str", request: "Request") -> "Any":
        """The phone's keyboard page for one TV. No cookies, no who's-watching: the code is the only credential."""
        try:
            norm = dependencies["nl_type"].check(
                code, dependencies["_client_address"](request)
            )
        except dependencies["TypingError"] as e:
            return dependencies["_type_page"](request, None, e.message, e.status)
        return dependencies["_type_page"](request, norm)

    async def nl_cast_receiver(request: "Request") -> "Any":
        """The custom Chromecast Web Receiver (M8). Runs on the Chromecast, so it never has the viewer cookie and
        never calls our API: everything it shows arrives in the load request from the sender, and the artwork URL
        carries a stream token."""
        resp = dependencies["templates"].TemplateResponse(
            request=request, name="nl/cast_receiver.html", context={}
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp

    async def nl_play_page(item_id: "int", request: "Request") -> "Any":
        """Minimal full-screen player page for one movie or episode."""
        return dependencies["_nl_page"](
            request,
            "nl/player.html",
            {"item_id": item_id},
            tv=request.query_params.get("tv") == "1",
        )

    _handlers = {
        "serve_index": serve_index,
        "nl_who_page": nl_who_page,
        "nl_who_choose": nl_who_choose,
        "nl_tv_who_page": nl_tv_who_page,
        "nl_tv_who_choose": nl_tv_who_choose,
        "nl_home": nl_home,
        "nl_movies_page": nl_movies_page,
        "nl_shows_page": nl_shows_page,
        "nl_title_page": nl_title_page,
        "nl_show_page": nl_show_page,
        "nl_watchlist_page": nl_watchlist_page,
        "nl_choose_page": nl_choose_page,
        "nl_admin_page": nl_admin_page,
        "nl_me_page": nl_me_page,
        "nl_tv_home": nl_tv_home,
        "nl_tv_search": nl_tv_search,
        "nl_tv_title": nl_tv_title,
        "nl_tv_show": nl_tv_show,
        "nl_tv_watchlist": nl_tv_watchlist,
        "nl_tv_choose": nl_tv_choose,
        "nl_tv_me": nl_tv_me,
        "nl_type_enter": nl_type_enter,
        "nl_type_page": nl_type_page,
        "nl_cast_receiver": nl_cast_receiver,
        "nl_play_page": nl_play_page,
    }
    for method, path, handler_name, *rest in ROUTES:
        options = dict(rest[0]) if rest else {}
        if options.get("response_class") == "HTMLResponse":
            options["response_class"] = dependencies["HTMLResponse"]
        router.add_api_route(path, _handlers[handler_name], methods=[method], **options)
    for name, handler in _handlers.items():
        dependencies[name] = handler
    return router
