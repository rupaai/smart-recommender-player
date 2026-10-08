"""
LLM mood tags (utils/mood_tag.py)

Tags each film with the dials, content flags and closed vocabularies from
docs/VIBE_QUESTION_BANK.md §4 (defined in services/vibe_vocab.py), using
Claude Haiku 5.5 through the Message Batches API (half price, asynchronous).
Results land in media_items.vibe_tags as JSON.

Spends real API money, so every command that submits work prints the
estimated cost first and asks before sending anything.

    # 1. A 200-film pilot: shows real cost, a cost projection and sample tags
    uv run --extra llm python utils/mood_tag.py pilot

    # 2. Everything else (asks again, with a projection from the pilot)
    uv run --extra llm python utils/mood_tag.py run

    # Other commands
    uv run --extra llm python utils/mood_tag.py collect      # resume after Ctrl-C
    uv run --extra llm python utils/mood_tag.py status       # batches and spend so far
    uv run --extra llm python utils/mood_tag.py show "Paddington 2"
    uv run --extra llm python utils/mood_tag.py preview      # print one request, no API call

Needs ANTHROPIC_API_KEY in .env. DATABASE_PATH overrides the catalogue
(default: catalogue_full.db). Run utils/backfill_tmdb_vibe.py first: the
tagger reads TMDB keywords, tagline and review snippets when they exist.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import DatabaseManager  # noqa: E402
from services.vibe_vocab import (  # noqa: E402
    CONTENT_FLAGS, DIAL_NAMES, FEELS, FEELS_GROUPS, GOOD_FOR, KNOWN, PALETTE,
    SEASON_WEATHER, SETTING, STORY_ERA, TIME_OF_DAY, dial_rubric_lines,
)

load_dotenv()

MODEL = "claude-haiku-5-5"
MAX_TOKENS = 1024
BATCH_SIZE = 10_000          # API limit is 100k requests / 256 MB; smaller batches finish sooner
POLL_SECONDS = 30
PILOT_SIZE = 200
REPORT_DIR = "reports"

# USD per million tokens, Batch API (50% off), prompts under 100k tokens.
# Source: platform.claude.com/docs/en/about-claude/pricing, checked 2026-10-08.
PRICE = {
    "input": 0.05,
    "cache_write": 0.0625,   # 5-minute cache write = 1.25x input
    "cache_read": 0.005,     # cache hit = 0.1x input
    "output": 0.25,
}
# Haiku 5.5's tokenizer: roughly 3.3 characters per token for English prose.
CHARS_PER_TOKEN = 3.3
EST_OUTPUT_TOKENS = 260      # measured by the pilot; used only before one has run

OVERVIEW_CHARS = 900
REVIEWS_CHARS = 900
KEYWORDS_MAX = 25


# ---------------------------------------------------------------------------
# Prompt and output schema
# ---------------------------------------------------------------------------

def build_system_prompt() -> str:
    feels = "\n".join(f"  - {group}: {', '.join(tags)}" for group, tags in FEELS_GROUPS.items())
    return f"""You tag films and TV series for a personal recommender. The tags let a short, playful quiz ("pick a drink", "how did today end?") find something that matches how the viewer feels tonight. Accuracy matters more than enthusiasm: a wrong "no gore" or "happy ending" can ruin someone's evening.

For each title you get its catalogue data: title, year, type, genres, runtime, country/language, overview, and when available the tagline, TMDB keywords and short audience review excerpts. Use your own knowledge of the title as well when you recognise it, and say how well you know it in `known` ("well", "somewhat", "not"). When you don't know it, infer carefully from the data and keep scores near the middle unless the data is clear.

