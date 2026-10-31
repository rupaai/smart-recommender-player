"""
Recommendations: /api/recommend, the Discover questions pop-up (/api/discover/*), feedback,
vibe status and the Wishmatch API (/api/rec/*). Which engine answers is `recommender.engine`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from services import vibe_vocab

from fastapi import APIRouter, HTTPException

from services import vibe_moods
from services.mood_popup import MoodSession
from services.recommender_service import MOOD_MAPPINGS
from services.wishmatch_movies import WishmatchMovies
from web import state
from web.helpers import _with_on_list
from web.models import (
    DiscoverAnswerRequest,
    DiscoverFinishRequest,
    DiscoverStartRequest,
    FeedbackRequest,
    ProfileBody,
    RecommendRequest,
    RecRequest,
    WishRequest,
)

router = APIRouter()


@router.get("/api/moods")
async def get_moods() -> List[Dict[str, Any]]:
    """Returns available mood options including sub-moods for the interactive picker."""
    moods_list = []
    for mood_id, info in MOOD_MAPPINGS.items():
        moods_list.append({
            "id": mood_id,
            "name": info["name"],
            "vibe": info.get("vibe", ""),
            "genres": info["genres"],
            "submoods": info.get("submoods", []),
        })
    return moods_list


@router.post("/api/recommend")
async def get_recommendations(req: RecommendRequest) -> Dict[str, Any]:
    """Runs recommendation pipeline with dynamic Tier 2 deep profiling, taste scoring, Bayesian rating, MMR diversity, and pagination.

    Executes off the main asyncio thread to prevent blocking event loop.
    """
    # Respect top_n legacy parameter if passed by client, otherwise use page_size
    effective_page_size = req.top_n if req.top_n is not None else req.page_size
    kwargs = _recommend_kwargs(req, effective_page_size)

    popup = state.popup_sessions.get(req.session_id)
    if popup is not None:
        # "Show more" after the questions pop-up: page through the same
        # answered pool instead of starting over.
        if hasattr(popup, "page"):     # a Wishmatch questions session keeps its own order
            results = await asyncio.to_thread(popup.page, req.page, effective_page_size)
        else:
            results = await asyncio.to_thread(
                state.recommender_service.finalize, popup.ranked(), req.page, effective_page_size,
                spread_exempt=_spread_exempt(popup))
            results["engine"] = "legacy"
    else:
        # Engine switch (recommender.engine): legacy / wishmatch, each falling back to the
        # untouched legacy recommender. Per-item exclusion is applied after routing so it works for all.
        results = await asyncio.to_thread(state.recommender.route, explicit_genres=req.genres, **kwargs)

    # exclude_ids: per-tap "not this one" — drop items the user dismissed so
    # the next request surfaces something else instead of the same list.
    if req.exclude_ids and results.get("items"):
        excluded = set(req.exclude_ids)
        results["items"] = [i for i in results["items"] if i.get("id") not in excluded]
        results["total_results"] = len(results["items"])
        results["has_more"] = False
    # Always advertise pagination. The engines already emit `has_more`, but a
    # result path that omits it (or an error envelope) previously left the UI
    # with no way to tell "that's everything" from "there's more" — and the UI
    # then hid its Show-more button, silently capping the user at one page.
    results.setdefault("has_more", False)
    results.setdefault("page", 1)
    results.setdefault("page_size", len(results.get("items") or []))
    _with_on_list(results.get("items"))

    # Feedback log (schema v7): one session per Discover request, one
    # impression per title shown. Logging must never break recommendations.
    try:
        session_id = req.session_id if req.session_id and state.db.session_exists(req.session_id) else None
        if session_id is None:
            session_id = state.db.create_discover_session(
                profile=req.profile,
                media_type=req.media_type,
                query_text=req.query,
                request=req.model_dump(exclude={"session_id", "exclude_ids", "page", "page_size", "top_n"}),
                engine=results.get("engine"),
            )
        start_rank = (req.page - 1) * effective_page_size + 1
        state.db.log_impressions(session_id, results.get("items") or [], start_rank)
        results["session_id"] = session_id
    except Exception as e:  # noqa: BLE001
        print(f"[Feedback] impression logging failed: {e}", flush=True)
    return results


def _recommend_kwargs(req: RecommendRequest, page_size: int) -> Dict[str, Any]:
    # Resolve mood genres so every engine receives the same context signal
    # (legacy already does this internally, so it is byte-equivalent for it).
    genres = req.genres
    if genres is None:
        genres = MOOD_MAPPINGS.get(req.mood_id, MOOD_MAPPINGS[8])["genres"]
    return dict(
        mood_id=req.mood_id,
        exclude_watched=not req.include_watched,
        era_preference=req.era,
        duration_preference=req.duration,
        page=req.page,
        page_size=page_size,
        sub_mood=req.sub_mood,
        query=req.query,
        media_type=req.media_type,
        genres_override=genres or None,
        min_rating_override=req.min_rating,
        variety=True,   # cold-start freshness: seeded 10-minute rotation
        profile=req.profile,
        remember_shown=True,   # push down what Discover showed lately and nobody opened
        skip_session_id=req.session_id,
    )


def _cluster_labels() -> Dict[int, str]:
    try:
        with state.db.get_connection() as conn:
            return {r[0]: r[1] for r in conn.execute("SELECT id, label FROM vibe_clusters")}
    except Exception:  # noqa: BLE001
        return {}


def _pool_for(kwargs: Dict[str, Any]) -> List[Dict[str, Any]]:
    res = state.recommender_service.recommend(**{**kwargs, "page": 1}, return_pool=True)
    return res.get("pool") or []


_MOOD_LABELS = {m["id"]: m["label"] for m in vibe_moods.MOODS}


def _mood_list(popup: Any, n: int = 3) -> Optional[List[Dict[str, Any]]]:
    """Top moods as [{id, label, p}] for the UI; None for sessions without a mood belief."""
    top = getattr(popup, "top_moods", None)
    if top is None:
        return None
    return [{"id": m, "label": _MOOD_LABELS.get(m, m), "p": round(p, 2)} for m, p in top(n)]


def _spread_exempt(popup: Any) -> set:
    """Genres a questions session asked for, which the genre spread leaves alone:
    the request's genre chips, plus Animation / Family once the answers point
    clearly at "Animated, any age" or "Fun for everyone"."""
    kwargs = (getattr(popup, "request", None) or {}).get("kwargs") or {}
    exempt = set(kwargs.get("genres_override") or [])
    belief = getattr(popup, "belief", None) or {}
    if belief.get("animated_any", 0.0) + belief.get("family_all", 0.0) >= 0.3:
        exempt |= {"Animation", "Family"}
    return exempt


def _popup_response(popup: Any, page_size: int, vibe: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if hasattr(popup, "page"):         # Wishmatch questions session (step 5)
        results = popup.page(1, page_size)
    else:
        results = state.recommender_service.finalize(popup.ranked(), 1, page_size, spread_exempt=_spread_exempt(popup))
        results["engine"] = "legacy"
    results.update({"session_id": popup.id, "question": popup.public_question()})
    moods = _mood_list(popup)
    if moods is not None:
        results["moods"] = moods
    _with_on_list(results.get("items"))
    if vibe is not None:
        results["vibe"] = vibe
    try:
        state.db.update_discover_session(popup.id, **popup.log_fields())
        if popup.done:
            state.db.log_impressions(popup.id, results.get("items") or [], 1)
    except Exception as e:  # noqa: BLE001
        print(f"[Feedback] pop-up logging failed: {e}", flush=True)
    return results


def _wishmatch_discover_start(req: DiscoverStartRequest, kwargs: Dict[str, Any], page_size: int) -> Dict[str, Any]:
    """Discover with questions picked for this request (Wishmatch step 5)."""
    session_id = state.db.create_discover_session(
        profile=req.profile, media_type=req.media_type, query_text=req.query,
        request=req.model_dump(exclude={"session_id", "exclude_ids", "page", "page_size", "top_n"}),
        engine="wishmatch+questions",
    )
    popup = state.wishmatch_movies.session(session_id, {**kwargs, "explicit_genres": req.genres})
    if req.ask:
        popup.next_question()
    popup.done = popup.current is None
    state.popup_sessions.put(popup)
    out = _popup_response(popup, page_size)
    if req.ask:
        out["question_skipped"] = None if popup.current is not None else (
            "no_results" if not popup.picks else "picks_agree")
    return out


@router.post("/api/discover/start")
async def discover_start(req: DiscoverStartRequest) -> Dict[str, Any]:
    """First results plus, when asking is on, the first question (always)."""
    page_size = req.top_n if req.top_n is not None else req.page_size
    kwargs = _recommend_kwargs(req, page_size)
    if state.recommender.engine == "wishmatch":
        try:
            return await asyncio.to_thread(_wishmatch_discover_start, req, kwargs, page_size)
        except Exception as e:  # noqa: BLE001 - fall back to the mood-map pop-up
            print(f"[Router] Wishmatch questions failed ({e}); using the mood-map pop-up.", flush=True)
    res = await asyncio.to_thread(state.recommender_service.recommend, **{**kwargs, "page": 1}, return_pool=True)
    pool = res.get("pool") or []
    session_id = state.db.create_discover_session(
        profile=req.profile, media_type=req.media_type, query_text=req.query,
        request=req.model_dump(exclude={"session_id", "exclude_ids", "page", "page_size", "top_n"}),
        engine="legacy+popup",
    )
    popup = MoodSession(session_id, {**kwargs, "kwargs": kwargs}, pool, _cluster_labels(),
                        state.recommender_service.vibe_index, mood_weight=state.recommender_cfg.get("mood_weight"))
    if req.ask and pool:
        await asyncio.to_thread(popup.next_question, force=True)
    popup.done = popup.current is None
    state.popup_sessions.put(popup)
    out = await asyncio.to_thread(_popup_response, popup, page_size, res.get("vibe"))
    if req.ask:
        # MoodSession always asks A3 when there is a pool, so only an empty pool skips.
        out["question_skipped"] = None if popup.current is not None else ("no_results" if not pool else None)
    return out


@router.post("/api/discover/answer")
async def discover_answer(req: DiscoverAnswerRequest) -> Dict[str, Any]:
    """Applies one answer (or "skip"), re-ranks, and picks the next question."""
    popup = state.popup_sessions.get(req.session_id)
    if popup is None:
        raise HTTPException(status_code=404, detail="That question session has expired. Search again.")
    try:
        popup.answer(req.question_id, req.option_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    kwargs = popup.request["kwargs"]
    # (PopupSession's D1 "who's on the sofa" pool rebuild has no MoodSession equivalent: the mood
    # bank has no profile question, so the pool never changes during a mood session.)
    if popup.should_stop():
        popup.done = True
    else:
        await asyncio.to_thread(popup.next_question)
        popup.done = popup.current is None
    return await asyncio.to_thread(_popup_response, popup, kwargs.get("page_size") or 20)


@router.post("/api/discover/finish")
async def discover_finish(req: DiscoverFinishRequest) -> Dict[str, Any]:
    """"Just show me": stop asking and keep the answers so far."""
    popup = state.popup_sessions.get(req.session_id)
    if popup is None:
        raise HTTPException(status_code=404, detail="That question session has expired. Search again.")
    popup.done = True
    popup.current = None
    return await asyncio.to_thread(_popup_response, popup, popup.request["kwargs"].get("page_size") or 20)


@router.post("/api/feedback")
async def record_feedback(req: FeedbackRequest) -> Dict[str, Any]:
    """Records what happened to a recommended title: opened (synopsis tapped),
    dismissed ("not this one"), downloaded, played or rated."""
    try:
        updated = state.db.record_outcome(req.media_item_id, req.outcome, req.session_id, req.rating)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"updated": updated}


@router.get("/api/feedback/stats")
async def feedback_stats() -> Dict[str, Any]:
    """How much training signal has been logged so far."""
    return state.db.get_feedback_stats()


@router.get("/api/vibe/status")
async def vibe_status() -> Dict[str, Any]:
    """Whether the vibe index (models/vibe/, built on the Mac) is loaded."""
    index = state.recommender_service.vibe_index
    out = index.status() if index else {"ready": False}
    out["mood_data"] = await asyncio.to_thread(state.db.get_mood_data_counts)
    return out


def _rec_domain(domain: str) -> WishmatchMovies:
    if domain != "movies":
        raise HTTPException(status_code=404, detail=f"No recommender for {domain!r}.")
    return state.wishmatch_movies


@router.post("/api/rec/{domain}/recommend")
async def rec_recommend(domain: str, req: RecRequest) -> Dict[str, Any]:
    """Wishmatch picks, whatever recommender.engine says; each item carries its score and reason words."""
    engine = _rec_domain(domain)
    return await asyncio.to_thread(
        engine.recommend, query=req.query, mood_id=req.mood_id, media_type=req.media_type, profile=req.profile,
        genres_override=req.genres, era_preference=req.era, duration_preference=req.duration,
        exclude_watched=not req.include_watched, page=req.page, page_size=req.page_size)


@router.post("/api/rec/{domain}/wish")
async def rec_wish(domain: str, req: WishRequest) -> Dict[str, Any]:
    """How Wishmatch read these words: mood dials, feelings, what to avoid, words it did not know."""
    # In a worker thread: the wish reader can wait seconds on Ollama and must not stall other requests.
    return await asyncio.to_thread(_rec_domain(domain).read_wish, req.text)


@router.get("/api/rec/{domain}/profile/{person_id}")
async def rec_profile_get(domain: str, person_id: int) -> Dict[str, Any]:
    """A person's written taste profile: {summary, likes, dislikes, model, edited, updated_at}. 404 if none."""
    _rec_domain(domain)
    profile = await asyncio.to_thread(state.db.get_wishmatch_profile, person_id)
    if not profile:
        raise HTTPException(status_code=404, detail="No taste profile for this person yet.")
    return profile


