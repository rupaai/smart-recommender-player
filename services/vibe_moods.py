"""
Mood map (services/vibe_moods.py)

The ~30 "night moods" from the vibe-questions plan (T1), as data. Each mood is
a recipe in terms the app already reads off a film (vibe_popup.features()):

    dials        {dial: target 0..1}   the 14 mood dials (vibe_vocab.DIAL_NAMES)
    dial_weight  {dial: 0..1}          how much that dial matters for this mood
    genres       {TMDB genre: 0..1}    soft genre weights
    keywords     [TMDB keyword terms]  matched as whole words inside a keyword
    feels        [vibe_vocab.FEELS]    "feels like" tags from the LLM tagger

fit(features, mood) mixes dial closeness with soft genre, keyword and feels
overlap into one 0..1 number. Nothing removes a film: a rom-com with middling
humour still fits the comedy moods a bit, just less than the romance ones.
A dial the film has no facet for counts 0.5 (neutral), so a catalogue without
facets (mars before the Mac build lands) still separates films by genres and
keywords alone.

GROUPS holds the six overlapping mood groups (Light, Heart, Thrill, Dark,
Mind, Drift) with each mood's membership weight; a mood's weights add to 1.

Not wired in yet: the mood belief (T2) and ranking (T3) will use this.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set, Tuple

from services.vibe_vocab import DIAL_NAMES, FEELS

# How the four parts mix. A part the film (or the mood) has no data for is left
# out and the rest are renormalised; dials are always in (missing ones are 0.5).
PART_WEIGHTS = {"dials": 0.45, "genres": 0.30, "keywords": 0.15, "feels": 0.10}
NEUTRAL = 0.5

# TV genres come combined ("Sci-Fi & Fantasy"); split them and map to the movie names.
_GENRE_ALIASES = {"sci-fi": "science fiction", "politics": "", "kids": "family", "soap": "drama"}


def _mood(id: str, label: str, dials: Dict[str, Tuple[float, float]], genres: Dict[str, float],
          keywords: List[str], feels: List[str]) -> Dict[str, Any]:
    return {
        "id": id, "label": label,
        "dials": {d: t for d, (t, _) in dials.items()},
        "dial_weight": {d: w for d, (_, w) in dials.items()},
        "genres": genres, "keywords": keywords, "feels": feels,
    }


# dials: {name: (target, weight)}
MOODS: List[Dict[str, Any]] = [
    _mood("cosy_comfort", "Cosy and comforting",
          {"tension": (0.1, 0.8), "ending_valence": (0.85, 0.8), "darkness": (0.1, 0.7), "weight": (0.25, 0.5),
           "humour": (0.5, 0.3), "gore": (0.0, 0.6)},
          {"Family": 1.0, "Animation": 0.3, "Comedy": 0.5, "Romance": 0.5},
          ["christmas", "small town", "friendship", "holiday", "found family"],
          ["warm", "cozy", "comforting", "gentle", "wholesome"]),
    _mood("switch_off", "Brain off, easy fun",
          {"attention": (0.1, 1.0), "humour": (0.75, 0.7), "weight": (0.1, 0.8), "energy": (0.6, 0.3),
           "tension": (0.25, 0.3)},
          {"Comedy": 1.0, "Action": 0.5, "Family": 0.4},
          ["parody", "spoof", "buddy", "slapstick", "road trip"],
          ["silly", "playful", "giddy", "joyful"]),
    _mood("laugh_out_loud", "Proper laughs",
          {"humour": (0.9, 1.0), "energy": (0.7, 0.6), "weight": (0.15, 0.4)},
          {"Comedy": 1.0},
          ["parody", "spoof", "slapstick", "stand-up", "satire", "buddy comedy"],
          ["silly", "giddy", "witty", "playful", "joyful"]),
    _mood("romance_warm", "Warm romance",
          {"ending_valence": (0.85, 0.8), "humour": (0.45, 0.3), "darkness": (0.15, 0.5), "tension": (0.25, 0.4),
           "weight": (0.35, 0.3)},
          {"Romance": 1.0, "Comedy": 0.4, "Drama": 0.3},
          ["love", "romance", "falling in love", "wedding", "soulmates", "romantic comedy"],
          ["tender", "warm", "giddy", "bittersweet", "hopeful"]),
    _mood("good_cry", "A good cry",
          {"weight": (0.85, 1.0), "ending_valence": (0.45, 0.6), "humour": (0.15, 0.3), "darkness": (0.35, 0.3)},
          {"Drama": 1.0, "Romance": 0.4, "Family": 0.2},
          ["terminal illness", "grief", "loss", "death of loved one", "tearjerker", "cancer"],
          ["bittersweet", "cathartic", "tender", "melancholy", "devastating"]),
    _mood("feel_good_true", "Uplifting true story",
          {"ending_valence": (0.9, 1.0), "weight": (0.5, 0.4), "fantasy": (0.05, 0.6), "energy": (0.55, 0.3)},
          {"Drama": 0.8, "History": 0.5, "Documentary": 0.5, "Comedy": 0.3},
          ["based on true story", "biography", "sports", "underdog", "true story", "inspirational"],
          ["triumphant", "hopeful", "joyful"]),
    _mood("edge_of_seat", "Edge of my seat",
          {"tension": (0.9, 1.0), "pace": (0.85, 0.8), "energy": (0.8, 0.5), "attention": (0.6, 0.3)},
          {"Thriller": 1.0, "Action": 0.6, "Crime": 0.4},
          ["chase", "race against time", "hostage", "kidnapping", "survival", "manhunt"],
          ["tense", "chaotic"]),
    _mood("big_spectacle", "Big and spectacular",
          {"spectacle": (0.9, 1.0), "energy": (0.85, 0.8), "pace": (0.75, 0.5), "attention": (0.35, 0.3)},
          {"Action": 1.0, "Adventure": 0.8, "Science Fiction": 0.6, "Fantasy": 0.4},
          ["superhero", "explosion", "alien invasion", "disaster", "epic battle"],
          ["epic-awe", "triumphant", "chaotic"]),
    _mood("adventure_escape", "Escape somewhere else",
          {"fantasy": (0.75, 0.8), "spectacle": (0.7, 0.6), "ending_valence": (0.7, 0.4), "darkness": (0.3, 0.3)},
          {"Adventure": 1.0, "Fantasy": 0.9, "Animation": 0.2, "Family": 0.3, "Science Fiction": 0.3},
          ["quest", "journey", "magic", "parallel world", "treasure", "another world"],
          ["epic-awe", "lush", "dreamy", "joyful"]),
    _mood("brainy_puzzle", "A puzzle to solve",
          {"attention": (0.9, 1.0), "strangeness": (0.5, 0.4), "tension": (0.6, 0.4), "talkiness": (0.6, 0.2)},
          {"Mystery": 1.0, "Thriller": 0.6, "Science Fiction": 0.4},
          ["twist", "puzzle", "time loop", "nonlinear timeline", "mind-bending", "investigation"],
          ["mind-bending", "cerebral"]),
    _mood("whodunit", "Cosy mystery",
          {"tension": (0.5, 0.6), "gore": (0.1, 0.7), "darkness": (0.3, 0.5), "humour": (0.4, 0.3),
           "attention": (0.65, 0.4)},
          {"Mystery": 1.0, "Crime": 0.7, "Comedy": 0.3},
          ["whodunit", "murder mystery", "detective", "investigation", "amateur detective"],
          ["witty", "playful", "cozy"]),
    _mood("dark_gritty", "Dark and gritty",
          {"darkness": (0.85, 1.0), "weight": (0.75, 0.7), "humour": (0.15, 0.3), "ending_valence": (0.35, 0.3),
           "fantasy": (0.1, 0.4)},
          {"Crime": 1.0, "Drama": 0.6, "Thriller": 0.6},
          ["gangster", "drug", "corruption", "police", "organized crime", "neo-noir"],
          ["gritty", "raw", "tense"]),
    _mood("scary_fun", "Fun scary",
          {"tension": (0.75, 0.8), "humour": (0.55, 0.7), "gore": (0.4, 0.3), "darkness": (0.55, 0.4)},
          {"Horror": 0.6, "Comedy": 0.7, "Thriller": 0.3},
          ["horror comedy", "zombie", "slasher", "haunted house", "monster"],
          ["eerie", "playful", "silly", "tense"]),
    _mood("proper_horror", "Proper horror",
          {"darkness": (0.9, 0.9), "gore": (0.7, 0.6), "tension": (0.9, 0.9), "humour": (0.1, 0.4)},
          {"Horror": 1.0, "Thriller": 0.4, "Mystery": 0.2},
          ["haunted house", "supernatural", "possession", "demon", "slasher", "jump scare", "ghost"],
          ["dread", "eerie", "unsettling", "haunting"]),
    _mood("weird_wonderful", "Weird and wonderful",
          {"strangeness": (0.9, 1.0), "fantasy": (0.6, 0.3), "attention": (0.6, 0.3)},
          {"Fantasy": 0.6, "Comedy": 0.5, "Science Fiction": 0.5, "Drama": 0.3},
          ["surreal", "absurd", "surrealism", "dark comedy", "alternate reality", "cult film"],
          ["mind-bending", "playful", "dreamy", "unsettling"]),
    _mood("slow_dreamy", "Slow and dreamy",
          {"pace": (0.1, 1.0), "energy": (0.15, 0.7), "talkiness": (0.45, 0.3), "tension": (0.2, 0.4)},
          {"Drama": 1.0, "Romance": 0.4},
          ["road trip", "summer", "coming of age", "nature", "slow cinema"],
          ["dreamy", "contemplative", "wistful", "lush", "sun-soaked", "melancholy"]),
    _mood("talky_smart", "Smart and talky",
          {"talkiness": (0.9, 1.0), "attention": (0.8, 0.8), "spectacle": (0.2, 0.4)},
          {"Drama": 1.0, "Comedy": 0.4, "Crime": 0.2},
          ["conversation", "courtroom", "debate", "dialogue", "single location", "philosophy"],
          ["witty", "cerebral", "contemplative"]),
    _mood("heist_caper", "Clever caper",
          {"humour": (0.5, 0.6), "pace": (0.8, 0.7), "tension": (0.6, 0.5), "darkness": (0.35, 0.3),
           "attention": (0.6, 0.3)},
          {"Crime": 1.0, "Comedy": 0.5, "Action": 0.5, "Thriller": 0.5},
          ["heist", "con artist", "robbery", "bank robbery", "thief", "scam"],
          ["stylish", "witty", "playful"]),
    _mood("nostalgic", "Nostalgic",
          {"ending_valence": (0.7, 0.4), "weight": (0.35, 0.3), "darkness": (0.2, 0.3)},
          {"Family": 0.5, "Comedy": 0.5, "Adventure": 0.5, "Drama": 0.4},
          ["coming of age", "1980s", "childhood", "nostalgia", "summer", "friendship"],
          ["nostalgic", "wistful", "warm", "bittersweet"]),
    _mood("cringe_comedy", "Awkward comedy",
          {"cringe": (0.9, 1.0), "humour": (0.8, 0.8)},
          {"Comedy": 1.0},
          ["mockumentary", "awkwardness", "embarrassment", "social satire", "dysfunctional family"],
          ["witty", "silly"]),
    _mood("animated_any", "Animated, any age",
          {"fantasy": (0.6, 0.2), "gore": (0.1, 0.3)},
          {"Animation": 1.0, "Family": 0.5},
          ["anime", "stop motion", "talking animals", "cartoon"],
          ["playful", "joyful", "warm"]),
    _mood("sci_fi_ideas", "Big ideas sci-fi",
          {"attention": (0.8, 0.8), "spectacle": (0.55, 0.4), "fantasy": (0.7, 0.4), "strangeness": (0.55, 0.3)},
          {"Science Fiction": 1.0, "Mystery": 0.3, "Drama": 0.3, "Thriller": 0.3},
          ["artificial intelligence", "time travel", "dystopia", "space", "alien", "simulation"],
          ["cerebral", "mind-bending", "epic-awe", "contemplative"]),
    _mood("war_history", "History and war",
          {"weight": (0.85, 0.9), "darkness": (0.65, 0.5), "fantasy": (0.05, 0.5), "spectacle": (0.6, 0.3)},
          {"War": 1.0, "History": 1.0, "Drama": 0.5},
          ["world war ii", "battle", "historical", "soldier", "war", "based on true story"],
          ["raw", "devastating", "epic-awe", "gritty"]),
    _mood("music_dance", "Music and dance",
          {"energy": (0.8, 0.8), "ending_valence": (0.75, 0.5), "humour": (0.4, 0.2)},
          {"Music": 1.0, "Romance": 0.3, "Comedy": 0.3, "Documentary": 0.2},
          ["musical", "dance", "band", "singer", "concert", "music"],
          ["joyful", "giddy", "triumphant"]),
    _mood("sweet_small", "Small and sweet",
          {"spectacle": (0.15, 1.0), "ending_valence": (0.85, 0.8), "weight": (0.25, 0.6), "darkness": (0.1, 0.5)},
          {"Comedy": 0.6, "Drama": 0.6, "Romance": 0.5, "Family": 0.4},
          ["friendship", "small town", "slice of life", "unlikely friendship", "indie"],
          ["tender", "gentle", "warm", "wholesome"]),
    _mood("revenge_thrill", "Revenge and payback",
          {"tension": (0.85, 0.8), "darkness": (0.65, 0.7), "energy": (0.75, 0.5), "gore": (0.55, 0.3)},
          {"Thriller": 1.0, "Action": 0.8, "Crime": 0.6},
          ["revenge", "vigilante", "assassin", "payback", "betrayal"],
          ["gritty", "tense", "raw"]),
    _mood("feel_bad_good", "Bleak but brilliant",
          {"ending_valence": (0.15, 1.0), "weight": (0.9, 0.9), "humour": (0.1, 0.3)},
          {"Drama": 1.0, "Crime": 0.3, "War": 0.3},
          ["tragedy", "addiction", "despair", "poverty", "downward spiral"],
          ["devastating", "haunting", "raw", "melancholy"]),
    _mood("background", "Something on in the background",
          {"attention": (0.1, 1.0), "talkiness": (0.4, 0.4), "tension": (0.2, 0.4), "weight": (0.2, 0.4)},
          {"Comedy": 0.7, "Documentary": 0.7, "Family": 0.4},
          ["sitcom", "nature", "travel", "cooking", "episodic"],
          ["cozy", "comforting", "gentle"]),
    _mood("date_night", "Date night",
          {"tension": (0.25, 0.5), "humour": (0.55, 0.5), "ending_valence": (0.8, 0.6), "gore": (0.1, 0.5),
           "cringe": (0.3, 0.2)},
          {"Romance": 0.6, "Comedy": 0.5, "Drama": 0.2},
          ["romantic comedy", "first date", "love", "relationship", "wedding"],
          ["giddy", "witty", "warm", "tender", "playful"]),
    _mood("family_all", "Fun for everyone",
          {"gore": (0.0, 1.0), "cringe": (0.2, 0.5), "darkness": (0.15, 0.6), "ending_valence": (0.8, 0.4)},
          {"Family": 1.0, "Animation": 0.7, "Adventure": 0.5, "Comedy": 0.5},
          ["talking animals", "friendship", "kids", "christmas", "family"],
          ["wholesome", "joyful", "playful", "warm"]),
]
BY_ID: Dict[str, Dict[str, Any]] = {m["id"]: m for m in MOODS}

# Group -> {mood id: membership}. A mood in two groups is split; its memberships add to 1.
GROUPS: Dict[str, Dict[str, float]] = {
    "light": {"cosy_comfort": 1.0, "switch_off": 1.0, "laugh_out_loud": 1.0, "cringe_comedy": 1.0,
              "sweet_small": 1.0, "family_all": 1.0, "animated_any": 1.0, "date_night": 0.4, "whodunit": 0.4,
              "scary_fun": 0.3},
    "heart": {"romance_warm": 1.0, "good_cry": 1.0, "feel_good_true": 1.0, "nostalgic": 1.0, "music_dance": 1.0,
              "date_night": 0.6},
    "thrill": {"edge_of_seat": 1.0, "heist_caper": 1.0, "revenge_thrill": 1.0, "big_spectacle": 1.0,
               "scary_fun": 0.7, "adventure_escape": 0.5},
    "dark": {"dark_gritty": 1.0, "proper_horror": 1.0, "feel_bad_good": 1.0, "war_history": 0.5},
    "mind": {"brainy_puzzle": 1.0, "talky_smart": 1.0, "sci_fi_ideas": 1.0, "weird_wonderful": 1.0,
             "whodunit": 0.6, "war_history": 0.5},
    "drift": {"slow_dreamy": 1.0, "background": 1.0, "adventure_escape": 0.5},
}
GROUP_LABELS: Dict[str, str] = {"light": "Light", "heart": "Heart", "thrill": "Thrill", "dark": "Dark",
                                "mind": "Mind", "drift": "Drift"}


def memberships(mood_id: str) -> Dict[str, float]:
    """{group: weight} for one mood."""
    return {g: ms[mood_id] for g, ms in GROUPS.items() if mood_id in ms}


# ---------------------------------------------------------------------------
# Fit
# ---------------------------------------------------------------------------

def _film_genres(genres: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for g in genres:
        for part in str(g).lower().split("&"):
            part = part.strip()
            part = _GENRE_ALIASES.get(part, part)
            if part:
                out.add(part)
    return out


def _kw_hit(term: str, keywords: Iterable[str]) -> bool:
    """Whole-word match, so "war" finds "world war ii" but not "award"."""
    padded = f" {term} "
    return any(padded in f" {k} " for k in keywords)


def _saturate(hits: int) -> float:
    """0 hits → 0, 1 → 0.5, 2 → 0.75, ... (one match already counts)."""
    return 1.0 - 0.5 ** hits


def parts(features: Dict[str, Any], mood: Dict[str, Any]) -> Dict[str, float]:
    """The four part scores (0..1) behind fit(); parts with no data are left out."""
    out: Dict[str, float] = {}

    facets = features.get("facets") or {}
    dw = mood.get("dial_weight") or {}
    total = sum(dw.values())
    if total > 0:
        s = 0.0
        for dial, w in dw.items():
            v = facets.get(dial)
            close = 1.0 - abs(float(v) - mood["dials"][dial]) if isinstance(v, (int, float)) else NEUTRAL
            s += w * close
        out["dials"] = s / total

    film_genres = _film_genres(features.get("genres") or ())
    mood_genres = {g.lower(): w for g, w in (mood.get("genres") or {}).items()}
    if film_genres and mood_genres:
        hits = [mood_genres.get(g, 0.0) for g in film_genres]
        # Share of the film that is this mood, plus whether it has the mood's core genre.
        out["genres"] = 0.6 * sum(hits) / len(hits) + 0.4 * max(hits)

    # All keywords in one padded string, built once per film: matching " term "
    # against it is the same whole-word test as _kw_hit, but fast enough for
    # 1,000 films x 30 moods. " | " keeps a term from spanning two keywords.
    kw_text = features.get("_kw_text")
    if kw_text is None:
        kws = [str(k).lower() for k in (features.get("kw") or ())]
        kw_text = features["_kw_text"] = (" " + " | ".join(kws) + " ") if kws else ""
    if kw_text and mood.get("keywords"):
        out["keywords"] = _saturate(sum(1 for t in mood["keywords"] if f" {t.lower()} " in kw_text))

    feels = set(features.get("feels") or ())
    if feels and mood.get("feels"):
        out["feels"] = _saturate(len(feels & set(mood["feels"])))
    return out


def fit(features: Dict[str, Any], mood: Dict[str, Any]) -> float:
    """How well one film (vibe_popup.features() output) fits one mood, 0..1."""
    p = parts(features, mood)
    if not p:
        return NEUTRAL
    total = sum(PART_WEIGHTS[k] for k in p)
    return max(0.0, min(1.0, sum(PART_WEIGHTS[k] * v for k, v in p.items()) / total))


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def validate() -> bool:
    """Raise ValueError listing every problem in MOODS / GROUPS; True when clean."""
    problems: List[str] = []
    ids = [m["id"] for m in MOODS]
    if len(set(ids)) != len(ids):
        problems.append("duplicate mood ids")
    for m in MOODS:
        mid = m["id"]
        for key in ("label", "dials", "dial_weight", "genres", "keywords", "feels"):
            if key not in m:
                problems.append(f"{mid}: missing {key}")
        if set(m["dials"]) != set(m["dial_weight"]):
            problems.append(f"{mid}: dials and dial_weight name different dials")
        for d, v in list(m["dials"].items()) + list(m["dial_weight"].items()):
            if d not in DIAL_NAMES:
                problems.append(f"{mid}: unknown dial {d!r}")
            if not 0.0 <= v <= 1.0:
                problems.append(f"{mid}: {d} = {v} outside 0..1")
        for g, w in m["genres"].items():
            if not 0.0 <= w <= 1.0:
                problems.append(f"{mid}: genre {g} weight {w} outside 0..1")
        for f in m["feels"]:
            if f not in FEELS:
                problems.append(f"{mid}: unknown feel {f!r}")
        if not (m["genres"] and m["keywords"] and m["feels"]):
            # Every mood carries all three, so a missing part can't favour one mood over another.
            problems.append(f"{mid}: needs genres, keywords and feels")
        total = sum(memberships(mid).values())
        if abs(total - 1.0) > 1e-6:
            problems.append(f"{mid}: group memberships add to {total:g}, not 1")
    for g, ms in GROUPS.items():
        for mid in ms:
            if mid not in BY_ID:
                problems.append(f"group {g}: unknown mood {mid!r}")
    if problems:
        raise ValueError("vibe_moods: " + "; ".join(problems))
    return True
