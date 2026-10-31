"""
Rating: the Rate tab queue, verdicts, season verdicts, "this or that" pairs, catalogue
search and TMDB import, origins (countries) and co-view history.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from services import verdicts as vd
from services.catalogue_search import tmdb_import, tmdb_search
from web import state
from web.helpers import _log_outcome, _with_on_list
from web.models import PairAnswer, RateRequest, SeasonRateRequest, TmdbImportRequest

router = APIRouter()


# Display names for the origin chips. The phrase written into the query must
# be one the recommender's parse_origin_intent() recognises.
ORIGIN_LABELS: Dict[str, str] = {
    # Per-country, not per-region: a chip labelled "South Asian" that counts
    # only Indian titles (1,432) while Bangladesh sits separately at 645 was
    # actively misleading. Each country counts its own rows, so the chip
    # number always matches the chip name. "South Asian" stays reachable as a
    # phrase in the free-text query, where it correctly unions five countries.
    "IN": "🇮🇳 Indian",
    "JP": "🇯🇵 Japanese",
    "KR": "🇰🇷 Korean",
    "CN": "🇨🇳 Chinese",
    "HK": "🇭🇰 Hong Kong",
    "TW": "🇹🇼 Taiwanese",
    "ES": "🇪🇸 Spanish",
    "FR": "🇫🇷 French",
    "DE": "🇩🇪 German",
    "IT": "🇮🇹 Italian",
    "BR": "🇧🇷 Brazilian",
    "MX": "🇲🇽 Mexican",
    "NG": "🇳🇬 Nigerian",
    "IR": "🇮🇷 Iranian",
    "EG": "🇪🇬 Egyptian",
    "TR": "🇹🇷 Turkish",
    "RU": "🇷🇺 Russian",
    "BD": "🇧🇩 Bangladeshi",
    "PK": "🇵🇰 Pakistani",
    "LK": "🇱🇰 Sri Lankan",
    "NP": "🇳🇵 Nepali",
    "SA": "🇸🇦 Saudi",
    "TH": "🇹🇭 Thai",
    "ID": "🇮🇩 Indonesian",
    "PH": "🇵🇭 Filipino",
    "VN": "🇻🇳 Vietnamese",
    "GR": "🇬🇷 Greek",
    "SE": "🇸🇪 Swedish",
    "DK": "🇩🇰 Danish",
    "NO": "🇳🇴 Norwegian",
    "FI": "🇫🇮 Finnish",
    "NL": "🇳🇱 Dutch",
    "PL": "🇵🇱 Polish",
    "UA": "🇺🇦 Ukrainian",
    "AR": "🇦🇷 Argentine",
    "CO": "🇨🇴 Colombian",
    "CL": "🇨🇱 Chilean",
}


# Multi-country groupings offered as a single chip. The phrase written into
# the query is one parse_origin_intent() recognises, so these route through
# exactly the same code path as typing them.
ORIGIN_GROUPS: Dict[str, List[str]] = {
    "South Asian": ["IN", "BD", "PK", "LK", "NP"],
    "Korean": ["KR"],
    "East Asian": ["JP", "CN", "HK", "TW", "KR"],
    "Latin American": ["BR", "MX", "AR", "CO", "CL"],
}


@router.get("/api/origins")
async def list_origins():
    """Countries of origin present in the catalogue, with counts.

    Only regions actually imported (utils/backfill_origin_country.py) appear,
    so the UI never offers a region with nothing to show. Feeds the "From"
    chips, which write an origin phrase into the free-text query — origin is a
    place, so it cannot be expressed as a genre.
    """
    with state.db.get_connection() as conn:
        rows = conn.execute(
            "SELECT origin_country, COUNT(*) AS n FROM media_items "
            "WHERE origin_country IS NOT NULL AND origin_country != '' "
            "GROUP BY origin_country ORDER BY n DESC;"
        ).fetchall()
    # A row can carry several codes ("IN,GB") for co-productions; count each.
    counts: Dict[str, int] = {}
    for raw, n in rows:
        for code in (raw or "").split(","):
            code = code.strip().upper()
            if code:
                counts[code] = counts.get(code, 0) + n
    labelled = [
        {"code": code, "name": ORIGIN_LABELS.get(code, code), "count": counts[code]}
        for code in sorted(counts, key=lambda c: -counts[c])
        if counts[code] >= 20
    ]

    # Regional groupings first in the UI, counted from their member countries
    # so the number on the chip is the number of films it will actually return.
    groups = []
    for label, members in ORIGIN_GROUPS.items():
        total = sum(counts.get(c, 0) for c in members)
        if total >= 20:
            groups.append({"code": label, "name": label, "count": total,
                           "countries": list(members)})
    return {"origins": groups + labelled}


@router.get("/api/ratings/genres")
async def list_rating_genres():
    """Genres worth offering as rating-queue chips.

    Derived from the catalogue rather than hardcoded in the UI: the old fixed
    list of 9 included "Documentary", which only 7 films in the catalogue
    carry — clicking it produced a 1–2 card queue that felt broken. Anything
    below the floor is omitted, and the count lets the UI show how deep each
    genre actually goes.
    """
    with state.db.get_connection() as conn:
        cursor = conn.cursor()
        rows = cursor.execute(
            "SELECT genres FROM media_items WHERE genres IS NOT NULL AND genres != ''"
        ).fetchall()

    counts: Dict[str, int] = {}
    for (raw,) in rows:
        for g in (raw or "").split(","):
            name = g.strip()
            if name:
                counts[name] = counts.get(name, 0) + 1

    # Below this a genre can't fill a 20-card page, so offering it is a trap.
    floor = 20
    ranked = sorted(
        ((name, n) for name, n in counts.items() if n >= floor),
        key=lambda kv: (-kv[1], kv[0]),
    )
    return {"genres": [{"name": name, "count": n} for name, n in ranked]}


@router.get("/api/ratings/queue")
async def get_rating_queue(
    profile_id: int = 1,
    media_type: str = "movie",
    limit: int = Query(default=25, ge=1, le=100),
    genres: Optional[str] = Query(default=None, description="Comma-separated genre filter"),
    seed: Optional[int] = Query(default=None, description="Rotation seed; omit to use a 10-min bucket"),
    balanced: bool = Query(default=False, description="Walk the genre wheel — best for building a taste profile fast"),
) -> Dict[str, Any]:
    """Next items for this profile to rate, highest-value first.

    Each profile rates independently — this is a to-do list *for the active
    profile*, not a list of their existing ratings. `genres` narrows to a slice
    (the UI's genre chips); `seed` rotates which films come up; `balanced` walks
    the genre wheel so a short rating session still covers many genres.
    """
    # FastAPI substitutes Query(...) defaults over HTTP, but a direct call
    # (tests, scripts) passes the Query object itself, which has no .split().
    # Normalise anything non-string to None.
    genre_list = (
        [g.strip() for g in genres.split(",") if g.strip()]
        if isinstance(genres, str) and genres
        else None
    )
    seed_value = seed if isinstance(seed, int) else None
    balanced_value = balanced if isinstance(balanced, bool) else False
    items = await asyncio.to_thread(
        state.db.get_rating_queue, profile_id, media_type, limit, genre_list, seed_value, balanced_value
    )

    # Attach what the OTHER profile already said, so the card can show
    # "you rated this 8/10" while the current profile rates it too.
    other_id = 2 if profile_id == 1 else 1
    for item in items:
        summary = state.db.get_rating_summary(item["id"])
        item["co_view"] = {
            "tier": summary["tier"],
            "tier_reason": summary["tier_reason"],
            "combined_score": summary["combined_score"],
            "disagreement": summary["disagreement"],
            "verdicts": summary["verdicts"],
        }
        item["other_profile_id"] = other_id
    _with_on_list(items)
    return {"profile_id": profile_id, "media_type": media_type, "items": items}


@router.post("/api/ratings")
async def rate_item(req: RateRequest) -> Dict[str, Any]:
    """Records one profile's verdict on one item (upsert).

    `status="not_seen"` means "we haven't watched this" — stored as its own
    state with no rating attached, never as a zero or a low score.
    """
    try:
        summary = await asyncio.to_thread(
            state.db.rate_item,
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
        raise HTTPException(status_code=400, detail=str(e)) from e
    if req.status == "seen":
        logged = vd.legacy_rating(req.verdict, req.did_not_finish) if req.verdict else req.user_rating
        if logged:
            _log_outcome(req.media_item_id, "rated", logged)
    return summary


@router.get("/api/ratings/vocab")
async def get_rating_vocab() -> Dict[str, Any]:
    """The verdicts and "why" chips the Rate card shows, from one place."""
    return {
        "verdicts": [{"id": k, **{"label": v["label"]}} for k, v in vd.VERDICTS.items()],
        "extreme": list(vd.EXTREME_VERDICTS),
        "chips": vd.CHIPS,
        "season_extra": vd.SEASON_DID_NOT_FINISH,
    }


@router.post("/api/ratings/season")
async def rate_season(req: SeasonRateRequest) -> Dict[str, Any]:
    """One optional per-season verdict on a TV show (upsert; empty clears it).

    A confirmed show verdict is never changed by this. Without one, the show
    gets a verdict derived from its seasons, marked inferred.
    """
    try:
        return await asyncio.to_thread(
            state.db.rate_season, req.media_item_id, req.profile_id, req.season_number, req.verdict, req.did_not_finish
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/ratings/details/{item_id}")
async def get_rating_details(item_id: int, profile_id: int = 1) -> Dict[str, Any]:
    """This person's verdict and answers for one title, plus the vibe-check
    questions to show for it."""
    details = await asyncio.to_thread(state.db.get_rating_details, item_id, profile_id)
    questions = await asyncio.to_thread(state.db.get_vibe_check_questions, item_id)
    seasons = await asyncio.to_thread(state.db.get_season_details, item_id, profile_id)
    return {"media_item_id": item_id, "profile_id": profile_id, "details": details, "questions": questions,
            "seasons": seasons}


@router.get("/api/ratings/pairs")
async def get_rating_pairs(
    profile_id: int = 1,
    media_type: str = "movie",
    limit: int = Query(default=10, ge=1, le=30),
    seed: Optional[int] = Query(default=None),
) -> Dict[str, Any]:
    """A "This or that" round: pairs of films this person has seen, same genre."""
    seed_value = seed if isinstance(seed, int) else None
    return await asyncio.to_thread(state.db.get_pair_round, profile_id, media_type, limit, seed_value)


@router.post("/api/ratings/pairs")
async def answer_rating_pair(req: PairAnswer) -> Dict[str, Any]:
    """One "This or that" answer. Skip is client-side: nothing is stored."""
    try:
        return await asyncio.to_thread(state.db.record_pair_answer, req.profile_id, req.a_id, req.b_id, req.choice)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/ratings/inferred-queue")
async def get_inferred_rating_queue(
    profile_id: int = 1,
    media_type: str = "movie",
    limit: int = Query(default=100, ge=1, le=300),
) -> Dict[str, Any]:
    """"Sort out my 7s": old 1-10 ratings that only have a guessed verdict."""
    items = await asyncio.to_thread(state.db.get_inferred_rating_queue, profile_id, media_type, limit)
    for item in items:
        _with_co_view(item)
    _with_on_list(items)
    return {"profile_id": profile_id, "media_type": media_type, "items": items}


def _with_co_view(item: Dict[str, Any]) -> Dict[str, Any]:
    """Adds both profiles' verdicts, the shape the Rate card already reads."""
    summary = state.db.get_rating_summary(item["id"])
    item["co_view"] = {
        "tier": summary["tier"],
        "tier_reason": summary["tier_reason"],
        "combined_score": summary["combined_score"],
        "disagreement": summary["disagreement"],
        "verdicts": summary["verdicts"],
    }
    return item


@router.get("/api/catalogue/search")
async def search_catalogue(
    q: str = Query(..., min_length=1, description="Title text; 2+ characters"),
    media_type: str = Query(default="all", description="movie | tv | all"),
    limit: int = Query(default=10, ge=1, le=25),
    profile_id: Optional[int] = Query(default=None, description="Adds this profile's own verdict"),
) -> Dict[str, Any]:
    """Search-to-rate (design doc §1): accent- and punctuation-insensitive title
    search over the catalogue, exact matches first, then by vote count."""
    mtype = media_type if isinstance(media_type, str) else "all"
    pid = profile_id if isinstance(profile_id, int) else None
    lim = limit if isinstance(limit, int) else 10
    items = await asyncio.to_thread(state.catalogue_search.search, q, mtype, lim, pid)
    return {"query": q, "items": _with_on_list([_with_co_view(it) for it in items])}


@router.get("/api/catalogue/tmdb-search")
async def search_tmdb(q: str = Query(..., min_length=2), media_type: str = "all") -> Dict[str, Any]:
    """Fallback when a title isn't in the catalogue. Needs TMDB_API_KEY + internet."""
    if not state.api_key:
        raise HTTPException(status_code=503, detail="TMDB_API_KEY is not set on the server.")
    try:
        items = await asyncio.to_thread(tmdb_search, state.api_key, state.db, q, media_type)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"query": q, "items": items}


