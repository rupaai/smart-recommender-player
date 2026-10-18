# The nightly job on your Mac

Once a day your Mac does the heavy work, then hands the result to the server (`mars`). It tries at 03:30 and then every hour at :30, and runs the first time the Mac is on and can reach the server. A Mac that is off, asleep or away from home is not an error: it just tries again next hour.

| Step | What happens | When |
|---|---|---|
| 1 Backup | Downloads a copy of the server's database (your ratings and watch history) to `~/CouchCinemaBackups/<date>.db`. Keeps 30 days. | Every night |
| 2 Catalogue | Looks for new films and shows on TMDB, keeps only good ones (at least 200 votes and rating 6.0), sends them to the server. | Weekly (first run a week after the last refresh) |
| 3 Snapshot | Downloads the database again so the model learns from the server's own ids. | Every night |
| 4 Train | Retrains the "what to watch next" model and the vibe search. | Every night |
| 5 Check | Scores the new models. If any score is more than 1% worse than what the server runs now, it is held back. | Every night |
| 6 Publish | Sends the new models to the server, which checks them and switches over with no downtime. | When step 5 passes |
| 7 Report | Pings healthchecks.io (if you set it up). Phone alert (ntfy) if anything broke. | Every night |

Nothing here can delete your films or ratings: the server never removes titles, and the Mac never sends watch history.

## Install (once)

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`). From the project folder in Terminal:

```
cd ~/codes/smart_movie_streamer
bash ops/mac/install.sh
```

It asks for the server address, the admin token and the ntfy topic, checks it can reach the server, and starts the job. The exact steps, with where to find the token, are in `docs/BUILD_GUIDE.md` (Step 3).

## Every day use

| I want to... | Command (on the Mac) |
|---|---|
| See last night's log | `cat ~/Library/Logs/CouchCinema/$(date +%F).log` |
| Run the whole night now | `bash ops/mac/nightly.sh` |
| Run it without the weekly catalogue part | `bash ops/mac/nightly.sh --skip-catalogue` |
| Only take a backup | `bash ops/mac/nightly.sh --skip-catalogue --skip-train` |
| Train but don't publish | `bash ops/mac/nightly.sh --skip-catalogue --skip-publish` |
| Run the catalogue refresh now, even if it is not due | `bash ops/mac/nightly.sh --catalogue` |
| Start the scheduled job right now | `launchctl kickstart -k gui/$(id -u)/com.couchcinema.nightly` |
| Check the job is installed | `launchctl print gui/$(id -u)/com.couchcinema.nightly` |
| Which model does the server run? | `uv run python ops/mac/cc.py model-status` |
| Go back to the previous model | `uv run python ops/mac/cc.py rollback-model` |
| Remove the job | `bash ops/mac/install.sh --uninstall` |

Once a day's run has reached the server it is done for that day, even if a step failed (you get the alert for that). If the Mac could not reach the server all day, you get one quiet alert after 21:00 saying so. `~/.couchcinema/last-run` holds the date of the last run that reached the server; delete it to make the next hourly try run again today.

## Settings

`config.yaml`, section `nightly:` (the server does not read it): the quality floor for new titles, how much worse a model may score before it is held back (`gate_max_drop_pct`), how long backups are kept, and `mood_tag_limit`. Mood tagging uses the Anthropic API and costs credit, so it ships at `0` (off).

Secrets live in `~/.couchcinema/env` (only you can read it): `CC_SERVER`, `ADMIN_TOKEN`, `NTFY_TOPIC`, and an optional `HEALTHCHECKS_NIGHTLY_URL`.

## What the scores mean

The check is a safety net, not a full test. It asks: given the films you liked so far, does the model's shortlist contain the next one you liked (`tiger_hit_rate`)? And, once the taste model has enough verdicts, how well does it order films you rated (`taste_spearman`, `taste_auc`). A model that got clearly worse, or broke, is not published. The first model with scores is always published.

## When something looks wrong

- **Phone alert "nightly has not run today":** the Mac was on but never reached the server (not at home and Tailscale off?). Nothing to fix; it tries again next hour.
- **Phone alert "nightly failed":** open the log (above); the last lines say which step stopped and why. The server keeps running the previous model.
- **"model held back":** not an error. The new model scored worse than the one on the server, so nothing changed. The bundle is kept in `~/CouchCinemaBundles/`.
- **"could not download the database":** the Mac couldn't reach the server. Check you are at home or on Tailscale.