## Dials
Score each dial as an integer 0-10 for how it feels to watch, judged against all films and series (not just its genre). Example titles anchor the scale:
{chr(10).join(dial_rubric_lines())}
For a TV series, score a typical episode and the series overall (pace, attention and talkiness per episode; ending_valence for the series' overall tone).

## Content flags
`flags`: list every flag that clearly applies (an on-screen or central plot element, not a passing mention). `flags_unsure`: flags that might apply but you can't confirm. Leave both empty when none apply. Allowed flags: {", ".join(CONTENT_FLAGS)}.

## Tags (use only the listed words)
- `feels`: 3-6 words for how it feels to watch, from:
{feels}
- `good_for`: 2-4 occasions, from: {", ".join(GOOD_FOR)}. "binge" is only for series.
- `setting`: 0-3 main settings, from: {", ".join(SETTING)}.
- `season_weather`: 0-2, only if the season or weather is a noticeable part of the mood, from: {", ".join(SEASON_WEATHER)}.
- `time_of_day`: one of {", ".join(TIME_OF_DAY)}.
- `palette`: 1-2 for the dominant look, from: {", ".join(PALETTE)}. Use "bw" only for black-and-white films.
- `story_era`: when the story is set (not the release year), one of: {", ".join(STORY_ERA)}.

## Vibe sentence
`vibe`: one sentence of at most 25 words describing how it feels to watch, in plain viewer language, without plot spoilers or the title. Example for Paddington 2: "A gentle, funny, warm-hearted caper that leaves you smiling; low stakes, lots of kindness, easy to follow."

Answer only with the JSON object."""


def output_schema() -> Dict[str, Any]:
    def str_array(values: List[str]) -> Dict[str, Any]:
        return {"type": "array", "items": {"type": "string", "enum": values}}

    return {
        "type": "object",
        "properties": {
            "known": {"type": "string", "enum": KNOWN},
            "dials": {
                "type": "object",
                "properties": {name: {"type": "integer"} for name in DIAL_NAMES},
                "required": DIAL_NAMES,
                "additionalProperties": False,
            },
            "flags": str_array(CONTENT_FLAGS),
            "flags_unsure": str_array(CONTENT_FLAGS),
            "feels": str_array(FEELS),
            "good_for": str_array(GOOD_FOR),
            "setting": str_array(SETTING),
            "season_weather": str_array(SEASON_WEATHER),
            "time_of_day": {"type": "string", "enum": TIME_OF_DAY},
            "palette": str_array(PALETTE),
            "story_era": {"type": "string", "enum": STORY_ERA},
            "vibe": {"type": "string"},
        },
        "required": [
            "known", "dials", "flags", "flags_unsure", "feels", "good_for", "setting",
            "season_weather", "time_of_day", "palette", "story_era", "vibe",
        ],
        "additionalProperties": False,
    }


def _clip(text: Optional[str], limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


def build_user_message(item: Dict[str, Any]) -> str:
    kind = "TV series" if item.get("media_type") == "tv" else "Film"
    lines = [f"{kind}: {item.get('title')}" + (f" ({item['release_year']})" if item.get("release_year") else "")]
    if item.get("original_title") and item["original_title"] != item.get("title"):
        lines.append(f"Original title: {item['original_title']}")
    if item.get("genres"):
        lines.append(f"Genres: {item['genres']}")
    if item.get("runtime"):
        lines.append(f"Runtime: {item['runtime']} min" + (" per episode" if item.get("media_type") == "tv" else ""))
    origin = ", ".join(x for x in (item.get("origin_country"), item.get("original_language")) if x)
    if origin:
        lines.append(f"Country / language: {origin}")
    if item.get("tagline"):
        lines.append(f"Tagline: {item['tagline']}")
    lines.append(f"Overview: {_clip(item.get('overview'), OVERVIEW_CHARS) or '(none)'}")
    keywords = [k.strip() for k in (item.get("tier2_keywords") or "").split(",") if k.strip()]
    if keywords:
        lines.append(f"Keywords: {', '.join(keywords[:KEYWORDS_MAX])}")
    if item.get("tier2_reviews_summary"):
        lines.append(f"Audience reviews (excerpts): {_clip(item['tier2_reviews_summary'], REVIEWS_CHARS)}")
    return "\n".join(lines)


def build_params(item: Dict[str, Any], system_prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        # A lookup-and-score task: thinking adds output tokens without helping much.
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        # The rubric is identical for every request, so it is cached (cache reads cost 10%).
        "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": build_user_message(item)}],
    }


# ---------------------------------------------------------------------------
# Validation: the schema guarantees shape; this clamps values and dedupes.
# ---------------------------------------------------------------------------

def normalise_tags(raw: Dict[str, Any]) -> Dict[str, Any]:
    dials = {}
    for name in DIAL_NAMES:
        try:
            value = int(raw["dials"][name])
        except (KeyError, TypeError, ValueError):
            continue
        dials[name] = round(min(max(value, 0), 10) / 10.0, 2)

    def keep(values: Iterable[str], allowed: List[str], cap: int) -> List[str]:
        out: List[str] = []
        for v in values or []:
            if v in allowed and v not in out:
                out.append(v)
        return out[:cap]

    flags = keep(raw.get("flags"), CONTENT_FLAGS, len(CONTENT_FLAGS))
    unsure = [f for f in keep(raw.get("flags_unsure"), CONTENT_FLAGS, len(CONTENT_FLAGS)) if f not in flags]
    return {
        "known": raw.get("known") if raw.get("known") in KNOWN else "not",
        "dials": dials,
        "flags": flags,
        "flags_unsure": unsure,
        "feels": keep(raw.get("feels"), FEELS, 6),
        "good_for": keep(raw.get("good_for"), GOOD_FOR, 4),
        "setting": keep(raw.get("setting"), SETTING, 3),
        "season_weather": keep(raw.get("season_weather"), SEASON_WEATHER, 2),
        "time_of_day": raw.get("time_of_day") if raw.get("time_of_day") in TIME_OF_DAY else "mixed",
        "palette": keep(raw.get("palette"), PALETTE, 2),
        "story_era": raw.get("story_era") if raw.get("story_era") in STORY_ERA else None,
        "vibe": " ".join(str(raw.get("vibe") or "").split())[:300],
        "model": MODEL,
        "tagged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

ITEM_COLUMNS = (
    "mi.id, mi.media_type, mi.title, mi.original_title, mi.release_year, mi.genres, mi.runtime, "
    "mi.overview, mi.tagline, mi.tier2_keywords, mi.tier2_reviews_summary, mi.origin_country, "
    "mi.original_language, mi.vote_count"
)


def _rows(conn: sqlite3.Connection, sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _pending_ids(conn: sqlite3.Connection) -> set:
    """Items already inside a submitted, uncollected batch."""
    ids: set = set()
    for (blob,) in conn.execute("SELECT item_ids FROM mood_tag_batches WHERE status = 'submitted'"):
        ids.update(json.loads(blob))
    return ids


def select_pilot(conn: sqlite3.Connection, size: int = PILOT_SIZE) -> List[Dict[str, Any]]:
    """200 titles you can judge: your own rated films first, then a genre-spread
    of well-known films, some series, and some obscure films (the long tail is
    where tags matter most and the model knows least)."""
    untagged = "mi.vibe_tags IS NULL AND COALESCE(mi.overview, '') != ''"
    pending = _pending_ids(conn)
    chosen: Dict[int, Dict[str, Any]] = {}

    def add(rows: List[Dict[str, Any]], cap: int) -> None:
        n = 0
        for r in rows:
            if n >= cap or len(chosen) >= size:
                return
            if r["id"] in chosen or r["id"] in pending:
                continue
            chosen[r["id"]] = r
            n += 1

    # 1. Films you've rated (you can judge these tags at a glance).
    add(_rows(conn, f"""
        SELECT DISTINCT {ITEM_COLUMNS} FROM media_items mi
        JOIN watch_history w ON w.media_item_id = mi.id
        WHERE w.status = 'seen' AND {untagged}
        ORDER BY w.user_rating DESC"""), round(size * 0.35))

    # 2. Popular films, round-robin over first genre so it isn't all Drama.
    popular = _rows(conn, f"""
        SELECT {ITEM_COLUMNS} FROM media_items mi
        WHERE mi.media_type = 'movie' AND {untagged}
        ORDER BY mi.vote_count DESC LIMIT 1500""")
    by_genre: Dict[str, List[Dict[str, Any]]] = {}
    for r in popular:
        by_genre.setdefault((r.get("genres") or "Other").split(",")[0].strip(), []).append(r)
    spread: List[Dict[str, Any]] = []
    while any(by_genre.values()) and len(spread) < 300:
        for g in list(by_genre):
            if by_genre[g]:
                spread.append(by_genre[g].pop(0))
    add(spread, round(size * 0.35))

    # 3. Series.
    add(_rows(conn, f"""
        SELECT {ITEM_COLUMNS} FROM media_items mi
        WHERE mi.media_type = 'tv' AND {untagged}
        ORDER BY mi.vote_count DESC LIMIT 200"""), max(1, round(size * 0.15)))

    # 4. The long tail: low-vote films with an overview.
    add(_rows(conn, f"""
        SELECT {ITEM_COLUMNS} FROM media_items mi
        WHERE mi.media_type = 'movie' AND {untagged} AND COALESCE(mi.vote_count, 0) BETWEEN 1 AND 400
        ORDER BY (mi.id * 2654435761) % 1000003 LIMIT 300"""), size)

    # Top up from popularity if anything above came up short.
    add(popular, size)
    return list(chosen.values())[:size]


def select_remaining(conn: sqlite3.Connection, limit: int = 0, min_votes: int = 0,
                     media_type: Optional[str] = None) -> List[Dict[str, Any]]:
    clauses = ["mi.vibe_tags IS NULL", "COALESCE(mi.overview, '') != ''"]
    params: List[Any] = []
    if min_votes:
        clauses.append("COALESCE(mi.vote_count, 0) >= ?")
        params.append(min_votes)
    if media_type:
        clauses.append("mi.media_type = ?")
        params.append(media_type)
    sql = f"SELECT {ITEM_COLUMNS} FROM media_items mi WHERE {' AND '.join(clauses)} ORDER BY mi.vote_count DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    pending = _pending_ids(conn)
    return [r for r in _rows(conn, sql, tuple(params)) if r["id"] not in pending]


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def cost_of(usage: Dict[str, int]) -> float:
    return (
        usage.get("input_tokens", 0) * PRICE["input"]
        + usage.get("cache_creation_input_tokens", 0) * PRICE["cache_write"]
        + usage.get("cache_read_input_tokens", 0) * PRICE["cache_read"]
        + usage.get("output_tokens", 0) * PRICE["output"]
    ) / 1_000_000


def measured_cost_per_item(conn: sqlite3.Connection) -> Optional[float]:
    row = conn.execute(
        "SELECT SUM(cost_usd), SUM(succeeded + failed) FROM mood_tag_batches WHERE status = 'collected'"
    ).fetchone()
    if row and row[0] and row[1]:
        return row[0] / row[1]
    return None


def estimate(items: List[Dict[str, Any]], system_prompt: str) -> Dict[str, float]:
    """Character-based estimate (no API call). Assumes the rubric is read from
    cache on all but the first request of each batch; if caching misses, the
    'worst case' line applies."""
    sys_tokens = len(system_prompt) / CHARS_PER_TOKEN + 450   # + JSON schema / format overhead
    user_tokens = sum(len(build_user_message(i)) / CHARS_PER_TOKEN + 10 for i in items)
    out_tokens = EST_OUTPUT_TOKENS * len(items)
    cached = cost_of({
        "input_tokens": int(user_tokens),
        "cache_read_input_tokens": int(sys_tokens * len(items)),
        "output_tokens": out_tokens,
    })
    uncached = cost_of({
        "input_tokens": int(user_tokens + sys_tokens * len(items)),
        "output_tokens": out_tokens,
    })
    return {"cached": cached, "uncached": uncached, "per_item": cached / max(len(items), 1)}


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{prompt} Type 'yes' to continue: ").strip().lower() == "yes"
    except EOFError:
        return False


# ---------------------------------------------------------------------------
# Batch API
# ---------------------------------------------------------------------------

def client():
    try:
        import anthropic
    except ImportError:
        sys.exit("The anthropic package isn't installed. Run with: uv run --extra llm python utils/mood_tag.py ...")
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        sys.exit("ANTHROPIC_API_KEY is missing from .env.")
    return anthropic.Anthropic()


def submit(conn: sqlite3.Connection, items: List[Dict[str, Any]], label: str) -> List[str]:
    api = client()
    system_prompt, schema = build_system_prompt(), output_schema()
    batch_ids = []
    for start in range(0, len(items), BATCH_SIZE):
        chunk = items[start:start + BATCH_SIZE]
        requests = [
            {"custom_id": f"item-{it['id']}", "params": build_params(it, system_prompt, schema)}
            for it in chunk
        ]
        batch = api.messages.batches.create(requests=requests)
        conn.execute(
            "INSERT INTO mood_tag_batches (batch_id, label, model, item_ids) VALUES (?, ?, ?, ?)",
            (batch.id, label, MODEL, json.dumps([it["id"] for it in chunk])),
        )
        conn.commit()
        batch_ids.append(batch.id)
        print(f"[MoodTag] Submitted batch {batch.id} with {len(chunk)} titles.")
    return batch_ids


def wait_for(batch_ids: List[str]) -> None:
    api = client()
    remaining = list(batch_ids)
    started = time.time()
    while remaining:
        for bid in list(remaining):
            b = api.messages.batches.retrieve(bid)
            c = b.request_counts
            if b.processing_status == "ended":
                remaining.remove(bid)
                print(f"[MoodTag] {bid} finished: {c.succeeded} ok, {c.errored} errored, "
                      f"{c.expired} expired, {c.canceled} canceled.")
            else:
                done = c.succeeded + c.errored + c.expired + c.canceled
                total = done + c.processing
                print(f"[MoodTag] {bid}: {done}/{total} done "
                      f"({(time.time() - started) / 60:.0f} min so far). Ctrl-C is safe; "
                      f"run `collect` later.")
        if remaining:
            time.sleep(POLL_SECONDS)


def collect(conn: sqlite3.Connection, only: Optional[List[str]] = None) -> List[int]:
    """Fetch results for every submitted batch that has ended; returns tagged item ids."""
    api = client()
    rows = conn.execute(
        "SELECT batch_id FROM mood_tag_batches WHERE status = 'submitted'"
    ).fetchall()
    tagged: List[int] = []
    for (bid,) in rows:
        if only and bid not in only:
            continue
        b = api.messages.batches.retrieve(bid)
        if b.processing_status != "ended":
            print(f"[MoodTag] {bid} is still {b.processing_status}; try again later.")
            continue
        usage = {"input_tokens": 0, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0, "output_tokens": 0}
        ok = failed = 0
        for res in api.messages.batches.results(bid):
            item_id = int(res.custom_id.split("-", 1)[1])
            if res.result.type != "succeeded":
                failed += 1
                continue
            msg = res.result.message
            for key in usage:
                usage[key] += int(getattr(msg.usage, key, 0) or 0)
            if msg.stop_reason != "end_turn":
                failed += 1          # refusal / max_tokens: left untagged, retried next run
                continue
            text = next((blk.text for blk in msg.content if blk.type == "text"), "")
            try:
                tags = normalise_tags(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                failed += 1
                continue
            conn.execute(
                "UPDATE media_items SET vibe_tags = ?, vibe_tagged_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(tags, ensure_ascii=False), item_id),
            )
            tagged.append(item_id)
            ok += 1
        cost = cost_of(usage)
        conn.execute(
            "UPDATE mood_tag_batches SET status = 'collected', succeeded = ?, failed = ?, "
            "usage_json = ?, cost_usd = ? WHERE batch_id = ?",
            (ok, failed, json.dumps(usage), cost, bid),
        )
        conn.commit()
        print(f"[MoodTag] Collected {bid}: {ok} tagged, {failed} failed, real cost ${cost:.4f} "
              f"(tokens: {usage}).")
    return tagged


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def format_tags(item: Dict[str, Any], tags: Dict[str, Any]) -> str:
    dials = " · ".join(f"{k} {v:.1f}" for k, v in tags.get("dials", {}).items())
    flags = ", ".join(tags.get("flags") or []) or "none"
    if tags.get("flags_unsure"):
        flags += f" (unsure: {', '.join(tags['flags_unsure'])})"
    year = f" ({item['release_year']})" if item.get("release_year") else ""
    kind = "TV" if item.get("media_type") == "tv" else "film"
    return "\n".join([
        f"### {item['title']}{year}, {kind}, knows it: {tags.get('known')}",
        f"> {tags.get('vibe')}",
        "",
        f"- **Dials:** {dials}",
        f"- **Feels:** {', '.join(tags.get('feels') or [])}",
        f"- **Good for:** {', '.join(tags.get('good_for') or [])}",
        f"- **Flags:** {flags}",
        f"- **Setting:** {', '.join(tags.get('setting') or []) or '–'} · "
        f"season: {', '.join(tags.get('season_weather') or []) or '–'} · {tags.get('time_of_day')} · "
        f"palette: {', '.join(tags.get('palette') or [])} · set in: {tags.get('story_era')}",
        "",
    ])


def write_report(conn: sqlite3.Connection, item_ids: List[int], title: str, path: str) -> None:
    if not item_ids:
        return
    marks = ",".join("?" * len(item_ids))
    rows = _rows(conn, f"""
        SELECT mi.id, mi.title, mi.release_year, mi.media_type, mi.vibe_tags,
               (SELECT MAX(w.user_rating) FROM watch_history w
                 WHERE w.media_item_id = mi.id AND w.status = 'seen') AS my_rating
        FROM media_items mi WHERE mi.id IN ({marks})""", tuple(item_ids))
    rows.sort(key=lambda r: (r["my_rating"] is None, -(r["my_rating"] or 0)))
    batches = conn.execute(
        "SELECT SUM(cost_usd), SUM(succeeded), SUM(failed) FROM mood_tag_batches WHERE status='collected'"
    ).fetchone()
    per_item = measured_cost_per_item(conn) or 0.0
    left = conn.execute(
        "SELECT COUNT(*) FROM media_items WHERE vibe_tags IS NULL AND COALESCE(overview,'') != ''"
    ).fetchone()[0]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# {title}\n\n")
        fh.write(f"- Titles tagged in this run: {len(rows)}\n")
        fh.write(f"- Real spend so far (all runs): ${batches[0] or 0:.4f} for "
                 f"{batches[1] or 0} tagged / {batches[2] or 0} failed\n")
        fh.write(f"- Measured cost per title: ${per_item:.6f}\n")
        fh.write(f"- Untagged titles left: {left}, projected cost ${per_item * left:.2f}\n\n")
        fh.write("Films you've rated come first, so you can check the tags against what you remember.\n\n")
        for r in rows:
            if r["vibe_tags"]:
                fh.write(format_tags(r, json.loads(r["vibe_tags"])) + "\n")
    print(f"[MoodTag] Wrote {path}")


def print_projection(conn: sqlite3.Connection) -> None:
    per_item = measured_cost_per_item(conn)
    left = conn.execute(
        "SELECT COUNT(*) FROM media_items WHERE vibe_tags IS NULL AND COALESCE(overview,'') != ''"
    ).fetchone()[0]
    if per_item:
        print(f"[MoodTag] Measured ${per_item:.6f} per title. {left} untagged titles left: "
              f"about ${per_item * left:.2f} to tag them all.")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_preview(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    items = select_pilot(conn, 1)
    if not items:
        sys.exit("Nothing untagged to preview.")
    params = build_params(items[0], build_system_prompt(), output_schema())
    print(json.dumps(params, indent=2, ensure_ascii=False))
    est = estimate(select_pilot(conn, PILOT_SIZE), build_system_prompt())
    print(f"\nPilot estimate: ${est['cached']:.3f} (worst case if caching misses: ${est['uncached']:.3f})")
    full = select_remaining(conn)
    est_full = estimate(full, build_system_prompt())
    print(f"Whole catalogue ({len(full)} titles) estimate: ${est_full['cached']:.2f} "
          f"(worst case ${est_full['uncached']:.2f})")


def cmd_pilot(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    items = select_pilot(conn, args.size)
    if not items:
        sys.exit("Nothing untagged to pilot.")
    est = estimate(items, build_system_prompt())
    print(f"[MoodTag] Pilot: {len(items)} titles with {MODEL} on the Batch API.")
    print(f"[MoodTag] Estimated cost ${est['cached']:.3f} (worst case ${est['uncached']:.3f}).")
    if not confirm("Submit the pilot?", args.yes):
        print("[MoodTag] Nothing sent.")
        return
    ids = submit(conn, items, "pilot")
    wait_for(ids)
    tagged = collect(conn, ids)
    write_report(conn, tagged, "Mood-tag pilot", os.path.join(REPORT_DIR, "mood_tag_pilot.md"))
    print_projection(conn)


def cmd_run(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    items = select_remaining(conn, args.limit, args.min_votes, args.media_type)
    if not items:
        sys.exit("Everything is tagged already.")
    per_item = measured_cost_per_item(conn)
    est = estimate(items, build_system_prompt())
    print(f"[MoodTag] {len(items)} untagged titles, in {(len(items) - 1) // BATCH_SIZE + 1} batch(es).")
    if per_item:
        print(f"[MoodTag] From the pilot's measured cost: about ${per_item * len(items):.2f}.")
    print(f"[MoodTag] Character-based estimate: ${est['cached']:.2f} (worst case ${est['uncached']:.2f}).")
    if not confirm("Submit?", args.yes):
        print("[MoodTag] Nothing sent.")
        return
    ids = submit(conn, items, "run")
    wait_for(ids)
    tagged = collect(conn, ids)
    write_report(conn, tagged[:300], "Mood-tag run (first 300)", os.path.join(REPORT_DIR, "mood_tag_run.md"))
    print_projection(conn)


def cmd_collect(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    tagged = collect(conn)
    write_report(conn, tagged[:300], "Mood tags collected", os.path.join(REPORT_DIR, "mood_tag_collected.md"))
    print_projection(conn)


def cmd_status(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    total = 0.0
    for bid, created, label, status, ok, failed, cost in conn.execute(
        "SELECT batch_id, created_at, label, status, succeeded, failed, cost_usd "
        "FROM mood_tag_batches ORDER BY created_at"
    ):
        total += cost or 0
        print(f"{created}  {label:5}  {status:9}  {ok or 0:6} ok  {failed or 0:4} failed  "
              f"${cost or 0:.4f}  {bid}")
    tagged = conn.execute("SELECT COUNT(*) FROM media_items WHERE vibe_tags IS NOT NULL").fetchone()[0]
    print(f"Total spend ${total:.4f}. {tagged} titles tagged.")
    print_projection(conn)


def cmd_show(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    rows = _rows(conn, "SELECT id, title, release_year, media_type, vibe_tags FROM media_items "
                       "WHERE title LIKE ? AND vibe_tags IS NOT NULL ORDER BY vote_count DESC LIMIT 5",
                 (f"%{args.title}%",))
    if not rows:
        print("No tagged title matches.")
    for r in rows:
        print(format_tags(r, json.loads(r["vibe_tags"])))


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM mood tags via the Batch API")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pilot")
    p.add_argument("--size", type=int, default=PILOT_SIZE)
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    r = sub.add_parser("run")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--min-votes", type=int, default=0)
    r.add_argument("--media-type", choices=["movie", "tv"])
    r.add_argument("--yes", action="store_true")
    sub.add_parser("collect")
    sub.add_parser("status")
    sub.add_parser("preview")
    s = sub.add_parser("show")
    s.add_argument("title")
    args = ap.parse_args()

    db_path = os.getenv("DATABASE_PATH") or "catalogue_full.db"
    DatabaseManager(db_path)              # applies migrations (v6 adds the vibe columns)
    conn = sqlite3.connect(db_path)
    try:
        {"pilot": cmd_pilot, "run": cmd_run, "collect": cmd_collect, "status": cmd_status,
         "show": cmd_show, "preview": cmd_preview}[args.cmd](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
