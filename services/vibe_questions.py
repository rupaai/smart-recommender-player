"""
The "guess the vibe" question bank (services/vibe_questions.py)

docs/VIBE_QUESTION_BANK.md as data. Each option's `fx` says what the answer
changes; services/vibe_popup.py applies it to the belief and the pool.

Effect keys (see the bank's notation table):
    set      {dial: target}       target with confidence 0.7
    weak     {dial: target}       target with confidence 0.4
    shift    {dial: ±delta}       move the target, confidence +0.2 (cap 0.9)
    max/min  {dial: value}        hard dial filter (penalty if < 30 films survive)
    feels+ / feels-  [tags]       mood-tag "feels" (also matches good_for and keywords)
    good+    [tags]               "good for" tags
    kw+ / kw- / kwx  [keywords]   TMDB keyword boost / penalty / hard exclude
                                  (kwx also matches the LLM content flags)
    genre+ / genrex [genres]      genre boost / hard exclude
    runtime_max  minutes          hard filter (TV: per-episode runtime)
    runtime_soft minutes          penalty only
    long_bonus   True             +0.1 for runtime ≥ 140
    media        "tv"             hard filter
    tv_bonus     float            boost TV
    year         [lo, hi]         hard filter
    english      True             original language English (animation exempt)
    foreign_bonus float           boost non-English
    foreign_quality 7.5           non-English only when rated ≥ 7.5
    quality_min  7.5              hard rating floor
    slow_quality 7.5              rating floor for films with pace < 0.4
    popular      True             vote_count in the pool's top 10 %
    obscure      True             boost the less-voted half
    seen_only    True             rewatch (the pool is unseen, so this option is usually dead)
    profile      me|together      whose taste counts (rebuilds the pool)
    none         True             answered, no change

Questions that need data the app doesn't have yet are left out and noted:
C4 (country options generated from the pool) and H4 (recent-cluster
fatigue) come with Phase 5, together with cluster affinity, which C1's
"usual place, new dish" and "tiny place" options also lean on.
"""

from __future__ import annotations

from typing import Any, Dict, List

ABOUT_YOU = {"A3", "D1", "A1", "F1", "F4"}
FUN = {"A3": 1.2, "D1": 1.2, "F1": 1.2, "F4": 1.2, "B1": 1.2, "E2": 1.2}
AVOID = {"H1", "H2", "H3"}
CLOSERS = {"I1", "I2"}
ALTS = [{"B1", "B5"}, {"F1", "F2"}, {"G1", "G2"}, {"H1", "H2", "H3"}, {"A1", "D2"}]


