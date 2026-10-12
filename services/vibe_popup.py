"""
"Guess the vibe" pop-up engine (services/vibe_popup.py)

Design doc §3 and docs/VIBE_QUESTION_BANK.md §1–2. The pop-up asks about
you and your evening, one question at a time (up to 6). Every answer updates
a *belief* (a target and confidence per dial, plus filters and tag
boosts/penalties), and the same candidate pool is re-ranked under it, so
results change live behind the pop-up.

    score = pool score (quality + taste + vibe similarity, from the recommender)
          + FACET_WEIGHT · Σ conf · (1 − |film_dial − target|)
          + tag / keyword / genre boosts and penalties
    hard filters drop films, unless fewer than MIN_SURVIVORS would be left,
    in which case they become penalties (the bank's "strong penalty" rule).

Choosing the next question (bank §2): eligible questions are simulated
option by option against the pool; a question is worth asking only if the
top 10 would change (value ≥ MIN_VALUE). Options that would leave fewer than
10 fitting films (avoid options: that remove < 5 % of the top 50) are hidden,
and a question needs 3 live options. Fixed slots: the first question is about
you, one avoid question by question 4, closers only after question 4.
Stops after MAX_QUESTIONS, when the top 5 hasn't moved for two answers, or
on a clear gap between #5 and #6.

Facets come from training/build_vibe_index.py (media_items.facets), with
the LLM mood tags' dials as a fallback. Without either, dial answers can't
split the pool, those questions score 0 and are skipped automatically.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from services.vibe_questions import ABOUT_YOU, ALTS, AVOID, BY_ID, CLOSERS, FUN

MAX_QUESTIONS = 6
MIN_VALUE = 0.3
FACET_WEIGHT = 2.0
STRONG, WEAK = 0.7, 0.4
MIN_SURVIVORS = 30
LIVE_FIT = 10
AVOID_SHARE = 0.05
POOL_TOP = 300
GAP_STOP = 1.5
SESSION_TTL = 3 * 3600

STOPWORDS = {"a", "an", "the", "and", "or", "but", "to", "of", "for", "with", "something", "some", "movie", "film",
             "show", "watch", "tonight", "i", "we", "me", "us", "want", "like", "really", "just", "please", "maybe",
             "kind", "sort", "that", "this", "is", "be", "in", "on", "at", "it", "my", "our"}


# ---------------------------------------------------------------------------
# Film features
# ---------------------------------------------------------------------------

def _json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        out = json.loads(value)
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}


def features(item: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the belief can test on one film, computed once per item."""
    fx = item.get("_popup_fx")
    if fx is not None:
        return fx
    tags = _json(item.get("vibe_tags"))
    facets = {k: v for k, v in _json(item.get("facets")).items() if isinstance(v, (int, float))}
    for k, v in (tags.get("dials") or {}).items():
        if isinstance(v, (int, float)):
            facets.setdefault(k, float(v))
    kws = {k.strip().lower() for k in (item.get("tier2_keywords") or "").split(",") if k.strip()}
    flags = {f.replace("_", " ") for f in (tags.get("flags") or [])}
    fx = {
        "facets": facets,
        "feels": set(tags.get("feels") or []),
        "good": set(tags.get("good_for") or []),
        "palette": set(tags.get("palette") or []),
        "kw": kws,
        "flags": flags,
        "tagged": bool(tags),
        "genres": {g.strip().lower() for g in (item.get("genres") or "").split(",") if g.strip()},
        "runtime": int(item.get("runtime") or 0),
        "year": int(item.get("release_year") or 0),
        "lang": (item.get("original_language") or "").lower(),
        "rating": float(item.get("rating") or 0.0),
        "votes": int(item.get("vote_count") or 0),
        "cluster": item.get("vibe_cluster_id"),
        "tv": item.get("media_type") == "tv",
    }
    item["_popup_fx"] = fx
    return fx


def _has_tag(fx: Dict[str, Any], term: str) -> bool:
    if term in fx["feels"] or term in fx["good"]:
        return True
    return any(term in k for k in fx["kw"])


def _has_kw(fx: Dict[str, Any], term: str) -> bool:
    return any(term in k for k in fx["kw"]) or any(term in f or f in term for f in fx["flags"])


# ---------------------------------------------------------------------------
# Belief
# ---------------------------------------------------------------------------

