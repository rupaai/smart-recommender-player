"""
Taste model (services/taste_model.py)

Turns a profile's rated films (verdict, chips, vibe-check answers) into the two
ranking terms of design doc §2.6 that were not built yet:

  facet_fit         how well a film's mood dials match the levels this person
                    likes (and stay inside the limits they said they hit)
  cluster_affinity  mean taste score per vibe cluster, shrunk toward zero

Pure functions, no database or file access, so the tests need no fixtures.
`services/taste_runtime.py` loads the rated rows and calls these.

Rated record (one dict per film):
  taste_score   -1..+1            inferred   1 = guessed from an old 1-10 rating
  facets        {dial: 0..1}      vibe_tags  {"feels": [...], "palette": [...], "dials": {...}}
  vibe_cluster_id                 worked / didnt   chip id lists
  vibe_check    {question id: option id | [option ids]}   (multi-pick may be a list or a string)
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "weights": {"facet_fit": 1.0, "cluster_affinity": 0.8},
    "min_verdicts": 10,          # confirmed + inferred verdicts needed before anything changes
    "inferred_weight": 0.5,      # an inferred verdict counts this much of a confirmed one
    "min_dial_evidence": 4.0,    # weighted films with a value for a dial before it counts
    "evidence_k": 5.0,           # per-dial shrink: n / (n + k)
    "overall_k": 10.0,           # whole-model shrink: n / (n + k), so a thin history changes little
    "cluster_k": 5.0,            # cluster shrink: n / (n + k)
    "correction": 0.2,           # how far a vibe-check answer moves a film's tagged dial
    "min_tolerance": 0.12,       # floor on the spread, so one very consistent dial isn't a knife edge
    "dislike_push": 0.25,        # how far the dial level moves away from the disliked films' level
    "chip_gain": 0.5,            # extra dial weight (at most) for chips that keep showing up on loved films
    "chip_k": 3.0,
    "avoid_strength": 0.5,       # penalty near a level blamed on films I disliked
    "limit_strength": 0.5,       # penalty beyond a tolerance limit
    "limit_margin": 0.1,
    "reason_min_share": 0.2,     # a contribution must be this share of the biggest to earn a badge
}

# Chip -> catalogue features. Chips with no feature are ignored in the maths
# (acting, music, characters, "made me feel something").
CHIP_DIALS: Dict[str, Tuple[str, ...]] = {
    "pace": ("pace",),
    "humour": ("humour",),
    "ending": ("ending_valence",),
    "story": ("attention", "strangeness"),
    "visuals": ("spectacle",),
}
CHIP_SETS: Dict[str, Tuple[str, ...]] = {
    "vibe": ("feels",),
    "visuals": ("palette",),
}
IGNORED_CHIPS = ("acting", "music", "characters", "feeling")

# Vibe-check answer -> (dial, shift sign, tolerance limit kind or None).
# A shift moves the film's tagged dial for this person: "Darker than expected"
# raises darkness. "As expected" / "Just right" confirm the tag (no shift).
# A limit is only recorded when the film was liked: "Too much" tension on a
# film I liked is how much I can take; it is not a reason to dislike it.
# Ending answers say how well it landed, not which way, so they are logged only.
ANSWER_EFFECTS: Dict[str, Dict[str, Tuple[str, float, Optional[str]]]] = {
    "darker": {"darker": ("darkness", +1, None), "lighter": ("darkness", -1, None)},
    "leaves": {"lifted": ("weight", -0.5, None), "wrecked": ("weight", +0.75, None)},
    "pace": {"dragged": ("pace", -1, "min"), "rushed": ("pace", +1, "max")},
    "brain": {"half": ("attention", -1, None), "lost": ("attention", +1, "max")},
    "tension": {"toomuch": ("tension", +1, "max"), "wantmore": ("tension", -1, None)},
    "funny": {"funnier": ("humour", +1, None), "notreally": ("humour", -1, None)},
    "weird": {"tooweird": ("strangeness", +1, "max"), "ordinary": ("strangeness", -1, None)},
}

# Reason badge text per feature. Dials read "matches how you like <x>".
DIAL_PHRASE: Dict[str, str] = {
    "energy": "matches the energy you like",
    "attention": "asks for about the focus you like to give",
    "weight": "about as heavy as you like",
    "darkness": "about as dark as you like",
    "pace": "matches how you like pacing",
    "tension": "has the tension you like",
    "ending_valence": "ends the way you like",
    "humour": "funny the way you like",
    "strangeness": "as strange as you like",
    "cringe": "about as awkward as you like",
    "gore": "about as bloody as you like",
    "fantasy": "has the fantasy you like",
    "talkiness": "as talky as you like",
    "spectacle": "has the scale you like",
    "feels": "feels like films you loved",
    "palette": "looks like films you loved",
}

SET_FEATURES = ("feels", "palette")


# --- Helpers -----------------------------------------------------------------
def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        out = json.loads(value)
        return out if isinstance(out, dict) else {}
    except (TypeError, ValueError):
        return {}


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    if isinstance(value, str):
        try:
            out = json.loads(value)
            if isinstance(out, list):
                return out
        except ValueError:
            pass
        return [value]
    return []


def answers_for(vibe_check: Any, qid: str) -> List[str]:
    """One question's answers as a list. Multi-pick questions may hold a list,
    a single string (older rows) or nothing."""
    raw = _as_dict(vibe_check).get(qid)
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    return [str(a) for a in items if a]


def config_with_defaults(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = {**DEFAULTS, "weights": dict(DEFAULTS["weights"])}
    for k, v in (cfg or {}).items():
        if k == "weights" and isinstance(v, dict):
            out["weights"].update({wk: float(wv) for wk, wv in v.items()})
        elif k in out:
            out[k] = v
    return out


def item_features(item: Dict[str, Any]) -> Dict[str, Any]:
    """Dials and tag sets of one catalogue row. `facets` (the Mac build) wins
    over the LLM tag dials, as in the questions pop-up."""
    tags = _as_dict(item.get("vibe_tags"))
    dials = {k: float(v) for k, v in _as_dict(item.get("facets")).items()
             if isinstance(v, (int, float)) and not isinstance(v, bool)}
    for k, v in (tags.get("dials") or {}).items():
        if isinstance(v, (int, float)):
            dials.setdefault(k, float(v))
    return {
        "dials": dials,
        "feels": {str(x) for x in (tags.get("feels") or []) if x},
        "palette": {str(x) for x in (tags.get("palette") or []) if x},
        "cluster": item.get("vibe_cluster_id"),
    }


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# --- Corrections from the vibe-check -------------------------------------------
def corrected_dials(dials: Dict[str, float], vibe_check: Any, correction: float = 0.2,
                    ) -> Tuple[Dict[str, float], List[Tuple[str, str]]]:
    """The film's dials as this person experienced them, plus the tolerance
    limits their answers mark as (dial, "min" | "max").

    Only dials the film already has a value for are shifted (a shift on an
    unknown level would invent data).
    """
    out = dict(dials)
    limits: List[Tuple[str, str]] = []
    for qid, effects in ANSWER_EFFECTS.items():
        for answer in answers_for(vibe_check, qid):
            effect = effects.get(answer)
            if not effect:
                continue            # "as expected" / "just right": confirms the tag
            dial, sign, limit = effect
            if dial in out:
                out[dial] = _clip(out[dial] + sign * correction)
            if limit:
                limits.append((dial, limit))
    return out, limits


def _confidence(record: Dict[str, Any], inferred_weight: float) -> float:
    return inferred_weight if record.get("inferred") else 1.0


# --- Building the model ---------------------------------------------------------
def build_taste_model(rated: Sequence[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The learned taste of one profile. `active` is False below the evidence
    gate, and an inactive model scores nothing."""
    c = config_with_defaults(cfg)
    films = [r for r in rated if isinstance(r.get("taste_score"), (int, float))]
    n_verdicts = len(films)
    n_eff = sum(_confidence(r, c["inferred_weight"]) for r in films)
    model: Dict[str, Any] = {
        "active": n_verdicts >= int(c["min_verdicts"]),
        "n_verdicts": n_verdicts,
        "n_confirmed": sum(1 for r in films if not r.get("inferred")),
        "n_effective": round(n_eff, 3),
        "confidence": n_eff / (n_eff + c["overall_k"]) if n_eff > 0 else 0.0,
        "dials": {}, "sets": {}, "clusters": {}, "limits": {}, "config": c,
    }
    if not model["active"]:
        return model

    # Per film: corrected dials, tag sets, limits, weights.
    prepared: List[Dict[str, Any]] = []
    for r in films:
        feats = r.get("features") or item_features(r)
        dials, limits = corrected_dials(feats["dials"], r.get("vibe_check"), c["correction"])
        felt = answers_for(r.get("vibe_check"), "felt")
        score = float(r["taste_score"])
        prepared.append({
            "score": score, "conf": _confidence(r, c["inferred_weight"]),
            "dials": dials, "limits": limits if score > 0 else [],
            "feels": set(felt) or feats["feels"], "palette": feats["palette"],
            "cluster": feats["cluster"],
            "worked": [x for x in _as_list(r.get("worked"))], "didnt": [x for x in _as_list(r.get("didnt"))],
        })

    # Chip evidence per feature: worked on a film I loved, didn't on one I disliked.
    worked_hits: Dict[str, float] = {}
    blamed: Dict[str, List[Tuple[float, float]]] = {}      # dial -> [(level, weight)]
    for p in prepared:
        for chip in p["worked"]:
            if p["score"] >= 0.5:
                for d in CHIP_DIALS.get(chip, ()) + CHIP_SETS.get(chip, ()):
                    worked_hits[d] = worked_hits.get(d, 0.0) + p["conf"]
        for chip in p["didnt"]:
            if p["score"] <= -0.3:
                for d in CHIP_DIALS.get(chip, ()):
                    if d in p["dials"]:
                        blamed.setdefault(d, []).append((p["dials"][d], p["conf"]))
                for d in CHIP_SETS.get(chip, ()):
                    blamed.setdefault(d, []).append((-1.0, p["conf"]))   # sets: counted, level unused

    all_dials = {d for p in prepared for d in p["dials"]}
    for dial in sorted(all_dials):
        liked = [(p["dials"][dial], p["score"] * p["conf"]) for p in prepared
                 if dial in p["dials"] and p["score"] > 0]
        disliked = [(p["dials"][dial], -p["score"] * p["conf"]) for p in prepared
                    if dial in p["dials"] and p["score"] < 0]
        evidence = sum(p["conf"] for p in prepared if dial in p["dials"])
        w_liked = sum(w for _, w in liked)
        if not liked or w_liked <= 0 or evidence < c["min_dial_evidence"]:
            continue
        mean = sum(v * w for v, w in liked) / w_liked
        var = sum(w * (v - mean) ** 2 for v, w in liked) / w_liked
        pref = mean
        w_dis = sum(w for _, w in disliked)
        if w_dis > 0:
            dis_mean = sum(v * w for v, w in disliked) / w_dis
            pref = _clip(mean + c["dislike_push"] * (mean - dis_mean) * (w_dis / (w_dis + 1.0)))
        hits = worked_hits.get(dial, 0.0)
        chip_weight = 1.0 + c["chip_gain"] * hits / (hits + c["chip_k"])
        avoid = None
        if blamed.get(dial):
            bw = sum(w for _, w in blamed[dial])
            avoid = {"level": sum(v * w for v, w in blamed[dial]) / bw,
                     "strength": c["avoid_strength"] * bw / (bw + c["chip_k"])}
        model["dials"][dial] = {
            "pref": round(pref, 4), "tolerance": round(max(math.sqrt(var), c["min_tolerance"]), 4),
            "evidence": round(evidence, 3),
            "weight": round(chip_weight * evidence / (evidence + c["evidence_k"]), 4),
            "avoid": avoid,
        }

    # Tolerance limits: the strictest "too much" / "dragged" level among liked films.
    for p in prepared:
        for dial, kind in p["limits"]:
            level = p["dials"].get(dial)
            if level is None or dial not in model["dials"]:
                continue
            cur = model["limits"].get(dial, {})
            if kind == "max":
                cur["max"] = min(cur.get("max", 1.0), level)
            else:
                cur["min"] = max(cur.get("min", 0.0), level)
            model["limits"][dial] = cur

    # Tag sets (feels, palette): affinity per tag, shrunk by how often it shows up.
    for feature in SET_FEATURES:
        num: Dict[str, float] = {}
        den: Dict[str, float] = {}
        for p in prepared:
            for tag in p[feature]:
                num[tag] = num.get(tag, 0.0) + p["score"] * p["conf"]
                den[tag] = den.get(tag, 0.0) + p["conf"]
        affinity = {t: num[t] / (den[t] + 2.0) for t in num if den[t] >= 2.0}
        if len(affinity) >= 3:
            hits = worked_hits.get(feature, 0.0)
            model["sets"][feature] = {
                "affinity": {t: round(a, 4) for t, a in affinity.items()},
                "weight": round((1.0 + c["chip_gain"] * hits / (hits + c["chip_k"])) * 0.5, 4),
            }

    # Cluster affinity: mean taste score per cluster, shrunk by n / (n + 5).
    sums: Dict[Any, List[float]] = {}
    for p in prepared:
        if p["cluster"] is None:
            continue
        s = sums.setdefault(p["cluster"], [0.0, 0.0])
        s[0] += p["score"] * p["conf"]
        s[1] += p["conf"]
    for cid, (total, n) in sums.items():
        model["clusters"][cid] = round((total / n) * (n / (n + c["cluster_k"])), 4)
    return model