@router.post("/api/catalogue/tmdb-import")
async def import_from_tmdb(req: TmdbImportRequest, profile_id: Optional[int] = None) -> Dict[str, Any]:
    """Adds one TMDB title to the catalogue and returns it ready to rate."""
    if not state.api_key:
        raise HTTPException(status_code=503, detail="TMDB_API_KEY is not set on the server.")
    try:
        item_id = await asyncio.to_thread(tmdb_import, state.api_key, state.db, req.media_type, req.tmdb_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    state.catalogue_search.invalidate()
    item = state.db.get_item_by_id(item_id) or {"id": item_id}
    item["in_library"] = bool(item.pop("sources", None))
    return _with_co_view(item)


@router.get("/api/ratings/summary/{item_id}")
async def get_rating_summary(item_id: int) -> Dict[str, Any]:
    """Both profiles' verdicts on one item, the combined score, and its tier."""
    return state.db.get_rating_summary(item_id)


@router.get("/api/ratings/co-view")
async def get_co_view_history(min_liked: float = 7.0) -> Dict[str, Any]:
    """Films BOTH profiles rated >= min_liked — the virtual-user sequence.

    This is what a `together` recommendation is grounded in; it stays empty
    until enough films have been rated by both.
    """
    items = state.db.get_co_view_history(min_liked=min_liked)
    return {
        "count": len(items),
        "min_liked": min_liked,
        "items": items,
        "note": (
            "Both profiles have liked these films — the shared taste signal."
            if items
            else "Nothing rated by both profiles yet. Rate together to build this."
        ),
    }
