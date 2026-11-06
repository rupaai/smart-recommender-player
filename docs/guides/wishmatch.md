# Wishmatch: how recommendations are picked

Wishmatch is the recommender the app uses (`recommender.engine: wishmatch`). It replaced an
older rules engine ("legacy"), which is still there as a safety net: if Wishmatch ever fails,
legacy answers and the log says so.

## The idea in one picture

```mermaid
flowchart LR
  W["Your wish<br/>'funny but not silly,<br/>something short'"] --> R["1. Read the wish<br/>into mood words"]
  R --> C["2. Pick ~2,000<br/>well-known candidates"]
  C --> Q{"3. Do the top picks<br/>really differ?"}
  Q -- yes --> A["Ask 1-2 questions<br/>'silly or witty?'"]
  A --> S
  Q -- no --> S["4. Score each one:<br/>match + taste + quality<br/>(+ crowd)"]
  S --> O["5. Mix films and shows,<br/>show each with its reasons"]
```

## 1. Reading the wish

The app has a fixed vocabulary of mood words (`services/vibe_vocab.py`): 14 **dials** (how
funny, how dark, how fast, grounded or fantastical...) and **feelings and occasions** (cosy,
tense, heartwarming, date night, background...).

Two readers turn your typed words into that vocabulary:

- **The word lookup** (instant): a list of words and phrases and what they mean, in
  `recommender_rules.yaml` under `wishmatch.lexicon`. "laugh" means funny, "not scary" rules out
  tense and horror, "80s" sets the years, "sci-fi" means Science Fiction.
- **The wish reader** (a small AI model, Qwen 2.5 3B, running in Ollama on the server):
  for words the lookup doesn't know ("something like a warm hug"). It may only answer with
  vocabulary words. It is free but slow on the server's processor (4-8 seconds for a new wish),
  so:
  - when the lookup understood every word, the search doesn't wait for the model,
  - a search waits at most `wait_seconds` (8); a slower answer is kept for "Show more",
  - every answer is saved (`wishmatch_wishes.json`), so the same wish is instant next time,
  - the model stays loaded for an hour after a search (about 2.5 GB of memory),
  - every call starts with the same fixed instructions (the list of mood words). That list is
    most of what the model has to read, and Ollama remembers the start of the last prompt, so
    a new wish only costs the model reading the few new words. Question wording uses the same
    start, so it doesn't push the list out of the model's memory,
  - at server start (on mars only), the app loads the model, words every mood-dial question
    once (kept in `wishmatch_wordings.json`) and reads one throwaway wish
    (`wish_reader.warm_at_start`, `questions.preword_at_start`). `GET /api/rec/status` shows how it went.

To time it: `uv run python utils/time_wishmatch.py http://<server>:<port>` runs a few known wishes,
new wishes and question searches and prints the seconds for each (free; no admin token).

A word that does nothing is fixed by adding it to `wishmatch.lexicon`, not by changing code.

## 2. Candidates

Wishmatch doesn't score all 50,000 titles. It takes about 2,000 that are **well known**
(how well the tagging model knew a title is a good sign of how famous it is), split 70/30
between films and shows, plus up to 300 films that MovieLens fans of your favourites also loved
(see "crowd" below). Asking for "hidden gems" swaps in the less-known ones instead.

Hard limits come first: a typed genre ("comedy"), years ("80s"), a country or language
("korean"), and anything already seen (see "together" below).

## 3. Questions (at most two)

Wishmatch looks at its top 40 picks. If they spread widely on one dial (some silly, some
witty) or split between feelings, it asks about that, with two example titles per answer. It
never asks about something your wish already said, and asks nothing when the picks agree.
An answer counts half as much as a word you typed. The AI model words the question; if it is
slow, a plain "Which feels right: A or B?" is used.

## 4. Scoring

Each candidate gets one number, made of:

| Part | Weight | What it measures |
|---|---|---|
| match | 1.0 | how well the title's mood tags fit the wish's words |
| taste | 0.6 | how much this person (or both of you) liked titles with similar moods |
| quality | 0.3 | its IMDb score (TMDB's until OMDb has one) and, when there is one, its Rotten Tomatoes score; counts less when few people voted |
| crowd | 0.4 | films only: MovieLens fans of films you loved also loved this |
| genre hint | 0.4 | a word that hints at a genre ("scary" -> Horror) |

All weights live in `recommender_rules.yaml` under `wishmatch.weights`.

With no rating filter chosen, titles rated below 6 are left out (`wishmatch.default_min_rating`, the IMDb
score). "Any rating" lets them back in; 7+ and 8+ mean the IMDb score too. Our own ratings only steer taste.

**Taste** comes from your verdicts: feelings and dials that are more common among titles you
loved than among everything you rated count for you; the same for what you disliked. Your
written **taste profile** (made at Train time, see [training](training.md)) adds what you
declared you like or avoid. With few ratings, taste counts less (it is "shrunk").

**Alone or together:**
- Alone: your own taste, plus the other person's at 0.2. Anything you've seen is left out.
- Together: both tastes count the same, and a title either of you would dislike is pushed
  down. What both have seen is left out; what one has seen drops below unseen titles.

## 5. Results

Films and shows are mixed 2:1 (`wishmatch.mix`). Each card shows up to three reasons ("cosy,
witty, fans of Paddington loved it"). Content warnings are never shown as reasons.

## Today's picks (nothing typed)

Each day mars makes a list of films for you, one for Gabriele and one for both of you, from what you
watched in the last 30 days. Nothing is typed: the "wish" is made from those titles instead.

1. What counts as watched lately: Jellyfin's played marks (with the date, per person), plus titles
   marked seen or rated in the app. Your verdict counts: a loved title about twice, a "not for me"
   title only as moods to avoid. A watch two weeks ago counts half as much as one today.
2. The wish: the mood dials those titles agree on (say all calm and warm), and the feelings most of
   them share (cosy, witty). Where they disagree, that dial is left out.
3. The rest is ordinary Wishmatch: your taste, the MovieLens fans, IMDb / Rotten Tomatoes quality,
   well-known titles, nothing seen and nothing played lately.
4. Each card says "Because you watched X": the recent title it is most like.

On the home page (phone, TV and laptop) it is the "Picked for you today" row. A film already on the
server opens its page; any other card is a "Get it" button: mars finds the best copy (Best match, as on
the Download tab) and downloads it.

The list is made the first time someone asks each day and kept in `daily_picks.json` next to the
database. API: `GET /api/nl/daily-picks` (for the viewer on that screen; "Both of us" gets the joint
list). Settings: `wishmatch.daily_picks` and `wishmatch.recent` in `recommender_rules.yaml`. Free: no
model is asked.

## Costs

Free. The wish reader runs on the server, the tagging on the Mac. The only paid option is
writing taste profiles or tags with Claude Haiku instead (under 1 cent per Train for
profiles, about 1 cent per 100 titles for tags), and only when you ask for it.

## Tuning it

| To... | Change (in `recommender_rules.yaml`) |
|---|---|
| teach it a word | `wishmatch.lexicon` (or `wishmatch.genre_hints` for a genre) |
| more shows, fewer films | `wishmatch.mix` |
| more famous / more obscure picks | `wishmatch.candidates.known`, `min_popularity` |
| care more about your taste than the wish | `wishmatch.weights.taste` up, `match` down |
| fewer or no questions | `wishmatch.questions.max` |
| wait less for the AI reader | `wishmatch.wish_reader.wait_seconds` |
| skip loading the AI at server start | `wishmatch.wish_reader.warm_at_start: false` |
| let the partner count more when alone | `wishmatch.people.other_weight` |
| look further back for today's picks | `wishmatch.daily_picks.days` |
| let older watches count more | `wishmatch.recent.half_life_days` |

After a change, `uv run python utils/compare_engines.py` runs 30 sample wishes and checks each
top 10 (right genre, well known, nothing already seen...) on a copy of the catalogue.

## Where it lives

- `packages/wishmatch/`: the core. It knows nothing about films, so it can be lifted out
  for another project (music, food) by writing a new adapter.
- `services/wishmatch_movies/`: the film and TV part (reads the catalogue, the questions
  pop-up, the engine switch).
- Live status: `GET /api/rec/status` (is the wish reader up, how long it took last time).

- IMDb and Rotten Tomatoes scores: `services/title_scores.py` collects them a little each day on
  mars (OMDb's free key allows 1,000 lookups a day, best-known films first). Wishmatch's quality and
  rating filter use them (`title_quality` in `services/wishmatch_movies/adapter.py`).

What is planned next is in `docs/WISHMATCH_NEXT.md`.