def _o(oid: str, emoji: str, label: str, fx: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": oid, "emoji": emoji, "label": label, "fx": fx}


BANK: List[Dict[str, Any]] = [
    # --- A. How much you've got left -------------------------------------
    {"id": "A1", "group": "A", "text": "Your brain right now is a…", "options": [
        _o("charged", "🔋", "Fully charged phone",
           {"set": {"attention": 0.8}, "feels+": ["mind-bending", "cerebral"],
            "kw+": ["plot twist", "nonlinear timeline", "conspiracy"]}),
        _o("tabs", "🗂️", "Browser with 12 tabs open", {"set": {"attention": 0.5}}),
        _o("low", "🪫", "10 % battery",
           {"set": {"attention": 0.25}, "shift": {"energy": -0.1}, "kw-": ["nonlinear timeline"],
            "runtime_soft": 115}),
        _o("sleep", "💤", "Already in sleep mode",
           {"set": {"attention": 0.1, "energy": 0.2}, "good+": ["background", "dozing off"], "tv_bonus": 0.2}),
    ]},
    {"id": "A2", "group": "A", "text": "Pick a seat", "options": [
        _o("hammock", "🌴", "A hammock", {"set": {"energy": 0.15}, "shift": {"pace": -0.15}, "feels+": ["quiet", "mellow"]}),
        _o("sofa", "🛋️", "Big sofa with a blanket", {"set": {"energy": 0.35}, "feels+": ["cozy", "warm"]}),
        _o("stool", "🪑", "Kitchen stool, leaning forward",
           {"set": {"energy": 0.6}, "shift": {"attention": 0.1}, "feels+": ["gripping", "tense"]}),
        _o("coaster", "🎢", "A rollercoaster seat",
           {"set": {"energy": 0.9, "pace": 0.85}, "feels+": ["thrilling", "adrenaline", "chaotic"]}),
    ]},
    {"id": "A3", "group": "A", "text": "How did today end?", "options": [
        _o("wrung", "🫠", "Wrung out, too many people",
           {"set": {"energy": 0.25}, "weak": {"attention": 0.35}, "feels+": ["quiet", "solitary", "dreamy"],
            "kw-": ["ensemble cast", "party"]}),
        _o("bored", "🥱", "Bored, nothing happened",
           {"set": {"energy": 0.75}, "obscure": True, "feels+": ["thrilling", "chaotic"]}),
        _o("stressed", "🌀", "Stressed, head still buzzing",
           {"max": {"darkness": 0.4, "tension": 0.4}, "set": {"weight": 0.3}, "feels+": ["soothing", "gentle"],
            "good+": ["winding down"]}),
        _o("great", "✨", "Great day, keep it going",
           {"set": {"weight": 0.2, "energy": 0.65}, "feels+": ["giddy", "joyful"]}),
    ]},
    {"id": "A4", "group": "A", "text": "When does tonight end?", "options": [
        _o("hour", "⏰", "Bed is calling in an hour", {"runtime_max": 95, "shift": {"attention": -0.1}}),
        _o("normal", "🌙", "Normal bedtime", {"runtime_max": 125}),
        _o("noalarm", "🕯️", "No alarm tomorrow", {"long_bonus": True}),
        _o("season", "📺", "Tonight is a season, not a film", {"media": "tv", "good+": ["binge"]}),
    ]},
    # --- B. What your heart needs ------------------------------------------
    {"id": "B1", "group": "B", "text": "Pick a drink", "options": [
        _o("cocoa", "☕", "Hot chocolate", {"set": {"weight": 0.2}, "max": {"darkness": 0.35}, "feels+": ["warm", "comforting"]}),
        _o("espresso", "⚡", "A strong espresso",
           {"shift": {"energy": 0.15}, "set": {"weight": 0.35}, "feels+": ["witty", "stylish"]}),
        _o("wine", "🍷", "Red wine, slowly",
           {"set": {"weight": 0.7}, "shift": {"pace": -0.1}, "feels+": ["bittersweet", "melancholy", "tender"]}),
        _o("tequila", "🥃", "A tequila shot",
           {"set": {"energy": 0.8, "weight": 0.1}, "feels+": ["giddy", "chaotic", "silly"]}),
    ]},
    {"id": "B2", "group": "B", "text": "Pick the last line of tonight's story", "options": [
        _o("happy", "🌈", "“…and they all lived happily.”", {"min": {"ending_valence": 0.7}, "max": {"weight": 0.45}}),
        _o("worth", "🩹", "“…it hurt, but it was worth it.”",
           {"set": {"ending_valence": 0.65, "weight": 0.7}, "feels+": ["cathartic", "hopeful"]}),
        _o("never", "🌫️", "“…and nothing was ever the same.”",
           {"set": {"ending_valence": 0.2, "weight": 0.85}, "feels+": ["haunting", "devastating"]}),
        _o("what", "🌀", "“…wait, what just happened?”",
           {"shift": {"attention": 0.2}, "feels+": ["mind-bending"], "kw+": ["twist ending", "unreliable narrator"]}),
    ]},
    {"id": "B3", "group": "B", "text": "Put on a soundtrack for right now", "options": [
        _o("lofi", "🎧", "Lo-fi beats", {"set": {"energy": 0.25}, "weak": {"attention": 0.35}, "feels+": ["mellow", "dreamy", "quiet"]}),
        _o("orchestra", "🎻", "A huge orchestra swelling", {"set": {"spectacle": 0.85}, "feels+": ["epic-awe", "epic"]}),
        _o("synth", "🌃", "Synth-pop at night", {"feels+": ["neon-nocturnal", "stylish"], "kw+": ["night", "los angeles", "1980s"]}),
        _o("folk", "🪕", "Folk guitar by a fire", {"shift": {"pace": -0.15}, "feels+": ["earnest", "wistful"], "kw+": ["countryside", "road trip"]}),
        _o("brass", "🎺", "A full brass band", {"shift": {"energy": 0.2}, "feels+": ["joyful", "theatrical"], "kw+": ["musical"]}),
    ]},
    {"id": "B4", "group": "B", "text": "Who do you want to be for two hours?", "options": [
        _o("mirror", "🪞", "Someone with my exact problems",
           {"max": {"fantasy": 0.2}, "year": [2000, 2100], "kw-": ["period piece"], "feels+": ["relatable"]}),
        _o("brave", "🦁", "Someone braver than me",
           {"min": {"ending_valence": 0.6}, "feels+": ["triumphant", "underdog"], "kw+": ["underdog", "sports", "based on true story"]}),
        _o("century", "🕰️", "Someone from another century",
           {"kw+": ["period piece", "18th century", "19th century"], "feels+": ["lush"]}),
        _o("other", "🐉", "Someone not quite human, somewhere else",
           {"min": {"fantasy": 0.7}, "genre+": ["Fantasy", "Science Fiction", "Animation", "Sci-Fi & Fantasy"]}),
    ]},
    {"id": "B5", "group": "B", "text": "Pick a swimming spot", "options": [
        _o("paddling", "🛟", "A paddling pool", {"set": {"weight": 0.1}, "max": {"darkness": 0.3}}),
        _o("lake", "🏞️", "A calm lake", {"set": {"weight": 0.4}}),
        _o("sea", "🌊", "The open sea", {"set": {"weight": 0.7}}),
        _o("deep", "🕳️", "A deep dive, no light", {"set": {"weight": 0.95}}),
    ]},
    # --- C. Comfort vs novelty ------------------------------------------------
    {"id": "C1", "group": "C", "text": "Pick a restaurant", "options": [
        _o("usual", "🍝", "My usual, same order", {"seen_only": True, "feels+": ["comforting"]}),
        _o("newdish", "🍜", "My usual place, a new dish", {"none": True}),
        _o("friend", "⭐", "The place a friend swears by", {"popular": True, "quality_min": 7.5}),
        _o("tiny", "🏮", "A tiny place with no English menu", {"foreign_bonus": 0.3, "obscure": True}),
    ]},
    {"id": "C2", "group": "C", "text": "Set the time machine", "options": [
        _o("bw", "🎞️", "Black-and-white glamour", {"year": [1900, 1965]}),
        _o("vhs", "📼", "VHS days", {"year": [1975, 1999]}),
        _o("dvd", "💿", "The DVD shelf", {"year": [2000, 2012]}),
        _o("fresh", "🆕", "Fresh out", {"year": [2018, 2100]}),
        _o("any", "🤷", "Don't mind", {"none": True}),
    ]},
    {"id": "C3", "group": "C", "text": "Reading subtitles tonight feels like…", "options": [
        _o("bonus", "📖", "A bonus, I'm all in", {"foreign_bonus": 0.25, "shift": {"attention": 0.1}}),
        _o("fine", "👍", "Fine, if it's worth it", {"foreign_quality": 7.5}),
        _o("homework", "📚", "Homework", {"english": True}),
    ]},
    # --- D. Who's there --------------------------------------------------------
    {"id": "D1", "group": "D", "text": "Who's on the sofa?", "options": [
        _o("me", "🧍", "Just me", {"profile": "me", "shift": {"weight": 0.1, "attention": 0.1}}),
        _o("cuddled", "💑", "Me and him, cuddled up",
           {"profile": "together", "good+": ["date night"], "feels+": ["romantic", "tender"]}),
        _o("phone", "📱", "Me and him, but he'll be on his phone",
           {"profile": "together", "max": {"attention": 0.4}, "good+": ["half-watching"]}),
        _o("friends", "🍕", "Friends over",
           {"profile": "together", "set": {"energy": 0.65}, "max": {"weight": 0.4},
            "good+": ["friends over", "a crowd-pleaser"]}),
    ]},
    {"id": "D2", "group": "D", "text": "What's happening around the screen?", "options": [
        _o("dark", "🌑", "Nothing. Lights off.", {"set": {"attention": 0.75}, "feels+": ["immersive"], "shift": {"spectacle": 0.2}}),
        _o("dinner", "🍛", "Dinner on our laps",
           {"set": {"attention": 0.45}, "good+": ["eating dinner"], "kw-": ["gore", "vomit", "body horror"]}),
        _o("chores", "🧺", "Cooking or folding laundry",
           {"set": {"attention": 0.15}, "tv_bonus": 0.3, "good+": ["background while cooking"]}),
        _o("talking", "💬", "We'll be talking half the time", {"set": {"attention": 0.3}, "feels+": ["silly", "quotable"]}),
    ]},
    {"id": "D3", "group": "D", "text": "How should you two feel walking out of the room?", "options": [
        _o("hands", "🤝", "Holding hands",
           {"good+": ["date night"], "feels+": ["romantic", "tender"], "min": {"ending_valence": 0.6}}),
        _o("arguing", "🗣️", "Arguing about the ending",
           {"shift": {"attention": 0.15}, "good+": ["talking about after"], "kw+": ["open ending", "twist ending"]}),
        _o("quoting", "😂", "Quoting it all week", {"min": {"humour": 0.7}, "feels+": ["silly", "witty"]}),
        _o("trip", "✈️", "Planning a trip", {"feels+": ["sun-soaked"], "kw+": ["travel", "italy", "road trip"]}),
    ]},
    # --- E. Tolerance for tension ------------------------------------------
    {"id": "E1", "group": "E", "text": "Pick tomorrow's front-page headline", "options": [
        _o("dog", "🐶", "“Local dog elected mayor”", {"max": {"darkness": 0.15}}),
        _o("manor", "🏰", "“Mystery at the manor”",
           {"set": {"darkness": 0.35}, "max": {"darkness": 0.5}, "kw+": ["murder mystery", "whodunit"]}),
        _o("heist", "💰", "“Heist of the century”",
           {"set": {"darkness": 0.5}, "shift": {"energy": 0.15}, "kw+": ["heist", "con artist"]}),
        _o("case", "🗂️", "“Case reopened after 20 years”",
           {"min": {"darkness": 0.65}, "shift": {"attention": 0.1}, "kw+": ["serial killer", "investigation"]}),
    ]},
    {"id": "E2", "group": "E", "text": "A door is open a crack in a dark hallway. You…", "options": [
        _o("close", "🚪", "Close it and go to bed",
           {"max": {"tension": 0.3}, "kwx": ["jump scare"], "genrex": ["Horror"], "feels-": ["eerie", "dread"]}),
        _o("peek", "🔦", "Peek in with the phone torch", {"set": {"tension": 0.5}, "feels+": ["eerie"]}),
        _o("walk", "🚶", "Walk in", {"min": {"tension": 0.7}, "feels+": ["dread", "eerie"], "genre+": ["Horror", "Thriller"]}),
        _o("behind", "😈", "I'm the one behind the door",
           {"min": {"darkness": 0.75}, "feels+": ["unsettling"], "kw+": ["psychopath", "villain"]}),
    ]},
    {"id": "E3", "group": "E", "text": "Awkward silences are…", "options": [
        _o("fave", "😬", "My favourite thing", {"min": {"cringe": 0.6}, "kw+": ["mockumentary", "awkward"]}),
        _o("doses", "🙂", "Fine in small doses", {"max": {"cringe": 0.6}}),
        _o("hide", "🫣", "Unbearable, I hide behind a cushion",
           {"max": {"cringe": 0.25}, "kw-": ["mockumentary", "humiliation"]}),
    ]},
    {"id": "E4", "group": "E", "text": "Tonight's red is…", "options": [
        _o("balloon", "🎈", "A balloon", {"max": {"gore": 0.1}}),
        _o("wine", "🍷", "Wine spilled on the carpet", {"max": {"gore": 0.3}}),
        _o("steak", "🥩", "A medium-rare steak", {"max": {"gore": 0.65}}),
        _o("blood", "🩸", "Just say it", {"none": True}),
    ]},
    # --- F. Aesthetic and sensory mood -------------------------------------
    {"id": "F1", "group": "F", "text": "Pick a window to look out of", "options": [
        _o("rain", "🌧️", "Rain on a city street", {"feels+": ["melancholy", "romantic"], "kw+": ["rain", "city"]}),
        _o("beach", "🌅", "A beach at golden hour", {"feels+": ["sun-soaked"], "kw+": ["beach", "summer"]}),
        _o("neon", "🌆", "A neon alley", {"feels+": ["neon-nocturnal", "stylish"], "kw+": ["cyberpunk", "neo-noir", "night"]}),
        _o("cabin", "❄️", "A snowy cabin", {"feels+": ["cozy", "wintry"], "kw+": ["christmas", "winter", "snow"]}),
        _o("forest", "🌲", "A forest at dawn", {"feels+": ["dreamy"], "kw+": ["forest", "nature"], "shift": {"fantasy": 0.2}}),
    ]},
    {"id": "F2", "group": "F", "text": "Pick a colour palette", "options": [
        _o("pastel", "🩷", "Pastel pinks and mint", {"palette": "pastel", "feels+": ["playful"]}),
        _o("grain", "🟫", "Warm film grain, browns and orange", {"palette": "warm-grain", "feels+": ["nostalgic"], "kw+": ["1970s"]}),
        _o("cold", "🩶", "Cold blues and greys", {"palette": "cold", "feels+": ["cerebral", "tense"]}),
        _o("saturated", "❤️", "Saturated reds and greens", {"palette": "saturated", "feels+": ["lush"]}),
        _o("bw", "⬛", "Black and white", {"palette": "bw"}),
    ]},
    {"id": "F3", "group": "F", "text": "Where would you rather be stuck for a weekend?", "options": [
        _o("room", "🛋️", "One room, great company",
           {"min": {"talkiness": 0.7}, "max": {"spectacle": 0.3}, "kw+": ["single location"]}),
        _o("train", "🚂", "A train across a continent", {"kw+": ["journey", "train", "ensemble cast"]}),
        _o("desert", "🏜️", "A huge empty desert", {"min": {"spectacle": 0.75}, "shift": {"pace": -0.1}, "kw+": ["desert", "epic"]}),
        _o("space", "🛰️", "A space station", {"kw+": ["space", "isolation"], "shift": {"fantasy": 0.2}}),
    ]},
    {"id": "F4", "group": "F", "text": "Pick a texture", "options": [
        _o("wool", "🧶", "A wool blanket", {"feels+": ["cozy", "warm", "gentle"], "max": {"darkness": 0.35}}),
        _o("silk", "🪡", "Silk", {"feels+": ["stylish", "lush"], "shift": {"pace": -0.1}}),
        _o("gravel", "🪨", "Gravel", {"feels+": ["gritty", "raw"], "max": {"fantasy": 0.3}}),
        _o("bubble", "🫧", "Bubble wrap", {"feels+": ["playful", "silly"], "min": {"humour": 0.6}, "shift": {"energy": 0.15}}),
    ]},
    {"id": "F5", "group": "F", "text": "Pick your dinner guest", "options": [
        _o("talker", "🗯️", "The one who never stops talking, brilliantly", {"min": {"talkiness": 0.75}, "feels+": ["witty"]}),
        _o("storyteller", "📜", "The great storyteller", {"set": {"talkiness": 0.5}, "kw+": ["narration"]}),
        _o("quiet", "👁️", "The quiet one who notices everything", {"max": {"talkiness": 0.3}, "feels+": ["contemplative"]}),
        _o("fireworks", "🎆", "The one who brought fireworks",
           {"min": {"spectacle": 0.75}, "shift": {"energy": 0.2}, "max": {"talkiness": 0.4}}),
    ]},
    # --- G. Pace and patience ------------------------------------------------
    {"id": "G1", "group": "G", "text": "Pick a way to cross the river", "options": [
        _o("row", "🚣", "A rowing boat, no hurry", {"set": {"pace": 0.2}, "feels+": ["contemplative"]}),
        _o("ferry", "⛴️", "A ferry, with a coffee", {"set": {"pace": 0.45}}),
        _o("jetski", "🚤", "A jet ski", {"set": {"pace": 0.8}, "shift": {"energy": 0.1}}),
        _o("vine", "🐒", "Swinging across on a vine",
           {"set": {"pace": 0.9}, "shift": {"strangeness": 0.2}, "feels+": ["chaotic"]}),
    ]},
    {"id": "G2", "group": "G", "text": "If nothing happens for 20 minutes, you…", "options": [
        _o("relax", "🧘", "Relax into it", {"weak": {"pace": 0.25}}),
        _o("trust", "🤞", "Trust it, if it's going somewhere", {"min": {"pace": 0.25}, "slow_quality": 7.5}),
        _o("phone", "📱", "Check my phone", {"min": {"pace": 0.5}}),
        _o("gone", "🏃", "Already gone", {"min": {"pace": 0.75}, "runtime_max": 110}),
    ]},
    # --- H. What would ruin tonight -----------------------------------------
    {"id": "H1", "group": "H", "text": "What would ruin tonight?", "options": [
        _o("sob", "😭", "Someone dies and I sob",
           {"max": {"weight": 0.6}, "kwx": ["death of loved one", "terminal illness"], "feels-": ["devastating"]}),
        _o("jump", "👻", "A jump scare", {"kwx": ["jump scare"], "max": {"tension": 0.5}, "genrex": ["Horror"]}),
        _o("lost", "🤯", "Feeling lost or stupid", {"max": {"attention": 0.55}, "kwx": ["nonlinear timeline", "surreal"]}),
        _o("cruel", "😣", "People being cruel to each other",
           {"max": {"darkness": 0.5, "cringe": 0.4}, "kwx": ["abuse", "bullying", "humiliation"]}),
        _o("bored", "😴", "Being bored", {"min": {"pace": 0.45, "energy": 0.35}}),
    ]},
    {"id": "H2", "group": "H", "text": "Which plot twist would make you throw the remote?", "options": [
        _o("dog", "🐕", "Something happens to the dog", {"kwx": ["dog", "animal death", "animal cruelty", "animal harm"]}),
        _o("child", "🧒", "A child in danger", {"kwx": ["child in peril", "kidnapping", "child abuse"]}),
        _o("hospital", "🏥", "Hospitals and diagnoses", {"kwx": ["cancer", "terminal illness", "hospital"]}),
        _o("cheat", "💔", "Someone cheats", {"kwx": ["infidelity", "adultery"]}),
        _o("fine", "🆗", "None of those, I'm fine", {"none": True}),
    ]},
    {"id": "H3", "group": "H", "text": "Last time movie night went wrong, it was…", "options": [
        _o("long", "⌛", "Too long", {"runtime_max": 115}),
        _o("slow", "🐢", "Too slow", {"min": {"pace": 0.45}}),
        _o("sad", "🌧️", "Too sad", {"max": {"weight": 0.5}}),
        _o("weird", "🫥", "Too weird", {"max": {"strangeness": 0.45}, "kw-": ["surreal", "absurdism"]}),
        _o("predictable", "🥱", "Too predictable", {"min": {"strangeness": 0.3}, "quality_min": 7.0}),
    ]},
    # --- I. Closers (options generated from the pool) -----------------------
    {"id": "I1", "group": "I", "text": "Which of these feels closest?", "options": []},
    {"id": "I2", "group": "I", "text": "This one or that one?", "options": []},
]

BY_ID: Dict[str, Dict[str, Any]] = {q["id"]: q for q in BANK}
