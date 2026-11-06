# New & popular: new episodes and films to download

This part of the player app shows TV episodes that came out recently, popular films, and what's popular on
each streaming service (Netflix, Prime Video, Apple TV+ and so on), so you can download them in one tap.
All the work happens on the server (mars). The TV and the web pages only show the list and send your tap.

## Where you see it

- **Home** (web, TV and laptop): a "New episodes" row. Your shows come first, then popular ones. Choosing a
  poster opens the New & popular page at that show.
- **New & popular page**: "New & popular" in the TV and laptop menu, "New" on the web (`/nl/new`). At the
  top are tabs: "All" and one per streaming service. The page remembers the last tab. Each show
  has a Download button ("Get S01E01"), "Other copies", "Seasons" and, if it's on the server, "Open show".
  "Seasons" lists every season; pick one to see its episodes (on the server, out, or coming on a date) and
  get any episode, or "Get whole season" once a season has finished. After a tap the button says "Downloading" and links to Downloads.

## The lists on the All tab

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

**Popular movies.** Films trending on TMDB this week, with the same rating, genre, language and taste bars.
Left out: films one of you already marked as seen, and films out less than 30 days (those are usually
only in cinemas, so there is no copy to get yet).

## The streaming-service tabs

Each tab shows "Shows on Netflix" and "Movies on Netflix" (and so on): the most popular titles TMDB lists
on that service in your country, with the same rating, genre, language and taste bars. Shows start at
S1E1 like popular shows; films you've seen are left out. Old titles count too, not only new ones.

The country is `watch_region` (default `US`), because each country's Netflix has different titles. Set
yours in `config.local.yaml` (or `homelab/config.yaml` on mars):

```yaml
new_episodes:
  watch_region: GB
```

## What the Download button gets

Always one episode, the next one in watching order, so you can start watching while the rest come:

- **A show you never had** (popular shows, or one only on Watch later): **S1E1**, even if the show is on
  season 3. Use "Seasons" to start somewhere else.
- **A show with files on the server**: the first episode you're missing, going on from the first episode
  you have. If you have S1E1-3 and S2E1, that's S2E2; a gap (S1E2 missing) comes first.
- **A show you marked as seen but have no files of** (you watched it somewhere else): the first episode of
  the latest season.

A whole season is only downloaded when you choose "Get whole season" in the season list.

**A film**: "Get movie" searches for "Title Year" and keeps copies whose name has the title and a year
within one of the film's. If none is found it says so: films often show up once they're out to stream or buy.

The server searches for that episode the same way the Download tab does and ranks copies with "Best match"
(quality, how many people share it, sensible size). It drops copies of a different episode, a different show
or several seasons at once, then starts the best one. "Choose another copy" shows the full list.
Progress shows on the Downloads screen as usual. The links to the copies never leave the server.

If no copy is found yet, it says so: new episodes often turn up a few hours after they air.

## New episodes by themselves

On mars, every 3 hours, the server looks at each show you're watching: it has files on the server, and
one of you watched it (or a new file came) in the last 60 days. It keeps the next 3 unwatched episodes
ready, counted after the last episode the one of you furthest along has watched.

It gets them **one episode at a time, in order** (S02E01, then S02E02...). It never takes a season pack,
because a pack only shows up when every episode is in; a single episode shows up as soon as it's done.
A show with a download still running waits for it. The usual disk limits apply (500 GB, 20 GB free).

To see what the last look did per show: `GET /api/nl/auto-episodes`.

Settings, in `auto_episodes:` (`homelab/config.yaml` for mars):

| Setting | What it does | Default |
|---|---|---|
| `enabled` | on or off (mars: on) | off |
| `check_hours` | how often mars looks | 3 |
| `episodes_ahead` | unwatched episodes kept ready per show | 3 |
| `active_days` | how long a show counts as "being watched" | 60 |
| `shows_per_run` | most downloads started in one look | 10 |

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
| `movies_max` | cards in Popular movies | 18 |
| `movie_min_days_out` | how long a trending film must be out | 30 |
| `watch_region` | country for the service tabs (two letters) | US |
| `provider_max` | shows and films per section on a service tab | 18 |
| `providers` | the service tabs: key, name and TMDB watch-provider id | Netflix, Prime Video, Apple TV+, Disney+, Max, Hulu, Paramount+ |

To add a service, add a line under `providers` with its TMDB id (TMDB's "watch providers" list has them).

## Files

- `services/nl_new_episodes.py`: the lists, the service tabs, which episode to offer, and picking a copy.
- `web/routes/nl_new.py`: `GET /api/nl/new-episodes` (`?provider=netflix` for a tab), `GET /api/nl/new-episodes/copies`,
  `GET /api/nl/new-episodes/show/{tmdb_id}/seasons`, `.../season/{n}`, `POST /api/nl/new-episodes/download`.
- `tests/test_nl_new_episodes.py`.
- `services/nl_auto_episodes.py`: new episodes by themselves; `tests/test_nl_auto_episodes.py`.