def new_belief() -> Dict[str, Any]:
    return {"dials": {}, "max": {}, "min": {}, "feels+": [], "feels-": [], "good+": [], "kw+": [], "kw-": [],
            "kwx": [], "genre+": [], "genrex": [], "flags": {}, "cluster": {}, "items": {}, "profile": None}


def apply_effects(belief: Dict[str, Any], fx: Dict[str, Any]) -> Dict[str, Any]:
    b = copy.deepcopy(belief)
    for dial, t in (fx.get("set") or {}).items():
        b["dials"][dial] = [float(t), max(STRONG, b["dials"].get(dial, [0, 0])[1])]
    for dial, t in (fx.get("weak") or {}).items():
        cur = b["dials"].get(dial)
        if not cur or cur[1] < WEAK:
            b["dials"][dial] = [float(t), WEAK]
    for dial, d in (fx.get("shift") or {}).items():
        t, c = b["dials"].get(dial, [0.5, 0.0])
        b["dials"][dial] = [min(max(t + d, 0.0), 1.0), min(c + 0.2, 0.9)]
    for key in ("max", "min"):
        for dial, v in (fx.get(key) or {}).items():
            cur = b[key].get(dial)
            b[key][dial] = (min(cur, v) if key == "max" else max(cur, v)) if cur is not None else v
    for key in ("feels+", "feels-", "good+", "kw+", "kw-", "kwx", "genre+", "genrex"):
        for v in fx.get(key) or []:
            v = v.lower() if key.startswith(("kw", "genre")) else v
            if v not in b[key]:
                b[key].append(v)
    for key in ("runtime_max", "runtime_soft", "quality_min", "slow_quality", "foreign_quality"):
        if key in fx:
            cur = b["flags"].get(key)
            better = min if key.startswith("runtime") else max
            b["flags"][key] = better(cur, fx[key]) if cur is not None else fx[key]
    for key in ("long_bonus", "media", "tv_bonus", "year", "english", "foreign_bonus", "popular", "obscure",
                "seen_only", "palette"):
        if key in fx:
            b["flags"][key] = fx[key]
    for cid, w in (fx.get("cluster") or {}).items():
        b["cluster"][cid] = b["cluster"].get(cid, 0.0) + w
    for iid, w in (fx.get("items") or {}).items():
        b["items"][iid] = b["items"].get(iid, 0.0) + w
    if fx.get("profile"):
        b["profile"] = fx["profile"]
    return b


def _filters(belief: Dict[str, Any], stats: Dict[str, Any]) -> List[Tuple[str, Callable[[Dict[str, Any]], float]]]:
    """Hard filters as (name, overshoot(fx)) — 0 means the film passes."""
    out: List[Tuple[str, Callable[[Dict[str, Any]], float]]] = []
    for dial, v in belief["max"].items():
        out.append((f"{dial}≤{v}", lambda fx, d=dial, v=v: max(0.0, fx["facets"].get(d, v) - v)))
    for dial, v in belief["min"].items():
        out.append((f"{dial}≥{v}", lambda fx, d=dial, v=v: max(0.0, v - fx["facets"].get(d, v))))
    if belief["kwx"]:
        out.append(("kwx", lambda fx: 1.0 if any(_has_kw(fx, k) for k in belief["kwx"]) else 0.0))
    if belief["genrex"]:
        out.append(("genrex", lambda fx: 1.0 if fx["genres"] & set(belief["genrex"]) else 0.0))
    f = belief["flags"]
    if f.get("runtime_max"):
        rmax = f["runtime_max"]
        # TV runtime is per episode: "bed in an hour" still allows a 45-minute episode.
        out.append(("runtime", lambda fx: 0.0 if not fx["runtime"] or fx["runtime"] <= (min(rmax, 50) if fx["tv"] else rmax)
                    else 1.0))
    if f.get("media") == "tv":
        out.append(("media", lambda fx: 0.0 if fx["tv"] else 1.0))
    if f.get("year"):
        lo, hi = f["year"]
        out.append(("year", lambda fx: 0.0 if not fx["year"] or lo <= fx["year"] <= hi else 1.0))
    if f.get("english"):
        out.append(("english", lambda fx: 0.0 if fx["lang"] in ("en", "") or "animation" in fx["genres"] else 1.0))
    if f.get("quality_min"):
        out.append(("quality", lambda fx: 0.0 if fx["rating"] >= f["quality_min"] else 1.0))
    if f.get("foreign_quality"):
        out.append(("foreign_quality", lambda fx: 0.0 if fx["lang"] in ("en", "") or fx["rating"] >= f["foreign_quality"]
                    else 1.0))
    if f.get("slow_quality"):
        out.append(("slow_quality", lambda fx: 0.0 if fx["facets"].get("pace", 1.0) >= 0.4 or fx["rating"] >= f["slow_quality"]
                    else 1.0))
    if f.get("popular"):
        out.append(("popular", lambda fx: 0.0 if fx["votes"] >= stats["votes_p90"] else 1.0))
    if f.get("seen_only"):
        out.append(("seen_only", lambda fx: 1.0))          # the pool holds unseen titles only
    return out


