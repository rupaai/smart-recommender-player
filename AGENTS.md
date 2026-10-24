# Rules for AI coding helpers

This file is for any AI helper (Claude, Gemini, and others) working in this repo.
People: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Start here

Read [PROJECT_HANDOVER.md](PROJECT_HANDOVER.md) first. It is the knowledge base for the project.
Its section 6 ("Rules and conventions") also applies to you. Follow it. If this file and that section ever differ, ask Labiba.
Also read the waiting notes in `handover/notes/`.

## Commands

Install: `uv sync --group dev`

Run all checks: `make check`

Parts of it:

- `make lint` runs ruff.
- `make test` runs every `tests/test_*.py` file as its own script.
- `make test-mac` runs the Mac shell tests.

Run the app: `uv run python server.py`, then open http://localhost:8000.

Try changes on a copy of the catalogue: `DATABASE_PATH=/tmp/copy.db`.

## Work rules

- Keep PRs small and about one thing.
- Fix causes, not symptoms. Add a test for every fix and every new behavior.
- Run `make check` before you say the work is done.
- Keep the docs current. Do not edit the feature blocks in PROJECT_HANDOVER.md. Add a note in `handover/notes/<feature>/` (see handover/README.md), and mark it `ready` when the feature is done. A bot folds it into PROJECT_HANDOVER.md. Keep each note's line short.
- Write plain, short text for users.

## Git rules

- Never push to `main`. Work on a branch and open a PR with `gh pr create`.
- PR titles start with `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `ci:`, `style:`, `refactor:` or `build:` (`feat!:` for a breaking change); the `PR title` workflow fails the rest.
- Never merge a PR. Labiba reviews and merges.
- Do not fork. Push branches to `rupaai/smart-recommender-player`.
- Commit or push only when the person you work for asks.

## Secrets

Only Labiba sees app secret values (TMDB, Jellyfin, Prowlarr keys, `ADMIN_TOKEN`).
Never ask for them, print them, log them, or commit them. This applies to you and to any AI helper.
Do not paste them into screenshots, tests, docs or PR text. Do not read `.env` out loud.

## Mars and production

Never run things on mars and never change production without asking Labiba first.
Do not touch Home Assistant or Matter on mars.
Deployment is by Homelab (`homelab.yaml`). A merge to `main` builds the image. Do not deploy by hand.

## Money

Do not run anything that spends API money. See PROJECT_HANDOVER.md section 6.
