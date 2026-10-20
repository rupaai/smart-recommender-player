"""
Recommender Service (services/recommender_service.py)
Production-grade multi-stage candidate scoring, vector similarity, 
Bayesian rating weighting, MMR diversity pruning, and pagination.
"""

from __future__ import annotations

import os
import random
import re
import math
import time
from typing import Any, Dict, List, Optional, Tuple, Set

import requests
import numpy as np

from services.paths import models_dir
from database import DatabaseManager


# ===========================================================================
# Helpers & Math Utilities
# ===========================================================================
def _split_lower(csv: Optional[str]) -> List[str]:
    """Splits a comma-separated string into lowercase stripped tokens."""
    if not csv:
        return []
    return [t.strip().lower() for t in str(csv).split(",") if t.strip()]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculates cosine similarity between two float vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    va, vb = np.array(a), np.array(b)
    norm_a, norm_b = np.linalg.norm(va), np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Measures set overlap between genre/keyword sets for MMR diversity."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union if union > 0 else 0.0


def calculate_weighted_rating(
    rating: float,
    num_votes: int,
    min_votes: int = 1000,
    global_mean: float = 6.5,
) -> float:
    """Applies IMDb's Bayesian Weighted Rating formula to eliminate 1-vote 10/10 noise."""
    if num_votes <= 0:
        return global_mean
    return (num_votes / (num_votes + min_votes)) * rating + (
        min_votes / (num_votes + min_votes)
    ) * global_mean


# ===========================================================================
# Mood ↔ Genre Mapping & Config
# ===========================================================================
MOOD_MAPPINGS: Dict[int, Dict[str, Any]] = {
    1: {"name": "😊 Happy / Feel-Good", "vibe": "light, funny, uplifting", "genres": ["Comedy", "Family", "Animation", "Music"]},
    2: {"name": "😢 Sad / Emotional", "vibe": "moving, heartfelt, tearjerker", "genres": ["Drama", "Romance"]},
    3: {"name": "😱 Thrilling / On the Edge", "vibe": "intense, gripping, high-stakes", "genres": ["Action", "Thriller", "Adventure"]},
    4: {"name": "👻 Scary / Spooky", "vibe": "dark, tense, hair-raising", "genres": ["Horror", "Mystery"]},
    5: {"name": "❤️ Romantic", "vibe": "warm, swoony, tender", "genres": ["Romance"]},
    6: {"name": "🤯 Mind-Bending / Thoughtful", "vibe": "clever, cerebral, twisty", "genres": ["Science Fiction", "Sci-Fi", "Mystery"]},
    # "Relaxing" previously mapped to Documentary/Animation/Family only, which
    # returned preschool animation and nature docs — and, once the user added
    # their own genre chips, films like Silence of the Lambs leaked in anyway
    # because the mood contributed nothing to the actual filter. Relaxing is
    # better expressed as a *tone* (gentle pacing, no violence) than as a
    # genre, so these are deliberately the warm/low-stakes genres.
    7: {"name": "😌 Relaxing / Chill", "vibe": "gentle, cozy, low-stakes", "genres": ["Animation", "Family", "Comedy", "Romance", "Music"]},
    8: {"name": "🎲 Surprise Me!", "vibe": "anything goes", "genres": []},
}

# Natural-language intent the free-text query is actually asking for. The old
# scoring substring-matched raw query words against overviews, so a prompt like
# "relaxing but not too complicated, a bit funny" scored every film whose
# blurb happened to contain "funny" — noise, not intent. These are weighted,
# genre-mapped signals so the query steers the *filter*, not just the ranking.
# "South Asian", "Korean", "Nigerian" are PLACES, not genres. The recommender
# had no country signal at all, so asking for South Asian films returned
# Hollywood every time — genre filters could never match a place. Each entry
# maps a phrase to the region and to the audio languages that actually
# identify that region's cinema.
#
# Language is the primary signal, not country: TMDB mislabels origin for
# co-productions and outright wrong entries (it files Point Break as `IN`), but
# a film's original_language is reliable. Both are required — the region must
# match AND the language must belong to it.
ORIGIN_INTENT: Dict[str, Dict[str, Any]] = {
    # Regional groupings first. Matched longest-phrase-first, so "south asian"
    # and "south korean" resolve before the bare country names below.
    "south asian": {
        # Every South Asian country, not just India. Bangladeshi titles are a
        # large, distinct cinema (the Apu Trilogy, Tareque Masud, by-pass
        # road films) and were being excluded whenever "Indian" was the only
        # country checked. Bangladesh, Pakistan, Sri Lanka and Nepal are
        # first-class members of this group.
        "countries": ["IN", "BD", "PK", "LK", "NP"],
        "languages": ["hi", "ta", "te", "ml", "kn", "bn", "pa", "mr", "gu", "ur", "si", "ne"],
        "label": "South Asian",
    },
    "bangladeshi": {"countries": ["BD"],
                    "languages": ["bn"], "label": "Bangladeshi"},
    "bangladesh": {"countries": ["BD"],
                   "languages": ["bn"], "label": "Bangladeshi"},
    "bengali": {"countries": ["BD", "IN"],
                "languages": ["bn"], "label": "Bengali (Bangladesh/India)"},
    "deshi": {"countries": ["BD"], "languages": ["bn"], "label": "Bangladeshi (deshi)"},
    "pakistani": {"countries": ["PK"], "languages": ["ur", "pa"], "label": "Pakistani"},
    "sri lankan": {"countries": ["LK"], "languages": ["si"], "label": "Sri Lankan"},
    "nepali": {"countries": ["NP"], "languages": ["ne"], "label": "Nepali"},
    # Single-country entries are generated so every chip the UI can emit has a
    # matching intent. Hand-maintaining two lists (labels in server.py, intents
    # here) let 15 chips ship with no parser entry and silently do nothing.
    "thai": {"countries": ["TH"], "languages": ["th"], "label": "Thai"},
    "indonesian": {"countries": ["ID"], "languages": ["id"], "label": "Indonesian"},
    "filipino": {"countries": ["PH"], "languages": ["tl"], "label": "Filipino"},
    "vietnamese": {"countries": ["VN"], "languages": ["vi"], "label": "Vietnamese"},
    "greek": {"countries": ["GR"], "languages": ["el"], "label": "Greek"},
    "swedish": {"countries": ["SE"], "languages": ["sv"], "label": "Swedish"},
    "danish": {"countries": ["DK"], "languages": ["da"], "label": "Danish"},
    "norwegian": {"countries": ["NO"], "languages": ["no", "nb"], "label": "Norwegian"},
    "finnish": {"countries": ["FI"], "languages": ["fi"], "label": "Finnish"},
    "dutch": {"countries": ["NL"], "languages": ["nl"], "label": "Dutch"},
    "polish": {"countries": ["PL"], "languages": ["pl"], "label": "Polish"},
    "ukrainian": {"countries": ["UA"], "languages": ["uk"], "label": "Ukrainian"},
    # Latin American countries, reachable individually. Without these,
    # "mexican movies" parsed as no origin intent at all and silently returned
    # unfiltered Hollywood — the exact failure this whole feature exists to
    # prevent, resurfacing for any country name missing from the table.
    "mexican": {"countries": ["MX"], "languages": ["es"], "label": "Mexican"},
    "peruvian": {"countries": ["PE"], "languages": ["es"], "label": "Peruvian"},
    "argentine": {"countries": ["AR"], "languages": ["es"], "label": "Argentine"},
    "argentinian": {"countries": ["AR"], "languages": ["es"], "label": "Argentine"},
    "colombian": {"countries": ["CO"], "languages": ["es"], "label": "Colombian"},
    "chilean": {"countries": ["CL"], "languages": ["es"], "label": "Chilean"},
    "east asian": {
        "countries": ["JP", "CN", "HK", "TW", "KR"],
        "languages": ["ja", "zh", "ko"],
        "label": "East Asian",
    },
    "latin american": {
        "countries": ["BR", "MX", "AR", "CO", "CL"],
        "languages": ["pt", "es"],
        "label": "Latin American",
    },
    "chinese": {
        "countries": ["CN", "HK", "TW"],
        # A country of origin rather than an ethnicity: HK and TW films are
        # overwhelmingly Mandarin and are routinely excluded by a CN-only
        # filter. Language is what the user actually sees, so it drives both.
        "languages": ["zh", "yue"],
        "label": "Chinese-language",
    },
    "portuguese": {"countries": ["BR", "PT"], "languages": ["pt"], "label": "Portuguese"},
    "arabic": {
        "countries": ["EG", "SA", "AE", "JO", "LB", "IQ"],
        "languages": ["ar"], "label": "Arabic",
    },
    "south korean": {"countries": ["KR"], "languages": ["ko"], "label": "South Korean"},
    "north korean": {"countries": ["KP"], "languages": ["ko"], "label": "North Korean"},
    "indian": {
        # `bn` stays here as well as on Bangladesh: much of Indian Bengali
        # cinema predates the 1947 partition (the Apu Trilogy is Bengali
        # language, Indian origin). Dropping it would lose those classics.
        "countries": ["IN"],
        "languages": ["hi", "ta", "te", "ml", "kn", "bn", "pa", "mr", "gu"],
        "label": "Indian",
    },
    "bollywood": {"countries": ["IN"], "languages": ["hi"],
                  "label": "Bollywood (Hindi)"},
    "kollywood": {"countries": ["IN"], "languages": ["te"], "label": "Telugu"},
    "tollywood": {"countries": ["IN"], "languages": ["ta", "te"], "label": "Tamil/Telugu"},
    "malayalam": {"countries": ["IN"], "languages": ["ml"], "label": "Malayalam"},
    "korean": {"countries": ["KR"], "languages": ["ko"], "label": "Korean"},
    "k-drama": {"countries": ["KR"], "languages": ["ko"], "label": "Korean"},
    "japanese": {"countries": ["JP"], "languages": ["ja"], "label": "Japanese"},
    "anime": {"countries": ["JP"], "languages": ["ja"], "label": "Japanese (anime)"},
    "chinese": {"countries": ["CN", "HK", "TW"], "languages": ["zh", "cn"],
                "label": "Chinese"},
    "nigerian": {"countries": ["NG"], "languages": ["yo"], "label": "Nigerian"},
    "spanish": {"countries": ["ES", "MX", "AR", "CO", "CL"], "languages": ["es"],
                "label": "Spanish-language"},
    "french": {"countries": ["FR"], "languages": ["fr"], "label": "French"},
    "german": {"countries": ["DE"], "languages": ["de"], "label": "German"},
    "italian": {"countries": ["IT"], "languages": ["it"], "label": "Italian"},
    "portuguese": {"countries": ["BR", "PT"], "languages": ["pt"], "label": "Portuguese"},
    "russian": {"countries": ["RU"], "languages": ["ru"], "label": "Russian"},
    "turkish": {"countries": ["TR"], "languages": ["tr"], "label": "Turkish"},
    "iranian": {"countries": ["IR"], "languages": ["fa"], "label": "Iranian"},
    "egyptian": {"countries": ["EG"], "languages": ["ar"], "label": "Egyptian/Arabic"},
    "nordic": {"countries": ["SE", "NO", "DK", "FI", "IS"], "languages": ["sv", "no", "da", "fi"],
               "label": "Nordic"},
}

