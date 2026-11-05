# New & popular: new episodes to download

This part of the player app shows TV episodes that came out recently, so you can download them in one tap.
All the work happens on the server (mars). The TV and the web pages only show the list and send your tap.

## Where you see it

- **Home** (web, TV and laptop): a "New episodes" row. Your shows come first, then popular ones. Choosing a
  poster opens the New & popular page at that show.
- **New & popular page**: "New & popular" in the TV and laptop menu, "New" on the web (`/nl/new`). Each show
  has a Download button ("Get S02E05" or "Get season 3"), "Other copies" and, if it's on the server,
  "Open show". After a tap the button says "Downloading" and links to Downloads.

## The two lists

**New episodes of your shows.** A show counts as "yours" when the server has files of it, one of you marked
it as seen, or it is on someone's Watch later list. A show someone rated below 5 doesn't count. For each one
the server asks TMDB (the free film and TV database) for its latest episode. If that episode came out in the
last 45 days and you don't have it, the show is listed.

"Have it" means the server has, or once had, a file of that episode. So an episode the watched cleanup
deleted after you saw it won't come back as "new".

**Popular right now.** The shows trending on TMDB this week that are still airing. Left out:
- shows rated under 7.0 on TMDB, or with fewer than 100 votes;
- news, talk, reality, soap and kids' shows;
- non-English shows below the "foreign titles" bar in `recommender_rules.yaml` (same rule as recommendations);
- shows that clearly don't fit your taste. The taste score is Wishmatch's: how the show's mood tags line up
  with what you rated. A brand-new show has no tags yet, so it is kept and simply not taste-checked.

Shows that fit your taste move up the list; the rest follow TMDB's trending order.

## What the Download button gets

- If the latest season has **finished** and you're missing two or more of its episodes: the whole season,
  as one "season pack" (one download with every episode).
- Otherwise: the **first episode you're missing** from the latest season. For a show you've never had, that's
  episode 1 of the season that's airing now.

The server searches for that episode the same way the Download tab does and ranks copies with "Best match"
(quality, how many people share it, sensible size). It drops copies of a different episode, a different show
or several seasons at once, then starts the best one. "Choose another copy" shows the full list.
Progress shows on the Downloads screen as usual. The links to the copies never leave the server.

If no copy is found yet, it says so: new episodes often turn up a few hours after they air.

## Cost

Free. It uses TMDB (free) and the torrent search you already have. No paid AI.

## Change it

Everything is in `new_episodes:` in `config.yaml`:

| Setting | What it does | Default |
|---|---|---|
| `recent_days` | how recent the latest episode must be | 45 |
| `your_shows_max` | how many of your shows are checked | 60 |
| `skip_rated_below` | a show rated below this isn't "yours" | 5 |
| `popular_pages` | TMDB trending pages read (20 shows each) | 2 |
| `popular_max` | cards in Popular right now | 18 |
| `min_rating`, `min_votes` | the rating bar for popular shows | 7.0, 100 |
| `min_taste` | how badly a show may fit your taste (-1 to 1) | -0.15 |
| `taste_weight` | how much taste counts against trending in the order | 0.6 |
| `skip_genres` | genres never shown in Popular | News, Talk, Reality, Soap, Kids |
| `cache_hours` | how long TMDB answers are reused | 6 |
| `list_minutes` | how long a built list is reused | 15 |

## Files

- `services/nl_new_episodes.py`: the lists, which episode to offer, and picking a copy.
- `web/routes/nl_new.py`: `GET /api/nl/new-episodes`, `GET /api/nl/new-episodes/copies`,
  `POST /api/nl/new-episodes/download`.
- `tests/test_nl_new_episodes.py`.
