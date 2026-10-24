"""
Written taste profiles (utils/wishmatch_profiles.py)

For each person (row in `profiles`) this reads the titles they rated that have
mood tags (media_items.vibe_tags), shows Claude Haiku the ones they loved most and
disliked most, and stores a short written profile in wishmatch_profiles:
a summary in plain second person, plus the mood words (services/vibe_vocab
FEELS + GOOD_FOR) they like and avoid. Wishmatch counts those words when scoring
(wishmatch.taste.declared_weight in recommender_rules.yaml). A profile somebody
edited by hand (edited = 1) is never overwritten unless --overwrite-edited.

Spends real API money (a few hundredths of a cent per person), so without --yes
it only prints the estimated cost and the command to run, and calls nothing.

    uv run --extra llm python utils/wishmatch_profiles.py                  # estimate only
    uv run --extra llm python utils/wishmatch_profiles.py --yes            # make and store profiles
    uv run --extra llm python utils/wishmatch_profiles.py --yes --push http://server:8000
        # also PUT each new profile to <URL>/api/rec/movies/profile/<person_id>
        # (so the Mac, which has the API key and the ratings copy, can send them to the server)

Needs ANTHROPIC_API_KEY in .env for --yes. DATABASE_PATH or --db picks the
database (default: catalogue_full.db).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import DatabaseManager  # noqa: E402
from services.vibe_vocab import FEELS, GOOD_FOR  # noqa: E402
from utils import mood_tag  # noqa: E402

MODEL = mood_tag.MODEL
MAX_TOKENS = 600
LOVED_MAX = 40
DISLIKED_MAX = 20
LIKES_MAX = 8
DISLIKES_MAX = 6
SUMMARY_WORDS = 90

# USD per million tokens, standard (non-batch) Haiku 5.5: twice the Batch API prices in mood_tag.PRICE.
# Source: platform.claude.com/docs/en/about-claude/pricing, checked 2026-10-08.
PRICE_INPUT = 0.10
PRICE_OUTPUT = 0.50
CHARS_PER_TOKEN = mood_tag.CHARS_PER_TOKEN      # ~3.3 characters per token for English prose
EST_OUTPUT_TOKENS = 400                         # per person, used for the estimate only

WORDS = list(dict.fromkeys(FEELS + GOOD_FOR))
LOVED_ABOVE = 0.2        # a score above this counts as loved, below -LOVED_ABOVE as disliked


# ---------------------------------------------------------------------------
# Prompt, schema, validation
# ---------------------------------------------------------------------------

def system_prompt() -> str:
    return (
        "You write a short taste profile for one person of a household film and TV recommender. "
        "You get the titles they loved and the ones they disliked, each with a score from -1 (hated) to +1 "
        "(loved), a one-line description of how it feels to watch, and its mood words. "
        "Find what the loved titles share and what the disliked ones share. Do not just list titles.\n\n"
        f"- `summary`: at most {SUMMARY_WORDS} words, plain second person ('You enjoy ...'), what they enjoy and "
        "what they avoid. No title names, no spoilers.\n"
        f"- `likes`: up to {LIKES_MAX} words they are drawn to, only from this list: {', '.join(WORDS)}.\n"
        f"- `dislikes`: up to {DISLIKES_MAX} words they steer away from, only from the same list.\n"
        "Only use a word in `dislikes` when the disliked titles clearly share it. Answer only with the JSON object."
    )


def output_schema() -> Dict[str, Any]:
    # The API's structured output refuses "maxItems"; the prompt asks for the limit and clean_words() enforces it.
    def words(cap: int) -> Dict[str, Any]:
        return {"type": "array", "items": {"type": "string", "enum": WORDS}}

    return {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "likes": words(LIKES_MAX), "dislikes": words(DISLIKES_MAX)},
        "required": ["summary", "likes", "dislikes"],
        "additionalProperties": False,
    }


def build_params(user_message: str) -> Dict[str, Any]:
    """One Messages API request (not batch), built like mood_tag.build_params."""
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": output_schema()}},
        "system": system_prompt(),
        "messages": [{"role": "user", "content": user_message}],
    }


def clean_words(raw: Any, cap: int, exclude: Tuple[str, ...] = ()) -> List[str]:
    """Only vocabulary words, no repeats, at most `cap`."""
    out: List[str] = []
    for w in raw if isinstance(raw, list) else []:
        w = str(w).strip().lower()
        if w in WORDS and w not in out and w not in exclude:
            out.append(w)
    return out[:cap]


def clean_summary(text: Any) -> str:
    words = str(text or "").split()
    return " ".join(words[:SUMMARY_WORDS])


# ---------------------------------------------------------------------------
# What each person rated
# ---------------------------------------------------------------------------

def rated_titles(conn: sqlite3.Connection, person_id: int) -> List[Dict[str, Any]]:
    """Seen titles with mood tags: score = the verdict's taste_score, else the 1-10 rating mapped around 6.5."""
    rows = conn.execute(
        """
        SELECT mi.title, mi.release_year AS year, mi.vibe_tags, wh.user_rating, rd.taste_score
        FROM watch_history wh
        JOIN media_items mi ON mi.id = wh.media_item_id
        LEFT JOIN rating_details rd ON rd.profile_id = wh.profile_id AND rd.media_item_id = wh.media_item_id
        WHERE wh.profile_id = ? AND wh.status = 'seen'
          AND mi.vibe_tags IS NOT NULL AND mi.vibe_tags != ''
          AND (rd.taste_score IS NOT NULL OR wh.user_rating IS NOT NULL)
        """,
        (person_id,),
    ).fetchall()
    out = []
    for r in rows:
        score = float(r["taste_score"]) if r["taste_score"] is not None else (float(r["user_rating"]) - 6.5) / 3.5
        try:
            tags = json.loads(r["vibe_tags"])
        except (TypeError, ValueError):
            continue
        if not isinstance(tags, dict):
            continue
        out.append({"title": r["title"], "year": r["year"], "score": max(-1.0, min(1.0, score)), "tags": tags})
    return out


