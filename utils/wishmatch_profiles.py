"""
Written taste profiles (utils/wishmatch_profiles.py)

For each person (row in `profiles`) this reads the titles they rated that have
mood tags (media_items.vibe_tags), shows a model the ones they loved most and
disliked most, and stores a short written profile in wishmatch_profiles:
a summary in plain second person, plus the mood words (services/vibe_vocab
FEELS + GOOD_FOR) they like and avoid. Wishmatch counts those words when scoring
(wishmatch.taste.declared_weight in recommender_rules.yaml). A profile somebody
edited by hand (edited = 1) is never overwritten unless --overwrite-edited.

By default the model is the open-weight one in the Mac's Ollama (wishmatch.tagging_model,
the bake-off winner): free, about a minute per person. --haiku uses Claude Haiku instead,
which spends real API money (about a tenth of a cent for two people), so with --haiku and
without --yes it only prints the estimated cost and calls nothing.

    uv run python utils/wishmatch_profiles.py                          # free, Ollama
    uv run python utils/wishmatch_profiles.py --push http://server:7101
        # also PUT each new profile to <URL>/api/rec/movies/profile/<person_id>
    uv run --extra llm python utils/wishmatch_profiles.py --haiku         # Haiku: estimate only
    uv run --extra llm python utils/wishmatch_profiles.py --haiku --yes   # Haiku: make them (costs money)

OLLAMA_URL picks the Ollama server (default http://localhost:11434). --haiku needs
ANTHROPIC_API_KEY in .env. DATABASE_PATH or --db picks the database (default: catalogue_full.db).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import DatabaseManager  # noqa: E402
from services.vibe_vocab import FEELS, GOOD_FOR  # noqa: E402
from utils import mood_tag  # noqa: E402
from utils.mood_tag_ollama import rules_model  # noqa: E402

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


def data_words(rated: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """Liked and avoided mood words worked out from the ratings, no model needed: a word is liked when
    it is more common among the titles they loved than among everything they rated (seen on 2+ of
    them), and avoided when it is more common among the ones they disliked."""
    def words(t: Dict[str, Any]) -> set:
        return {w for w in (t["tags"].get("feels") or []) + (t["tags"].get("good_for") or []) if w in WORDS}

    def lifted(group: List[Dict[str, Any]], cap: int) -> List[str]:
        if not group or not rated:
            return []
        base = {w: sum(w in words(t) for t in rated) / len(rated) for w in WORDS}
        counts = {w: sum(w in words(t) for t in group) for w in WORDS}
        scored = [(counts[w] / len(group) - base[w], w) for w in WORDS if counts[w] >= 2]
        return [w for lift, w in sorted(scored, reverse=True) if lift > 0][:cap]

    loved = [t for t in rated if t["score"] > LOVED_ABOVE]
    disliked = [t for t in rated if t["score"] < -LOVED_ABOVE]
    likes = lifted(loved, LIKES_MAX)
    return likes, [w for w in lifted(disliked, DISLIKES_MAX) if w not in likes]


def summary_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}


def summary_prompt(likes: List[str], dislikes: List[str]) -> str:
    """For a small model: the words are worked out already, it only writes the summary."""
    return (
        f"\nTheir ratings show they are drawn to: {', '.join(likes) or 'nothing clear yet'}; "
        f"and steer away from: {', '.join(dislikes) or 'nothing clear yet (no dislikes rated)'}.\n"
        f"Write only `summary`: three short sentences, at most {SUMMARY_WORDS} words, second person ('You enjoy ...'), "
        "no title names. "
        "Answer only with the JSON object."
    )


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

def ask_ollama(client: Any, message: str, rated: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Words from the ratings (data_words), and only the summary from the model: a small model
    asked for the words too tended to copy the start of the word list."""
    likes, dislikes = data_words(rated)
    raw = client.generate(message + summary_prompt(likes, dislikes), schema=summary_schema(), system=system_prompt())
    if not raw:
        return None
    try:
        summary = (json.loads(raw) or {}).get("summary")
    except (ValueError, AttributeError):
        # A small model often runs past its length limit and the JSON is cut off: keep the text it wrote.
        m = re.search(r'"summary"\s*:\s*"(.*)', raw, re.S)
        summary = m.group(1).rstrip('"}\n ').replace('\\"', '"') if m else None
    return {"summary": finish_sentence(summary), "likes": likes, "dislikes": dislikes}


