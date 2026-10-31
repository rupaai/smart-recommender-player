"""
Request bodies (pydantic models) the API accepts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field



# --- Request Models ---
class RecommendRequest(BaseModel):
    mood_id: int = 8
    era: Optional[str] = None
    duration: Optional[str] = None
    page: int = Field(default=1, ge=1, description="Page index (1-based)")
    page_size: int = Field(default=10, ge=1, le=50, description="Items per page")
    top_n: Optional[int] = Field(default=None, description="Legacy limit parameter alias")
    include_watched: bool = False
    query: Optional[str] = None
    media_type: str = "movie"  # 'movie' or 'tv'
    sub_mood: Optional[str] = None
    genres: Optional[List[str]] = None
    min_rating: Optional[float] = None
    profile: str = "together"  # 'me' | 'partner' | 'together'
    exclude_ids: Optional[List[int]] = None
    session_id: Optional[int] = None  # feedback log: pass back for "Show more" pages


class DiscoverStartRequest(RecommendRequest):
    ask: bool = True  # False = "Just show me" / pop-up turned off in filters


class DiscoverAnswerRequest(BaseModel):
    session_id: int
    question_id: str
    option_id: str  # an option id, or "skip"


class DiscoverFinishRequest(BaseModel):
    session_id: int


class FeedbackRequest(BaseModel):
    media_item_id: int
    outcome: str  # 'opened' | 'dismissed' | 'downloaded' | 'played' | 'rated'
    session_id: Optional[int] = None
    rating: Optional[float] = Field(default=None, ge=1, le=10)


class StartWatchRequest(BaseModel):
    media_item_id: int


class FinishWatchRequest(BaseModel):
    session_id: int
    user_rating: Optional[float] = None
    verdict: Optional[str] = None
    notes: Optional[str] = None
    profile_ids: Optional[List[int]] = None  # who watched; default: the Northern Lights viewer (M4)


class CancelWatchRequest(BaseModel):
    session_id: int


# --- Co-view rating models (multi-profile, schema v3) ---
class RateRequest(BaseModel):
    media_item_id: int
    profile_id: int = 1
    user_rating: Optional[float] = Field(default=None, ge=1, le=10)
    status: str = "seen"  # 'seen' | 'not_seen'
    notes: Optional[str] = None
    # Verdict rating (plan: "Better ratings"). With a verdict, user_rating is ignored.
    verdict: Optional[str] = None  # rewatch | loved | liked | meh | not_for_me
    did_not_finish: bool = False
    worked: Optional[List[str]] = None
    didnt: Optional[List[str]] = None
    vibe_check: Optional[Dict[str, Any]] = None


class SeasonRateRequest(BaseModel):
    media_item_id: int
    profile_id: int = 1
    season_number: int = Field(ge=1)
    # Same five verdicts as a show. No verdict and no did_not_finish clears it.
    verdict: Optional[str] = None
    did_not_finish: bool = False


class TmdbImportRequest(BaseModel):
    media_type: str  # 'movie' | 'tv'
    tmdb_id: int


class WatchLaterRequest(BaseModel):
    media_item_id: int
    profile_id: int


class RenameProfileRequest(BaseModel):
    name: str


class TorrentSearchRequest(BaseModel):
    query: str
    sort_by_size: bool = True  # legacy; ignored when `sort` is given
    sort: Optional[str] = None  # 'best' | 'smallest' | 'quality'
    media_type: str = "movie"  # 'movie' or 'tv'


class TorrentDownloadRequest(BaseModel):
    magnet_link: str
    movie_id: Optional[int] = None
    movie_title: str
    media_type: str = "movie"  # 'movie' or 'tv'


class TorrentCancelRequest(BaseModel):
    download_id: Optional[str] = None
    movie_id: Optional[int] = None  # legacy clients; only safe when exactly one copy is active


class RecRequest(BaseModel):
    query: Optional[str] = None
    mood_id: int = 8
    media_type: Optional[str] = None  # 'movie', 'tv', or none for both
    profile: str = "together"         # 'me' | 'partner' | 'together'
    genres: Optional[List[str]] = None
    era: Optional[str] = None
    duration: Optional[str] = None
    include_watched: bool = False
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=50)


class WishRequest(BaseModel):
    text: str = ""


class ProfileBody(BaseModel):
    summary: str
    likes: List[str] = []
    dislikes: List[str] = []
    model: Optional[str] = None
    edited: Optional[bool] = None


class NLTrackChoice(BaseModel):
    source: Optional[int] = Field(None, ge=1, description="media_sources.id the tracks belong to (the episode)")
    audio: Optional[Dict[str, Any]] = Field(None, description="the chosen audio track {index, language, label, ...}")
    subtitle: Optional[Dict[str, Any]] = Field(None, description='the chosen subtitle track, or {"off": true}')


class NLSubtitleDownload(BaseModel):
    subtitle_id: str = Field(..., min_length=1, max_length=512)


class NLProgressRequest(BaseModel):
    jellyfin_id: str
    play_session_id: str = ""
    media_source_id: str = ""
    position_seconds: float = Field(0.0, ge=0)
    paused: bool = False
    event: Literal["start", "progress", "stop"]
    play_method: str = "DirectPlay"
    # M9: which title this is (the page knows), the length and whether the player reached the end.
    media_item_id: Optional[int] = Field(None, ge=1)
    source_id: Optional[int] = Field(None, ge=1)
    duration_seconds: Optional[float] = Field(None, ge=0)
    ended: bool = False


class NLReactBody(BaseModel):
    reaction: str


class NLHiddenBody(BaseModel):
    hidden: bool


class NLPactBody(BaseModel):
    together: bool


class NLUnwatchBody(BaseModel):
    source_id: Optional[int] = None   # which episode of a show
    person: Optional[str] = None      # whose data; needed on "Both of us"


class NLShowPrefs(BaseModel):
    autoplay: bool


class NLSkipIntro(BaseModel):
    skip_intro: bool


# --- Northern Lights picks for two (M6) ---
class NLWatchlistAdd(BaseModel):
    media_item_id: int
    scope: str = "shared"   # "shared" (default) or "me" (Just me)


class NLInterestBody(BaseModel):
    state: Optional[str] = None   # yes | maybe | pass | null (clear)
    person: Optional[str] = None  # whose answer; needed on "Both of us"


# --- Northern Lights choosing for two (M9b) ---
class NLChooseBody(BaseModel):
    mood: Optional[int] = None
    genre: Optional[str] = Field(None, max_length=40)
    max_minutes: Optional[int] = None
    exclude: List[int] = Field(default_factory=list, max_length=60)
    mode: str = "pick"             # "pick" or "yeses" (a random title both said yes to)
    tz: Optional[int] = None       # the browser's UTC offset in minutes east of UTC


class NLVetoBody(BaseModel):
    kind: str                      # "tonight" or "never"
    person: Optional[str] = None   # whose "never"; needed on "Both of us"
    tz: Optional[int] = None


# --- Northern Lights phone keyboard for TV search (M8b) ---
class NLTypeSession(BaseModel):
    previous_key: Optional[str] = Field(None, max_length=64)


class NLTypeText(BaseModel):
    text: str = Field("", max_length=1000)   # the real limit (100 characters) is applied by services/nl_type.py
    submit: bool = False


class PairAnswer(BaseModel):
    profile_id: int = 1
    a_id: int
    b_id: int
    choice: str  # a | b | same
