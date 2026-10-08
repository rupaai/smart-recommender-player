"""
Vibe vocabulary (services/vibe_vocab.py)

One place for the dials, content flags and closed tag lists from
docs/VIBE_QUESTION_BANK.md §4. The LLM tagger (utils/mood_tag.py), the vibe
build (training/build_vibe_index.py) and, later, the questions pop-up all
read from here, so a tag can never mean two different things.

Dials are stored 0-1. The tagger asks for 0-10 integers (cheaper and more
consistent for a small model) and divides by 10.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# name -> (low end, high end, rubric anchors at 0 / 5 / 10, anchor sentences for
# the free embedding fallback: (low sentence, high sentence)).
# The first five are the original facets (design doc §2.5); the rest are the
# question bank's additions in priority order.
DIALS: Dict[str, Dict[str, object]] = {
    "energy": {
        "low": "calm, gentle", "high": "intense, adrenaline",
        "rubric": ("Paterson", "Knives Out", "Mad Max: Fury Road"),
        "anchors": ("a calm, gentle, relaxing film", "an intense, adrenaline-filled, high-energy film"),
    },
    "attention": {
        "low": "background-friendly, easy to follow", "high": "needs full focus, twisty",
        "rubric": ("Paddington 2", "The Martian", "Tenet"),
        "anchors": ("an easy, simple film you can half-watch", "a complex, twisty film that needs your full attention"),
    },
    "weight": {
        "low": "light, breezy", "high": "heavy, gut-punch",
        "rubric": ("Legally Blonde", "The Intouchables", "Grave of the Fireflies"),
        "anchors": ("a light, breezy, carefree film", "a heavy, emotionally devastating film"),
    },
    "darkness": {
        "low": "wholesome", "high": "violent, disturbing",
        "rubric": ("Paddington 2", "Inside Man", "Se7en"),
        "anchors": ("a wholesome, innocent, family-friendly film", "a dark, violent, disturbing film"),
    },
    "pace": {
        "low": "slow-burn", "high": "fast",
        "rubric": ("Jeanne Dielman", "Hidden Figures", "Speed"),
        "anchors": ("a slow, patient, slow-burn film", "a fast-paced film that never slows down"),
    },
    "tension": {
        "low": "relaxed, low stakes", "high": "nail-biting suspense",
        "rubric": ("Paddington 2", "Knives Out", "Uncut Gems"),
        "anchors": ("a relaxed, low-stakes film", "a nail-biting, suspenseful thriller"),
    },
    "ending_valence": {
        "low": "bleak ending", "high": "uplifting ending",
        "rubric": ("Requiem for a Dream", "La La Land", "Crazy Rich Asians"),
        "anchors": ("a bleak, devastating ending", "a hopeful, uplifting, happy ending"),
    },
    "humour": {
        "low": "not funny", "high": "laugh-out-loud",
        "rubric": ("Schindler's List", "Knives Out", "Airplane!"),
        "anchors": ("a serious film with no jokes", "a hilarious, laugh-out-loud comedy"),
    },
    "strangeness": {
        "low": "conventional", "high": "surreal",
        "rubric": ("The Proposal", "Groundhog Day", "Eraserhead"),
        "anchors": ("a conventional, familiar, predictable film", "a surreal, bizarre, experimental film"),
    },
    "cringe": {
        "low": "no second-hand embarrassment", "high": "excruciating awkwardness",
        "rubric": ("The Princess Bride", "Meet the Parents", "The Office (UK)"),
        "anchors": ("a film with no awkward or embarrassing moments", "an excruciatingly awkward cringe comedy"),
    },
    "gore": {
        "low": "no blood", "high": "graphic gore",
        "rubric": ("Up", "Gladiator", "Evil Dead (2013)"),
        "anchors": ("a film with no blood or violence on screen", "a gory film with graphic blood and body horror"),
    },
    "fantasy": {
        "low": "grounded realism", "high": "another world",
        "rubric": ("Spotlight", "Big Fish", "Spirited Away"),
        "anchors": ("a realistic, grounded film set in the real world", "a fantasy set in another magical world"),
    },
    "talkiness": {
        "low": "visual, few words", "high": "dialogue-driven",
        "rubric": ("All Is Lost", "Forrest Gump", "12 Angry Men"),
        "anchors": ("a visual, almost wordless film", "a dialogue-driven, talky film"),
    },
    "spectacle": {
        "low": "intimate scale", "high": "epic scale",
        "rubric": ("12 Angry Men", "The Bourne Identity", "Lawrence of Arabia"),
        "anchors": ("a small, intimate film", "an epic, vast, spectacular film"),
    },
}
DIAL_NAMES: List[str] = list(DIALS)

# Booleans for the avoid questions (bank §4.2). A flag the tagger didn't list
# is "no" only when it knows the film well; otherwise "unknown".
CONTENT_FLAGS: List[str] = [
    "animal_harm", "child_in_peril", "death_of_loved_one", "terminal_illness",
    "infidelity", "sexual_violence", "suicide", "jump_scares", "body_horror",
    "bullying_humiliation", "war_violence",
]

# "Feels like", closed list of 37 (bank §4.4), grouped for the prompt.
FEELS_GROUPS: Dict[str, List[str]] = {
    "warm": ["warm", "cozy", "comforting", "gentle", "tender", "wholesome"],
    "up": ["joyful", "giddy", "silly", "playful", "witty", "triumphant", "hopeful"],
    "low": ["bittersweet", "melancholy", "wistful", "nostalgic", "haunting", "devastating", "cathartic"],
    "edge": ["tense", "eerie", "dread", "unsettling", "gritty", "raw", "chaotic"],
    "mind": ["cerebral", "mind-bending", "contemplative", "dreamy"],
    "look and place": ["sun-soaked", "wintry", "neon-nocturnal", "lush", "epic-awe", "stylish"],
}
FEELS: List[str] = [f for group in FEELS_GROUPS.values() for f in group]

# "Good for", closed list of 16 (bank §4.5).
GOOD_FOR: List[str] = [
    "winding down", "a bad day", "background while cooking", "eating dinner",
    "half-watching", "dozing off", "date night", "friends over", "a crowd-pleaser",
    "rainy afternoon", "sunday hangover", "can't sleep", "binge", "talking about after",
    "when you're sick", "a long free evening",
]

# Setting and look tags (bank §4.3).
SETTING: List[str] = [
    "city", "countryside", "coast", "forest", "desert", "snow", "space",
    "single-location", "road-journey", "small-town",
]
SEASON_WEATHER: List[str] = ["summer", "winter", "autumn", "spring", "rain"]
TIME_OF_DAY: List[str] = ["mostly-day", "mostly-night", "mixed"]
PALETTE: List[str] = ["pastel", "warm-grain", "cold", "saturated", "bw", "neon", "naturalistic"]
STORY_ERA: List[str] = [
    "ancient", "medieval", "1500s-1700s", "1800s", "1900s-1930s", "1940s", "1950s",
    "1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s", "near-future",
    "far-future", "timeless-or-invented-world",
]
KNOWN: List[str] = ["well", "somewhat", "not"]


def dial_rubric_lines() -> List[str]:
    """One prompt line per dial: name, ends, and the 0 / 5 / 10 example films."""
    lines = []
    for name, d in DIALS.items():
        r0, r5, r10 = d["rubric"]  # type: ignore[misc]
        lines.append(
            f"- {name}: 0 = {d['low']}, 10 = {d['high']}. "
            f"Examples: {r0} ≈ 0, {r5} ≈ 5, {r10} ≈ 10."
        )
    return lines


def dial_anchor_pairs() -> Dict[str, Tuple[str, str]]:
    """Anchor sentences for the free fallback (design doc §2.5)."""
    return {name: tuple(d["anchors"]) for name, d in DIALS.items()}  # type: ignore[misc]