# --- Scoring ----------------------------------------------------------------------
def dial_fit(level: float, spec: Dict[str, Any]) -> float:
    """-1..+1: 1 at the preferred level, 0 about one tolerance away, -1 at three."""
    return 1.0 - 2.0 * min(1.0, abs(level - spec["pref"]) / (3.0 * spec["tolerance"]))


def facet_fit(model: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    """{"value": -1..+1, "parts": {feature: contribution}}. Value 0 and no
    parts when the model is inactive or the film has nothing to compare."""
    empty = {"value": 0.0, "parts": {}}
    if not model.get("active"):
        return empty
    feats = item.get("features") or item_features(item)
    c = model["config"]
    parts: Dict[str, float] = {}
    total_w = 0.0
    for dial, spec in model["dials"].items():
        level = feats["dials"].get(dial)
        if level is None:
            continue
        fit = dial_fit(level, spec)
        avoid = spec.get("avoid")
        if avoid and avoid["level"] >= 0:
            near = max(0.0, 1.0 - abs(level - avoid["level"]) / 0.25)
            fit -= avoid["strength"] * near
        lim = model["limits"].get(dial) or {}
        if "max" in lim and level > lim["max"] + c["limit_margin"]:
            fit -= c["limit_strength"] * min(1.0, (level - lim["max"] - c["limit_margin"]) / 0.2)
        if "min" in lim and level < lim["min"] - c["limit_margin"]:
            fit -= c["limit_strength"] * min(1.0, (lim["min"] - c["limit_margin"] - level) / 0.2)
        parts[dial] = spec["weight"] * fit
        total_w += spec["weight"]
    for feature, spec in model["sets"].items():
        tags = feats.get(feature) or set()
        known = [spec["affinity"][t] for t in tags if t in spec["affinity"]]
        if not known:
            continue
        parts[feature] = spec["weight"] * (sum(known) / len(known))
        total_w += spec["weight"]
    if total_w <= 0:
        return empty
    scale = model["confidence"] / total_w
    parts = {k: v * scale for k, v in parts.items()}
    return {"value": _clip(sum(parts.values()), -1.0, 1.0), "parts": parts}


def cluster_affinity(model: Dict[str, Any], item: Dict[str, Any]) -> float:
    if not model.get("active"):
        return 0.0
    cid = item.get("vibe_cluster_id")
    return float(model["clusters"].get(cid, 0.0)) if cid is not None else 0.0


def blend(models: Sequence[Tuple[float, Dict[str, Any]]], item: Dict[str, Any]) -> Dict[str, Any]:
    """Score one film for one or two people. `models` is [(mix weight, model)];
    inactive models drop out and the rest are renormalised, so a partner with no
    history doesn't dilute the other person's taste."""
    live = [(w, m) for w, m in models if m.get("active") and w > 0]
    if not live:
        return {"facet_fit": 0.0, "cluster_affinity": 0.0, "parts": {}, "cluster_id": item.get("vibe_cluster_id")}
    total = sum(w for w, _ in live)
    ff = 0.0
    ca = 0.0
    parts: Dict[str, float] = {}
    for w, m in live:
        f = facet_fit(m, item)
        ff += (w / total) * f["value"]
        ca += (w / total) * cluster_affinity(m, item)
        for k, v in f["parts"].items():
            parts[k] = parts.get(k, 0.0) + (w / total) * v
    return {"facet_fit": round(ff, 4), "cluster_affinity": round(ca, 4),
            "parts": {k: round(v, 4) for k, v in parts.items()}, "cluster_id": item.get("vibe_cluster_id")}


def reasons(scored: Dict[str, Any], weights: Dict[str, float],
            cluster_label: Optional[str] = None, limit: int = 2, min_share: float = 0.2) -> List[str]:
    """Badge texts from the real largest positive contributors (weighted, so a
    dial and the cluster compete on the points they actually added)."""
    cands: List[Tuple[float, str]] = []
    for feature, part in (scored.get("parts") or {}).items():
        if part > 0 and feature in DIAL_PHRASE:
            cands.append((weights.get("facet_fit", 1.0) * part, DIAL_PHRASE[feature]))
    ca = scored.get("cluster_affinity") or 0.0
    if ca > 0 and cluster_label:
        cands.append((weights.get("cluster_affinity", 0.8) * ca, f"from your {cluster_label} cluster"))
    cands.sort(key=lambda x: x[0], reverse=True)
    if not cands:
        return []
    top = cands[0][0]
    return [text for pts, text in cands[:limit] if pts >= min_share * top and pts > 0.02]