def pool_stats(pool: List[Dict[str, Any]]) -> Dict[str, Any]:
    votes = sorted(features(it)["votes"] for it in pool) or [0]
    return {"votes_p90": votes[int(len(votes) * 0.9) - 1 if len(votes) > 1 else 0],
            "votes_median": votes[len(votes) // 2]}


def score_pool(pool: List[Dict[str, Any]], belief: Dict[str, Any],
               stats: Optional[Dict[str, Any]] = None) -> List[Tuple[float, Dict[str, Any]]]:
    """(score, item) for the films that survive, best first."""
    stats = stats or pool_stats(pool)
    min_survivors = min(MIN_SURVIVORS, max(1, len(pool) // 3))
    filters = _filters(belief, stats)
    alive = list(pool)
    penalties: Dict[int, float] = {}
    for name, over in filters:
        kept = [it for it in alive if over(features(it)) <= 0]
        if len(kept) >= min_survivors:
            alive = kept
        else:                       # too strict for this pool: penalise instead of removing
            for it in alive:
                o = over(features(it))
                if o > 0:
                    penalties[id(it)] = penalties.get(id(it), 0.0) + 2.0 + 3.0 * o

    f = belief["flags"]
    out = []
    for it in alive:
        fx = features(it)
        s = float(it.get("_popup_base", it.get("recommendation_score") or 0.0)) - penalties.get(id(it), 0.0)
        fit = 0.0
        for dial, (t, c) in belief["dials"].items():
            v = fx["facets"].get(dial)
            fit += c * (0.5 if v is None else 1 - abs(v - t))
        s += FACET_WEIGHT * fit
        s += min(0.4, 0.15 * sum(_has_tag(fx, t) for t in belief["feels+"] + belief["good+"]))
        s -= 0.3 * sum(_has_tag(fx, t) for t in belief["feels-"])
        s += min(0.3, 0.1 * sum(_has_kw(fx, k) for k in belief["kw+"]))
        s -= 0.3 * sum(_has_kw(fx, k) for k in belief["kw-"])
        if belief["genre+"] and fx["genres"] & set(belief["genre+"]):
            s += 0.2
        if belief["kwx"] and not fx["tagged"]:
            s -= 0.1                          # unknown content: just below known-clean films
        if f.get("tv_bonus") and fx["tv"]:
            s += f["tv_bonus"]
        if f.get("long_bonus") and fx["runtime"] >= 140:
            s += 0.1
        if f.get("foreign_bonus") and fx["lang"] not in ("en", ""):
            s += f["foreign_bonus"]
        if f.get("obscure") and fx["votes"] < stats["votes_median"]:
            s += 0.2
        if f.get("palette") and f["palette"] in fx["palette"]:
            s += 0.3
        if fx["cluster"] is not None:
            s += belief["cluster"].get(fx["cluster"], 0.0)
        s += belief["items"].get(it.get("id"), 0.0)
        out.append((s, it))
    out.sort(key=lambda t: -t[0])
    return out


def top_ids(scored: List[Tuple[float, Dict[str, Any]]], n: int) -> List[int]:
    return [it["id"] for _, it in scored[:n]]


def option_fit(fx_opt: Dict[str, Any], stats: Dict[str, Any]) -> Callable[[Dict[str, Any]], bool]:
    """item -> does this film fit the option on its own terms? (live options, posters)"""
    one = apply_effects(new_belief(), fx_opt)
    filters = [over for _, over in _filters(one, stats)]
    targets = fx_opt.get("set") or {}
    tags = (fx_opt.get("feels+") or []) + (fx_opt.get("good+") or [])
    kws = fx_opt.get("kw+") or []
    tag_only = not targets and not one["max"] and not one["min"] and bool(tags or kws)

    def check(item: Dict[str, Any]) -> bool:
        fx = features(item)
        if any(over(fx) > 0 for over in filters):
            return False
        for dial, t in targets.items():
            v = fx["facets"].get(dial)
            if v is None or abs(v - t) > 0.25:
                return False
        if tag_only:
            return any(_has_tag(fx, t) for t in tags) or any(_has_kw(fx, k) for k in kws)
        return True
    return check


# ---------------------------------------------------------------------------
# Text that already answers a question
# ---------------------------------------------------------------------------

IMPLIED = [
    (r"\bfriends?\b|\bparty\b", "D1", "friends"),
    (r"\b(alone|by myself|just me|solo)\b", "D1", "me"),
    (r"\b(date night|with (him|my (boyfriend|bf|partner))|cuddl\w*)\b", "D1", "cuddled"),
    (r"\b(long day|long shift|after work|exhausted|tired|stressed|bored|great day)\b", "A3", None),
    (r"\b(short|quick|under \d|hours?|long one|epic)\b|\bseries\b|\bbinge\b|\bepisodes?\b", "A4", None),
    (r"\b(new|haven'?t seen|never seen|rewatch|again)\b", "C1", None),
    (r"\b(subtitles?|english|foreign)\b", "C3", None),
    (r"\b(\d0s|\d{4}s|classic|old|recent|new release)\b", "C2", None),
    (r"\b(no|not|nothing|without)\s+(\w+\s+)?(scary|sad|gore|gory|violent|horror)\b", "H1", None),
]


def implied_answers(query: Optional[str], request: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """{question id: option id or None}. None means "already covered by your
    text, don't ask"; an option id is also applied to the belief."""
    text = (query or "").lower()
    out: Dict[str, Optional[str]] = {}
    for pattern, qid, oid in IMPLIED:
        if qid not in out and re.search(pattern, text):
            out[qid] = oid
    if request.get("duration_preference"):
        out.setdefault("A4", None)
    if request.get("era_preference"):
        out.setdefault("C2", None)
    if request.get("media_type") == "tv":
        out.setdefault("A4", None)
    return out


def meaningful_words(query: Optional[str]) -> int:
    return len([w for w in re.findall(r"[a-z']+", (query or "").lower()) if w not in STOPWORDS and len(w) > 2])


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class PopupSession:
    def __init__(self, session_id: int, request: Dict[str, Any], pool: List[Dict[str, Any]],
                 cluster_labels: Optional[Dict[int, str]] = None, vibe_index: Any = None):
        self.id = session_id
        self.request = request
        self.cluster_labels = cluster_labels or {}
        self.vibe_index = vibe_index
        self.belief = new_belief()
        self.asked: List[Tuple[str, str]] = []         # (question id, option id | "skip" | "implied:<oid>")
        self.top5_history: List[Set[int]] = []
        self.current: Optional[Dict[str, Any]] = None
        self.done = False
        self.updated = time.time()
        self.set_pool(pool)
        for qid, oid in implied_answers(request.get("query"), request).items():
            if oid:
                self.belief = apply_effects(self.belief, self._option(BY_ID[qid], oid)["fx"])
            self.asked.append((qid, f"implied:{oid or ''}"))

    # -- pool -----------------------------------------------------------------
    def set_pool(self, pool: List[Dict[str, Any]]) -> None:
        for it in pool:
            it.setdefault("_popup_base", float(it.get("recommendation_score") or 0.0))
            features(it)
        self.pool = pool[:POOL_TOP] if len(pool) > POOL_TOP else pool
        self.stats = pool_stats(self.pool)

    def scored(self, belief: Optional[Dict[str, Any]] = None) -> List[Tuple[float, Dict[str, Any]]]:
        return score_pool(self.pool, belief or self.belief, self.stats)

    def ranked(self) -> List[Dict[str, Any]]:
        """The pool re-scored under the current answers, ready for finalize()."""
        out = []
        for s, it in self.scored():
            copy_it = {k: v for k, v in it.items() if not k.startswith("_popup")}
            copy_it["recommendation_score"] = round(s, 2)
            copy_it["popup_boost"] = round(s - float(it.get("_popup_base") or 0.0), 2)
            out.append(copy_it)
        return out

    # -- questions ------------------------------------------------------------
    @property
    def asked_ids(self) -> Set[str]:
        return {q for q, _ in self.asked}

    @property
    def answered_count(self) -> int:
        return sum(1 for _, a in self.asked if not a.startswith("implied:"))

    def _option(self, q: Dict[str, Any], oid: str) -> Dict[str, Any]:
        for o in q["options"]:
            if o["id"] == oid:
                return o
        raise KeyError(f"{q['id']} has no option {oid}")

    def _conf(self, dial: str) -> float:
        return self.belief["dials"].get(dial, [0.5, 0.0])[1]

    def _target(self, dial: str) -> Optional[float]:
        d = self.belief["dials"].get(dial)
        if d:
            return d[0]
        if dial in self.belief["max"]:
            return self.belief["max"][dial]
        if dial in self.belief["min"]:
            return self.belief["min"][dial]
        return None

    def _share(self, pred: Callable[[Dict[str, Any]], bool], n: int = POOL_TOP) -> float:
        items = self.pool[:n]
        return sum(1 for it in items if pred(features(it))) / max(len(items), 1)

    def _ask_when(self, qid: str) -> bool:
        b, n = self.belief, self.answered_count
        known = lambda d: self._conf(d) >= 0.5 or d in b["max"] or d in b["min"]  # noqa: E731
        feels_thin = len(b["feels+"]) + len(b["good+"]) < 2
        dark = self._target("darkness")
        rules = {
            "A1": not known("attention"), "A2": not known("energy"), "A3": n == 0,
            "A4": not b["flags"].get("runtime_max") and not b["flags"].get("media"),
            "B1": not known("weight"), "B5": not known("weight") and known("energy"),
            "B2": known("weight") and not known("ending_valence") and (self._target("weight") or 0) > 0.25,
            "B3": feels_thin and n >= 2, "B4": not known("fantasy"),
            "C1": True,
            "C2": self._era_spread() >= 3,
            "C3": self._share(lambda fx: fx["lang"] not in ("en", "")) >= 0.25 and (self._target("attention") or 0.5) >= 0.3,
            "D1": True, "D2": not known("attention"),
            "D3": b["profile"] == "together" and not known("weight"),
            "E1": not known("darkness"),
            "E2": dark is not None and dark >= 0.4 and not known("tension"),
            "E3": self._share(lambda fx: "comedy" in fx["genres"]) >= 0.3,
            "E4": (dark is not None and dark >= 0.5) or self._share(lambda fx: bool(fx["genres"] & {"action", "horror"})) >= 0.25,
            "F1": feels_thin, "F2": n >= 3, "F3": n >= 2 and not known("spectacle") and not known("talkiness"),
            "F4": feels_thin,
            "F5": 0.3 <= (self._target("attention") or 0.0) <= 0.7 and not known("talkiness"),
            "G1": not known("pace"), "G2": not known("pace"),
            "H1": True, "H2": max(self._target("weight") or 0, dark or 0) >= 0.5, "H3": True,
            "I1": n >= 4, "I2": n >= 4 and self.vibe_index is not None and self.vibe_index.is_ready(),
        }
        return rules.get(qid, False)

    def _era_spread(self) -> int:
        bands = [(0, 1965), (1966, 1999), (2000, 2012), (2013, 2100)]
        years = [features(it)["year"] for it in self.pool if features(it)["year"]]
        if not years:
            return 0
        return sum(1 for lo, hi in bands if sum(lo <= y <= hi for y in years) / len(years) >= 0.1)

    def _eligible(self, any_first: bool = False) -> List[Dict[str, Any]]:
        asked = self.asked_ids
        n = self.answered_count
        out = []
        for qid, q in BY_ID.items():
            if qid in asked or any(qid in alt and alt & asked for alt in ALTS):
                continue
            if n == 0 and qid not in ABOUT_YOU and not any_first:
                continue
            if qid in CLOSERS and n < 4:
                continue
            if qid in AVOID and asked & AVOID:
                continue
            if self._ask_when(qid):
                out.append(q)
        return out

    def _generated_options(self, q: Dict[str, Any], scored: List[Tuple[float, Dict[str, Any]]]) -> List[Dict[str, Any]]:
        top20 = [it for _, it in scored[:20]]
        if q["id"] == "I1":
            counts: Dict[int, int] = {}
            for it in top20:
                c = features(it)["cluster"]
                if c is not None:
                    counts[c] = counts.get(c, 0) + 1
            big = [c for c, k in sorted(counts.items(), key=lambda t: -t[1]) if k >= 2][:3]
            if len(big) < 3:
                return []
            opts = []
            for c in big:
                fxo = {"cluster": {o: (0.8 if o == c else -0.3) for o in big}}
                opts.append({"id": f"c{c}", "emoji": "🎞️", "label": self.cluster_labels.get(c, f"Group {c}").capitalize(),
                             "fx": fxo, "cluster": c})
            opts.append({"id": "none", "emoji": "🙅", "label": "None of these", "fx": {"cluster": {c: -0.4 for c in big}}})
            return opts
        if q["id"] == "I2":
            ids = [it["id"] for it in scored[:10]]
            vecs = {i: self.vibe_index.item_vector(i) for i in ids}
            ids = [i for i in ids if vecs[i] is not None]
            if len(ids) < 2:
                return []
            a, b = min(((x, y) for k, x in enumerate(ids) for y in ids[k + 1:]),
                       key=lambda p: float(vecs[p[0]] @ vecs[p[1]]))
            pool_ids = [it["id"] for it in self.pool]
            sa = self.vibe_index.similarities(vecs[a], pool_ids)
            sb = self.vibe_index.similarities(vecs[b], pool_ids)
            title = {it["id"]: it.get("title") for it in self.pool}
            opt_a = {i: round(1.0 * sa.get(i, 0) - 0.4 * sb.get(i, 0), 3) for i in pool_ids}
            opt_b = {i: round(1.0 * sb.get(i, 0) - 0.4 * sa.get(i, 0), 3) for i in pool_ids}
            return [
                {"id": f"f{a}", "emoji": "🎬", "label": title[a], "fx": {"items": opt_a}, "film": a},
                {"id": f"f{b}", "emoji": "🎬", "label": title[b], "fx": {"items": opt_b}, "film": b},
                {"id": "neither", "emoji": "🙅", "label": "Neither", "fx": {"items": {a: -0.6, b: -0.6}}},
            ]
        return q["options"]

    def _live(self, q: Dict[str, Any], opts: List[Dict[str, Any]], scored, now_top50: List[int]) -> List[Dict[str, Any]]:
        if q["id"] in CLOSERS:
            return opts
        live = []
        for o in opts:
            fx = o["fx"]
            if fx.get("none") or fx.get("profile") and len(fx) == 1:
                live.append(o)
                continue
            if q["id"] in AVOID:
                after = set(top_ids(score_pool(self.pool, apply_effects(self.belief, fx), self.stats), 10 ** 6))
                removed = sum(1 for i in now_top50 if i not in after)
                if removed >= max(1, AVOID_SHARE * len(now_top50)):
                    live.append(o)
                continue
            ok = option_fit(fx, self.stats)
            n_fit = sum(1 for _, it in scored[:POOL_TOP] if ok(it))
            if n_fit >= min(LIVE_FIT, max(1, len(self.pool) // 5)):
                live.append(o)
        return live

    def _value(self, opts: List[Dict[str, Any]], now_top10: List[int]) -> float:
        if not now_top10:
            return 0.0
        total = 0.0
        for o in opts:
            alt = top_ids(score_pool(self.pool, apply_effects(self.belief, o["fx"]), self.stats), 10)
            overlap = len(set(alt) & set(now_top10)) / len(now_top10)
            total += 1 - overlap
        return total / len(opts)

    def next_question(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """Picks the most useful next question. With force, a question is
        returned whenever any has live options, even below MIN_VALUE (used
        for the first question, so the pop-up opens on every search)."""
        if self.done or self.answered_count >= MAX_QUESTIONS:
            self.current = None
            return None
        scored = self.scored()
        now10, now50 = top_ids(scored, 10), top_ids(scored, 50)
        best = fallback = None
        avoid_due = self.answered_count == 3 and not (self.asked_ids & AVOID)
        tries = [False, True] if force and self.answered_count == 0 else [False]
        for any_first in tries:
            for q in self._eligible(any_first):
                opts = self._live(q, self._generated_options(q, scored), scored, now50)
                if len(opts) < (2 if q["id"] in CLOSERS else 3):
                    continue
                raw = self._value(opts, now10)
                if fallback is None or raw > fallback[0]:
                    fallback = (raw, q, opts)
                if raw < MIN_VALUE:
                    continue
                group_penalty = 0.5 if any(BY_ID[a]["group"] == q["group"] for a in self.asked_ids
                                           if a in BY_ID) else 1.0
                value = raw * FUN.get(q["id"], 1.0) * group_penalty
                if avoid_due and q["id"] in AVOID:
                    value += 100              # fixed slot: one avoid question by question 4
                if best is None or value > best[0]:
                    best = (value, q, opts)
            if best is not None or fallback is not None:
                break
        if best is None and force:
            best = fallback
        if best is None:
            self.current = None
            return None
        _, q, opts = best
        self.current = {"id": q["id"], "text": q["text"], "options": opts}
        return self.current

    def public_question(self) -> Optional[Dict[str, Any]]:
        """The current question as the UI sees it, with two example films per option."""
        if not self.current:
            return None
        out_opts = []
        for o in self.current["options"]:
            if o.get("film"):
                examples = [it for it in self.pool if it["id"] == o["film"]]
            elif o.get("cluster") is not None:
                examples = [it for _, it in self.scored() if features(it)["cluster"] == o["cluster"]][:2]
            elif o["fx"].get("none") or o["id"] in ("none", "neither"):
                examples = []
            else:
                sim = score_pool(self.pool, apply_effects(self.belief, o["fx"]), self.stats)
                ok = option_fit(o["fx"], self.stats)
                examples = [it for _, it in sim if ok(it)][:2]
            out_opts.append({
                "id": o["id"], "emoji": o["emoji"], "label": o["label"],
                "examples": [{"id": it["id"], "title": it.get("title"), "year": it.get("release_year"),
                              "poster_url": it.get("poster_url")} for it in examples[:2]],
            })
        return {"id": self.current["id"], "text": self.current["text"], "options": out_opts,
                "step": self.answered_count + 1, "max_steps": MAX_QUESTIONS}

    def answer(self, question_id: str, option_id: str) -> None:
        if not self.current or self.current["id"] != question_id:
            raise ValueError("That question isn't the one being asked.")
        if option_id != "skip":
            opt = next((o for o in self.current["options"] if o["id"] == option_id), None)
            if opt is None:
                raise ValueError("Unknown option.")
            self.belief = apply_effects(self.belief, opt["fx"])
        self.asked.append((question_id, option_id))
        self.current = None
        self.updated = time.time()
        self.top5_history.append(set(top_ids(self.scored(), 5)))

    def should_stop(self) -> bool:
        if self.done or self.answered_count >= MAX_QUESTIONS:
            return True
        h = self.top5_history
        if len(h) >= 3 and h[-1] == h[-2] == h[-3]:
            return True
        if self.answered_count >= 2:
            scored = self.scored()
            if len(scored) > 5 and scored[4][0] - scored[5][0] >= GAP_STOP:
                return True
        return False

    def log_fields(self) -> Dict[str, Any]:
        answers = {q: a for q, a in self.asked}
        return {"answers_json": json.dumps(answers), "asked_order": json.dumps([q for q, _ in self.asked]),
                "belief_json": json.dumps(self.belief, default=str)}


def should_auto_ask(query: Optional[str], pool: List[Dict[str, Any]]) -> bool:
    """Design doc §3: short text, or the top 20 spread over many vibe clusters."""
    if meaningful_words(query) < 6:
        return True
    clusters = {features(it)["cluster"] for it in pool[:20] if features(it)["cluster"] is not None}
    return len(clusters) >= 6


def has_mood_data(pool: List[Dict[str, Any]]) -> bool:
    """True when some film in the pool has mood dials or LLM tags. Without them
    (no Mac build or mood-tag run on this server's catalogue) no dial question can
    split the pool, so the pop-up never appears."""
    return any(features(it)["facets"] or features(it)["tagged"] for it in pool[:POOL_TOP])


def skip_reason(query: Optional[str], pool: List[Dict[str, Any]], asked: bool) -> Optional[str]:
    """Why a requested pop-up didn't open, for the UI and the logs; None when it did."""
    if asked:
        return None
    if not pool:
        return "no_results"
    if not has_mood_data(pool):
        return "no_mood_data"
    if not should_auto_ask(query, pool):
        return "specific_request"
    return "nothing_to_ask"


class SessionStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._s: Dict[int, PopupSession] = {}

    def put(self, s: PopupSession) -> None:
        with self._lock:
            now = time.time()
            for k in [k for k, v in self._s.items() if now - v.updated > SESSION_TTL]:
                del self._s[k]
            self._s[s.id] = s

    def get(self, sid: Optional[int]) -> Optional[PopupSession]:
        with self._lock:
            return self._s.get(sid) if sid is not None else None
