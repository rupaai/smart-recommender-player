"""
IMDb and Rotten Tomatoes scores for the catalogue (services/title_scores.py)

Once a day, on mars prod only, this fills the score columns of `media_items` a little at a time:

  1. **IMDb number** (`imdb_id`, like tt0133093): asked from TMDB (`/movie/{id}/external_ids`,
     `/tv/{id}/external_ids`) with the TMDB key the app already has. Free.
  2. **Scores**: asked from OMDb (omdbapi.com) by IMDb number. One lookup gives the IMDb score and vote count,
     the Rotten Tomatoes critics score (`rt_score`, 0-100) and the Metacritic score. Labiba chose OMDb's free
     key (2026-11-05): 1,000 lookups a day.

Best-known first: films before shows (Rotten Tomatoes rarely scores shows), then most TMDB votes first. Titles
never looked up go before a refresh of old scores. Lookups are counted per day in `title_scores_days`, so a
restart never goes over the daily limit.

    title_scores:
      enabled: true
      run_in: [prod]            # Homelab environments that run it (HOMELAB_ENV); dev would share the key's limit
      omdb_per_day: 1000        # OMDb's free key allows 1,000 lookups a day
      imdb_ids_per_day: 2000    # TMDB lookups for IMDb numbers (free; kept ahead of OMDb)
      films_first: true
      refresh_after_days: 180   # ask again for scores older than this, once every title has some

Keys come from the environment (Homelab secrets): TMDB_API_KEY and OMDB_API_KEY. A key is never logged.
Without OMDB_API_KEY only the IMDb numbers are fetched.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

TMDB_URL = "https://api.themoviedb.org/3"
OMDB_URL = "https://www.omdbapi.com/"
TIMEOUT = 10
FIRST_RUN_DELAY = 15 * 60       # seconds after start; after the watched cleanup's first run
RUN_EVERY = 24 * 60 * 60

# OMDb answers these when the whole run must stop (nothing more will work today).
STOP_ERRORS = ("request limit reached", "invalid api key", "no api key provided")


def settings(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("title_scores") or {}

    def number(key: str, default: int) -> int:
        try:
            return max(0, int(raw.get(key, default)))
        except (TypeError, ValueError):
            return default

    run_in = raw.get("run_in", ["prod"])
    return {
        "enabled": bool(raw.get("enabled", False)),
        "run_in": [str(e).strip() for e in run_in] if isinstance(run_in, list) else ["prod"],
        "omdb_per_day": number("omdb_per_day", 1000),
        "imdb_ids_per_day": number("imdb_ids_per_day", 2000),
        "films_first": bool(raw.get("films_first", True)),
        "refresh_after_days": max(1, number("refresh_after_days", 180)),
        "pause_seconds": float(raw.get("pause_seconds", 0.2) or 0),
    }


def runs_here(cfg: Dict[str, Any], env: Optional[str] = None) -> bool:
    """On when enabled and, under Homelab, in one of `run_in`. Outside Homelab (no HOMELAB_ENV) `enabled` decides."""
    env = (os.getenv("HOMELAB_ENV", "") if env is None else env).strip()
    return cfg["enabled"] and (not env or env in cfg["run_in"])


def _number(text: Any, kind: Callable[[str], Any]) -> Any:
    """OMDb sends numbers as text ("8.7", "2,145,000", "N/A")."""
    if text is None:
        return None
    clean = str(text).replace(",", "").replace("%", "").strip()
    if not clean or clean.upper() == "N/A":
        return None
    try:
        return kind(clean)
    except ValueError:
        return None


def parse_omdb(data: Dict[str, Any]) -> Dict[str, Any]:
    """OMDb's answer -> {imdb_rating, imdb_votes, rt_score, metacritic} (None where OMDb has none)."""
    rt = None
    for r in data.get("Ratings") or []:
        if isinstance(r, dict) and r.get("Source") == "Rotten Tomatoes":
            rt = _number(r.get("Value"), int)
    rating = _number(data.get("imdbRating"), float)
    votes = _number(data.get("imdbVotes"), int)
    meta = _number(data.get("Metascore"), int)
    return {
        "imdb_rating": rating if rating is None or 0 < rating <= 10 else None,
        "imdb_votes": votes,
        "rt_score": rt if rt is None or 0 <= rt <= 100 else None,
        "metacritic": meta if meta is None or 0 <= meta <= 100 else None,
    }


