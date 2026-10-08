"""
Vibe documents (services/vibe_document.py)

One paragraph per title describing what it is and how it feels (design doc
§2.1). Phase 3 embeds these with bge-small; the clusters are named from them.

    Paddington 2 (2017). Tagline. A bear takes odd jobs to buy a pop-up book...
    Keywords: kindness, family, prison, heist. Viewers: "pure joy" | "...".
    Mood: A gentle, warm caper. Feels: warm, cozy. Calm, gentle; light, breezy.
    Good for: winding down, family night. Notes: "..."

Sources, best first: overview + tagline, TMDB keywords, review snippets,
LLM mood tags (vibe_tags), the profiles' own notes. Every part is optional;
a title with only an overview still gets a document.

bge-small reads at most 512 tokens (~2000 characters), so each part has a
budget and the whole thing is clipped at MAX_CHARS. The mood part comes before
the reviews so clipping never drops it.

    uv run python -m services.vibe_document "Paddington 2"   # print one
    uv run python -m services.vibe_document --stats            # coverage
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any, Dict, Iterator, List, Optional, Tuple

from services.vibe_vocab import DIALS

MAX_CHARS = 1900
OVERVIEW_CHARS = 700
KEYWORDS_MAX = 20
REVIEWS_CHARS = 450
NOTES_CHARS = 250
# A dial only goes into the text when it is clearly at one end. Middling
# values say nothing in words and would just dilute the embedding.
DIAL_LOW, DIAL_HIGH = 0.25, 0.75


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0] + "…"


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?…\"" else text + "."


def dial_words(dials: Dict[str, Any]) -> List[str]:
    """The labels of dials that sit clearly at one end, e.g. 'calm, gentle'."""
    words = []
    for name, spec in DIALS.items():
        value = dials.get(name)
        if not isinstance(value, (int, float)):
            continue
        if value <= DIAL_LOW:
            words.append(str(spec["low"]))
        elif value >= DIAL_HIGH:
            words.append(str(spec["high"]))
    return words


def mood_text(tags: Optional[Dict[str, Any]]) -> str:
    if not tags:
        return ""
    parts = []
    if tags.get("vibe"):
        parts.append(f"Mood: {_sentence(tags['vibe'])}")
    if tags.get("feels"):
        parts.append(f"Feels: {', '.join(tags['feels'])}.")
    words = dial_words(tags.get("dials") or {})
    if words:
        parts.append(_sentence("; ".join(words).capitalize()))
    if tags.get("good_for"):
        parts.append(f"Good for: {', '.join(tags['good_for'])}.")
    place = [*tags.get("setting", []), *tags.get("season_weather", [])]
    if place:
        parts.append(f"Setting: {', '.join(place)}.")
    return " ".join(parts)


def build_document(item: Dict[str, Any], notes: Optional[List[str]] = None) -> str:
    """item: a media_items row as a dict (title, release_year, overview,
    tagline, tier2_keywords, tier2_reviews_summary, vibe_tags as JSON text or
    dict). notes: free-text notes from watch_history."""
    head = item.get("title") or "Untitled"
    if item.get("release_year"):
        head += f" ({item['release_year']})"
    if item.get("media_type") == "tv":
        head += ", series"
    parts = [_sentence(head)]
    if item.get("tagline"):
        parts.append(_sentence(item["tagline"]))
    if item.get("overview"):
        parts.append(_sentence(_clip(item["overview"], OVERVIEW_CHARS)))
    if item.get("genres"):
        parts.append(f"Genres: {item['genres']}.")
    if item.get("tier2_keywords"):
        kws = [k.strip() for k in item["tier2_keywords"].split(",") if k.strip()][:KEYWORDS_MAX]
        parts.append(f"Keywords: {', '.join(kws)}.")

    tags = item.get("vibe_tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            tags = None
    mood = mood_text(tags)
    if mood:
        parts.append(mood)

    if item.get("tier2_reviews_summary"):
        parts.append(f"Viewers: {_clip(item['tier2_reviews_summary'], REVIEWS_CHARS)}")
    clean_notes = [n.strip() for n in notes or [] if n and n.strip()]
    if clean_notes:
        parts.append(f"Notes: \"{_clip(' / '.join(clean_notes), NOTES_CHARS)}\"")

    doc = " ".join(p for p in parts if p)
    return doc if len(doc) <= MAX_CHARS else doc[: MAX_CHARS].rsplit(" ", 1)[0] + "…"


_COLUMNS = ("id, media_type, title, release_year, overview, tagline, genres, "
            "tier2_keywords, tier2_reviews_summary, vibe_tags")


def iter_documents(conn: sqlite3.Connection, media_type: Optional[str] = None,
                   ids: Optional[List[int]] = None) -> Iterator[Tuple[int, str]]:
    """(media_item_id, document) for every titled item, in id order."""
    notes: Dict[int, List[str]] = {}
    for item_id, note in conn.execute(
        "SELECT media_item_id, notes FROM watch_history WHERE notes IS NOT NULL AND TRIM(notes) != ''"
    ):
        notes.setdefault(item_id, []).append(note)

    where, args = ["title IS NOT NULL"], []
    if media_type:
        where.append("media_type = ?")
        args.append(media_type)
    if ids:
        where.append(f"id IN ({','.join('?' * len(ids))})")
        args.extend(ids)
    cur = conn.execute(f"SELECT {_COLUMNS} FROM media_items WHERE {' AND '.join(where)} ORDER BY id", args)
    names = [d[0] for d in cur.description]
    for row in cur:
        item = dict(zip(names, row))
        yield item["id"], build_document(item, notes.get(item["id"]))


def coverage(conn: sqlite3.Connection) -> Dict[str, Any]:
    q = ("SELECT COUNT(*), SUM(overview IS NOT NULL AND overview != ''), SUM(tagline IS NOT NULL), "
         "SUM(tier2_keywords IS NOT NULL), SUM(tier2_reviews_summary IS NOT NULL), "
         "SUM(vibe_tags IS NOT NULL) FROM media_items WHERE title IS NOT NULL")
    total, overview, tagline, kw, rev, tags = (v or 0 for v in conn.execute(q).fetchone())
    pct = lambda n: f"{n} ({100 * n / total:.0f}%)" if total else "0"  # noqa: E731
    return {"titles": total, "overview": pct(overview), "tagline": pct(tagline),
            "keywords": pct(kw), "reviews": pct(rev), "mood_tags": pct(tags)}


def main(argv: List[str]) -> None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from database import DatabaseManager

    db_path = os.getenv("DATABASE_PATH") or "catalogue_full.db"
    DatabaseManager(db_path)                      # runs migrations
    conn = sqlite3.connect(db_path)
    if not argv or argv[0] == "--stats":
        for k, v in coverage(conn).items():
            print(f"{k:>10}: {v}")
        return
    title = " ".join(argv)
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM media_items WHERE title = ? COLLATE NOCASE ORDER BY vote_count DESC LIMIT 3", (title,))]
    if not ids:
        print(f"No title '{title}' in {db_path}")
    for _, doc in iter_documents(conn, ids=ids):
        print(doc, f"\n({len(doc)} characters)\n")


if __name__ == "__main__":
    main(sys.argv[1:])