QUERY_INTENT: Dict[str, Dict[str, Any]] = {
    "relaxing": {"genres": ["Comedy", "Drama", "Romance", "Science Fiction"],
                 "exclude_genres": ["Horror", "War"],
                 "weight": 0.25},
    # "Chill" is a TONE, not a genre. Mapping it to Animation/Family/Music made
    # every chill request a children's-film request — which is how a request
    # for a satirical sci-fi about covert alien control (They Live) returned a
    # Mario cartoon. Low weight on purpose: it nudges, and only when no
    # stronger genre signal is present.
    "chill": {"genres": ["Comedy", "Drama", "Science Fiction", "Romance"],
              "exclude_genres": ["Horror", "War"],
              "weight": 0.2},
    "cozy": {"genres": ["Family", "Animation", "Romance", "Comedy"],
             "exclude_genres": ["Horror", "Thriller", "Crime"], "weight": 0.3},
    "funny": {"genres": ["Comedy"], "weight": 0.3},
    "complicated": {"exclude_genres": ["Horror", "Thriller", "Crime", "War"], "weight": 0.25},
    "simple": {"exclude_genres": ["Horror", "Thriller", "Crime", "War"], "weight": 0.25},
    "dark": {"genres": ["Horror", "Thriller", "Crime"], "weight": 0.3},
    "scary": {"genres": ["Horror"], "weight": 0.3},
    "thoughtful": {"genres": ["Drama", "Science Fiction"], "weight": 0.3},
    "smart": {"genres": ["Science Fiction", "Mystery"], "weight": 0.25},
}

# ===========================================================================
# THEMATIC CONCEPTS
# ===========================================================================
# Genres describe WHAT a film is; the thing this recommender was missing is
# what a film is ABOUT. "We liked that it had a great concept to interpret"
# carries no genre at all, so it matched nothing and the request fell back to
# whatever the nearest mood chip allowed — which is how a request for a
# satirical sci-fi about hidden puppetmasters returned a Mario cartoon.
#
# Each theme lists the concrete nouns that signal it in a plot synopsis.
# Matching is deliberately factual (a disguised alien, a hidden master mind),
# not vibes: a theme must be earned by the film's actual subject matter.
THEMES: Dict[str, Dict[str, Any]] = {
    "hidden_puppeteer": {
        "label": "hidden controllers among us",
        # The They Live premise: an unseen force covertly directing people.
        # "alien" alone is too broad to carry meaning, so it is paired with the
        # masquerade that makes the premise work.
        "terms": ["aliens are", "alien disguised", "shape-shifting",
                  "shapeshifting", "infiltrat", "masquerading as",
                  "masquerade", "lulling the public", "secret society",
                  "illuminati", "shadow government", "mind control",
                  "brainwash", "hypnot", "puppeteer", "covertly control",
                  "secretly control", "among us"],
        "genres": ["Science Fiction", "Thriller"],
    },
    "exploitation": {
        "label": "people used by others",
        # Deliberately narrow. Bare "control" and "corporate" match almost any
        # synopsis — a car commercial and a superhero film both contain them —
        # which is how Spider-Man and Ford v Ferrari scored as "people used by
        # others". These are phrases where exploitation is the actual subject.
        "terms": ["exploit", "manipulat", "puppet master", "puppeteer",
                  "brainwash", "mind control", "used by", "enslave",
                  "exploitation", "parasite", "feeds on", "predatory",
                  "coerc", "blackmail", "pulling their strings",
                  "who pull the strings", "on the payroll",
                  "clandestine", "black ops", "corporate espionage"],
        "genres": ["Science Fiction", "Thriller", "Drama"],
    },
    "identity": {
        "label": "questions of identity and self",
        "terms": ["identity", "who am i", "imposter", "clone", "cloning",
                  "double", "look-alike", "replica", "android", "cyborg",
                  "memory", "remember", "simulate", "virtual reality"],
        "genres": ["Science Fiction", "Drama"],
    },
    "surveillance": {
        "label": "watched and watched back",
        "terms": ["surveill", "watched", "monitor", "big brother",
                  "camera", "tracking", "spied", "observed", "pods"],
        "genres": ["Science Fiction", "Thriller"],
    },
    "satire": {
        "label": "satire of everyday life",
        "terms": ["satir", "lampoon", "mock", "parody", "ridicule",
                  "consumerism", "conformity", "middle class",
                  "bureaucracy", "corporate"],
        "genres": ["Comedy", "Science Fiction"],
    },
    "philosophical": {
        "label": "big ideas, open to interpretation",
        "terms": ["meaning", "existence", "reality", "question",
                  "dilemma", "philosoph", "meaning of", "identity of",
                  "mortality", "free will", "consciousness", "paradox"],
        "genres": ["Science Fiction", "Drama"],
    },
}

# Phrases in a request that mean "I want a concept worth thinking about".
# Kept separate from QUERY_INTENT because these carry no genre of their own —
# they are a QUALITY bar, applied across whatever genres survive the rest of
# the request. This is what "great concept up to interpret" matched on.
CONCEPT_REQUEST_TERMS = frozenset({
    "concept", "concepts", "conceptual", "interpret", "interpretation",
    "interpretable", "intellectual", "thoughtprovoking", "smart",
    "clever", "subtext", "metaphor", "metaphorical", "symbolic",
    "mindbending", "philosophical", "meaningful", "bigidea", "ideas",
    "premise", "satire", "satirical", "allegory", "allegorical",
})

# Genres a concept-driven request is allowed to draw from. Deliberately wide:
# when the user is asking for an idea rather than a genre, restricting to
# whatever adjectives happened to appear in the sentence loses the answer.
THEMES_GENRES_FOR_CONCEPT = frozenset({
    "Science Fiction", "Thriller", "Drama", "Comedy", "Mystery", "Action",
    "Horror", "Crime",
})

# Genres that are structurally incompatible with "a concept worth
# interpreting". Not a quality judgement on the film — a mismatch with the
# request. A Mario cartoon cannot satisfy a request for a satirical concept,
# however good it is.
CONCEPT_INCOMPATIBLE_GENRES = frozenset({
    "Family", "Animation", "Music", "Musical",
})

# Words too generic to carry signal — dropped before any matching so they
# don't produce spurious overview hits.
QUERY_STOPWORDS = frozenset({
    "would", "like", "want", "wantto", "wantto", "movie", "film", "watch",
    "something", "high", "quality", "good", "great", "best", "really",
    "should", "have", "that", "this", "with", "but", "not", "too", "very",
    "complicated", "some", "kind", "sort", "bit", "which", "there", "here",
    "about", "more", "less", "also", "then", "than", "them", "they", "been",
    "were", "was", "you", "your", "our", "we", "us", "me", "my", "and", "for",
    "from", "one", "two", "all", "can", "will", "just", "get", "got", "see",
})