def card_scores(row: Dict[str, Any]) -> Dict[str, Any]:
    """What a card shows (Labiba, 2026-10-24): IMDb and Rotten Tomatoes first; TMDB's rating only while a title
    has no IMDb score yet. {"imdb": 7.8 | None, "rt": 93 | None, "tmdb": 7.5 | None}."""
    imdb = row.get("imdb_rating")
    tmdb = row.get("rating")
    return {
        "imdb": round(float(imdb), 1) if imdb else None,
        "rt": int(row["rt_score"]) if row.get("rt_score") is not None else None,
        "tmdb": round(float(tmdb), 1) if tmdb and not imdb else None,
    }


class StopRun(Exception):
    """A service said no for today (limit reached, bad key, no network). Nothing is marked."""


class TitleScores:
    def __init__(self, config: Dict[str, Any], db_path: str, session: Optional[requests.Session] = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cfg = settings(config)
        self.db_path = db_path
        self.session = session or requests.Session()
        self.now = now
        self.sleep = sleep
        self._lock = threading.Lock()

    # ---- database ---------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _today(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    def used_today(self) -> Dict[str, int]:
        with self._conn() as conn:
            row = conn.execute("SELECT tmdb_calls, omdb_calls FROM title_scores_days WHERE day = ?",
                               (self._today(),)).fetchone()
        return {"tmdb": row["tmdb_calls"] if row else 0, "omdb": row["omdb_calls"] if row else 0}

    def _count_call(self, conn: sqlite3.Connection, which: str) -> None:
        col = "tmdb_calls" if which == "tmdb" else "omdb_calls"
        conn.execute("INSERT OR IGNORE INTO title_scores_days (day) VALUES (?)", (self._today(),))
        conn.execute(f"UPDATE title_scores_days SET {col} = {col} + 1 WHERE day = ?", (self._today(),))

    def _order(self) -> str:
        films = "CASE WHEN media_type = 'movie' THEN 0 ELSE 1 END, " if self.cfg["films_first"] else ""
        return f"ORDER BY {films}COALESCE(vote_count, 0) DESC, id ASC"

    def needing_imdb_id(self, limit: int) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, media_type, tmdb_id FROM media_items WHERE tmdb_id IS NOT NULL "
                f"AND COALESCE(imdb_id_checked, 0) = 0 {self._order()} LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def needing_scores(self, limit: int) -> List[Dict[str, Any]]:
        """Never looked up first; then, only if none are left, scores older than refresh_after_days."""
        cutoff = (self.now() - timedelta(days=self.cfg["refresh_after_days"])).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, imdb_id FROM media_items WHERE COALESCE(imdb_id, '') != '' "
                f"AND scores_checked_at IS NULL {self._order()} LIMIT ?", (limit,)).fetchall()
            if len(rows) < limit:
                rows += conn.execute(
                    "SELECT id, imdb_id FROM media_items WHERE COALESCE(imdb_id, '') != '' "
                    "AND scores_checked_at IS NOT NULL AND scores_checked_at < ? "
                    "ORDER BY scores_checked_at ASC, id ASC LIMIT ?", (cutoff, limit - len(rows))).fetchall()
        return [dict(r) for r in rows]

    def progress(self) -> Dict[str, int]:
        """How far the backfill is: titles with an IMDb number, with scores, with a Rotten Tomatoes score."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS titles, "
                "SUM(COALESCE(imdb_id, '') != '') AS with_imdb_id, "
                "SUM(scores_checked_at IS NOT NULL) AS looked_up, "
                "SUM(imdb_rating IS NOT NULL) AS with_imdb_rating, "
                "SUM(rt_score IS NOT NULL) AS with_rt_score FROM media_items").fetchone()
        return {k: int(row[k] or 0) for k in row.keys()}

    # ---- services ---------------------------------------------------------
    def fetch_imdb_id(self, media_type: str, tmdb_id: int, key: str) -> str:
        """The IMDb number TMDB has, '' when it has none (or the title is gone). Raises StopRun on other failures."""
        try:
            resp = self.session.get(f"{TMDB_URL}/{media_type}/{tmdb_id}/external_ids",
                                    params={"api_key": key}, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise StopRun(f"TMDB unreachable ({type(e).__name__})") from None
        if resp.status_code == 404:
            return ""
        if resp.status_code != 200:
            raise StopRun(f"TMDB answered {resp.status_code}")
        imdb_id = str((resp.json() or {}).get("imdb_id") or "").strip()
        return imdb_id if imdb_id.startswith("tt") else ""

    def fetch_scores(self, imdb_id: str, key: str) -> Dict[str, Any]:
        """OMDb's scores for one title (all None when OMDb doesn't know it). Raises StopRun when it says stop."""
        try:
            resp = self.session.get(OMDB_URL, params={"i": imdb_id, "apikey": key}, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise StopRun(f"OMDb unreachable ({type(e).__name__})") from None
        try:
            data = resp.json() or {}
        except ValueError:
            data = {}
        error = str(data.get("Error") or "")
        if resp.status_code == 401 or any(s in error.lower() for s in STOP_ERRORS):
            raise StopRun(f"OMDb said: {error or resp.status_code}")
        if resp.status_code != 200:
            raise StopRun(f"OMDb answered {resp.status_code}")
        if str(data.get("Response")) != "True":
            return parse_omdb({})       # "Incorrect IMDb ID." etc.: mark it looked up, with no scores
        return parse_omdb(data)

    # ---- one run ----------------------------------------------------------
    def run_once(self) -> Dict[str, Any]:
        """Does today's share. Returns what happened (also logged)."""
        if not self._lock.acquire(blocking=False):
            return {"skipped": "already running"}
        try:
            return self._run()
        finally:
            self._lock.release()

    def _run(self) -> Dict[str, Any]:
        tmdb_key = os.getenv("TMDB_API_KEY", "").strip()
        omdb_key = os.getenv("OMDB_API_KEY", "").strip()
        used = self.used_today()
        report: Dict[str, Any] = {"imdb_ids": 0, "scored": 0, "stopped": []}
        pause = self.cfg["pause_seconds"]
        # The two parts stop on their own: TMDB being down still lets OMDb score titles with a number already.
        try:
            left = self.cfg["imdb_ids_per_day"] - used["tmdb"]
            if tmdb_key and left > 0:
                for item in self.needing_imdb_id(left):
                    imdb_id = self.fetch_imdb_id(item["media_type"], item["tmdb_id"], tmdb_key)
                    with self._conn() as conn:
                        conn.execute("UPDATE media_items SET imdb_id = ?, imdb_id_checked = 1 WHERE id = ?",
                                     (imdb_id, item["id"]))
                        self._count_call(conn, "tmdb")
                    report["imdb_ids"] += 1
                    if pause:
                        self.sleep(pause)
        except StopRun as e:
            report["stopped"].append(str(e))
        try:
            left = self.cfg["omdb_per_day"] - used["omdb"]
            if omdb_key and left > 0:
                for item in self.needing_scores(left):
                    scores = self.fetch_scores(item["imdb_id"], omdb_key)
                    with self._conn() as conn:
                        conn.execute(
                            "UPDATE media_items SET imdb_rating = ?, imdb_votes = ?, rt_score = ?, metacritic = ?, "
                            "scores_checked_at = ? WHERE id = ?",
                            (scores["imdb_rating"], scores["imdb_votes"], scores["rt_score"], scores["metacritic"],
                             self.now().isoformat(), item["id"]))
                        self._count_call(conn, "omdb")
                    report["scored"] += 1
                    if pause:
                        self.sleep(pause)
        except StopRun as e:
            report["stopped"].append(str(e))
        report["missing_keys"] = [k for k, v in (("TMDB_API_KEY", tmdb_key), ("OMDB_API_KEY", omdb_key)) if not v]
        report["progress"] = self.progress()
        p = report["progress"]
        print(f"[TitleScores] {report['imdb_ids']} IMDb numbers, {report['scored']} scored today"
              + (f"; stopped: {'; '.join(report['stopped'])}" if report["stopped"] else "")
              + (f"; not set: {', '.join(report['missing_keys'])}" if report["missing_keys"] else "")
              + f". So far {p['with_imdb_id']}/{p['titles']} have an IMDb number, {p['looked_up']} looked up, "
              f"{p['with_rt_score']} with a Rotten Tomatoes score.", flush=True)
        return report
