"""
The engine switch: `recommender.engine` in config.yaml picks who answers.

  legacy     today's behaviour, unchanged (the rules engine, with TIGER per engine_mode)
  tiger      TIGER first (engine_mode generative_only), legacy when it can't answer
  wishmatch  Wishmatch; any failure falls back to legacy and the log says so

The legacy and TIGER engines are only ever called as they are.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

ENGINES = ("legacy", "tiger", "wishmatch")


def engine_name(cfg: Optional[Dict[str, Any]]) -> str:
    name = str((cfg or {}).get("engine") or "legacy").strip().lower()
    if name not in ENGINES:
        print(f"[Router] Unknown recommender.engine {name!r}; using legacy.", flush=True)
        return "legacy"
    return name


class EngineSwitch:
    """Stands in for the legacy RecommenderService on every screen: `recommend()` has its
    arguments, and anything else is passed through to it."""

    def __init__(self, engine: str, legacy: Any, generative: Any, wishmatch: Any, route_fn: Any,
                 engine_mode: Optional[str] = None):
        self.engine = engine
        self.legacy = legacy
        self.generative = generative
        self.wishmatch = wishmatch
        self.route_fn = route_fn            # generative_retriever.route_recommend
        self.engine_mode = engine_mode

    def __getattr__(self, name: str) -> Any:
        return getattr(self.legacy, name)

    def _wishmatch_or(self, fallback: Any, **kwargs: Any) -> Dict[str, Any]:
        try:
            return self.wishmatch.recommend(**kwargs)
        except Exception as e:  # noqa: BLE001 - a Wishmatch failure must never empty a screen
            print(f"[Router] Wishmatch failed ({e}); falling back to legacy.", flush=True)
            result = fallback()
            result["wishmatch_error"] = str(e)
            return result

    def route(self, explicit_genres: Optional[List[str]] = None, **kwargs: Any) -> Dict[str, Any]:
        """Discover's /api/recommend. explicit_genres: the genre chips the person picked (the
        legacy kwargs also carry the mood card's genres, which Wishmatch reads as words instead)."""
        def legacy_route(mode: Optional[str] = None) -> Dict[str, Any]:
            return self.route_fn(self.legacy, self.generative, mode or self.engine_mode, **kwargs)

        if self.engine == "wishmatch":
            return self._wishmatch_or(legacy_route, **{**kwargs, "genres_override": explicit_genres})
        if self.engine == "tiger":
            return legacy_route("generative_only")
        return legacy_route()

    def recommend(self, **kwargs: Any) -> Dict[str, Any]:
        """The legacy recommend() signature, for the Northern Lights rows and Choose for us."""
        if self.engine == "wishmatch":
            return self._wishmatch_or(lambda: self.legacy.recommend(**kwargs), **kwargs)
        return self.legacy.recommend(**kwargs)
