# Training on your Mac (the Train button)

Nothing runs on a schedule. When you want fresh picks, open **Train CouchCinema** (Applications in your home folder, or Spotlight; drag it to the Dock to keep it handy). A Terminal window opens, checks the server can be reached, then your Mac does the heavy work and hands the result to the server (`mars`), which the app uses straight away. It takes about 10 to 20 minutes, and you get a Mac notification when it's done.

| Step | What happens | When |
|---|---|---|
| 1 Backup | Downloads a copy of the server's database (your ratings and watch history) to `~/CouchCinemaBackups/<date>.db`. Keeps 30 days. | Every run |
| 2 Catalogue | Looks for new films and shows on TMDB, keeps only good ones (at least 200 votes and rating 6.0), sends them to the server. | When the last refresh is a week old |
| 3 Snapshot | Downloads the database again so the model learns from the server's own ids. | Every run |
| 4 Train | Retrains the "what to watch next" model and the vibe search. | Every run |
| 5 Check | Scores the new models. If any score is more than 1% worse than what the server runs now, it is held back. | Every run |
| 6 Publish | Sends the new models to the server, which checks them and switches over with no downtime. | When step 5 passes |
| 7 Report | Mac notification with the result; pings healthchecks.io (if you set it up) and a phone alert (ntfy) if anything broke. | Every run |

Nothing here can delete your films or ratings: the server never removes titles, and the Mac never sends watch history.

## Install (once)

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`). From the project folder in Terminal:

```
cd ~/codes/smart_movie_streamer
bash ops/mac/install.sh
```

It asks for the server address, the admin token and the ntfy topic, checks it can reach the server, puts the Train button in `~/Applications`, and removes the old 03:30 schedule if it is there.

- **Server address:** `http://192.168.1.183:8000` (mars). Homelab keeps port 8000 open for the app on prod, so the address from before the move still works.
- **Admin token:** Homelab makes it for you. In the Homelab companion go to Apps -> Northern Lights -> Secrets -> prod -> ADMIN_TOKEN -> Copy. Without it the server answers 404 on `/admin`.

## Every day use

| I want to... | Command (on the Mac) |
|---|---|
| Train now (same as the button) | `bash ops/mac/train-now.sh` |
| See today's log | `cat ~/Library/Logs/CouchCinema/$(date +%F).log` |
| Run it without the weekly catalogue part | `bash ops/mac/nightly.sh --skip-catalogue` |
| Only take a backup | `bash ops/mac/nightly.sh --skip-catalogue --skip-train` |
| Train but don't publish | `bash ops/mac/nightly.sh --skip-catalogue --skip-publish` |
| Refresh the catalogue now, even if it is not due | `bash ops/mac/nightly.sh --catalogue` |
| Which model does the server run? | `uv run python ops/mac/cc.py model-status` |
| Go back to the previous model | `uv run python ops/mac/cc.py rollback-model` |
| Remove the button | `bash ops/mac/install.sh --uninstall` |

The finer-grained commands above run `nightly.sh` directly (the name is from when it ran at night). If you set up a healthchecks.io check for the old nightly job, pause or delete it there: with no schedule it would report missing pings.

## Settings

`config.yaml`, section `nightly:` (the server does not read it): the quality floor for new titles, how much worse a model may score before it is held back (`gate_max_drop_pct`), how long backups are kept, and `mood_tag_limit`. Mood tagging uses the Anthropic API and costs credit, so it ships at `0` (off).

Secrets live in `~/.couchcinema/env` (only you can read it): `CC_SERVER`, `ADMIN_TOKEN`, `NTFY_TOPIC`, and an optional `HEALTHCHECKS_NIGHTLY_URL`.

## What the scores mean

The check is a safety net, not a full test. It asks: given the films you liked so far, does the model's shortlist contain the next one you liked (`tiger_hit_rate`)? And, once the taste model has enough verdicts, how well does it order films you rated (`taste_spearman`, `taste_auc`). A model that got clearly worse, or broke, is not published. The first model with scores is always published.

## When something looks wrong

- **"Can't reach the server":** you are not at home and Tailscale is off. Nothing was changed; press the button again once you are connected.
- **"Training stopped with an error" (or phone alert "nightly failed"):** the lines above it in Terminal, or the log, say which step stopped and why. The server keeps running the previous model.
- **"model held back":** not an error. The new model scored worse than the one on the server, so nothing changed. The bundle is kept in `~/CouchCinemaBundles/`.
- **"could not download the database":** the Mac couldn't reach the server. Check you are at home or on Tailscale.
