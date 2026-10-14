"""Shared route-table builder."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


def build_router(handlers, routes, *, tags):
    router = APIRouter(tags=[tags])
    for route in routes:
        method, path, handler_name, *rest = route
        kwargs = dict(rest[0]) if rest else {}
        if kwargs.get("response_class") == "HTMLResponse":
            kwargs["response_class"] = HTMLResponse
        router.add_api_route(path, handlers[handler_name], methods=[method], **kwargs)
    return router
