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

Builds Phase 6 (Mac ↔ server data) and Phase 7 (nightly training).

**Start it:**

> Use Sonnet for this thread. Build milestone M3 from docs/DEPLOYMENT_PLAN.md (Phase 6 and 7), following docs/BUILD_GUIDE.md. M1 and M2 are merged and the app is live on mars. Same rules: Haiku subagents for (Haiku) tasks, one PR per phase, tick checkboxes, update the handover. Finish with a "Your turn" checklist for deploying it, installing the nightly job on my Mac, and checking the first night's run.

**Your turn when it says so:**
1. Merge the M3 PRs and press Deploy.
2. On your Mac, run the install command from the checklist and paste the admin token from M2.
3. Next morning: you'll have an ntfy message if anything failed. Otherwise open `http://mars:8000/healthz`: `model_bundle` should show last night's date.

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
