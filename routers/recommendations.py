"""Routes for the recommendations domain."""

from ._build import build_router as build_router_impl

ROUTES = [
    ("POST", "/api/recommend", "get_recommendations"),
    ("POST", "/api/discover/start", "discover_start"),
    ("POST", "/api/discover/answer", "discover_answer"),
    ("POST", "/api/discover/finish", "discover_finish"),
    ("POST", "/api/feedback", "record_feedback"),
    ("GET", "/api/feedback/stats", "feedback_stats"),
    ("GET", "/api/vibe/status", "vibe_status"),
    ("GET", "/api/nl/picks", "nl_picks_row"),
    ("GET", "/api/nl/choose/options", "nl_choose_options"),
    ("POST", "/api/nl/choose", "nl_choose_pick"),
    ("POST", "/api/nl/choose/chosen/{item_id}", "nl_choose_chosen"),
    ("GET", "/api/nl/vetoes", "nl_vetoes_list"),
    ("POST", "/api/nl/veto/{item_id}", "nl_veto_set"),
    ("DELETE", "/api/nl/veto/{item_id}", "nl_veto_clear"),
]


def build_router(handlers):
    return build_router_impl(handlers, ROUTES, tags="recommendations")