# Tokens that must never be treated as a genre request on their own.
QUERY_NON_GENRE = frozenset({"scifi", "concept", "concepts", "idea", "ideas"})

# Genre names typed into a request. Only NEGATED mentions act on these ("no
# animation", "nothing animated or scary"): they become hard exclusions. Before
# they existed, "funny, no animation" matched no intent word at all, so
# "animation" was kept as a content word and BOOSTED every film whose synopsis
# or keywords said animation. "Drama" is left out on purpose: "no drama" usually
# means low conflict, and excluding Drama would empty most of the catalogue.
GENRE_WORDS: Dict[str, str] = {
    "animation": "Animation", "animated": "Animation", "animations": "Animation",
    "cartoon": "Animation", "cartoons": "Animation",
    "horror": "Horror", "comedy": "Comedy", "comedies": "Comedy",
    "romance": "Romance", "romantic": "Romance", "romcom": "Romance", "romcoms": "Romance",
    "documentary": "Documentary", "documentaries": "Documentary",
    "musical": "Music", "musicals": "Music",
    "war": "War", "western": "Western", "westerns": "Western",
    "thriller": "Thriller", "thrillers": "Thriller", "crime": "Crime",
    "mystery": "Mystery", "fantasy": "Fantasy", "action": "Action",
    "adventure": "Adventure", "superhero": "Action", "superheroes": "Action",
    "history": "History", "historical": "History",
    "scifi": "Science Fiction", "sci": "Science Fiction",
    "family": "Family", "kids": "Family",
}
# Words a negation reaches through when it lists genres: "no animation or
# musicals", "nothing animated, scary or kids stuff".
GENRE_LIST_FILLERS = {"or", "and", "nor", "any", "more", "movies", "movie", "films",
                      "film", "stuff", "please", "fi", "too", "much", "a", "an"}
GENRE_NEGATION_REACH = 6


def _genre_negated(tokens: List[str], i: int) -> bool:
    """A negator before a genre word, reaching back through a list of genres."""
    for j in range(i - 1, max(i - 1 - GENRE_NEGATION_REACH, -1), -1):
        # "less animation" is a softer wish than "no animation": not an exclusion.
        if tokens[j] in NEGATORS and tokens[j] != "less":
            return True
        if tokens[j] in GENRE_WORDS or tokens[j] in GENRE_LIST_FILLERS or tokens[j] in QUERY_INTENT:
            continue
        return False
    return False


GUILTY_PLEASURE_KEYWORDS = frozenset({
    "guilty pleasure", "so bad it's good", "so bad its good",
    "cringe", "b-movie", "b movie", "trash", "campy", "cheesy"
})

TV_GENRE_ALIASES: Dict[str, List[str]] = {
    "action": ["Action & Adventure", "Action"],
    "adventure": ["Action & Adventure", "Adventure"],
    "science fiction": ["Sci-Fi & Fantasy", "Science Fiction"],
    "sci-fi": ["Sci-Fi & Fantasy", "Sci-Fi"],
    "fantasy": ["Sci-Fi & Fantasy", "Fantasy"],
    "family": ["Family", "Kids"],
}


