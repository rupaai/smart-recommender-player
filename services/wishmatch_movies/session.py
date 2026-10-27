"""
The Discover questions pop-up, Wishmatch's way (build step 5).

Used instead of the mood-map session (services/mood_popup.MoodSession) when
recommender.engine is wishmatch. Same methods, so the /api/discover routes drive
either: the wish is read once, the picks scored, and each question is the split
that tells the top picks apart best (packages/wishmatch/wishmatch/questions.py),
worded by the Ollama model (each wording remembered, so it is asked once), else by a fixed sentence.
Each answer adds to the wish and the picks are scored again (about 0.1 s).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from wishmatch import apply_answer, next_question
from wishmatch.settings import merged

EMOJI = {"low": "🌙", "high": "⚡"}


class WishmatchSession:
    def __init__(self, session_id: int, request: Dict[str, Any], service: Any):
        self.id = session_id
        self.request = request                      # {"kwargs": the legacy recommend kwargs, ...}
        self.service = service
        self.updated = time.time()
        self.done = False
        self.current: Optional[Dict[str, Any]] = None
        self.asked: List[tuple] = []
        self._worded: Optional[Dict[str, Any]] = None
        kw = request["kwargs"]
        self.req = service.build_request(
            query=kw.get("query"), mood_id=kw.get("mood_id", 8), sub_mood=kw.get("sub_mood"),
            media_type=kw.get("media_type"), profile=kw.get("profile", "together"),
            exclude_watched=kw.get("exclude_watched", True), genres=kw.get("explicit_genres"),
            era=kw.get("era_preference"), duration=kw.get("duration_preference"),
            min_rating=kw.get("min_rating_override"))
        self.wish = service.engine.read(self.req.text)
        self._score()

    # --- scoring ------------------------------------------------------------------
    def _score(self) -> None:
        self.result = self.service.engine.recommend(self.req, wish=self.wish)
        self.picks = self.result["picks"]

    @property
    def settings(self) -> Dict[str, Any]:
        return merged(self.service.adapter.rules())

    @property
    def answered_count(self) -> int:
        return len(self.asked)

    def ranked(self) -> List[Dict[str, Any]]:
        return self.service.rows(self.picks[:200])

    def page(self, page: int, page_size: int) -> Dict[str, Any]:
        picks = self.picks[:200]
        start = (page - 1) * page_size
        return {"total_results": len(picks), "page": page, "page_size": page_size,
                "has_more": start + page_size < len(picks),
                "items": self.service.rows(picks[start:start + page_size]), "engine": "wishmatch",
                "wish": self.wish.as_dict()}

    # --- questions ----------------------------------------------------------------
    def next_question(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """The split that tells the top picks apart best, or None (they agree, or enough was asked).
        `force` is accepted for MoodSession's signature; a question is never invented."""
        self.current = next_question(self.picks, self.wish, self.service.adapter.vocabulary(), self.settings,
                                     {q for q, _ in self.asked})
        self._worded = None
        return self.current

    def public_question(self) -> Optional[Dict[str, Any]]:
        if not self.current:
            return None
        if self._worded is None:
            words = [o["words"][0] for o in self.current["options"]]
            self._worded = self.service.word_question(self.current["about"], words)
        ids = [e for o in self.current["options"] for e in o["examples"]]
        rows = self.service.db.get_items_by_ids(ids) if ids else {}
        opts = []
        for o, label in zip(self.current["options"], self._worded["options"]):
            examples = [{"title": rows[e].get("title"), "poster_url": rows[e].get("poster_url")}
                        for e in o["examples"] if e in rows]
            opts.append({"id": o["id"], "emoji": EMOJI.get(o["id"], "✨"), "label": label, "examples": examples})
        return {"id": self.current["id"], "text": self._worded["question"], "options": opts,
                "step": self.answered_count + 1, "max_steps": int(self.settings["questions"]["max"])}

    def answer(self, question_id: str, option_id: str) -> None:
        if not self.current or self.current["id"] != question_id:
            raise ValueError("That question isn't the one being asked.")
        self.wish = apply_answer(self.wish, self.current, option_id,
                                 weight=float(self.settings["questions"].get("answer_weight", 0.5)))
        self.asked.append((question_id, option_id))
        self.current = None
        self.updated = time.time()
        if option_id != "skip":
            self._score()

    def should_stop(self) -> bool:
        return self.done or self.answered_count >= int(self.settings["questions"]["max"])

    def log_fields(self) -> Dict[str, Any]:
        return {"answers_json": json.dumps(dict(self.asked)), "asked_order": json.dumps([q for q, _ in self.asked]),
                "belief_json": json.dumps(self.wish.as_dict())}
