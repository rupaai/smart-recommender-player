"""
The movie adapter: films and shows as Wishmatch items.

- vocabulary: services/vibe_vocab.py (the words Haiku tagged the catalogue with);
- items: every title with mood tags (media_items.vibe_tags), with this app's hard
  filters: already seen, typed genre names ("comedy", "no horror"), a named place
  ("korean"), the foreign-title rule, genre chips, era, length, rating floor;
- reactions: ratings and verdicts (rating_details.taste_score, else the 1-10 rating);
- crowd: MovieLens neighbours (films their fans also liked), from <MODELS_DIR>/wishmatch/crowd.json;
- rules: the wishmatch: section of recommender_rules.yaml.

The tagged catalogue is read once and kept in memory; it is read again when the
number of tagged titles changes (checked at most every `refresh_seconds`).
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set

from wishmatch import Item, Reaction, Request, Vocabulary

from services import paths as model_paths
from services import vibe_vocab
from services.recommender_rules import RULES, foreign_gate, passes_foreign_gate, rules_path, word_index
from services.recommender_service import parse_origin_intent

TAG_FIELDS = ("feels", "good_for", "setting", "season_weather", "flags")
NEGATORS = {"no", "not", "without", "nothing", "never", "avoid", "don't", "dont", "except", "but"}
_TOKEN = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")


def load_wishmatch_rules(path: Optional[str] = None) -> Dict[str, Any]:
    """The wishmatch: section of recommender_rules.yaml ({} when missing or broken)."""
    path = path or rules_path()
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        section = data.get("wishmatch") if isinstance(data, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as e:  # noqa: BLE001 - a bad rules file must not stop the server
        print(f"[Wishmatch] Could not read {path} ({e}); using defaults.", flush=True)
        return {}


def _intern_set(words: Iterable[str]) -> Set[str]:
    return {sys.intern(str(w)) for w in words if w}


def _genres(csv: Optional[str]) -> Set[str]:
    return {g.strip().lower() for g in str(csv or "").split(",") if g.strip()}


def typed_decade(text: str) -> Optional[tuple]:
    """A decade typed in the wish ("80s", "1980s", "the 90s", "2000s") as (first year, last year)."""
    m = re.search(r"\b(19|20)?([0-9])0'?s\b", (text or "").lower())
    if not m:
        return None
    century = m.group(1) or ("20" if m.group(2) in "012" else "19")
    start = int(century + m.group(2) + "0")
    return start, start + 9


class MovieAdapter:
    """Wishmatch's view of the catalogue. `db` is the app's DatabaseManager."""

    def __init__(self, db: Any, rules_file: Optional[str] = None, refresh_seconds: float = 300.0,
                 crowd_file: Optional[str] = None):
        self.db = db
        self.rules_file = rules_file
        self.crowd_file = crowd_file
        self._crowd: Dict[int, Dict[int, float]] = {}
        self._crowd_mtime: Optional[float] = None
        self._crowd_built_at: Optional[str] = None
        self.refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._items: Dict[int, Item] = {}
        self._signature: Optional[tuple] = None
        self._checked = 0.0
        self._rules: Optional[Dict[str, Any]] = None

    # --- the contract -------------------------------------------------------
    def vocabulary(self) -> Vocabulary:
        traits = {name: {"low": str(d["low"]), "high": str(d["high"])} for name, d in vibe_vocab.DIALS.items()}
        return Vocabulary(traits=traits, tags={
            "feels": list(vibe_vocab.FEELS),
            "good_for": list(vibe_vocab.GOOD_FOR),
            "setting": list(vibe_vocab.SETTING),
            "season_weather": list(vibe_vocab.SEASON_WEATHER),
            "flags": list(vibe_vocab.CONTENT_FLAGS),
        })

    def rules(self) -> Dict[str, Any]:
        if self._rules is None:
            self._rules = load_wishmatch_rules(self.rules_file)
        return self._rules

    def reload_rules(self) -> None:
        self._rules = None

    def items(self, request: Request) -> Iterable[Item]:
        catalogue = self.catalogue()
        f = request.filters or {}
        text = request.text or ""
        kinds = set(request.kinds) if request.kinds else None
        only = set(request.only_ids) if request.only_ids is not None else None
        exclude = set(f.get("exclude_ids") or [])
        exclude |= self._seen_to_exclude(request)

        want_genres, avoid_genres = self.typed_genres(text)
        chips = {str(g).strip().lower() for g in (f.get("genres") or []) if str(g).strip()}
        origin = parse_origin_intent(text) if text else None
        any_language = bool(set(_TOKEN.findall(text.lower()))
                            & {str(w).lower() for w in (RULES.get("foreign_titles") or {}).get("any_language_words") or []})
        gates = {k: foreign_gate(RULES, k) for k in ("movie", "tv")}
        era, duration = f.get("era"), f.get("duration")
        decade = typed_decade(text)
        min_rating = f.get("min_rating")

        for iid, item in catalogue.items():
            if kinds and item.kind not in kinds:
                continue
            if only is not None and iid not in only:
                continue
            if iid in exclude:
                continue
            x = item.extra
            genres = x["genres"]
            if want_genres and not all(self._has_genre(genres, g, item.kind) for g in want_genres):
                continue
            if avoid_genres and any(self._has_genre(genres, g, item.kind) for g in avoid_genres):
                continue
            if chips and not any(self._has_genre(genres, g, item.kind) for g in chips):
                continue
            if origin:
                if not (set(origin["countries"]) & x["countries"] or x["language"] in origin["languages"]):
                    continue
            elif only is None and not any_language:
                row = {"original_language": x["language"], "rating": x["rating"], "vote_count": item.popularity}
                if not passes_foreign_gate(row, item.kind, gates.get(item.kind)):
                    continue
            if decade and not (decade[0] <= (x["year"] or 0) <= decade[1]):
                continue
            if era == "modern" and (x["year"] or 0) < 2015:
                continue
            if era == "2000s" and not (2000 <= (x["year"] or 0) <= 2014):
                continue
            if era == "classic" and (x["year"] or 9999) >= 2000:
                continue
            if item.kind == "movie" and duration == "short" and not (1 <= (x["runtime"] or 0) <= 104):
                continue
            if item.kind == "movie" and duration == "epic" and (x["runtime"] or 0) <= 140:
                continue
            if min_rating is not None and (x["rating"] or 0) < float(min_rating):
                continue
            yield item

    def describe(self, item_ids: List[Any]) -> Dict[Any, Item]:
        catalogue = self.catalogue()
        return {i: catalogue[i] for i in item_ids if i in catalogue}

    def reactions(self, person: Any) -> List[Reaction]:
        """Every seen-and-rated title: the verdict's taste score, else the 1-10 rating around 6.5."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT wh.media_item_id AS id, wh.user_rating, rd.taste_score
                FROM watch_history wh
                LEFT JOIN rating_details rd
                       ON rd.profile_id = wh.profile_id AND rd.media_item_id = wh.media_item_id
                WHERE wh.profile_id = ? AND wh.status = 'seen'
                  AND (rd.taste_score IS NOT NULL OR wh.user_rating IS NOT NULL)
                """,
                (person,),
            ).fetchall()
        out = []
        for r in rows:
            if r["taste_score"] is not None:
                score = float(r["taste_score"])
            else:
                score = (float(r["user_rating"]) - 6.5) / 3.5
            out.append(Reaction(item_id=int(r["id"]), score=max(-1.0, min(1.0, score))))
        return out

    def profile(self, person: Any) -> Optional[Dict[str, Any]]:
        """The written taste profile's liked / disliked words (utils/wishmatch_profiles.py), or None."""
        try:
            stored = self.db.get_wishmatch_profile(int(person))
        except (TypeError, ValueError):
            return None
        if not stored or not (stored["likes"] or stored["dislikes"]):
            return None
        return {"likes": stored["likes"], "dislikes": stored["dislikes"]}

    def bonus(self, request: Request) -> Any:
        """A soft pull towards genres the wish hints at without naming them ("scary" -> Horror,
        "laugh" -> Comedy; genre_hints in recommender_rules.yaml): item -> points, or None."""
        merged_hints: Dict[str, List[str]] = {g: list(w) for g, w in (RULES.get("genre_hints") or {}).items()}
        for g, words in (self.rules().get("genre_hints") or {}).items():     # Wishmatch's own extra hints
            merged_hints.setdefault(g, []).extend(words or [])
        hints = word_index(merged_hints)
        toks = re.findall(r"[a-z0-9]+", (request.text or "").lower())
        hinted: Set[str] = set()
        for i, t in enumerate(toks):
            if any(w in NEGATORS for w in toks[max(0, i - 3):i]):
                continue
            hinted.update(g.lower() for g in hints.get(t, []))
        points = float(((self.rules().get("weights") or {}).get("genre_hint", 0.0)))
        if not hinted or not points:
            return None
        return lambda item: points if any(self._has_genre(item.extra["genres"], g, item.kind) for g in hinted) else 0.0

    def crowd(self, item_ids: List[Any]) -> Dict[Any, Dict[Any, float]]:
        """Films that fans of these films also liked, from MovieLens (crowd.json in the model bundle,
        built by training/build_wishmatch_crowd.py at Train time). No file = no crowd data."""
        table = self._crowd_table()
        return {i: table[i] for i in item_ids if i in table}

    def crowd_status(self) -> Dict[str, Any]:
        table = self._crowd_table()
        return {"films": len(table), "path": self._crowd_path(), "built_at": self._crowd_built_at}

    def _crowd_path(self) -> str:
        return self.crowd_file or os.path.join(model_paths.models_dir(), "wishmatch", "crowd.json")

    def _crowd_table(self) -> Dict[int, Dict[int, float]]:
        path = self._crowd_path()
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            self._crowd, self._crowd_mtime, self._crowd_built_at = {}, None, None
            return self._crowd
        if mtime != self._crowd_mtime:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._crowd = {int(k): {int(o): float(sim) for o, sim in v}
                               for k, v in (data.get("neighbours") or {}).items()}
                self._crowd_built_at = data.get("built_at")
            except (OSError, ValueError, TypeError, AttributeError) as e:
                print(f"[Wishmatch] Could not read {path} ({e}); running without crowd data.", flush=True)
                self._crowd, self._crowd_built_at = {}, None
            self._crowd_mtime = mtime
        return self._crowd

    # --- this app's helpers --------------------------------------------------
    def people(self) -> List[int]:
        with self.db.get_connection() as conn:
            return [int(r[0]) for r in conn.execute("SELECT id FROM profiles ORDER BY id")]

    def typed_genres(self, text: str) -> tuple:
        """Genre names typed in the wish ("comedy", "no horror"), as (wanted, avoided) lowercase names."""
        index = word_index(RULES.get("genre_names") or {})
        toks = re.findall(r"[a-z0-9]+", (text or "").lower().replace("sci-fi", "scifi"))
        want: Set[str] = set()
        avoid: Set[str] = set()
        for i, t in enumerate(toks):
            for genre in index.get(t, []):
                negated = any(w in NEGATORS for w in toks[max(0, i - 3):i])
                (avoid if negated else want).add(genre.lower())
        return want - avoid, avoid

    @staticmethod
    def _has_genre(item_genres: Set[str], genre: str, kind: str) -> bool:
        if genre in item_genres:
            return True
        if kind == "tv":
            aliases = (RULES.get("tv_genre_aliases") or {}).get(genre) or []
            return any(a.lower() in item_genres for a in aliases)
        return False

    def seen_by(self, person: Any) -> Set[int]:
        """Titles this person has marked as seen."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT media_item_id FROM watch_history WHERE profile_id = ? AND status = 'seen'", (person,)
            ).fetchall()
        return {int(r[0]) for r in rows}

    def _seen_to_exclude(self, request: Request) -> Set[int]:
        """Alone: what the viewer has seen. Together: what everyone at the screen has seen."""
        mode = (request.filters or {}).get("exclude_seen", True)
        if not mode or not request.people:
            return set()
        people = list(request.people) if request.together else [request.people[0]]
        with self.db.get_connection() as conn:
            seen_sets = []
            for p in people:
                rows = conn.execute(
                    "SELECT media_item_id FROM watch_history WHERE profile_id = ? AND status = 'seen'", (p,)
                ).fetchall()
                seen_sets.append({int(r[0]) for r in rows})
        return set.intersection(*seen_sets) if seen_sets else set()

    def catalogue(self) -> Dict[int, Item]:
        """id -> Item for every title with mood tags, re-read when the tagged count changes."""
        now = time.time()
        if self._items and now - self._checked < self.refresh_seconds:
            return self._items
        with self._lock:
            if self._items and time.time() - self._checked < self.refresh_seconds:
                return self._items
            with self.db.get_connection() as conn:
                sig = tuple(conn.execute(
                    "SELECT COUNT(*), MAX(vibe_tagged_at), MAX(id) FROM media_items "
                    "WHERE vibe_tags IS NOT NULL AND vibe_tags != ''").fetchone())
                if sig != self._signature or not self._items:
                    self._items = self._load(conn)
                    self._signature = sig
            self._checked = time.time()
            return self._items

    def _load(self, conn: Any) -> Dict[int, Item]:
        cfg = self.rules()
        floor = float(cfg.get("quality_floor", 5.0))
        ceil = float(cfg.get("quality_ceiling", 8.5))
        span = max(0.1, ceil - floor)
        out: Dict[int, Item] = {}
        rows = conn.execute(
            "SELECT id, media_type, vote_count, rating, original_language, origin_country, genres, "
            "release_year, runtime, vibe_tags FROM media_items "
            "WHERE vibe_tags IS NOT NULL AND vibe_tags != ''")
        for r in rows:
            try:
                tags = json.loads(r["vibe_tags"])
            except (TypeError, ValueError):
                continue
            if not isinstance(tags, dict):
                continue
            dials = tags.get("dials") or {}
            traits = {sys.intern(k): float(v) for k, v in dials.items()
                      if k in vibe_vocab.DIALS and isinstance(v, (int, float))}
            words: List[str] = []
            for field_name in TAG_FIELDS:
                value = tags.get(field_name) or []
                if isinstance(value, list):
                    words.extend(str(w) for w in value)
            rating = float(r["rating"] or 0.0)
            votes = float(r["vote_count"] or 0)
            # A rating from few votes counts less (a 9.0 from 40 votes is not a 9.0 from 40,000).
            confidence = votes / (votes + float(cfg.get("quality_votes", {}).get(str(r["media_type"]), 300)))
            out[int(r["id"])] = Item(
                id=int(r["id"]),
                kind=sys.intern(str(r["media_type"] or "movie")),
                traits=traits,
                tags=_intern_set(words),
                vibe=str(tags.get("vibe") or ""),
                known=sys.intern(str(tags.get("known") or "not")),
                popularity=float(r["vote_count"] or 0),
                quality=max(0.0, min(1.0, (rating - floor) / span)) * confidence,
                extra={
                    "genres": _intern_set(_genres(r["genres"])),
                    "countries": _intern_set(c.strip().upper() for c in str(r["origin_country"] or "").split(",")),
                    "language": str(r["original_language"] or "").strip().lower(),
                    "rating": rating,
                    "year": r["release_year"],
                    "runtime": r["runtime"],
                },
            )
        return out
