"""
The engine switch: `recommender.engine` in config.yaml picks who answers.

  legacy     the rules engine (services/recommender_service.py), unchanged
  wishmatch  Wishmatch; any failure falls back to legacy and the log says so

The legacy engine is only ever called as it is. (The old TIGER engine was removed on
2026-10-31; `engine: tiger` now means legacy.)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

ENGINES = ("legacy", "wishmatch")


def engine_name(cfg: Optional[Dict[str, Any]]) -> str:
    name = str((cfg or {}).get("engine") or "legacy").strip().lower()
    if name not in ENGINES:
        print(f"[Router] Unknown recommender.engine {name!r}; using legacy.", flush=True)
        return "legacy"
    return name


class EngineSwitch:
    """Stands in for the legacy RecommenderService on every screen: `recommend()` has its
    arguments, and anything else is passed through to it."""

    def __init__(self, engine: str, legacy: Any, wishmatch: Any):
        self.engine = engine
        self.legacy = legacy
        self.wishmatch = wishmatch

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
        def legacy_route() -> Dict[str, Any]:
            result = self.legacy.recommend(**kwargs)
            result["engine"] = "legacy"
            return result

        if self.engine == "wishmatch":
            return self._wishmatch_or(legacy_route, **{**kwargs, "genres_override": explicit_genres})
        return legacy_route()

    def recommend(self, **kwargs: Any) -> Dict[str, Any]:
        """The legacy recommend() signature, for the Northern Lights rows and Choose for us."""
        if self.engine == "wishmatch":
            return self._wishmatch_or(lambda: self.legacy.recommend(**kwargs), **kwargs)
        return self.legacy.recommend(**kwargs)