def finish_sentence(text: Any) -> str:
    """At most SUMMARY_WORDS words, cut back to the last full sentence when there is one."""
    cut = clean_summary(text)
    end = max(cut.rfind(". "), cut.rfind("."), cut.rfind("!"))
    return cut[:end + 1] if end > len(cut) // 2 else cut


def ask_haiku(api: Any, message: str, usage: Dict[str, int]) -> Optional[Dict[str, Any]]:
    msg = api.messages.create(**build_params(message))
    usage["in"] += msg.usage.input_tokens
    usage["out"] += msg.usage.output_tokens
    if msg.stop_reason != "end_turn":
        return None
    try:
        return json.loads(msg.content[0].text)
    except (ValueError, IndexError, AttributeError):
        return None


def main(argv: Optional[List[str]] = None, api: Any = None, ollama: Any = None) -> int:
    ap = argparse.ArgumentParser(description="Written taste profiles from ratings (Ollama by default, free).")
    ap.add_argument("--db", default=None, help="database (default: $DATABASE_PATH or catalogue_full.db)")
    ap.add_argument("--model", default=None, help="Ollama model (default: wishmatch.tagging_model)")
    ap.add_argument("--haiku", action="store_true", help="use Claude Haiku instead of Ollama (costs money)")
    ap.add_argument("--yes", action="store_true", help="with --haiku: call the API (without it: estimate only)")
    ap.add_argument("--push", metavar="URL", default=None, help="also PUT each new profile to <URL>/api/rec/movies/profile/<id>")
    ap.add_argument("--overwrite-edited", action="store_true", help="also replace profiles that were edited by hand")
    args = ap.parse_args(argv)

    db_path = args.db or os.getenv("DATABASE_PATH") or "catalogue_full.db"
    db = DatabaseManager(db_path)
    jobs = []      # (person, message, rated titles)
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
            jobs.append((person, build_user_message(loved, disliked), rated_titles(conn, person["id"])))
    for person in skipped:
        print(f"  {person['name']}: profile was edited by hand, kept (--overwrite-edited replaces it)")

    if not jobs:
        print("Nothing to do.")
        return 0

    usage = {"in": 0, "out": 0}
    if args.haiku:
        model = MODEL
        chars = sum(len(system_prompt()) + len(m) for _, m, _ in jobs)
        tokens_in, tokens_out, estimate = estimate_cost(chars, len(jobs))
        print(f"{len(jobs)} profile(s) with {model}: about {tokens_in:,} input + {tokens_out:,} output tokens, "
              f"estimated cost ${estimate:.4f} (standard, non-batch prices).")
        if not args.yes:
            command = "uv run --extra llm python utils/wishmatch_profiles.py --haiku"
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
        ask = lambda message, rated: ask_haiku(api, message, usage)  # noqa: E731
    else:
        model = args.model or rules_model()
        if not model:
            print("No Ollama model: set wishmatch.tagging_model or pass --model.")
            return 1
        if ollama is None:
            from utils.mood_tag_ollama import client as ollama_client
            ollama = ollama_client(model)
            if not ollama.status().get("has_model"):
                print(f"Ollama has no {model}; run `ollama pull {model}` first.")
                return 1
        print(f"{len(jobs)} profile(s) with {model} in Ollama (free).")
        ask = lambda message, rated: ask_ollama(ollama, message, rated)  # noqa: E731

    for person, message, rated in jobs:
        raw = ask(message, rated)
        if not isinstance(raw, dict):
            print(f"  {person['name']}: the answer was not usable, skipped")
            continue
        summary = clean_summary(raw.get("summary"))
        likes = clean_words(raw.get("likes"), LIKES_MAX)
        dislikes = clean_words(raw.get("dislikes"), DISLIKES_MAX, exclude=tuple(likes))
        if not summary:
            print(f"  {person['name']}: the answer had no summary, skipped")
            continue
        db.set_wishmatch_profile(person["id"], summary, likes, dislikes, model, edited=False)
        print(f"  {person['name']}: {summary}\n    likes: {', '.join(likes) or '-'}  |  dislikes: {', '.join(dislikes) or '-'}")
        if args.push:
            ok = push(args.push, person["id"], {"summary": summary, "likes": likes, "dislikes": dislikes, "model": model})
            print(f"    pushed to {args.push}" if ok else "    NOT pushed")
    if args.haiku:
        print(f"Spent ${cost_of(usage['in'], usage['out']):.4f} ({usage['in']:,} input + {usage['out']:,} output tokens).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
