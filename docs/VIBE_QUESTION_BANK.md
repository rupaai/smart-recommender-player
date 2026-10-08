# Vibe question bank: "guess the vibe" pop-up

Replaces the placeholder bank in [RECOMMENDER_V2_DESIGN.md §3](RECOMMENDER_V2_DESIGN.md#3-questions-pop-up-requests-2-and-4). Written 2026-10-08.

The pop-up works like Akinator, but the thing it is guessing is a feeling, not a film. Every question asks about **you and your evening** (your state, your surroundings, a sensation, an image). Every answer still turns into something the ranker can measure: a dial target, a filter, or a boost/penalty on mood tags, keywords or clusters.

Contents:
1. [How to read the bank](#how-to-read-the-bank)
2. [The bank (33 questions)](#1-the-bank)
3. [Choosing the next question](#2-choosing-the-next-question)
4. [Three sample sessions](#3-three-sample-sessions)
5. [What the catalogue needs to store](#4-what-the-catalogue-needs-to-store)
6. [The short set (10 questions)](#5-the-short-set)

---

## How to read the bank

### The belief the pop-up builds

While you answer, the system keeps a running **vibe belief**. For each dial it holds a target value and a confidence (0 = no idea, 1 = certain):

```
belief = {
  energy:    {target: 0.5, conf: 0.0},
  attention: {target: 0.5, conf: 0.0},
  weight:    {target: 0.5, conf: 0.0},
  darkness:  {target: 0.5, conf: 0.0},
  pace:      {target: 0.5, conf: 0.0},
  ...proposed dials (§4)...
  filters:   [...],         # hard rules, SQL
  tags_plus: {...}, tags_minus: {...},  # mood tags / keywords, with weights
  query_vec_nudges: [...]    # cluster or film vectors blended into the query
}
```

`facet_fit` in the final score (design doc §2.6) becomes `Σ conf · (1 − |film_dial − target|)` over the dials, so a dial nobody asked about has no effect.

### Notation used in the "Changes" column

| Notation | Meaning |
|---|---|
| `energy → 0.2` | Set the dial's target to 0.2 with **confidence 0.7** (one clear answer). |
| `energy → 0.2 (weak)` | Same, confidence 0.4. Used when an answer only hints at a dial. |
| `energy ↑0.15` / `↓0.15` | Move the current target by that much. Confidence +0.2 (cap 0.9). |
| `darkness ≤ 0.35` | **Hard filter**. Films above it are removed. If fewer than 30 films survive, the filter becomes a strong penalty instead (`−1.0 · overshoot`). |
| `feels+ warm, cozy` | Boost films with that "feels like" tag: +0.15 per matching tag, capped at +0.4. |
| `feels− eerie` | Penalty −0.3 per matching tag. |
| `good_for+ date night` | Same as `feels+`, for "good for" tags. |
| `kw+ heist` / `kw− gore` | TMDB keyword boost (+0.1) / penalty (−0.3). Works today, before mood tags exist. |
| `kw✕ animal death` | Hard exclude on a keyword or content flag. |
| `filter: runtime ≤ 100` | SQL filter (runtime, year, language, country, media type, seen/unseen). |
| `cluster+ <name>` | Blend that cluster's centroid into the query vector (weight 0.3). |
| `profile = together` | Sets whose taste counts (existing `PROFILE_MIX`). |
| `tension⁺` | A **proposed** dial or tag from §4. Each has a fallback that works on today's data (listed in §4), so the question still works before the catalogue is extended. |

"e.g." films are examples that land squarely in that option, picked so the options visibly pull apart. For the avoid questions (group H), the examples are films that option **removes**.

---

## 1. The bank

33 questions in 9 groups. Each group is one thing the pop-up is really trying to find out. Within a group, questions marked **alt** cover the same dial and are never both asked in one session.

| Group | What it finds out | Main dials | Questions |
|---|---|---|---|
| A | How much you've got left (tiredness, bandwidth, time) | attention, energy, runtime | A1–A4 |
| B | What your heart needs | weight, ending valence⁺, feels | B1–B5 |
| C | Comfort vs novelty | seen/unseen, cluster affinity, era, language, country | C1–C4 |
| D | Who's there, what's happening around the screen | profile, attention, good_for | D1–D3 |
| E | Tolerance for tension | darkness, tension⁺, gore⁺, cringe⁺ | E1–E4 |
| F | Aesthetic and sensory mood | feels, setting⁺, palette⁺, talkiness⁺, spectacle⁺ | F1–F5 |
| G | Pace and patience | pace | G1–G2 |
| H | What would ruin tonight (avoid) | content flags⁺, caps on any dial | H1–H4 |
| I | Closers built from the pool itself | query vector | I1–I2 |

---

### A. How much you've got left

#### A1 · "Your brain right now is a…"
**Finds out:** attention (and a little energy). **Ask when:** attention conf < 0.5. A strong opener: it splits almost any pool.

| Option | Changes | e.g. |
|---|---|---|
| 🔋 Fully charged phone | `attention → 0.8` · `feels+ mind-bending, cerebral` · `kw+ plot twist, nonlinear timeline, conspiracy` | *Memento*, *Tinker Tailor Soldier Spy* |
| 🗂️ Browser with 12 tabs open | `attention → 0.5` | *Knives Out*, *The Martian* |
| 🪫 10 % battery | `attention → 0.25` · `energy ↓0.1` · `kw− nonlinear timeline` · `filter: runtime ≤ 115` (soft) | *Paddington 2*, *Chef* |
| 💤 Already in sleep mode | `attention → 0.1` · `energy → 0.2` · `good_for+ background, dozing off` · prefer TV episodes or rewatches (+0.2) | *The Great British Bake Off*, *Brooklyn Nine-Nine* |

#### A2 · "Pick a seat"
**Finds out:** energy. **Ask when:** energy conf < 0.5 and A3 hasn't already set it.

| Option | Changes | e.g. |
|---|---|---|
| 🌴 A hammock | `energy → 0.15` · `pace ↓0.15` · `feels+ quiet, mellow` | *Paterson*, *Perfect Days* |
| 🛋️ Big sofa with a blanket | `energy → 0.35` · `feels+ cozy, warm` | *About Time*, *You've Got Mail* |
| 🪑 Kitchen stool, leaning forward | `energy → 0.6` · `attention ↑0.1` · `feels+ gripping` | *Spotlight*, *Moneyball* |
| 🎢 A rollercoaster seat | `energy → 0.9` · `pace → 0.85` · `feels+ thrilling, adrenaline` | *Mad Max: Fury Road*, *John Wick* |

#### A3 · "How did today end?"
**Finds out:** *why* you're tired, which says what will help: energy, weight and what to avoid at once. **Ask when:** first question of the session (it's about you, not a film). Skip if the text already describes the day ("after a long shift").

| Option | Changes | e.g. |
|---|---|---|
| 🫠 Wrung out, too many people | `energy → 0.25` · `attention → 0.35 (weak)` · `feels+ quiet, solitary, dreamy` · `kw− ensemble cast, party` | *Lost in Translation*, *Columbus* |
| 🥱 Bored, nothing happened | `energy → 0.75` · novelty +0.2 (unseen, low cluster affinity allowed) · `feels+ thrilling, chaotic` | *Baby Driver*, *Everything Everywhere All at Once* |
| 🌀 Stressed, head still buzzing | `darkness ≤ 0.4` · `tension⁺ ≤ 0.4` · `weight → 0.3` · `feels+ soothing, gentle` · `good_for+ winding down` | *My Neighbor Totoro*, *Julie & Julia* |
| ✨ Great day, keep it going | `weight → 0.2` · `energy → 0.65` · `feels+ giddy, joyful` | *Mamma Mia!*, *Sing Street* |

#### A4 · "When does tonight end?"
**Finds out:** runtime and movie vs TV. **Ask when:** runtime and media type are both unknown. More useful on weekday evenings after 21:30; low priority on Friday/Saturday.

| Option | Changes | e.g. |
|---|---|---|
| ⏰ Bed is calling in an hour | `filter: runtime ≤ 95` or TV with episode ≤ 45 min · `attention ↓0.1` | *Palm Springs*, *Fleabag* |
| 🌙 Normal bedtime | `filter: runtime ≤ 125` | *The Nice Guys*, *Arrival* |
| 🕯️ No alarm tomorrow | no runtime limit · +0.1 for runtime ≥ 140 (long films finally get a chance) | *The Lord of the Rings: The Fellowship of the Ring*, *Oppenheimer* |
| 📺 Tonight is a season, not a film | `filter: media = tv` · `good_for+ binge` · prefer serialized | *Severance*, *The Bear* |

---

### B. What your heart needs

#### B1 · "Pick a drink"
**Finds out:** emotional weight plus a feel. **Ask when:** weight conf < 0.5. **alt** B5.

| Option | Changes | e.g. |
|---|---|---|
| ☕ Hot chocolate | `weight → 0.2` · `darkness ≤ 0.35` · `feels+ warm, comforting` | *The Holiday*, *Ratatouille* |
| ⚡ A strong espresso | `energy ↑0.15` · `weight → 0.35` · `feels+ witty, sharp, stylish` | *The Grand Budapest Hotel*, *The Favourite* |
| 🍷 Red wine, slowly | `weight → 0.7` · `pace ↓0.1` · `feels+ bittersweet, melancholy, tender` | *Past Lives*, *Before Midnight* |
| 🥃 A tequila shot | `energy → 0.8` · `weight → 0.1` · `feels+ giddy, chaotic, silly` | *Superbad*, *The Hangover* |

#### B2 · "Pick the last line of tonight's story"
**Finds out:** how it should end (ending valence⁺) and whether a twist is welcome. **Ask when:** weight is known but valence isn't, usually after B1/B5. Skip if weight target ≤ 0.25 (light films nearly all end happily, so it won't split them).

| Option | Changes | e.g. |
|---|---|---|
| 🌈 "…and they all lived happily." | `ending_valence⁺ ≥ 0.7` · `weight ≤ 0.45` | *Crazy Rich Asians*, *The Princess Bride* |
| 🩹 "…it hurt, but it was worth it." | `ending_valence⁺ → 0.65` · `weight → 0.7` · `feels+ cathartic, hopeful` | *Coco*, *Good Will Hunting* |
| 🌫️ "…and nothing was ever the same." | `ending_valence⁺ → 0.2` · `weight → 0.85` · `feels+ haunting, devastating` | *Aftersun*, *Incendies* |
| 🌀 "…wait, what just happened?" | `attention ↑0.2` · `feels+ mind-bending` · `kw+ twist ending, unreliable narrator` | *Shutter Island*, *The Prestige* |

#### B3 · "Put on a soundtrack for right now"
**Finds out:** the overall feel (a shortcut into the mood-tag space). **Ask when:** fewer than 2 `feels+` tags are set by question 3 or later.

| Option | Changes | e.g. |
|---|---|---|
| 🎧 Lo-fi beats | `energy → 0.25` · `attention → 0.35 (weak)` · `feels+ mellow, dreamy, quiet` | *Her*, *Frances Ha* |
| 🎻 A huge orchestra swelling | `spectacle⁺ → 0.85` · `feels+ epic, awe` · `filter: runtime` unchanged | *Interstellar*, *Dune* |
| 🌃 Synth-pop at night | `feels+ neon, nocturnal, stylish` · `kw+ night, los angeles, 1980s` | *Drive*, *Tron: Legacy* |
| 🪕 Folk guitar by a fire | `pace ↓0.15` · `feels+ earnest, wistful` · `setting⁺ + countryside, road` | *Nomadland*, *Into the Wild* |
| 🎺 A full brass band | `energy ↑0.2` · `feels+ joyful, theatrical` · `kw+ musical` | *The Greatest Showman*, *La La Land* |

#### B4 · "Who do you want to be for two hours?"
**Finds out:** escape vs mirror (realism⁺ ↔ fantasy). **Ask when:** fantasy⁺ unknown and the pool has both realist dramas and fantasy/sci-fi/animation (each ≥ 20 %).

| Option | Changes | e.g. |
|---|---|---|
| 🪞 Someone with my exact problems | `fantasy⁺ ≤ 0.2` · contemporary setting (year ≥ 2000, `kw− period piece`) · `feels+ relatable` | *Lady Bird*, *The Worst Person in the World* |
| 🦁 Someone braver than me | `ending_valence⁺ ≥ 0.6` · `feels+ triumphant, underdog` · `kw+ underdog, sports, true story` | *Rocky*, *Erin Brockovich* |
| 🕰️ Someone from another century | `kw+ period piece, 18th century, 19th century` · `feels+ lush` | *Pride & Prejudice* (2005), *Amadeus* |
| 🐉 Someone not quite human, somewhere else | `fantasy⁺ ≥ 0.7` · genres Fantasy / Sci-Fi / Animation +0.2 | *Spirited Away*, *Avatar* |

#### B5 · "Pick a swimming spot"
**Finds out:** emotional weight, as a pure 4-step scale. **Ask when:** weight conf < 0.5 and B1 was not asked. **alt** B1 (use B5 when the energy dial is already set, since B1 also moves energy).

| Option | Changes | e.g. |
|---|---|---|
| 🛟 A paddling pool | `weight → 0.1` · `darkness ≤ 0.3` | *Legally Blonde*, *School of Rock* |
| 🏞️ A calm lake | `weight → 0.4` | *The Intouchables*, *Little Miss Sunshine* |
| 🌊 The open sea | `weight → 0.7` | *Life of Pi*, *Moonlight* |
| 🕳️ A deep dive, no light | `weight → 0.95` · `darkness` limit lifted | *Requiem for a Dream*, *Grave of the Fireflies* |

---

### C. Comfort vs novelty

#### C1 · "Pick a restaurant"
**Finds out:** rewatch vs new, and how far from your taste to wander. **Ask when:** always worth asking once, by question 4. Skip if the text says "new", "haven't seen" or "rewatch".

| Option | Changes | e.g. |
|---|---|---|
| 🍝 My usual, same order | `filter: seen by viewer, rating ≥ 8` · `feels+ comforting` · ignore `include_watched=false` | *Notting Hill*, *The Devil Wears Prada* (if you rated them high) |
| 🍜 My usual place, a new dish | `filter: unseen` · `cluster_affinity ≥ 0.4` (your favourite clusters only) | *Game Night*, *Hunt for the Wilderpeople* |
| ⭐ The place a friend swears by | `filter: unseen` · vote_count in top 10 % · quality ≥ 7.5 | *Parasite*, *Top Gun: Maverick* |
| 🏮 A tiny place with no English menu | `filter: unseen` · `cluster_affinity ≤ 0.1` (unexplored clusters) · non-English +0.2 · vote_count bottom 50 % allowed | *Shoplifters*, *Burning* |

#### C2 · "Set the time machine"
**Finds out:** era. **Ask when:** era unknown **and** the pool spans ≥ 3 of these bands with ≥ 10 % each. Otherwise it won't change anything.

| Option | Changes | e.g. |
|---|---|---|
| 🎞️ Black-and-white glamour | `filter: year ≤ 1965` | *Some Like It Hot*, *Roman Holiday* |
| 📼 VHS days | `filter: 1975 ≤ year ≤ 1999` | *Back to the Future*, *Groundhog Day* |
| 💿 The DVD shelf | `filter: 2000 ≤ year ≤ 2012` | *Ocean's Eleven*, *Amélie* |
| 🆕 Fresh out | `filter: year ≥ 2018` | *Anatomy of a Fall*, *The Holdovers* |
| 🤷 Don't mind | no change (counts as "answered") | – |

#### C3 · "Reading tonight feels like…"
**Finds out:** subtitles. **Ask when:** ≥ 25 % of the pool is non-English **and** attention target ≥ 0.3 (if you're in sleep mode, assume English without asking).

| Option | Changes | e.g. |
|---|---|---|
| 📖 A bonus, I'm all in | non-English +0.25 · `attention ↑0.1` | *Oldboy*, *Portrait of a Lady on Fire* |
| 👍 Fine, if it's worth it | non-English allowed only with quality ≥ 7.5 | *The Lives of Others*, *Drive My Car* |
| 📚 Homework | `filter: original_language = en` (animation exempt, it's usually dubbed) | *Mrs. Doubtfire*, *Knives Out* |

#### C4 · "Book a trip"
**Finds out:** country/culture. **Ask when:** novelty is mid-to-high (C1 answer 3 or 4, or A3 "bored") and ≥ 4 countries each hold ≥ 8 % of the pool. Options are the top 4 countries in the pool, written as places; the ones below are typical.

| Option | Changes | e.g. |
|---|---|---|
| ☂️ A weekend in London | `filter: country ∈ {GB, IE}` | *Hot Fuzz*, *Love Actually* |
| 🏮 A night market in Seoul | `filter: country = KR` | *Memories of Murder*, *Decision to Leave* |
| 🥐 A café in Paris | `filter: country = FR` | *The Taste of Things*, *Call My Agent!* |
| 🚗 A road trip across America | `filter: country = US` · `kw+ road trip` | *Thelma & Louise*, *Nebraska* |
| 🧭 Somewhere I'd never think of | `filter: original_language ∉ your top 5 watched languages` | *Wadjda*, *The Lunchbox* |

---

### D. Who's there, what's happening around the screen

#### D1 · "Who's on the sofa?"
**Finds out:** profile and social context. **Ask when:** first or second question, unless the text or the profile toggle already says. Changes whose taste counts, so it is high value.

| Option | Changes | e.g. |
|---|---|---|
| 🧍 Just me | `profile = me` · weight and attention limits relaxed (+0.1 to targets) | *A Ghost Story*, *Tár* |
| 💑 Me and him, cuddled up | `profile = together` · `good_for+ date night` · `feels+ romantic, tender (weak)` | *Before Sunrise*, *Crazy, Stupid, Love* |
| 📱 Me and him, but he'll be on his phone | `profile = together` · `attention ≤ 0.4` · `good_for+ half-watching` · prefer shared-liked clusters | *The Office* (US), *Ted Lasso* |
| 🍕 Friends over | `profile = together` · `energy → 0.65` · `weight ≤ 0.4` · `good_for+ friends over` · `feels+ crowd-pleaser` | *21 Jump Street*, *The Mummy* (1999) |

#### D2 · "What's happening around the screen?"
**Finds out:** attention, from what you'll actually be doing. **Ask when:** attention still uncertain (conf < 0.5) after A1, or as **alt** to A1 when D1 said "together".

| Option | Changes | e.g. |
|---|---|---|
| 🌑 Nothing. Lights off. | `attention → 0.75` · `feels+ immersive` · `spectacle⁺ ↑0.2` | *Gravity*, *Blade Runner 2049* |
| 🍛 Dinner on our laps | `attention → 0.45` · `good_for+ eating dinner` · `kw− gore, vomit, body horror` | *Big Night*, *The Hundred-Foot Journey* |
| 🧺 Cooking or folding laundry | `attention → 0.15` · TV +0.3 · `good_for+ background while cooking` | *Queer Eye*, *Gilmore Girls* |
| 💬 We'll be talking half the time | `attention → 0.3` · `feels+ silly, quotable` · rewatch +0.2 | *Clueless*, *Shrek* |

#### D3 · "How should you two feel walking out of the room?"
**Finds out:** what the shared experience is for. **Ask when:** `profile = together` and weight/feels are still open.

| Option | Changes | e.g. |
|---|---|---|
| 🤝 Holding hands | `good_for+ date night` · `feels+ romantic, tender` · `ending_valence⁺ ≥ 0.6` | *About Time*, *The Big Sick* |
| 🗣️ Arguing about the ending | `attention ↑0.15` · `feels+ debatable, ambiguous` · `kw+ open ending, twist ending` | *Inception*, *Gone Girl* |
| 😂 Quoting it all week | `humour⁺ ≥ 0.7` · `feels+ quotable, silly` | *Anchorman*, *Mean Girls* |
| ✈️ Planning a trip | `feels+ wanderlust, sun-soaked` · `kw+ travel, italy, scenery` | *Call Me by Your Name*, *The Secret Life of Walter Mitty* |

---

### E. Tolerance for tension

#### E1 · "Pick tomorrow's front-page headline"
**Finds out:** darkness (violence, moral weight, how grim). **Ask when:** darkness conf < 0.5. High value: darkness is the dial most likely to ruin a night if it's wrong.

| Option | Changes | e.g. |
|---|---|---|
| 🐶 "Local dog elected mayor" | `darkness ≤ 0.15` | *Paddington 2*, *Babe* |
| 🏰 "Mystery at the manor" | `darkness → 0.35` · `darkness ≤ 0.5` · `kw+ murder mystery, whodunit` | *Knives Out*, *Clue* |
| 💰 "Heist of the century" | `darkness → 0.5` · `energy ↑0.15` · `kw+ heist, con artist` | *Inside Man*, *The Italian Job* (2003) |
| 🗂️ "Case reopened after 20 years" | `darkness ≥ 0.65` · `attention ↑0.1` · `kw+ serial killer, investigation` | *Zodiac*, *Prisoners* |

#### E2 · "A door is open a crack in a dark hallway. You…"
**Finds out:** fear and suspense (tension⁺), separately from darkness. **Ask when:** darkness target ≥ 0.4 (no point asking someone who chose "dog elected mayor") and tension⁺ unknown.

| Option | Changes | e.g. |
|---|---|---|
| 🚪 Close it and go to bed | `tension⁺ ≤ 0.3` · `kw✕ jump scare` · genre Horror ✕ · `feels− eerie, dread` | *The Intern*, *Notting Hill* |
| 🔦 Peek in with the phone torch | `tension⁺ → 0.5` · `feels+ eerie (light), mystery` | *Glass Onion*, *Coraline* |
| 🚶 Walk in | `tension⁺ ≥ 0.7` · `feels+ dread, eerie` · Horror/Thriller +0.2 | *Get Out*, *The Others* |
| 😈 I'm the one behind the door | `darkness ≥ 0.75` · `feels+ unsettling` · `kw+ villain protagonist, psychopath` | *Nightcrawler*, *American Psycho* |

#### E3 · "Awkward silences are…"
**Finds out:** cringe tolerance⁺ (second-hand embarrassment). **Ask when:** Comedy is ≥ 30 % of the pool. Cringe is the most common reason a comedy "ruins" a night, and no other dial captures it.

| Option | Changes | e.g. |
|---|---|---|
| 😬 My favourite thing | `cringe⁺ ≥ 0.6` · `kw+ mockumentary, awkward` | *Curb Your Enthusiasm*, *The Office* (UK) |
| 🙂 Fine in small doses | `cringe⁺ ≤ 0.6` | *Booksmart*, *Meet the Parents* |
| 🫣 Unbearable, I hide behind a cushion | `cringe⁺ ≤ 0.25` · `kw− mockumentary, humiliation` | *The Princess Bride*, *Game Night* |

#### E4 · "Tonight's red is…"
**Finds out:** gore⁺ (on-screen blood), which darkness alone doesn't separate (*Zodiac* is dark but not gory). **Ask when:** darkness target ≥ 0.5, or Action/Horror ≥ 25 % of the pool.

| Option | Changes | e.g. |
|---|---|---|
| 🎈 A balloon | `gore⁺ ≤ 0.1` | *Up*, *Amélie* |
| 🍷 Wine spilled on the carpet | `gore⁺ ≤ 0.3` (implied, off-screen) | *Rear Window*, *Murder on the Orient Express* (2017) |
| 🥩 A medium-rare steak | `gore⁺ ≤ 0.65` | *The Bourne Identity*, *Gladiator* |
| 🩸 Just say it | no limit · `kw+ gore` allowed | *Kill Bill: Vol. 1*, *Evil Dead* (2013) |

---

### F. Aesthetic and sensory mood

#### F1 · "Pick a window to look out of"
**Finds out:** setting and atmosphere. **Ask when:** feels tags are thin (< 2) or as a fun opener on a weekend. Very good at splitting films that sit at the same dial values.

| Option | Changes | e.g. |
|---|---|---|
| 🌧️ Rain on a city street | `feels+ melancholy, romantic, urban` · `setting⁺ city, rain` | *In the Mood for Love*, *Lost in Translation* |
| 🌅 A beach at golden hour | `feels+ sun-soaked, sensual, summery` · `setting⁺ coast, summer` | *The Talented Mr. Ripley*, *Y Tu Mamá También* |
| 🌆 A neon alley | `feels+ neon, nocturnal, stylish` · `setting⁺ city, night` · `kw+ cyberpunk, neo-noir` | *Akira*, *Ghost in the Shell* (1995) |
| ❄️ A snowy cabin | `feels+ cozy, wintry` · `setting⁺ snow, winter` · `kw+ christmas, winter` | *Klaus*, *Happiest Season* |
| 🌲 A forest at dawn | `feels+ mystical, earthy` · `setting⁺ forest, nature` · `fantasy⁺ ↑0.2` | *Princess Mononoke*, *Pan's Labyrinth* |

#### F2 · "Pick a colour palette"
**Finds out:** visual style (palette⁺). **Ask when:** **alt** to F1, later in the session, as a tie-breaker when the top 20 agree on dials but differ in look.

| Option | Changes | e.g. |
|---|---|---|
| 🩷 Pastel pinks and mint | `palette⁺ pastel` · `feels+ whimsical, playful` | *Barbie*, *Marie Antoinette* |
| 🟫 Warm film grain, browns and orange | `palette⁺ warm-grain` · `feels+ nostalgic` · `kw+ 1970s` | *Licorice Pizza*, *Almost Famous* |
| 🩶 Cold blues and greys | `palette⁺ cold` · `feels+ clinical, cerebral, tense` | *Ex Machina*, *Sicario* |
| ❤️ Saturated reds and greens | `palette⁺ saturated` · `feels+ lush, heightened` | *Amélie*, *Hero* |
| ⬛ Black and white | `palette⁺ bw` | *Roma*, *Belfast* |

#### F3 · "Where would you rather be stuck for a weekend?"
**Finds out:** scale and setting: intimate and talky vs vast and visual. **Ask when:** spectacle⁺ and talkiness⁺ both unknown, mid-to-late session.

| Option | Changes | e.g. |
|---|---|---|
| 🛋️ One room, great company | `talkiness⁺ ≥ 0.7` · `spectacle⁺ ≤ 0.3` · `kw+ single location, dialogue-driven` | *12 Angry Men*, *Carnage* |
| 🚂 A train across a continent | `kw+ journey, train, ensemble cast` · `feels+ adventurous` | *The Darjeeling Limited*, *Snowpiercer* |
| 🏜️ A huge empty desert | `spectacle⁺ ≥ 0.75` · `pace ↓0.1` · `kw+ desert, epic` | *Lawrence of Arabia*, *The English Patient* |
| 🛰️ A space station | `kw+ space, isolation` · `fantasy⁺ ↑0.2` · `feels+ eerie (weak)` | *Moon*, *Alien* |

#### F4 · "Pick a texture"
**Finds out:** the core "feels like" tag. Works on instinct, so it's a good question for when you're too tired to think. **Ask when:** feels tags thin, any point in the session.

| Option | Changes | e.g. |
|---|---|---|
| 🧶 A wool blanket | `feels+ cozy, warm, gentle` · `darkness ≤ 0.35` | *Little Women* (2019), *Fantastic Mr. Fox* |
| 🪡 Silk | `feels+ elegant, sensual, slow` · `pace ↓0.1` | *Phantom Thread*, *Carol* |
| 🪨 Gravel | `feels+ gritty, raw` · `fantasy⁺ ≤ 0.3` | *Uncut Gems*, *The Wrestler* |
| 🫧 Bubble wrap | `feels+ playful, silly` · `humour⁺ ≥ 0.6` · `energy ↑0.15` | *Spider-Man: Into the Spider-Verse*, *Shaun of the Dead* |

#### F5 · "Pick your dinner guest"
**Finds out:** talkiness⁺ vs visual storytelling and spectacle. **Ask when:** attention target 0.3–0.7 (it decides *how* the attention is spent) and talkiness⁺ unknown.

| Option | Changes | e.g. |
|---|---|---|
| 🗯️ The one who never stops talking, brilliantly | `talkiness⁺ ≥ 0.75` · `feels+ witty` | *The Social Network*, *His Girl Friday* |
| 📜 The great storyteller | `talkiness⁺ → 0.5` · `feels+ whimsical, sweeping` · `kw+ narrated` | *Big Fish*, *Forrest Gump* |
| 👁️ The quiet one who notices everything | `talkiness⁺ ≤ 0.3` · `feels+ contemplative` | *WALL·E*, *All Is Lost* |
| 🎆 The one who brought fireworks | `spectacle⁺ ≥ 0.75` · `energy ↑0.2` · `talkiness⁺ ≤ 0.4` | *Mission: Impossible – Fallout*, *Godzilla Minus One* |

---

### G. Pace and patience

#### G1 · "Pick a way to cross the river"
**Finds out:** pace target. **Ask when:** pace conf < 0.5 **and** pace isn't already implied: skip if energy conf ≥ 0.6 and the pool's pace/energy correlation is > 0.7 (it would just repeat the energy answer).

| Option | Changes | e.g. |
|---|---|---|
| 🚣 A rowing boat, no hurry | `pace → 0.2` · `feels+ contemplative` | *The Remains of the Day*, *Drive My Car* |
| ⛴️ A ferry, with a coffee | `pace → 0.45` | *Hidden Figures*, *Brooklyn* |
| 🚤 A jet ski | `pace → 0.8` · `energy ↑0.1` | *Edge of Tomorrow*, *Kingsman: The Secret Service* |
| 🐒 Swinging across on a vine | `pace → 0.9` · `strangeness⁺ ↑0.2` · `feels+ chaotic, unhinged` | *Scott Pilgrim vs. the World*, *Kung Fu Hustle* |

#### G2 · "If nothing happens for 20 minutes, you…"
**Finds out:** slow-burn **tolerance**, a hard floor rather than a target. **Ask when:** **alt** to G1. Prefer G2 when A1/A2 said you're tired (a floor protects you better than a target), or when ≥ 25 % of the pool has pace < 0.3.

| Option | Changes | e.g. |
|---|---|---|
| 🧘 Relax into it | slow films allowed, `pace → 0.25 (weak)` | *Paterson*, *Aftersun* |
| 🤞 Trust it, if it's going somewhere | `pace ≥ 0.25` · quality ≥ 7.5 for films with pace < 0.4 | *There Will Be Blood*, *Zodiac* |
| 📱 Check my phone | `pace ≥ 0.5` · `kw+ cold open` | *Jurassic Park*, *Get Out* |
| 🏃 Already gone | `pace ≥ 0.75` · `filter: runtime ≤ 110` | *Speed*, *Crank* |

---

### H. What would ruin tonight (avoid)

Avoid questions only show options whose removals hit ≥ 5 % of the current top 50. An option that removes nothing is a dead option and is hidden (design doc §3 rule). **One avoid question per session**, asked by question 4.

#### H1 · "What would ruin tonight?"
**Finds out:** the single biggest dealbreaker. **Ask when:** default avoid question.

| Option | Changes | Removes e.g. |
|---|---|---|
| 😭 Someone dies and I sob | `weight ≤ 0.6` · `kw✕ death of loved one, terminal illness` · `feels− devastating` | *Marley & Me*, *A Monster Calls* |
| 👻 A jump scare | `kw✕ jump scare` · `tension⁺ ≤ 0.5` · Horror ✕ | *The Conjuring*, *Insidious* |
| 🤯 Feeling lost or stupid | `attention ≤ 0.55` · `kw✕ nonlinear timeline, surreal` | *Tenet*, *Mulholland Drive* |
| 😣 People being cruel to each other | `darkness ≤ 0.5` · `cringe⁺ ≤ 0.4` · `kw✕ abuse, bullying, humiliation` | *Whiplash*, *Dogville* |
| 😴 Being bored | `pace ≥ 0.45` · `energy ≥ 0.35` | *Jeanne Dielman*, *Paterson* |

#### H2 · "Which plot twist would make you throw the remote?"
**Finds out:** specific content you don't want tonight (content flags⁺). **Ask when:** weight or darkness target ≥ 0.5 **and** at least 2 flags each appear in ≥ 5 % of the top 50. **alt** H1.

| Option | Changes | Removes e.g. |
|---|---|---|
| 🐕 Something happens to the dog | `kw✕ dog dies, animal death, animal cruelty` | *Hachi: A Dog's Tale*, *Old Yeller* |
| 🧒 A child in danger | `kw✕ child in peril, kidnapping, child abuse` | *Prisoners*, *Room* |
| 🏥 Hospitals and diagnoses | `kw✕ cancer, terminal illness, hospital` | *The Fault in Our Stars*, *Still Alice* |
| 💔 Someone cheats | `kw✕ infidelity, adultery` | *Unfaithful*, *Closer* |
| 🆗 None of those, I'm fine | no change (records that you're unbothered tonight) | – |

#### H3 · "Last time movie night went wrong, it was…"
**Finds out:** your most common failure mode. **Ask when:** **alt** H1, used in the first ~20 sessions (before the logs can tell us). The answer is also saved as a weak standing preference (weight 0.2) that decays over 30 days.

| Option | Changes | Removes e.g. |
|---|---|---|
| ⌛ Too long | `filter: runtime ≤ 115` | *The Irishman*, *Babylon* |
| 🐢 Too slow | `pace ≥ 0.45` | *Drive My Car*, *First Reformed* |
| 🌧️ Too sad | `weight ≤ 0.5` | *Manchester by the Sea*, *Grave of the Fireflies* |
| 🫥 Too weird | `strangeness⁺ ≤ 0.45` · `kw− surreal, absurdism` | *The Lobster*, *Mulholland Drive* |
| 🥱 Too predictable | `strangeness⁺ ≥ 0.3` · quality ≥ 7.0 · `kw− formulaic` penalty on lowest-distinctiveness clusters | *The Proposal*, *Just Go with It* |

#### H4 · "Had enough of anything lately?"
**Finds out:** fatigue with what you've watched recently. **Ask when:** in the last 14 days, ≥ 3 watched films sit in one cluster. Options are generated: the top 3 recent clusters plus "Nope, more please".

| Option (example) | Changes | Removes e.g. |
|---|---|---|
| 🦸 Superheroes | cluster affinity −0.6 for that cluster tonight | *Thor: Ragnarok*, *Shang-Chi* |
| 🔪 True crime | same | *Zodiac*, *Mindhunter* |
| 💘 Rom-coms | same | *How to Lose a Guy in 10 Days*, *Set It Up* |
| 🔁 Nope, more please | that cluster +0.3 | – |

---

### I. Closers built from the pool

These are generated from the films left in the pool, so their options are always real. They are usually the last question.

#### I1 · "Which of these feels closest?"
**Finds out:** the vibe cluster. **Ask when:** stop rules aren't met after 4+ questions and the top 20 spread over ≥ 3 clusters. Options: the 3 biggest clusters in the top 20, each shown with 2 posters, plus "None of these".

| Option (example) | Changes | e.g. |
|---|---|---|
| "Gentle comfort comedies" | `cluster+` that cluster (0.3) · other two −0.15 | *Paddington 2*, *Chef* |
| "Warm small-town dramas" | same | *The Holdovers*, *Brooklyn* |
| "Feel-good animation" | same | *Klaus*, *Ratatouille* |
| "None of these" | all three −0.2, pool widens by 100 films | – |

#### I2 · "This one or that one?"
**Finds out:** the last bit of direction, from two real films. **Ask when:** last question, if the top 10 still hold two clearly different groups. Picks the two top-10 films that are farthest apart in vibe space. Can repeat once.

| Option (example) | Changes |
|---|---|
| *The Grand Budapest Hotel* | query vector +0.25 toward it, −0.1 away from the other |
| *Arrival* | same, reversed |
| Neither | both −0.3 |

---

## 2. Choosing the next question

A short rule, run after every answer:

1. **Eligible questions:** not yet asked, no **alt** sibling asked, its "Ask when" condition holds, and no answer is already implied by your text (if "with friends" is in the text, D1 is answered as 🍕 automatically and never shown).
2. **Hide dead options.** An option needs ≥ 10 films in the current pool (top 300) that fit it, or, for avoid options, must remove ≥ 5 % of the top 50. A question left with fewer than 3 live options is dropped.
3. **Score each eligible question**, simulating each option against the pool:

   ```
   value(q) = Σ_options p(option) · (1 − overlap(top10_now, top10_if_option))
            × fun(q)            # 1.2 for A3, D1, F1, F4, B1, E2; 1.0 otherwise
            × usefulness(q)     # learned, starts at 1.0 (design doc §3)
            × group_penalty     # 0.5 if another question from the same group was already asked
   ```

   `p(option)` starts uniform and later uses your past answer frequencies, by weekday and hour. Questions whose best `value` is below 0.3 (top 10 would overlap > 70 % whatever you pick) are dropped.
4. **Fixed slots:**
   - **Question 1** comes from the "about you" set: A3, D1, A1, F1 or F4. Never C2, C3 or H. The first question should read *you*, not ask about films.
   - **One avoid question** (H1/H2/H3, whichever scores highest) by question 4, if any is eligible.
   - **Closers** (I1, I2) only after question 4.
5. Ask the highest-scoring question.

**Stop** when the top 5 has been stable for the last two answers, there's a clear score gap between #5 and #6, 6 questions have been asked, or you press Skip/Just show me.

Why this works: the overlap simulation is the "Guess Who?" rule from the design doc (ask what splits the pool), the group penalty forces it to cover different dimensions, and the fixed slots keep the opening personal and make sure something always guards against a ruined evening.

---

## 3. Three sample sessions

### Session 1: Tuesday, 22:40. Text: *"something"*. Profile toggle: Together.

| # | Question | Answer | Effect |
|---|---|---|---|
| 1 | A3 How did today end? | 🌀 Stressed, head still buzzing | darkness ≤ 0.4, tension ≤ 0.4, weight → 0.3, feels+ soothing |
| 2 | D1 Who's on the sofa? | 📱 Me and him, but he'll be on his phone | attention ≤ 0.4, half-watching |
| 3 | F1 Window | ❄️ A snowy cabin | feels+ cozy, wintry |
| 4 | H1 What would ruin tonight? | 😭 Someone dies and I sob | kw✕ death of loved one |
| 5 | C1 Restaurant | 🍜 My usual place, a new dish | unseen, favourite clusters only |
| – | *Stops: top 5 stable for two answers.* | | |

**The vibe:** calm (energy ~0.25), easy to follow (attention ≤ 0.4), light (weight 0.3), wholesome, warm and wintry, nobody dies, new to us but from clusters we already love.

**Leads to:** *Arthur Christmas* · *Elf* · *Paddington 2* · *Chef* · *Julie & Julia*

(*About Time* would have ranked high on everything else. H1 removes it: the father's death is the emotional centre.)

### Session 2: Saturday, 20:15. Text: empty. Profile: Me.

| # | Question | Answer | Effect |
|---|---|---|---|
| 1 | A3 How did today end? | 🥱 Bored, nothing happened | energy → 0.75, novelty +0.2 |
| 2 | A1 Your brain is a… | 🔋 Fully charged phone | attention → 0.8, feels+ cerebral |
| 3 | E1 Headline | 🗂️ "Case reopened after 20 years" | darkness ≥ 0.65, kw+ investigation |
| 4 | H2 Throw the remote | 🆗 None of those | no change (dark pool has many flags, so the question was eligible) |
| 5 | C4 Book a trip | 🏮 A night market in Seoul | country = KR |
| 6 | G2 Nothing happens for 20 min | 🤞 Trust it, if it's going somewhere | pace ≥ 0.25, quality ≥ 7.5 for slow films |

**The vibe:** dark, absorbing, investigative Korean cinema. Full attention, slow-burn welcome if it pays off, high quality bar.

**Leads to:** *Memories of Murder* · *Mother* (2009) · *The Chaser* · *Decision to Leave* · *Burning*

### Session 3: Friday, 19:30. Text: *"something fun with friends"*. Profile: Together.

The text already answers D1 (🍕 Friends over: energy 0.65, weight ≤ 0.4, crowd-pleaser), so D1 is never shown.

| # | Question | Answer | Effect |
|---|---|---|---|
| 1 | F4 Pick a texture | 🫧 Bubble wrap | feels+ playful, silly, humour ≥ 0.6, energy ↑ |
| 2 | A4 When does tonight end? | 🌙 Normal bedtime | runtime ≤ 125 |
| 3 | E3 Awkward silences are… | 🫣 Unbearable | cringe ≤ 0.25 (comedy was > 30 % of the pool) |
| 4 | H3 Last time it went wrong | 🫥 Too weird | strangeness ≤ 0.45 |
| 5 | C2 Time machine | 📼 VHS days | 1975–1999 |
| – | *Stops: score gap after #5.* | | |

**The vibe:** big, warm, fun adventure-comedy everyone gets, no cringe, nothing odd, with an 80s/90s rewatchable feel.

**Leads to:** *Ghostbusters* (1984) · *Back to the Future* · *Men in Black* · *Galaxy Quest* · *The Mummy* (1999)

---

## 4. What the catalogue needs to store

The five existing dials cover maybe two-thirds of the bank. These additions cover the rest, roughly in priority order. Every one has a **free fallback** (anchor-sentence projection on the vibe vector, as in design doc §2.5, or TMDB keywords), so the questions work before an LLM tagging pass exists.

### 4.1 New dials (0–1)

| Dial | Low ↔ high | Used by | Fallback before LLM tags | Priority |
|---|---|---|---|---|
| `tension` | relaxed ↔ nail-biting suspense | A3, E2, H1 | Anchors: "a relaxed, low-stakes film" vs "a nail-biting, suspenseful thriller" | **Must** |
| `ending_valence` | bleak ↔ uplifting | B2, B4, D3 | Anchors on review snippets: "a hopeful, uplifting ending" vs "a bleak, devastating ending" | **Must** |
| `humour` | not funny ↔ laugh-out-loud | D3, F4 | Comedy genre + anchors "hilarious, laugh out loud" | **Must** |
| `strangeness` | conventional ↔ surreal | G1, H3 | Keywords surreal/absurdism/experimental + anchors | **Must** |
| `cringe` | none ↔ excruciating | E3, H1 | Keywords mockumentary, awkward, embarrassment | **Must** (only needed for comedies) |
| `gore` | none ↔ graphic | E4 | Keywords gore/blood/splatter + certification (R/18) | Should |
| `fantasy` | grounded realism ↔ another world | B4, F1, F3, F4 | Genres Fantasy/Sci-Fi/Animation + anchors | Should |
| `talkiness` | visual, few words ↔ dialogue-driven | F3, F5 | Anchors "dialogue-driven, talky" vs "visual, almost wordless" | Should |
| `spectacle` | intimate ↔ epic scale | B3, D2, F3, F5 | Budget (TMDB) + anchors "epic, vast, spectacular" | Could |

**Tip for the LLM tagger:** give each dial a 3-point rubric with an example film at 0, 0.5 and 1 (e.g. tension: *Paddington 2* / *Knives Out* / *Uncut Gems*). It keeps scores consistent across 50k films.

### 4.2 Content flags (booleans, for the avoid questions)

`animal_harm`, `child_in_peril`, `death_of_loved_one`, `terminal_illness`, `infidelity`, `sexual_violence`, `suicide`, `jump_scares`, `body_horror`, `bullying_humiliation`, `war_violence`.

Source: TMDB keywords first (many already exist as keywords), then the LLM pass over overview + reviews. These power H1, H2 and part of D2 ("dinner on our laps"). They are the one thing where a wrong "false" is costly, so treat a missing flag as "unknown", and when an avoid answer is active, rank unknowns slightly below known-clean films.

### 4.3 Setting and look tags

| Tag set | Values | Used by |
|---|---|---|
| `setting` | city, countryside, coast, forest, desert, snow, space, single-location, road/journey, small town | B3, F1, F3 |
| `season_weather` | summer, winter, autumn, rain | F1 (and later: seasonal weekday downloads) |
| `time_of_day` | mostly night / mostly day | F1 neon alley |
| `palette` | pastel, warm-grain, cold, saturated, bw, neon | F2 |
| `story_era` | when the story is **set** (decade), separate from release year | B4, C2 nuance |

Fallback: TMDB keywords cover setting and season well. Palette needs the LLM (or a poster/backdrop colour histogram, which is cheap and surprisingly good for pastel/cold/bw).

### 4.4 "Feels like" vocabulary (proposed closed list, 37)

Grouped so the tagger picks **3–6 per film**:

- **Warm:** warm, cozy, comforting, gentle, tender, wholesome
- **Up:** joyful, giddy, silly, playful, witty, triumphant, hopeful
- **Low:** bittersweet, melancholy, wistful, nostalgic, haunting, devastating, cathartic
- **Edge:** tense, eerie, dread, unsettling, gritty, raw, chaotic
- **Mind:** cerebral, mind-bending, contemplative, dreamy
- **Look and place:** sun-soaked, wintry, neon/nocturnal, lush, epic/awe, stylish

### 4.5 "Good for" vocabulary (proposed closed list, 16)

Pick **2–4 per film**: winding down, a bad day, background while cooking, eating dinner, half-watching, dozing off, date night, friends over, a crowd-pleaser, rainy afternoon, Sunday hangover, can't sleep, binge (TV), talking about after, when you're sick, a long free evening.

"Good for" tags are also the bridge to **Phase 6 (day-of-week downloads)**: once sessions are logged with weekday and hour, "Tuesday 22:40 → winding down, half-watching" becomes a learned pattern, and the downloader can pre-fetch films tagged that way.

### 4.6 Per-session context (stored in `discover_sessions`)

Add `weekday`, `hour`, `answers_json` keyed by question id + option id, and `asked_order`. This feeds `p(option)` in §2, the learned `usefulness(q)`, and Phase 6.

---

## 5. The short set

If the pop-up may only draw from 10 questions, keep these:

| # | Question | Why it stays |
|---|---|---|
| 1 | **A3** How did today end? | The best opener. It reads you, not films, and sets energy, weight and a soft avoid in one tap. |
| 2 | **D1** Who's on the sofa? | Changes whose taste counts and the social context. No other question does that. |
| 3 | **A1** Your brain right now is a… | Attention is the dial that splits pools hardest, and getting it wrong is the commonest bad night ("too complicated" or "boring"). |
| 4 | **E1** Tomorrow's headline | Darkness. A wrong darkness call is the fastest way to ruin a night. |
| 5 | **B1** Pick a drink | Weight plus a strong "feels" tag. Four very different, instantly understood answers. |
| 6 | **F1** Pick a window | The aesthetic question. Separates films that look identical on the dials, and it's the most fun to answer. |
| 7 | **C1** Pick a restaurant | Comfort vs novelty, including rewatch. Without it the system can't tell "safe" nights from "surprise me" nights. |
| 8 | **H1** What would ruin tonight? | The one avoid question. It catches the dealbreakers the dials miss. |
| 9 | **A4** When does tonight end? | Runtime and movie vs TV, in one question that doesn't feel like a form. |
| 10 | **I1** Which of these feels closest? | Always real options, built from the pool. A safety net that guarantees the last answer leads somewhere. |

Together they cover all five existing dials (pace is inferred from energy and attention, and I1 corrects it), runtime, media type, profile, novelty, one avoid and the aesthetic, and each question moves at least two of these. All ten work on today's data. Only two options touch proposed dials (A3 "stressed" uses tension⁺, H1 "cruel" uses cringe⁺), and both have keyword fallbacks.

**First additions beyond 10:** E2 (tension, once that dial exists), E3 (cringe, for comedy nights) and G2 (slow-burn tolerance).
