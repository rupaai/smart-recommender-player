"""
Wishmatch for this app: turns a recommend call into a Wishmatch request and the
picks back into the result shape every screen already reads.

`recommend(**kwargs)` takes the same arguments as the legacy
RecommenderService.recommend, so it can stand in for it on any screen. Each
returned item is the full catalogue row plus:
  wishmatch_score, wish_match, taste_fit, crowd_fit   the score and its main parts
  taste_reasons                            the reason words (the cards already show this field)
  vibe                                     Haiku's one-line description
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import os

from wishmatch import LLMWishReader, OllamaClient, QuestionWorder, Request, Wishmatch, word_question

from .adapter import MovieAdapter
from .session import WishmatchSession

KINDS = ("movie", "tv")


class WishmatchMovies:
    def __init__(self, db: Any, mood_mappings: Optional[Dict[int, Dict[str, Any]]] = None,
                 adapter: Optional[MovieAdapter] = None):
        self.db = db
        self.adapter = adapter or MovieAdapter(db)
        self.ollama = self._ollama(self.adapter.rules().get("wish_reader") or {})
        cfg = self.adapter.rules().get("wish_reader") or {}
        reader = LLMWishReader(self.ollama.generate, noun="films and TV shows",
                               wait_seconds=float(cfg.get("wait_seconds", 8)),
                               avoid_groups=list(cfg.get("avoid_groups") or ["flags"])) if self.ollama else None
        self.engine = Wishmatch(self.adapter, reader=reader)
        # Question wording waits less than a wish: a slow answer falls back to a fixed sentence this
        # time and is remembered, so each question is worded by the model once.
        qcfg = self.adapter.rules().get("questions") or {}
        self.question_client = self._ollama({**(self.adapter.rules().get("wish_reader") or {}),
                                             "max_tokens": 60}) if qcfg.get("model_wording", True) else None
        self.question_worder = QuestionWorder(self.question_client.generate, noun="films and TV shows",
                                              wait_seconds=float(qcfg.get("wording_timeout_seconds", 6))) \
            if self.question_client else None
        self.moods = mood_mappings or {}

    @staticmethod
    def _ollama(cfg: Dict[str, Any]) -> Optional[OllamaClient]:
        """The wish reader's Ollama connection (wishmatch.wish_reader in recommender_rules.yaml), or None.
        OLLAMA_URL picks the server (Homelab: http://ollama:11434); no URL = word lookup only."""
        url = os.getenv("OLLAMA_URL") or str(cfg.get("url") or "")
        if not cfg.get("enabled", True) or not url or not cfg.get("model"):
            return None
        options = {"temperature": 0, "num_predict": int(cfg.get("max_tokens", 80))}
        if cfg.get("threads"):
            options["num_thread"] = int(cfg["threads"])
        return OllamaClient(url, str(cfg["model"]), timeout=float(cfg.get("timeout_seconds", 60)),
                            keep_alive=str(cfg.get("keep_alive", "5m")), options=options)

    def start(self) -> None:
        """At server start: ask Ollama to download the wish reader's model if it lacks it (in the background).
        Under Homelab only in the environments `pull_in` names (prod), so dev never downloads it."""
        cfg = self.adapter.rules().get("wish_reader") or {}
        env = os.getenv("HOMELAB_ENV", "")
        if self.ollama and cfg.get("auto_pull", True) and (not env or env in (cfg.get("pull_in") or ["prod"])):
            self.ollama.ensure_model(background=True)

    # --- requests -------------------------------------------------------------
    def wish_text(self, query: Optional[str], mood_id: Optional[int], sub_mood: Optional[str] = None) -> str:
        """The typed words; with none, the mood card's vibe words stand in for a wish."""
        if query and query.strip():
            return query.strip()
        mood = self.moods.get(mood_id or 0) or {}
        vibe = str(mood.get("vibe") or "")
        if vibe.strip().lower() == "anything goes":
            vibe = ""
        return " ".join(w for w in (vibe, sub_mood or "") if w).strip()

    def people_for(self, profile: str) -> tuple:
        """('me' | 'partner' | 'together') -> (person ids, viewer first; together?)."""
        ids = self.adapter.people() or [1, 2]
        if profile == "partner" and len(ids) > 1:
            ids = [ids[1], ids[0]] + ids[2:]
        return ids, profile not in ("me", "partner")

    def build_request(self, *, query: Optional[str] = None, mood_id: int = 8, sub_mood: Optional[str] = None,
                      media_type: Optional[str] = None, profile: str = "together",
                      exclude_watched: bool = True, genres: Optional[List[str]] = None,
                      era: Optional[str] = None, duration: Optional[str] = None,
                      min_rating: Optional[float] = None, only_ids: Optional[List[int]] = None,
                      exclude_ids: Optional[List[int]] = None) -> Request:
        people, together = self.people_for(profile)
        return Request(
            text=self.wish_text(query, mood_id, sub_mood),
            people=people,
            together=together,
            kinds=[media_type] if media_type in KINDS else None,
            only_ids=list(only_ids) if only_ids is not None else None,
            filters={"exclude_seen": exclude_watched, "genres": genres or [], "era": era,
                     "duration": duration, "min_rating": min_rating, "exclude_ids": exclude_ids or []},
        )

    def word_question(self, about: str, options: List[str]) -> Dict[str, Any]:
        """A question's wording: the model's (remembered), or a fixed sentence."""
        if self.question_worder is None:
            return word_question(about, options, noun="films and TV shows")
        return self.question_worder.word(about, options)

    def run(self, request: Request) -> Dict[str, Any]:
        result = self.engine.recommend(request)
        picks = result["picks"]
        return {"picks": picks, "wish": result["wish"], "candidates": result["candidates"]}

    def rows(self, picks: List[Any]) -> List[Dict[str, Any]]:
        """Full catalogue rows for these picks, in order, with the Wishmatch fields added."""
        ids = [p.item.id for p in picks]
        because_ids = [p.because for p in picks if p.because is not None]
        rows = self.db.get_items_by_ids(list(set(ids) | set(because_ids))) if ids else {}
        max_reasons = int((self.adapter.rules().get("explain") or {}).get("max_reasons", 3))
        out = []
        for p in picks:
            row = dict(rows.get(p.item.id) or {"id": p.item.id, "media_type": p.item.kind})
            reasons = list(p.reasons)
            because = rows.get(p.because) if p.because is not None else None
            if because and because.get("title"):
                # The crowd's reason: fans of a film you loved also loved this one.
                reasons = reasons[:max(0, max_reasons - 1)] + [f"fans of {because['title']} loved it"]
            row.update({
                "wishmatch_score": p.score,
                "wish_match": p.match,
                "taste_fit": p.taste,
                "crowd_fit": p.crowd,
                "taste_reasons": reasons,
                "vibe": p.item.vibe,
            })
            out.append(row)
        return out

    # --- legacy-shaped entry point ---------------------------------------------
    def recommend(self, mood_id: int = 8, exclude_watched: bool = True, era_preference: Optional[str] = None,
                  duration_preference: Optional[str] = None, page: int = 1, page_size: int = 10,
                  sub_mood: Optional[str] = None, query: Optional[str] = None, media_type: Optional[str] = "movie",
                  genres_override: Optional[List[str]] = None, min_rating_override: Optional[float] = None,
                  profile: str = "together", return_pool: bool = False, only_ids: Optional[List[int]] = None,
                  pool_size: int = 200, **_ignored: Any) -> Dict[str, Any]:
        """Same arguments and result shape as RecommenderService.recommend (variety, memory and
        pool hooks are legacy steps and are ignored). media_type None or "any" mixes films and shows."""
        request = self.build_request(
            query=query, mood_id=mood_id, sub_mood=sub_mood, media_type=media_type, profile=profile,
            exclude_watched=exclude_watched, genres=genres_override, era=era_preference,
            duration=duration_preference, min_rating=min_rating_override, only_ids=only_ids)
        result = self.run(request)
        picks = result["picks"][:max(pool_size, page * page_size)]
        if return_pool:
            return {"pool": self.rows(picks), "engine": "wishmatch", "wish": result["wish"].as_dict()}
        start = (page - 1) * page_size
        page_picks = picks[start:start + page_size]
        return {
            "total_results": len(picks),
            "page": page,
            "page_size": page_size,
            "has_more": start + page_size < len(picks),
            "items": self.rows(page_picks),
            "engine": "wishmatch",
            "wish": result["wish"].as_dict(),
        }

    def session(self, session_id: int, kwargs: Dict[str, Any]) -> WishmatchSession:
        """A Discover questions session (step 5) for these recommend kwargs."""
        return WishmatchSession(session_id, {"kwargs": kwargs}, self)

    # --- extras for the /api/rec endpoints ----------------------------------------
    def read_wish(self, text: str) -> Dict[str, Any]:
        return self.engine.read(text).as_dict()

    def status(self) -> Dict[str, Any]:
        catalogue = self.adapter.catalogue()
        counts: Dict[str, Dict[str, int]] = {}
        for item in catalogue.values():
            by_known = counts.setdefault(item.kind, {})
            by_known[item.known] = by_known.get(item.known, 0) + 1
        reader = self.ollama.status() if self.ollama else {"enabled": False}
        return {"tagged_titles": len(catalogue), "by_type": counts, "crowd": self.adapter.crowd_status(),
                "wish_reader": reader}