# ===========================================================================
# Vector Embedding Adaptor
# ===========================================================================
def get_vector_embeddings(text: str) -> Optional[List[float]]:
    """Encodes text using ONNX Runtime if available."""
    model_path = os.path.join(models_dir(), "model.onnx")
    if not os.path.exists(model_path) or not text.strip():
        return None

    try:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-small-en-v1.5")
        encoded = tokenizer(
            text, padding="max_length", truncation=True, max_length=128, return_tensors="np"
        )
        session = ort.InferenceSession(model_path)
        inputs = {
            "input_ids": encoded["input_ids"],
            "attention_mask": encoded["attention_mask"],
            "token_type_ids": encoded.get("token_type_ids", encoded["input_ids"] * 0),
        }
        output = session.run(None, inputs)
        hidden = output[0]
        mask = encoded["attention_mask"]
        pooled = (hidden * mask[:, :, None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        return pooled[0].tolist()
    except Exception as e:
        print(f"[Recommender] Vector embedding error: {e}", flush=True)
        return None


# ===========================================================================
# Recommender Service Engine
# ===========================================================================
PROFILE_MIX: Dict[str, Tuple[float, float]] = {
    # (own weight, other weight), where "own" ALWAYS means the viewer the
    # result is for, never profile 1. Asymmetric on purpose: this is one
    # person's evening informed by the other's taste, not a committee.
    # Averaging 50/50 would recommend the compromise, which neither of them
    # asked for.
    #
    # `me` and `partner` deliberately share the same weights. The id swap in
    # rank_candidates_profile_aware already re-points "own" at the right
    # profile, so encoding a lower own-weight here would have inverted the
    # intent — "his picks" once weighted HIS taste at 0.25 and ranked the
    # viewer's own films first.
    "together": (0.6, 0.4),
    "me": (0.75, 0.25),
    "partner": (0.75, 0.25),
}

# Vibe search (design doc §2.6): weight of the text-to-film similarity, and
# how many nearest titles join the SQL shortlist.
# Memory of what Discover already showed (see RecommenderService._apply_memory).
# A title shown and not opened loses MEMORY_IGNORED points per showing, fading to
# nothing over MEMORY_IGNORED_DAYS; "Not this one" removes it for MEMORY_DISMISSED_DAYS.
MEMORY_IGNORED = 1.0
MEMORY_IGNORED_CAP = 3.0
MEMORY_IGNORED_DAYS = 14
MEMORY_DISMISSED = 5.0
MEMORY_DISMISSED_DAYS = 30

# Averaged taste (recommender.taste_formula: average). The old formula ADDED a
# weight per liked genre, so the more films of a genre you rated the bigger its
# bonus, a film with five genres beat one with two, and with ~85 ratings every
# film hit the +2.0 cap and taste stopped ranking anything. Here a genre's
# affinity is how much more (or less) you liked its films than your own average,
# shrunk towards 0 for genres with few ratings; a film gets the MEAN over its
# genres, so neither rating lots of one genre nor carrying many genres inflates it.
TASTE_GENRE_SHRINK = 3.0
TASTE_MEAN_SHRINK = 5.0
TASTE_KEYWORD_SHRINK = 2.0
TASTE_KEYWORD_MIN = 1         # a keyword needs this many rated films to count
TASTE_GENRE_SCALE = 4.0
TASTE_KEYWORD_SCALE = 3.0
TASTE_KEYWORD_FULL = 3        # keyword evidence counts fully from this many matches


def build_taste_affinity(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """rows: [{genres, tier2_keywords, score -1..+1}] for one profile ->
    {"genres": {g: affinity}, "keywords": {k: affinity}, "n": count}."""
    if not rows:
        return {"genres": {}, "keywords": {}, "n": 0}
    # Your average, shrunk towards neutral while there are few ratings: with one
    # rating, "above your own average" would be nothing at all.
    mean = sum(r["score"] for r in rows) / (len(rows) + TASTE_MEAN_SHRINK)

    def affinities(field: str, shrink: float, min_count: int) -> Dict[str, float]:
        total: Dict[str, float] = {}
        count: Dict[str, int] = {}
        for r in rows:
            for key in set(_split_lower(r.get(field))):
                total[key] = total.get(key, 0.0) + (r["score"] - mean)
                count[key] = count.get(key, 0) + 1
        return {k: total[k] / (count[k] + shrink) for k in total if count[k] >= min_count}

    return {
        "genres": affinities("genres", TASTE_GENRE_SHRINK, 1),
        "keywords": affinities("tier2_keywords", TASTE_KEYWORD_SHRINK, TASTE_KEYWORD_MIN),
        "n": len(rows),
    }


def averaged_taste(item_genres: Set[str], item_keywords: Set[str], affinity: Dict[str, Any]) -> float:
    """One film's taste score under the averaged formula (can be negative)."""
    genres = affinity.get("genres") or {}
    keywords = affinity.get("keywords") or {}
    score = 0.0
    if item_genres:
        score += TASTE_GENRE_SCALE * sum(genres.get(g, 0.0) for g in item_genres) / len(item_genres)
    hits = [keywords[k] for k in item_keywords if k in keywords]
    if hits:
        score += (TASTE_KEYWORD_SCALE * sum(hits) / len(hits)
                  * min(1.0, len(hits) / TASTE_KEYWORD_FULL))
    return score


# Genre spread: "shelves" a page should not fill up with unless asked. At most
# SPREAD_MAX titles from one shelf in any SPREAD_WINDOW consecutive picks.
SPREAD_GENRES = frozenset({"animation", "horror", "documentary", "music", "war", "western", "family"})
SPREAD_MAX = 2
SPREAD_WINDOW = 10


def apply_genre_spread(items: List[Dict[str, Any]], exempt: Optional[Set[str]] = None,
                       max_per_window: int = SPREAD_MAX, window: int = SPREAD_WINDOW) -> List[Dict[str, Any]]:
    """Reorder so no shelf genre takes more than `max_per_window` of any `window`
    consecutive picks. Titles are only moved later, never dropped; when nothing
    fits, the best remaining title goes next anyway. `exempt` holds the genres
    the request asked for (lower case), which are never held back."""
    shelves = SPREAD_GENRES - {g.lower() for g in (exempt or set())}
    if not shelves:
        return items
    tagged = [(it, set(_split_lower(it.get("genres"))) & shelves) for it in items]
    out: List[Tuple[Dict[str, Any], Set[str]]] = []
    while tagged:
        recent: Dict[str, int] = {}
        for _, gs in out[-(window - 1):]:
            for g in gs:
                recent[g] = recent.get(g, 0) + 1
        pick = next((k for k, (_, gs) in enumerate(tagged)
                     if all(recent.get(g, 0) < max_per_window for g in gs)), 0)
        out.append(tagged.pop(pick))
    return [it for it, _ in out]


VIBE_WEIGHT = 3.0
VIBE_SHORTLIST = 400

TIER_BOOSTS: Dict[str, float] = {
    # A: you both liked it — the scarcest and most useful signal
    # B: one of you loved it, the other hasn't seen it — "he'd recommend this"
    # C: exploration
    "A": 1.5,
    "B": 0.8,
    "C": 0.0,
}


def _origin_negated(before: str) -> bool:
    """True when the words just before an origin phrase negate it."""
    words = re.findall(r"[a-z]+", before)[-3:]
    if not words:
        return False
    return (any(w in NEGATORS for w in words) or words[-1] == "except"
            or words[-2:] in (["anything", "but"], ["everything", "but"]))


def parse_origin_intent(query: Optional[str]) -> Optional[Dict[str, Any]]:
    """Detect a request for cinema of a PLACE rather than a genre.

    "South Asian movies" names an origin, not a genre, so the genre filters
    could never match it and the recommender returned Hollywood regardless.
    Matches the longest phrase first ("south asian" before "asian") so the more
    specific region wins.

    Returns countries + languages to filter on, or None if the query names no
    origin.
    """
    if not query:
        return None
    text = query.lower()
    best = None
    for phrase, spec in ORIGIN_INTENT.items():
        # Whole words only (an optional plural "s"): a bare substring test read
        # "indiana jones" as Indian, "polished" as Polish and "animated" as anime.
        m = re.search(rf"\b{re.escape(phrase)}s?\b", text)
        # "no anime", "anything but korean": a negated place is not a request
        # for it (it used to restrict the results to exactly that place).
        if m and _origin_negated(text[:m.start()]):
            continue
        if m:
            # Prefer the longest matching phrase (most specific region).
            if best is None or len(phrase) > len(best[0]):
                best = (phrase, spec)
    if best is None:
        return None
    phrase, spec = best
    countries = list(spec.get("countries", []))
    # "South Asian" covers several countries, so allow the extras too.
    for extra in spec.get("also_countries", []):
        if extra not in countries:
            countries.append(extra)
    return {
        "phrase": phrase,
        "label": spec.get("label", phrase.title()),
        "countries": countries,
        "languages": list(spec.get("languages", [])),
    }


def _theme_mentioned(theme: str, tokens: List[str]) -> bool:
    """True when the request itself names a theme (not just its genre)."""
    haystack = " ".join(tokens)
    return any(t in haystack for t in THEMES[theme]["terms"])


NEGATORS = {"not", "no", "never", "without", "less", "nothing", "avoid", "skip",
            "dont", "don", "isn", "aren", "nor", "none"}
# Words that end a negation's reach: "not scary but funny" negates scary only.
NEGATION_BREAKERS = {"but", "and", "or", "yet", "though", "although", "however", "just", "also"}
NEGATION_WINDOW = 3
NEGATION_KEEPS_EFFECT = {"complicated", "simple"}


def _is_negated(tokens: List[str], i: int) -> bool:
    """A negator up to three words back, with no "but"/"and" in between.

    A one-word window missed "not too complicated" and "no really scary".
    """
    for j in range(i - 1, max(i - 1 - NEGATION_WINDOW, -1), -1):
        if tokens[j] in NEGATION_BREAKERS:
            return False
        if tokens[j] in NEGATORS:
            return True
    return False


def parse_query_intent(query: Optional[str]) -> Dict[str, Any]:
    """Turn a free-text prompt into genre include/exclude signals + weights.

    The old behaviour substring-matched every query word against item
    overviews, so "relaxing but not too complicated, a bit funny" boosted any
    film whose blurb contained "funny" while "relaxing" did nothing at all.
    Intent words are now recognised explicitly and mapped to genres, so they
    steer the hard filter (exclusions especially) as well as the score.

    Returns:
        include_genres: genres the user positively asked for
        exclude_genres: genres contradicting the request (hard-excluded)
        boost_genres:   genres to nudge in scoring
        weight:         how strongly the query should shape the result
    """
    result: Dict[str, Any] = {
        "include_genres": [], "exclude_genres": [],
        "boost_genres": [], "weight": 0.0,
        "concept_requested": False, "themes": [],
    }
    if not query or not query.strip():
        return result

    tokens = [w for w in re.findall(r"[a-z]+", query.lower()) if w]
    seen_include: Set[str] = set()
    seen_exclude: Set[str] = set()
    matched_terms: List[str] = []

    for i, word in enumerate(tokens):
        intent = QUERY_INTENT.get(word)
        if not intent:
            continue
        negated = _is_negated(tokens, i)
        matched_terms.append(word)
        # "not complicated" means simple, and both words carry the same
        # exclusions, so for these the negation must not flip anything.
        if negated and word in NEGATION_KEEPS_EFFECT:
            negated = False

        for g in intent.get("genres", []):
            if negated:
                seen_exclude.add(g)
            else:
                seen_include.add(g)
                if g not in result["boost_genres"]:
                    result["boost_genres"].append(g)

        for g in intent.get("exclude_genres", []):
            # "not too complicated" negates the *exclusion* this term implies.
            if negated:
                seen_include.add(g)
            else:
                seen_exclude.add(g)

        if not negated:
            result["weight"] += intent.get("weight", 0.2)

    # Typed genre names, negated: a hard exclusion the user asked for by name.
    named_exclude: Set[str] = set()
    negated_words: Set[str] = set()
    for i, word in enumerate(tokens):
        if word in GENRE_WORDS and _genre_negated(tokens, i):
            named_exclude.add(GENRE_WORDS[word])
            negated_words.add(word)
            matched_terms.append(f"no {word}")
        elif word not in QUERY_INTENT and _is_negated(tokens, i):
            # "no zombies": a negated word must never boost films that mention it.
            negated_words.add(word)
    seen_exclude |= named_exclude
    seen_include -= named_exclude
    result["boost_genres"] = [g for g in result["boost_genres"] if g not in named_exclude]

    # "A great concept to interpret" is a QUALITY bar, not a genre. Detected
    # separately so it can veto Family/Animation/Music and pull in themes,
    # rather than silently resolving to whichever mood genres were nearby.
    concept_requested = any(w in CONCEPT_REQUEST_TERMS for w in tokens)
    if concept_requested:
        matched_terms.append("concept")

    # A request that asks for a CONCEPT must not also be restricted to the
    # genres of the words that surround it. "Chill, a bit funny, but a great
    # concept like They Live" contains "funny", which pulls in Comedy — and
    # They Live is Sci-Fi/Action/Thriller with no Comedy at all, so the very
    # film the user named was hard-excluded by their own description of it.
    # Concept-bearing requests widen rather than narrow: the concept signal
    # discriminates on premise, so a fixed genre whitelist only gets in the way.
    if concept_requested:
        for g in THEMES_GENRES_FOR_CONCEPT:
            seen_include.add(g)

    # A genre cannot be both required and forbidden. The concept widening list
    # is a permissive safety net, not a demand, so when it collides with an
    # explicit exclusion the EXCLUSION wins and the genre leaves the include
    # list. Losing "Horror" from the pool is correct; the user's "chill, not
    # horror" is a real constraint and must not be silently discarded.
    if concept_requested:
        seen_include -= seen_exclude
    seen_include -= named_exclude

    result["include_genres"] = sorted(seen_include)
    result["exclude_genres"] = sorted(seen_exclude)
    result["named_exclude_genres"] = sorted(named_exclude)
    result["weight"] = round(min(result["weight"], 1.0), 2)
    result["matched_terms"] = matched_terms
    result["concept_requested"] = concept_requested
    result["themes"] = [name for name in THEMES if _theme_mentioned(name, tokens)]

    # Content words worth matching against overviews/titles: intent words are
    # already handled structurally, and stopwords carry no signal.
    result["content_words"] = sorted(
        w for w in set(tokens)
        if len(w) > 3 and w not in QUERY_STOPWORDS
        and w not in QUERY_INTENT and w not in QUERY_NON_GENRE
        and w not in negated_words
    )
    return result


def resolve_effective_genres(
    mood_genres: Optional[List[str]],
    chip_genres: Optional[List[str]],
    query: Optional[str],
) -> Dict[str, Any]:
    """Combine mood genres + user's chips + query intent into one filter spec.

    Precedence: explicit chips win (the user clicked them deliberately), then
    query intent, then the mood's own genres. Exclusions apply regardless —
    asking for "relaxing, not too complicated" should veto a violent film even
    if Horror is among the chips, because the phrase is a contradiction rather
    than a narrowing choice.
    """
    intent = parse_query_intent(query)
    chip_list = list(chip_genres or [])
    mood_list = list(mood_genres or [])

    include: List[str] = []
    for g in chip_list + intent["include_genres"] + mood_list:
        if g not in include:
            include.append(g)

    # Only apply exclusion filtering when the query actually expressed one,
    # and never let a mood's own genre veto its films (mood 4 = Horror must
    # still be able to return horror).
    exclude: List[str] = []
    named = set(intent.get("named_exclude_genres", []))
    if intent["exclude_genres"]:
        for g in intent["exclude_genres"]:
            # A genre typed by name ("no animation") is excluded even when the
            # mood's own genres include it: the user said so in words.
            if g in named:
                include = [x for x in include if x != g]
            elif g in include and g in mood_list and mood_list and intent["weight"] < 0.3:
                continue
            if g not in exclude:
                exclude.append(g)

    return {
        "include": include,
        "exclude": exclude,
        "boost": intent["boost_genres"],
        "weight": intent["weight"],
        "content_words": intent.get("content_words", []),
        "matched_terms": intent.get("matched_terms", []),
        "concept_requested": intent.get("concept_requested", False),
        "themes": intent.get("themes", []),
    }



# ===========================================================================
# Series order
# ===========================================================================
# Fields describing WHY an item ranked where it did. When a sequel is swapped
# for the earlier film you should watch first, the earlier film inherits the
# sequel's slot (and these scores) so the list order is unchanged.
_SERIES_INHERITED_KEYS = (
    "recommendation_score", "base_weighted_rating", "taste_bonus",
    "taste_bonus_own", "taste_bonus_other", "relevance_boost", "query_boost",
    "co_view_tier", "tier_boost", "matched_themes", "vibe_similarity", "vibe_boost",
    "facet_fit", "cluster_affinity", "taste_reasons",
)
_SERIES_INTERNAL_KEYS = ("seen", "series_position")


def apply_series_order(items: List[Dict[str, Any]], db: Any) -> List[Dict[str, Any]]:
    """Make films in a series come out in watch order.

    For each film that belongs to a series (TMDB collection, or a sequel
    title like "Toy Story 3" before the collection backfill has run):
      - if an EARLIER entry hasn't been seen by either of you, it replaces the
        sequel in the same slot ("start with Philosopher's Stone, not
        Deathly Hallows Part 1");
      - only one entry per series is kept (the next one to watch);
      - otherwise the film is kept and labelled with its position.

    Order-preserving and engine-agnostic: both engines call it on their ranked
    list before pagination. Any lookup failure returns the list unchanged.
    """
    if not items or db is None or not hasattr(db, "get_series_context"):
        return items
    movie_ids = [
        int(it["id"]) for it in items
        if it.get("id") and (it.get("media_type") or "movie") == "movie"
    ]
    try:
        ctx = db.get_series_context(movie_ids)
    except Exception as e:  # never let series logic break recommendations
        print(f"[Recommender] series lookup skipped ({e})", flush=True)
        return items
    if not ctx:
        return items

    out: List[Dict[str, Any]] = []
    used_series: Set[str] = set()
    used_ids: Set[int] = set()
    for it in items:
        iid = it.get("id")
        s = ctx.get(int(iid)) if iid else None
        if not s:
            if iid not in used_ids:
                out.append(it)
                used_ids.add(iid)
            continue
        if s["key"] in used_series:
            continue  # one film per series per list
        used_series.add(s["key"])
        name = s.get("name") or "the series"
        next_up = next((m for m in s["members"] if not m["seen"]), None)

        if next_up is None or next_up["id"] == iid or next_up["series_position"] > s["position"]:
            if iid in used_ids:
                continue
            it["series_name"] = name
            it["series_position"] = s["position"]
            it["series_size"] = s["size"]
            if s["position"] > 1:
                it["series_note"] = f"#{s['position']} of {s['size']} in {name} — you've seen the earlier ones"
            out.append(it)
            used_ids.add(iid)
            continue

        if next_up["id"] in used_ids:
            continue
        rep = {k: v for k, v in next_up.items() if k not in _SERIES_INTERNAL_KEYS}
        for k in _SERIES_INHERITED_KEYS:
            if k in it:
                rep[k] = it[k]
        rep["series_name"] = name
        rep["series_position"] = next_up["series_position"]
        rep["series_size"] = s["size"]
        rep["series_note"] = (
            f"Watch first: #{next_up['series_position']} of {s['size']} in {name}"
            f" (instead of {it.get('title')})"
        )
        rep["replaced_sequel_id"] = iid
        rep["replaced_sequel_title"] = it.get("title")
        out.append(rep)
        used_ids.add(next_up["id"])
    return out


class RecommenderService:
    def __init__(
        self,
        db_manager: DatabaseManager,
        api_key: Optional[str] = None,
        enable_tier2: bool = True,
        shortlist_size: int = 500,
        final_pool: int = 200,
        vibe_index: Optional[Any] = None,
        taste_runtime: Optional[Any] = None,
        taste_formula: str = "average",
    ):
        self.db = db_manager
        self.taste_formula = taste_formula     # "average" (build_taste_affinity) or "sum" (old, capped)
        self.taste_runtime = taste_runtime     # services.taste_runtime.TasteRuntime, optional
        self.vibe_index = vibe_index           # services.vibe_index.VibeIndex, optional
        self._title_search = None
        self.api_key = api_key
        self.enable_tier2 = enable_tier2
        self.shortlist_size = shortlist_size   # Funnel 2: quality-cut pool size
        self.final_pool = final_pool           # Funnel 4: diverse pool for pagination
        self.http = requests.Session()

    # -- Vibe search (design doc §2) ----------------------------------------
    def vibe_ready(self, query: Optional[str]) -> bool:
        """True when a typed request can be answered by vibe similarity."""
        return bool(query and query.strip() and self.vibe_index is not None and self.vibe_index.is_ready())

    def _vibe_query(self, query: str) -> Optional[Dict[str, Any]]:
        from services.vibe_index import resolve_like_titles

        if self._title_search is None:
            from services.catalogue_search import CatalogueSearch
            self._title_search = CatalogueSearch(self.db)
        try:
            like = resolve_like_titles(query, self.db, self._title_search)
            vec = self.vibe_index.query_vector(query, [t["id"] for t in like])
        except Exception as e:
            print(f"[Recommender] Vibe query failed ({e}); using the older ranking.", flush=True)
            return None
        if vec is None:
            return None
        return {"vector": vec, "like": like}

    def _apply_vibe(self, ranked: List[Dict[str, Any]], vibe: Dict[str, Any]) -> List[Dict[str, Any]]:
        """score += VIBE_WEIGHT · similarity, rescaled over this candidate pool.

        Raw cosines from bge sit in a narrow band (~0.55–0.8), so they are
        stretched from the pool's 25th percentile (0) to its best match (1).
        Titles added after the last index build have no vector and get 0.
        """
        sims = self.vibe_index.similarities(vibe["vector"], [it["id"] for it in ranked if it.get("id")])
        if not sims:
            return ranked
        values = sorted(sims.values())
        lo, hi = values[len(values) // 4], values[-1]
        span = max(hi - lo, 1e-6)
        like_ids = {t["id"] for t in vibe["like"]}
        for it in ranked:
            s = sims.get(it.get("id"))
            if s is None:
                continue
            boost = VIBE_WEIGHT * min(max((s - lo) / span, 0.0), 1.0)
            it["vibe_similarity"] = round(s, 3)
            it["vibe_boost"] = round(boost, 2)
            it["recommendation_score"] = round(float(it.get("recommendation_score") or 0.0) + boost, 2)
            if like_ids and it["id"] in like_ids:
                it["vibe_named"] = True
        ranked = [it for it in ranked if it.get("id") not in like_ids]   # don't suggest the film you named
        ranked.sort(key=lambda it: it.get("recommendation_score") or 0.0, reverse=True)
        return ranked

    def _apply_memory(self, ranked: List[Dict[str, Any]], skip_session_id: Optional[int]) -> List[Dict[str, Any]]:
        """Push down what Discover showed lately and nobody opened, and keep
        "Not this one" titles out for a month. Without this every search
        returned the same top 50, because the scores never change between nights."""
        try:
            memory = self.db.get_shown_memory(MEMORY_DISMISSED_DAYS, skip_session_id)
        except Exception as e:  # noqa: BLE001
            print(f"[Recommender] Shown-memory lookup failed ({e}); ranking without it.", flush=True)
            return ranked
        if not memory:
            return ranked
        penalties: Dict[int, float] = {}
        for iid, m in memory.items():
            penalty = sum(MEMORY_IGNORED * (1.0 - age / MEMORY_IGNORED_DAYS)
                          for age in m["ignored_ages"] if age < MEMORY_IGNORED_DAYS)
            penalty = min(penalty, MEMORY_IGNORED_CAP)
            if m["dismissed"]:
                penalty += MEMORY_DISMISSED
            if penalty > 0:
                penalties[iid] = penalty
        # A whole series shares the penalty: series order swaps a sequel for the
        # first film you haven't seen, which would bring that film straight back.
        try:
            ctx = self.db.get_series_context(list(penalties)) if penalties else {}
        except Exception:  # noqa: BLE001
            ctx = {}
        for iid, s in ctx.items():
            for member in s.get("members") or []:
                mid = member.get("id")
                if mid is not None and penalties.get(mid, 0.0) < penalties.get(iid, 0.0):
                    penalties[mid] = penalties[iid]
        for it in ranked:
            penalty = penalties.get(it.get("id"), 0.0)
            if penalty > 0:
                it["memory_penalty"] = round(penalty, 2)
                it["recommendation_score"] = round(float(it.get("recommendation_score") or 0.0) - penalty, 2)
        ranked.sort(key=lambda it: it.get("recommendation_score") or 0.0, reverse=True)
        return ranked

    def _resolve_profiles(self, profile: str) -> Tuple[int, int]:
        """Maps a profile selector to concrete profile ids.

        `together` is NOT a stored profile — it is a computed blend over both,
        so storing it would double-count every film both people rated.
        """
        ids = sorted(self.db.get_all_profile_tastes().keys())
        if not ids:
            return (1, 2)
        primary = ids[0]
        other = ids[1] if len(ids) > 1 else primary
        return (primary, other)

    def _taste_bonus(
        self,
        item_genres: Set[str],
        item_keywords: Set[str],
        own_taste: Dict[str, Any],
        other_taste: Dict[str, Any],
        mix: Tuple[float, float],
    ) -> Tuple[float, float, float]:
        """Blended taste bonus for one item, plus its own/other components.

        Profiles are blended HERE, at scoring time, never pre-averaged into a
        single profile — that would turn "you like horror, he doesn't" into a
        lukewarm average nobody actually wants.
        """
        own_w, other_w = mix

        def bonus_for(taste: Dict[str, Any]) -> float:
            if "affinity" in taste:
                return averaged_taste(item_genres, item_keywords, taste["affinity"])
            genres = taste.get("preferred_genres", {})
            keywords = taste.get("preferred_keywords", {})
            total = 0.0
            for g in item_genres:
                if g in genres:
                    total += genres[g] * 0.2
            for k in item_keywords:
                if k in keywords:
                    total += keywords[k] * 0.15
            return min(total, 2.0)

        own_bonus = bonus_for(own_taste)
        other_bonus = bonus_for(other_taste)
        blended = own_w * own_bonus + other_w * other_bonus
        return blended, own_bonus, other_bonus

    def rank_candidates_profile_aware(
        self,
        candidates: List[Dict[str, Any]],
        profile: str = "together",
        query: Optional[str] = None,
        exclude_ids: Optional[Set[int]] = None,
        content_words: Optional[List[str]] = None,
        intent_boost_genres: Optional[Set[str]] = None,
        intent_bonus: float = 0.0,
        concept_requested: bool = False,
    ) -> List[Dict[str, Any]]:
        """Profile-aware scoring: Bayesian quality + blended taste + co-view tier.

        The tier boost is what makes "together" work. Without it a Tier A film
        (you both loved it) always loses to a marginally better-rated unseen
        one, and your shared favourites quietly disappear from the list.
        """
        tastes = self.db.get_all_profile_tastes()
        if not tastes:
            tastes = {1: {"preferred_genres": {}, "preferred_keywords": {},
                          "rating_count": 0, "name": "Me", "weight": 0.0},
                      2: {"preferred_genres": {}, "preferred_keywords": {},
                          "rating_count": 0, "name": "Partner", "weight": 0.0}}
        ids = sorted(tastes.keys())
        own_id, other_id = ids[0], (ids[1] if len(ids) > 1 else ids[0])

        if profile == "partner":
            own_id, other_id = other_id, own_id   # view from his seat

        own_taste = tastes.get(own_id, {})
        other_taste = tastes.get(other_id, {})
        if self.taste_formula == "average":
            own_taste = {**own_taste, "affinity": build_taste_affinity(self.db.get_profile_taste_rows(own_id))}
            other_taste = {**other_taste, "affinity": build_taste_affinity(self.db.get_profile_taste_rows(other_id))}

        # For `me`/`partner` the mix is defined from the VIEWER's seat, so
        # swapping the ids must also swap which weight is "own". Swapping ids
        # alone would leave both modes using the same 0.75 on profile 1, making
        # `me` and `partner` return identical lists.
        if profile == "me":
            own_w, other_w = PROFILE_MIX["me"]
        elif profile == "partner":
            own_w, other_w = PROFILE_MIX["partner"]
        else:
            own_w, other_w = PROFILE_MIX["together"]
        mix = (own_w, other_w)

        coview = self.db.get_item_co_view(
            [c["id"] for c in candidates if c.get("id")], viewer_profile_id=own_id
        )
        exclude = exclude_ids or set()
        # In `me`/`partner` mode the viewer has no use for films they've already
        # rated. In `together` mode both may still have untested picks (tier B),
        # so only films BOTH have seen are dropped — handled upstream.
        viewer_seen_ids = {
            iid for iid, cv in coview.items() if own_id in (cv.get("seen_by") or [])
        }
        if profile in ("me", "partner"):
            exclude = exclude | viewer_seen_ids
        query_words = set(content_words) if content_words else set()
        # `intent_boost_genres` / `intent_bonus` come pre-computed from the
        # caller's query spec: genres the prompt asked for ("funny" -> Comedy)
        # get a real score bump. Previously only free-text overview matching
        # contributed, so a query could pull a film in and still rank it
        # mid-table.

        # Bayesian prior must scale with the pool being ranked. A fixed
        # min_votes=1000 suits a mainstream catalogue averaging ~820 votes, but
        # Bangladeshi cinema averages 3.7 and peaks at 190 — every title was
        # pinned to the 6.5 prior, so ranking degenerated to noise and 1-vote
        # films tied classics. Derive the prior from the actual candidates:
        # strong-attested regions are unaffected, small cinemas get a prior
        # they can actually climb out of.
        pool_votes = [int(c.get("vote_count") or 0) for c in candidates]
        pool_votes.sort()
        median_votes = pool_votes[len(pool_votes) // 2] if pool_votes else 0
        prior_min_votes = max(10, min(1000, median_votes))

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for item in candidates:
            theme_boost = 0.0
            if item.get("id") in exclude:
                continue

            base_score = calculate_weighted_rating(
                float(item.get("rating") or 6.0),
                int(item.get("vote_count") or 100),
                min_votes=prior_min_votes,
            )

            item_genres = set(_split_lower(item.get("genres")))
            item_keywords = set(_split_lower(item.get("tier2_keywords")))
            relevance_boost = 0.0
            # Query intent bump: the film carries a genre the prompt asked for
            # ("funny" -> Comedy), independent of either person's taste.
            if intent_boost_genres and (item_genres & intent_boost_genres):
                relevance_boost += intent_bonus
            taste_bonus, own_bonus, other_bonus = self._taste_bonus(
                item_genres, item_keywords, own_taste, other_taste, mix
            )

            cv = coview.get(item.get("id")) or {}
            tier = cv.get("tier", "C")
            tier_boost = TIER_BOOSTS.get(tier, 0.0)

            if query_words:
                item_text = f"{item.get('title')} {item.get('overview')} {item.get('tier2_keywords')}".lower()
                matches = sum(1 for w in query_words if len(w) > 3 and w in item_text)
                relevance_boost = min(matches * 0.3, 2.0)

                relevance_boost = min(matches * 0.3, 2.0)

            # Thematic signal. Genres say what a film IS; when the request is
            # about a concept ("hidden puppetmasters among us"), only the plot
            # can answer it. Scored against the synopsis rather than the genre
            # list, so a satirical Sci-Fi about covert alien control outranks a
            # Sci-Fi comedy about nothing in particular.
            if concept_requested:
                item_text_lower = (
                    f"{item.get('title')} {item.get('overview')}"
                ).lower()
                matched_themes = [
                    name for name in THEMES
                    if any(t in item_text_lower for t in THEMES[name]["terms"])
                ]
                if matched_themes:
                    theme_boost += min(0.6 + 0.35 * (len(matched_themes) - 1), 1.6)
                    item["matched_themes"] = [
                        THEMES[n]["label"] for n in matched_themes
                    ]
                # A request for an interpretable concept cannot be satisfied by
                # a family cartoon, however well rated. Soft penalty rather than
                # a hard filter: the user asked for "chill AND funny AND deep",
                # not for a genre whitelist, and a film should be able to
                # surprise us.
                if item_genres & CONCEPT_INCOMPATIBLE_GENRES:
                    theme_boost -= 1.2

            final_score = base_score + taste_bonus + tier_boost + relevance_boost + theme_boost

            item["recommendation_score"] = round(final_score, 2)
            item["base_weighted_rating"] = round(base_score, 2)
            item["taste_bonus"] = round(taste_bonus, 2)
            item["taste_bonus_own"] = round(own_bonus, 2)
            item["taste_bonus_other"] = round(other_bonus, 2)
            item["co_view_tier"] = tier
            item["tier_boost"] = tier_boost
            item["relevance_boost"] = round(relevance_boost, 2)
            if cv.get("combined_score") is not None:
                item["combined_score"] = cv["combined_score"]
            if cv.get("seen_by"):
                item["seen_by_profile_ids"] = cv["seen_by"]
            other_name = other_taste.get("name") or "They"
            if tier == "A":
                item["tier_reason"] = "You both liked it"
            elif tier == "B":
                item["tier_reason"] = f"{other_name} loved this — you haven't"

            scored.append((final_score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    def rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        taste_profile: Dict[str, Any],
        query: Optional[str] = None,
        concept_requested: bool = False,
    ) -> List[Dict[str, Any]]:
        """Multi-faceted scoring combining Bayesian rating, taste profile, and vector similarity."""
        preferred_genres = taste_profile.get("preferred_genres", {})
        preferred_keywords = taste_profile.get("preferred_keywords", {})

        # Compute query vector embedding if model is deployed
        query_vector = get_vector_embeddings(query) if query else None

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for item in candidates:
            raw_rating = float(item.get("rating") or 6.0)
            num_votes = int(item.get("num_votes") or item.get("vote_count") or 100)

            # 1. Base Score: Bayesian Weighted Rating
            base_score = calculate_weighted_rating(raw_rating, num_votes)

            # 2. Taste Affinity Score
            taste_bonus = 0.0
            item_genres = set(_split_lower(item.get("genres")))
            item_keywords = set(_split_lower(item.get("tier2_keywords")))

            for g in item_genres:
                if g in preferred_genres:
                    taste_bonus += preferred_genres[g] * 0.2
            for k in item_keywords:
                if k in preferred_keywords:
                    taste_bonus += preferred_keywords[k] * 0.15

            taste_bonus = min(taste_bonus, 2.0)

            # 3. Vector Semantic Relevance vs Term Matching Boost
            relevance_boost = 0.0
            if query_vector and item.get("embedding"):
                # Fast Cosine Similarity using stored JSON vector
                similarity = cosine_similarity(query_vector, item["embedding"])
                relevance_boost = similarity * 3.0
            elif query:
                # Text fall-back match
                q_words = set(re.findall(r"\w+", query.lower()))
                item_text = f"{item.get('title')} {item.get('overview')} {item.get('tier2_keywords')}".lower()
                matches = sum(1 for w in q_words if len(w) > 3 and w in item_text)
                relevance_boost = min(matches * 0.3, 2.0)

            # Same concept layer as the profile-aware path. A cold-start user
            # (no ratings yet) previously fell to this loop and got concept-
            # blind results, so the very first search of a new account could
            # return a cartoon for a request about hidden controllers.
            theme_boost = 0.0
            if concept_requested:
                item_text_lower = (
                    f"{item.get('title')} {item.get('overview')}"
                ).lower()
                matched_themes = [
                    n for n in THEMES
                    if any(t in item_text_lower for t in THEMES[n]["terms"])
                ]
                if matched_themes:
                    theme_boost += min(0.6 + 0.35 * (len(matched_themes) - 1), 1.6)
                    item["matched_themes"] = [THEMES[n]["label"] for n in matched_themes]
                if item_genres & CONCEPT_INCOMPATIBLE_GENRES:
                    theme_boost -= 1.2

            final_score = base_score + taste_bonus + relevance_boost + theme_boost

            item["recommendation_score"] = round(final_score, 2)
            item["base_weighted_rating"] = round(base_score, 2)
            item["taste_bonus"] = round(taste_bonus, 2)
            item["relevance_boost"] = round(relevance_boost, 2)

            scored.append((final_score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    def apply_mmr_diversity(
        self, candidates: List[Dict[str, Any]], top_n: int, lambda_param: float = 0.7
    ) -> List[Dict[str, Any]]:
        """Maximal Marginal Relevance (MMR) re-ranking to maximize diversity and remove duplicates."""
        if not candidates or top_n <= 0:
            return []

        # Genre sets and title stems computed once: the pool is now 200, and
        # re-splitting them inside the double loop made MMR the slowest step.
        genres_of = [frozenset(_split_lower(c.get("genres"))) for c in candidates]
        stem_of = [(c.get("title") or "").split(":")[0] for c in candidates]
        # max_sim[i]: item i's highest similarity to anything selected so far,
        # updated incrementally as each pick is made.
        max_sim = [0.0] * len(candidates)
        remaining = list(range(len(candidates)))
        selected: List[Dict[str, Any]] = []

        while remaining and len(selected) < top_n:
            best = max(remaining, key=lambda i: (
                lambda_param * (candidates[i].get("recommendation_score") or 0.0)
                - (1 - lambda_param) * max_sim[i], -i))
            remaining.remove(best)
            selected.append(candidates[best])
            for i in remaining:
                sim = jaccard_similarity(genres_of[i], genres_of[best])
                # Deduplicate franchises/same main title stems
                if stem_of[i] == stem_of[best]:
                    sim += 0.5
                if sim > max_sim[i]:
                    max_sim[i] = sim

        return selected

    def recommend(
        self,
        mood_id: int = 8,
        exclude_watched: bool = True,
        era_preference: Optional[str] = None,
        duration_preference: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
        sub_mood: Optional[str] = None,
        query: Optional[str] = None,
        media_type: str = "movie",
        genres_override: Optional[List[str]] = None,
        min_rating_override: Optional[float] = None,
        variety: bool = False,
        profile: str = "together",
        pool_hook: Optional[Any] = None,
        return_pool: bool = False,
        only_ids: Optional[List[int]] = None,
        remember_shown: bool = False,
        skip_session_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Executes multi-stage filtering, scoring, MMR diversity pruning, and pagination.

        only_ids limits the first candidate query to these catalogue ids (the Northern Lights
        "For both of us" row ranks only titles that are in the library).

        pool_hook(ranked) -> ranked re-scores the scored pool before series
        order and diversity (the questions pop-up applies its answers here).
        return_pool=True stops there and returns {"pool": ranked, ...}, so
        the pop-up can re-rank the same pool after every answer without
        running SQL again (see `finalize`).

        remember_shown=True pushes down titles Discover showed lately that
        nobody opened (skip_session_id: the session being paged through).
        """
        mood_info = MOOD_MAPPINGS.get(mood_id, MOOD_MAPPINGS[8])

        # Resolve Genres. The free-text query is a first-class filter input, not
        # just a scoring nudge: "relaxing, not too complicated" has to be able to
        # veto a violent film even when the mood's own genres include it.
        chip_genres = genres_override
        genre_spec = resolve_effective_genres(mood_info["genres"], chip_genres, query)
        genres = genre_spec["include"] or None
        excluded_genres = genre_spec["exclude"]
        intent_weight = genre_spec["weight"]
        content_words = genre_spec["content_words"]
        concept_requested = genre_spec.get("concept_requested", False)
        # Premise terms for the SQL shortlist. Only the concrete nouns from
        # THEMES — never the abstract request words ("concept", "interpret"),
        # which appear in no synopsis and would match nothing while diluting
        # the terms that do.
        concept_terms: List[str] = []
        if concept_requested:
            seen_terms: Set[str] = set()
            for theme in THEMES:
                for term in THEMES[theme]["terms"]:
                    key = term.strip().lower()
                    if key and key not in seen_terms:
                        seen_terms.add(key)
                        concept_terms.append(key)

        # Origin-of-cinema requests ("South Asian movies") are places, not
        # genres. Filter on country AND language so TMDB's origin mislabels
        # (Point Break filed as IN) don't leak Hollywood back in.
        origin_spec = parse_origin_intent(query)
        origin_countries = origin_spec["countries"] if origin_spec else None
        origin_languages = origin_spec["languages"] if origin_spec else None
        if origin_spec:
            print(
                f"[Recommender] Origin request '{origin_spec['label']}': "
                f"countries={origin_countries} languages={origin_languages}",
                flush=True,
            )

        if media_type == "tv" and genres:
            expanded = list(genres)
            for g in genres:
                expanded.extend(TV_GENRE_ALIASES.get(g.strip().lower(), []))
            genres = list(dict.fromkeys(expanded))
        if media_type == "tv" and excluded_genres:
            expanded_ex = []
            for g in excluded_genres:
                expanded_ex.append(g)
                expanded_ex.extend(TV_GENRE_ALIASES.get(g.strip().lower(), []))
            excluded_genres = list(dict.fromkeys(expanded_ex))

        # Baseline Minimum Rating Floor
        min_rating = 6.0 if min_rating_override is None else min_rating_override
        if query and any(kw in query.lower() for kw in GUILTY_PLEASURE_KEYWORDS):
            min_rating = 0.0

        # Stage 1: Quality-cut candidate funnel — hard filters + Bayesian
        # audience-weighted ordering computed in SQL (real vote counts), cut
        # to the shortlist so Python scoring never touches the full catalogue.
        #
        # NB: the SQL `exclude_watched` drops anything EITHER profile has
        # watched. In a profile-aware run that would delete every rated film
        # before the scorer could see its tier, making Tier A unreachable — so
        # watched exclusion is handled in Python below, where "seen by both"
        # can be distinguished from "seen by one".
        sql_exclude_watched = exclude_watched and profile not in PROFILE_MIX
        candidates = self.db.get_top_candidates(
            media_type=media_type,
            genres=genres if genres else None,
            exclude_genres=excluded_genres or None,
            origin_countries=origin_countries,
            origin_languages=origin_languages,
            exclude_watched=sql_exclude_watched,
            min_rating=min_rating,
            era=era_preference,
            duration=duration_preference if media_type == "movie" else None,
            concept_terms=concept_terms,
            limit=self.shortlist_size,
            only_ids=only_ids,
        )

        # A concept request must not be decided by the quality cut alone. SQL
        # returns the top `shortlist_size` by Bayesian rating, and a theme-
        # relevant film rated 7.3 loses to 250 better-rated titles that have
        # nothing to do with the request — They Live was filtered out of its
        # own shortlist by exactly this. For concept requests the shortlist is
        # widened so premise matching actually gets a chance to rank them.
        if concept_requested:
            extra = self.db.get_top_candidates(
                media_type=media_type,
                genres=genres if genres else None,
                exclude_genres=excluded_genres or None,
                origin_countries=origin_countries,
                origin_languages=origin_languages,
                exclude_watched=sql_exclude_watched,
                min_rating=min_rating,
                era=era_preference,
                duration=duration_preference if media_type == "movie" else None,
                concept_terms=concept_terms,
                limit=self.shortlist_size * 2,
            )
            seen_ids = {c.get("id") for c in candidates}
            candidates = list(candidates) + [
                c for c in extra if c.get("id") not in seen_ids
            ]

        # Vibe search: the titles whose vibe documents are closest to the
        # typed request join the shortlist, through the same hard filters, so
        # a well-matching 6.9 film isn't lost to 250 better-rated ones.
        vibe = self._vibe_query(query) if self.vibe_ready(query) else None
        if vibe:
            near = self.vibe_index.top(vibe["vector"], k=VIBE_SHORTLIST)
            extra = self.db.get_top_candidates(
                media_type=media_type,
                genres=genres if genres else None,
                exclude_genres=excluded_genres or None,
                origin_countries=origin_countries,
                origin_languages=origin_languages,
                exclude_watched=sql_exclude_watched,
                min_rating=min_rating,
                era=era_preference,
                duration=duration_preference if media_type == "movie" else None,
                limit=VIBE_SHORTLIST,
                only_ids=[i for i, _ in near],
            )
            seen_ids = {c.get("id") for c in candidates}
            candidates = list(candidates) + [c for c in extra if c.get("id") not in seen_ids]

        if not candidates:
            return {"total_results": 0, "page": page, "page_size": page_size, "items": []}

        # Era & Runtime Filters
        if era_preference == "modern":
            candidates = [c for c in candidates if (c.get("release_year") or 0) >= 2015] or candidates
        elif era_preference == "2000s":
            candidates = [c for c in candidates if 2000 <= (c.get("release_year") or 0) < 2015] or candidates
        elif era_preference == "classic":
            candidates = [c for c in candidates if (c.get("release_year") or 0) < 2000] or candidates

        if media_type == "movie" and duration_preference:
            if duration_preference == "short":
                candidates = [c for c in candidates if 0 < (c.get("runtime") or 0) < 105] or candidates
            elif duration_preference == "epic":
                candidates = [c for c in candidates if (c.get("runtime") or 0) > 140] or candidates

        # Profile-aware exclusion, applied in Python so "seen by both" can be told
        # apart from "seen by one".
        #
        # In EVERY profile mode, a film the viewer has already watched must not
        # be recommended back to them. `together` previously excluded only what
        # BOTH had seen, so a film only she had watched surfaced with a "she
        # hasn't seen this yet" style tier C badge — which is wrong: in
        # together mode the point is finding something neither has watched.
        # Tier B ("he loved it, you haven't") is for the RATING queue, which
        # decides what he should watch, not what the pair should watch.
        if exclude_watched and profile in PROFILE_MIX:
            coview_all = self.db.get_item_co_view(
                [c["id"] for c in candidates if c.get("id")], viewer_profile_id=None
            )
            candidates = [
                c for c in candidates
                if len((coview_all.get(c["id"]) or {}).get("seen_by") or []) == 0
            ]
            if not candidates:
                return {"total_results": 0, "page": page, "page_size": page_size, "items": []}

        # Stage 2: Personal Taste & Co-view Scoring
        # Profile-aware path blends the two people's tastes and applies tier
        # boosts; the legacy path is unchanged when no profile signal exists.
        has_profile_signal = any(
            t.get("rating_count", 0) > 0 for t in self.db.get_all_profile_tastes().values()
        )
        if has_profile_signal:
            ranked = self.rank_candidates_profile_aware(
                candidates, profile=profile, query=query,
                content_words=content_words,
                intent_boost_genres={
                    g.strip().lower()
                    for g in (genre_spec.get("boost") or []) if g and g.strip()
                },
                intent_bonus=0.5 * intent_weight,
                concept_requested=concept_requested,
            )
        else:
            taste = self.db.get_user_taste_profile(min_rating=7.0)
            ranked = self.rank_candidates(
                candidates, taste, query=query,
                concept_requested=concept_requested,
            )
            for item in ranked:
                item["co_view_tier"] = "C"
                item["tier_boost"] = 0.0

        if not ranked:
            return {"total_results": 0, "page": page, "page_size": page_size, "items": []}

        if vibe:
            ranked = self._apply_vibe(ranked, vibe)
        if self.taste_runtime is not None:
            ranked = self.taste_runtime.apply(ranked, profile)
        if remember_shown:
            ranked = self._apply_memory(ranked, skip_session_id)
        if pool_hook is not None:
            ranked = pool_hook(ranked)

        vibe_info = {"used": bool(vibe), "like_titles": [t["title"] for t in vibe["like"]] if vibe else []}
        if return_pool:
            return {"pool": ranked, "vibe": vibe_info}

        # Cold-start variety is suppressed when the request named an origin or
        # genre. Those are targeted queries where the user asked for a specific
        # shelf, and shuffling threw away the quality ordering: "bangladeshi
        # movies" surfaced 2-vote films above The Clay Bird and Monpura because
        # the shuffle, not the Bayesian score, decided the page.
        targeted = bool(
            origin_spec
            or genre_spec["include"]
            or genre_spec["exclude"]
            or genre_spec.get("weight", 0) > 0
            or bool(vibe)   # a vibe-ranked list is a targeted answer too
            or pool_hook is not None
        )
        result = self.finalize(ranked, page, page_size, shuffle=variety and not targeted,
                               spread_exempt=set(genre_spec["include"]))
        result["vibe"] = vibe_info
        return result

    def finalize(self, ranked: List[Dict[str, Any]], page: int = 1, page_size: int = 10,
                 shuffle: bool = False, spread_exempt: Optional[Set[str]] = None) -> Dict[str, Any]:
        """Series order, diversity and the page slice for an already-scored pool.

        spread_exempt: genres the request asked for, which the genre spread
        never holds back."""
        if not ranked:
            return {"total_results": 0, "page": page, "page_size": page_size, "items": [], "has_more": False}

        # Stage 2b: Series order. A sequel is swapped for the earliest entry
        # neither of you has seen, and each series appears at most once.
        ranked = apply_series_order(ranked, self.db)

        # Stage 3: Candidate Diversity Pruning (MMR)
        # Process the shortlist through MMR to build the diverse final pool.
        pool_size = min(len(ranked), self.final_pool)
        diverse_candidates = self.apply_mmr_diversity(ranked[:pool_size], top_n=pool_size)

        # Cold-start variety (opt-in): seeded shuffle that rotates every 10
        # minutes — repeat requests surface different picks, but the order is
        # stable within a bucket so pagination stays coherent while browsing.
        if shuffle and len(diverse_candidates) > page_size:
            random.Random(int(time.time() // 600)).shuffle(diverse_candidates)

        # Stage 3b: no shelf (animation, horror, ...) fills a page unless asked.
        diverse_candidates = apply_genre_spread(diverse_candidates, spread_exempt)

        # Stage 4: Pagination Slice
        total_results = len(diverse_candidates)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_items = diverse_candidates[start_idx:end_idx]

        return {
            "total_results": total_results,
            "page": page,
            "page_size": page_size,
            "has_more": end_idx < total_results,
            "items": paginated_items,
        }
