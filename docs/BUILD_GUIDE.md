# Build guide: how to run the deployment plan

Your step-by-step for turning `docs/DEPLOYMENT_PLAN.md` into a running server. You start four milestones, each with one copy-paste prompt. Claude builds; you step in only at the marked **Your turn** points.

**Why milestones and not one big prompt:** M2 needs your keys and a token from GitHub, M3 needs one command on your Mac, and each milestone ends with something you can check works. One giant prompt would either stall waiting for you or guess at things it shouldn't. Four short check-ins is the least hands-on way that stays safe.

**How you'll know it's your turn:** every prompt ends by telling Claude to finish with a **"Your turn"** checklist. When that message appears, the milestone is built and waiting on you. If it's anything else, you don't need to do anything.

---

## Step 0: Before you start (5 minutes)

1. Merge PR #13 ("Deployment plan") on GitHub, so every session can read the plan.
2. Make sure the repo is **private**: GitHub → repo → Settings → General → Danger Zone → visibility.
3. Have these ready somewhere you can copy from: your TMDB API key and Prowlarr API key (from your Mac's `.env`).

## Step 1: Milestone M1, Package (no server needed)

Builds Phase 0 (risk checks), Phase 1 (app portability), Phase 2 (Docker) and Phase 3 (CI).

**Start it:** post this as a new message in the project chat.

> Use Sonnet for this thread. Build milestone M1 from docs/DEPLOYMENT_PLAN.md (Phase 0, 1, 2 and 3), following docs/BUILD_GUIDE.md. Read PROJECT_HANDOVER.md and the plan first, not the whole repo. Follow "Rules for whoever builds this". Hand tasks marked (Haiku) to Haiku subagents. One branch and one PR per phase, each passing `make check`, opened in order so each builds on the last. Tick the plan's checkboxes and update the handover in each PR. Ask me only if something would change a decision in section 2 of the plan. When everything is up, finish with a "Your turn" checklist of exactly what I need to do.

**Your turn when it says so:**
1. Read the Phase 0 results in the first PR. If either check failed, the session will already have said what it changed.
2. Merge the PRs in order: Phase 0/1, then 2, then 3.
3. Protect `main`: GitHub → Settings → Branches → Add rule for `main` → tick "Require status checks to pass" and pick `check` and `image`.
4. Check: the Actions tab shows a green CI run on `main`, and Packages shows the image.

## Step 2: Milestone M2, Go live

Builds Phase 4 (setup script), Phase 5 (one-button deploy) and Phase 8 (alerts).

**Start it:** new message in the project chat.

> Use Sonnet for this thread. Build milestone M2 from docs/DEPLOYMENT_PLAN.md (Phase 4, 5 and 8), following docs/BUILD_GUIDE.md. M1 is merged. Same rules as before: Haiku subagents for (Haiku) tasks, one PR per phase, tick checkboxes, update the handover. The server is `mars` (see plan section 4a); don't touch anything on it yet, just build and test the scripts. Finish with a "Your turn" checklist that walks me through running bootstrap.sh, getting the runner token, copying my database, the first deploy, Jellyfin's first-run wizard, and the alerts.

**Your turn when it says so** (it will give exact commands; this is the shape):
1. Merge the M2 PRs.
2. On GitHub: Settings → Actions → Runners → New self-hosted runner → copy the token it shows.
3. From your Mac's Terminal: `ssh gabstevens@mars`, then run the bootstrap command from the checklist. Paste your keys and the runner token when asked. It prints an admin token; save it for M3.
4. Copy your database and films across (the checklist has the commands).
5. GitHub → Actions → Deploy → Run workflow. When it's green, open `http://mars:8000` for the app.
6. Open `http://mars:8096`, do Jellyfin's setup wizard (libraries `/media/Movies` and `/media/Shows`), turn on hardware transcoding, then create an API key under Dashboard → API Keys and run the one command the checklist gives you to save it.
7. Install the ntfy app on both phones and subscribe to the topic the bootstrap printed.

From here on, the app is live. To ship a change later: merge its PR, then Actions → Deploy → Run workflow.

**Prefer not to type the server commands yourself?** In the M2 thread, after merging, say: *"Do the server part for me from my Mac."* Claude will ask to start a session on your Mac and run the setup over SSH, pausing only to ask for the keys and the runner token.

## Step 3: Milestone M3, Learning loop

Builds Phase 6 (the server's admin API and model bundles, and the Mac's client) and Phase 7 (the nightly job on your Mac).

**Start it:**

> Use Sonnet for this thread. Build milestone M3 from docs/DEPLOYMENT_PLAN.md (Phase 6 and 7), following docs/BUILD_GUIDE.md. M1 and M2 are merged and the app is live on mars. Same rules: Haiku subagents for (Haiku) tasks, one PR per phase, tick checkboxes, update the handover. Finish with a "Your turn" checklist for deploying it, installing the nightly job on my Mac, and checking the first night's run. Please write the guide in a way which is easy to follow.

### Your turn when it says so

Every command says where it runs: **[Mac]** is Terminal on your Mac, **[mars]** is the server. Copy each one exactly. Your values are already filled in: server `192.168.1.183`, user `gabstevens`, project folder `~/codes/smart_movie_streamer`.

**A. Put the new version on the server (about 5 minutes)**

1. On GitHub, merge the M3 PR "Deploy phase 7" (phase 6, PR #26, is already merged). Wait for the green tick on `main` (Actions tab, CI).
2. GitHub, Actions, **Deploy**, Run workflow, branch `main`. Wait until it is green.
3. **[Mac]** Check the new admin door is there. It must answer `401` (locked), not `404` (missing):

```
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.1.183:8000/admin/model-status
```

**B. Get the two secrets from the server**

4. **[Mac]** Print them (it asks for the server's `sudo` password):

```
ssh -t gabstevens@192.168.1.183 "sudo grep -E '^(ADMIN_TOKEN|NTFY_TOPIC)=' /srv/couchcinema/.env"
```

Keep that Terminal window open. You need the part after `ADMIN_TOKEN=` and the part after `NTFY_TOPIC=`.

**C. Install the nightly job on your Mac (about 3 minutes)**

5. **[Mac]** Update the project and check `uv` is installed (`brew install uv` if the second line says "not found"):

```
cd ~/codes/smart_movie_streamer
git checkout main && git pull
command -v uv || echo "uv not found: run  brew install uv"
```

6. **[Mac]** Run the installer. Answer the three questions: press Enter for the address (`http://192.168.1.183:8000`), paste the admin token (nothing shows as you paste, that is normal), paste the ntfy topic:

```
bash ops/mac/install.sh
```

It ends with "Installed. It runs once a day from 03:30". If it says it could not reach the server, check you are on your home wifi, then run it again.

7. **[Mac]** Quick test, backup only (about 10 seconds). A new file should appear:

```
bash ops/mac/nightly.sh --skip-catalogue --skip-train
ls ~/CouchCinemaBackups
```

**D. The first real run (about 30 minutes)**

8. **[Mac]** Do the first full run yourself, while the Mac is awake and plugged in, so you can watch it. This trains the models and sends them to the server:

```
bash ops/mac/nightly.sh --skip-catalogue
```

In a second Terminal tab you can follow along with `tail -f ~/Library/Logs/CouchCinema/$(date +%F).log`. The last line says what happened: "published a new model" or "model held back: ..." (not an error, see `ops/mac/README.md`).

9. **[Mac]** Check the server picked it up. `model_bundle` should show a name starting `bundle-` with today's date:

```
curl -s http://192.168.1.183:8000/healthz
```

**E. Optional, for the Sunday catalogue refresh**

10. It needs your TMDB key in `~/codes/smart_movie_streamer/.env` (it is already there for the app). TMDB is not reachable from where Claude builds things, so this part has only been tested with stand-ins. Try it once yourself, it takes a while (the first time, up to an hour):

```
cd ~/codes/smart_movie_streamer
bash ops/mac/nightly.sh --catalogue --skip-train
```

If it fails, the log says which step; nothing on the server changes unless the last step (the patch) succeeds.

**F. Check the first night's run (next morning)**

11. You get a phone alert only if something broke. No alert means fine, but check anyway:
    - **[Mac]** `tail -5 ~/Library/Logs/CouchCinema/$(date +%F).log` should end with `7. done:`.
    - **[Mac]** `curl -s http://192.168.1.183:8000/healthz`: `model_bundle` shows last night's date.
    - **[Mac]** `launchctl print gui/$(id -u)/com.couchcinema.nightly | grep -E "state|last exit"`: `last exit code = 0`.
12. If the Mac is off or asleep at 03:30, the job runs the first hour after that it is on and can reach the server (it tries every hour at :30).

**If something goes wrong**

- Server refuses a model (the log says why): the old model keeps running; tell Claude "the nightly publish was refused, here is the log" and paste the last 20 lines.
- New model feels worse: **[Mac]** `uv run python ops/mac/cc.py rollback-model` goes back one model.
- Stop the nightly job: **[Mac]** `bash ops/mac/install.sh --uninstall`.

## Step 4: Milestone M4, Polish (whenever you like)

Phase 10 (README and design notes for interviews) can happen any time after M2. Phase 9 waits for the external drive.

> Use Haiku for this thread. Build Phase 10 from docs/DEPLOYMENT_PLAN.md, following docs/BUILD_GUIDE.md. One PR. Finish with a "Your turn" checklist.

When the drive arrives:

> Use Sonnet for this thread. Build Phase 9 from docs/DEPLOYMENT_PLAN.md. Finish with a "Your turn" checklist for plugging in the drive and running the move script.

---

## If something goes wrong

- **A session asks you a question mid-milestone:** answer in that thread; it carries on.
- **CI is red on a PR:** the session that opened it fixes it. You don't need to.
- **A deploy fails:** it rolls back automatically and you get an ntfy message. Reply in the M2 thread (or start a new one) with "The deploy failed, look at the latest Deploy run".
- **You want to undo a deploy:** Actions → Deploy → Run workflow, and enter the older commit's full SHA (copy it from the commits list on GitHub) as the ref.