@router.put("/api/rec/{domain}/profile/{person_id}")
async def rec_profile_put(domain: str, person_id: int, body: ProfileBody) -> Dict[str, Any]:
    """Store a person's written taste profile. likes / dislikes keep only words from services/vibe_vocab.
    `edited` says whether a person wrote it: by default true for a hand edit, false when `model` is given
    (utils/wishmatch_profiles.py --push). Returns the stored profile."""
    _rec_domain(domain)
    summary = body.summary.strip()
    if not summary:
        raise HTTPException(status_code=422, detail="summary must not be empty.")
    allowed = set(vibe_vocab.FEELS) | set(vibe_vocab.GOOD_FOR)

    def keep(words: List[str]) -> List[str]:
        return list(dict.fromkeys(w.strip().lower() for w in words if w.strip().lower() in allowed))

    edited = body.edited if body.edited is not None else not body.model
    await asyncio.to_thread(state.db.set_wishmatch_profile, person_id, summary, keep(body.likes), keep(body.dislikes),
                            body.model, edited)
    return await asyncio.to_thread(state.db.get_wishmatch_profile, person_id)


@router.get("/api/rec/status")
async def rec_status() -> Dict[str, Any]:
    """Which engine answers, and how many titles Wishmatch can use."""
    out: Dict[str, Any] = {"engine": state.recommender.engine}
    try:
        out["movies"] = await asyncio.to_thread(state.wishmatch_movies.status)
    except Exception as e:  # noqa: BLE001
        out["movies"] = {"error": str(e)}
    return out