def pick_extremes(rated: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(up to 40 most loved, up to 20 most disliked)."""
    loved = sorted((t for t in rated if t["score"] > LOVED_ABOVE), key=lambda t: -t["score"])[:LOVED_MAX]
    disliked = sorted((t for t in rated if t["score"] < -LOVED_ABOVE), key=lambda t: t["score"])[:DISLIKED_MAX]
    return loved, disliked


def _line(t: Dict[str, Any]) -> str:
    feels = ", ".join((t["tags"].get("feels") or []) + (t["tags"].get("good_for") or []))
    vibe = str(t["tags"].get("vibe") or "").strip()
    year = f" ({t['year']})" if t["year"] else ""
    return f"- {t['title']}{year} [{t['score']:+.1f}] {vibe} Words: {feels}".rstrip()


def build_user_message(loved: List[Dict[str, Any]], disliked: List[Dict[str, Any]]) -> str:
    parts = ["Loved:"] + [_line(t) for t in loved]
    parts += ["", "Disliked:"] + ([_line(t) for t in disliked] or ["- (none rated low)"])
    return "\n".join(parts)


def people(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    return [{"id": int(r["id"]), "name": r["name"]} for r in conn.execute("SELECT id, name FROM profiles ORDER BY id")]


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def estimate_cost(chars: int, n_people: int) -> Tuple[int, int, float]:
    """(input tokens, output tokens, USD) for `chars` of prompt text across `n_people` requests."""
    tokens_in = int(chars / CHARS_PER_TOKEN)
    tokens_out = EST_OUTPUT_TOKENS * n_people
    return tokens_in, tokens_out, (tokens_in * PRICE_INPUT + tokens_out * PRICE_OUTPUT) / 1_000_000


def cost_of(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * PRICE_INPUT + output_tokens * PRICE_OUTPUT) / 1_000_000


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------

def push(url: str, person_id: int, profile: Dict[str, Any]) -> bool:
    """PUT the profile to <url>/api/rec/movies/profile/<person_id>. Returns True when the server accepted it."""
    endpoint = f"{url.rstrip('/')}/api/rec/movies/profile/{person_id}"
    body = json.dumps({k: profile[k] for k in ("summary", "likes", "dislikes", "model")}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="PUT", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except Exception as e:  # noqa: BLE001 - report and carry on with the next person
        print(f"  push to {endpoint} failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None, api: Any = None) -> int:
    ap = argparse.ArgumentParser(description="Written taste profiles from ratings, made with Claude Haiku.")
    ap.add_argument("--db", default=None, help="database (default: $DATABASE_PATH or catalogue_full.db)")
    ap.add_argument("--yes", action="store_true", help="call the API and store the profiles (without it: estimate only)")
    ap.add_argument("--push", metavar="URL", default=None, help="also PUT each new profile to <URL>/api/rec/movies/profile/<id>")
    ap.add_argument("--overwrite-edited", action="store_true", help="also replace profiles that were edited by hand")
    args = ap.parse_args(argv)

    db_path = args.db or os.getenv("DATABASE_PATH") or "catalogue_full.db"
    db = DatabaseManager(db_path)
    jobs = []      # (person, message)
    skipped = []
    with db.get_connection() as conn:
        for person in people(conn):
            existing = db.get_wishmatch_profile(person["id"])
            if existing and existing["edited"] and not args.overwrite_edited:
                skipped.append(person)
                continue
            loved, disliked = pick_extremes(rated_titles(conn, person["id"]))
            if not loved and not disliked:
                print(f"  {person['name']}: no rated titles with mood tags, skipped")
                continue
            jobs.append((person, build_user_message(loved, disliked)))
    for person in skipped:
        print(f"  {person['name']}: profile was edited by hand, kept (--overwrite-edited replaces it)")

    if not jobs:
        print("Nothing to do.")
        return 0

    chars = sum(len(system_prompt()) + len(m) for _, m in jobs)
    tokens_in, tokens_out, estimate = estimate_cost(chars, len(jobs))
    print(f"{len(jobs)} profile(s) with {MODEL}: about {tokens_in:,} input + {tokens_out:,} output tokens, "
          f"estimated cost ${estimate:.4f} (standard, non-batch prices).")
    if not args.yes:
        command = "uv run --extra llm python utils/wishmatch_profiles.py"
        if args.db:
            command += f" --db {args.db}"
        command += " --yes"
        if args.push:
            command += f" --push {args.push}"
        if args.overwrite_edited:
            command += " --overwrite-edited"
        print(f"Nothing was sent. To make the profiles, run:\n    {command}")
        return 0

    api = api or mood_tag.client()
    total_in = total_out = 0
    for person, message in jobs:
        msg = api.messages.create(**build_params(message))
        total_in += msg.usage.input_tokens
        total_out += msg.usage.output_tokens
        if msg.stop_reason != "end_turn":
            print(f"  {person['name']}: the answer stopped early ({msg.stop_reason}), skipped")
            continue
        try:
            raw = json.loads(msg.content[0].text)
        except (ValueError, IndexError, AttributeError):
            print(f"  {person['name']}: the answer was not JSON, skipped")
            continue
        summary = clean_summary(raw.get("summary"))
        likes = clean_words(raw.get("likes"), LIKES_MAX)
        dislikes = clean_words(raw.get("dislikes"), DISLIKES_MAX, exclude=tuple(likes))
        if not summary:
            print(f"  {person['name']}: the answer had no summary, skipped")
            continue
        db.set_wishmatch_profile(person["id"], summary, likes, dislikes, MODEL, edited=False)
        print(f"  {person['name']}: {summary}\n    likes: {', '.join(likes) or '-'}  |  dislikes: {', '.join(dislikes) or '-'}")
        if args.push:
            ok = push(args.push, person["id"], {"summary": summary, "likes": likes, "dislikes": dislikes, "model": MODEL})
            print(f"    pushed to {args.push}" if ok else "    NOT pushed")
    print(f"Spent ${cost_of(total_in, total_out):.4f} ({total_in:,} input + {total_out:,} output tokens).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
