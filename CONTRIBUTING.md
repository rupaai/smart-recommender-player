# Contributing

Welcome. This guide takes you from nothing to your first pull request (PR).
You do not need to know GitHub yet. Go step by step.

Labiba (GitHub: `rupaai`) owns this repo. Labiba reviews and merges every PR.

Before you change code, read [PROJECT_HANDOVER.md](PROJECT_HANDOVER.md). It explains how the app works. [AGENTS.md](AGENTS.md) has the short rules for AI helpers.

## 1. Set up GitHub from zero

1. Make a free account at https://github.com/signup.
2. Tell Labiba your GitHub username. Labiba will send you a collaborator invite.
3. Accept the invite. Look in your email, or open https://github.com/rupaai/smart-recommender-player/invitations.
4. Install the tools. On a Mac, install [Homebrew](https://brew.sh) first, then run:

```bash
brew install git gh uv
```

5. Log in to GitHub from the terminal:

```bash
gh auth login
```

Pick GitHub.com, then HTTPS, then log in with the web browser. Follow the steps on screen.

6. Tell git who you are. Use the email on your GitHub account:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

7. Download the code:

```bash
gh repo clone rupaai/smart-recommender-player
cd smart-recommender-player
```

Do not fork the repo. Push your branches to this repo. The AI reviewer cannot run on forks.

## 2. Run the app and the tests

Install the packages:

```bash
uv sync --group dev
```

Run all the checks. This is what CI runs:

```bash
make check
```

You can also run one part:

```bash
make lint    # ruff
make test    # every tests/test_*.py file
```

Run the app:

```bash
uv run python server.py
```

Then open http://localhost:8000. The Northern Lights player is at http://localhost:8000/nl/.

The tests need no secrets. The app starts without API keys. Some features give a plain fallback when a key is missing. That is on purpose.

Use a copy of the catalogue when you try changes, so you never damage the real one:

```bash
cp catalogue.db /tmp/copy.db
DATABASE_PATH=/tmp/copy.db uv run python server.py
```

More run details are in section 5 of [PROJECT_HANDOVER.md](PROJECT_HANDOVER.md).

## 3. Make a change and open a PR

Start from an up to date `main`:

```bash
git checkout main
git pull
git checkout -b my-short-branch-name
```

Make your change. Add or update a test. Then run `make check`.

Save and push:

```bash
git add -A
git commit -m "Say what you changed in one short line"
git push -u origin my-short-branch-name
```

Open the PR:

```bash
gh pr create
```

Fill in the PR form. The template asks what changed, how you tested, and a short checklist.

Keep each PR small and about one thing.

## 4. What happens next

1. CI runs `make check` on your PR. A green tick means it passed. If it is red, open the PR page, read the failed step, fix it, and push again.
2. An AI reviewer (Claude) comments on your PR. Read the comments. Fix what makes sense, or reply if you disagree.
3. Labiba reviews and merges.

Do not merge your own PRs. Only Labiba merges.

After a merge to `main`, the app is built and deployed to the dev copy by Homelab. Labiba decides when it goes to prod.

## 5. Using Gemini

You can use Gemini CLI to help you code. Install it (it needs Node, `brew install node`):

```bash
npm install -g @google/gemini-cli
```

Run `gemini` inside the repo folder. It reads [GEMINI.md](GEMINI.md), which points to [AGENTS.md](AGENTS.md). So Gemini gets the project rules by itself.

Example prompts:

- "Read PROJECT_HANDOVER.md and explain in simple words how the Discover tab picks movies."
- "Find where the download progress is shown and add a test for it. Run make check."
- "I got this error when I ran make check. Explain what it means and suggest a fix." (Paste the error.)
- "Look at my changes with git diff. Do I need to update PROJECT_HANDOVER.md? If yes, draft the line."

You are still responsible for what you push. Read what the AI wrote before you commit it.

## 6. Rules

- **Keep the docs current.** Every PR that changes how something works must update [PROJECT_HANDOVER.md](PROJECT_HANDOVER.md) in the same PR.
- **Secrets.** Only Labiba sees app secret values. Never ask for them, print them, log them, or commit them. This applies to you and to any AI helper.
- **Mars.** Never run things on mars or change production without asking Labiba. Mars is the home box where the app runs.
- **Fix causes and add tests.** Find why something broke and fix that. Do not hide the symptom. Add a test that would have caught it.
- Never push to `main`. Always use a branch and a PR.
- **PR titles** start with `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `ci:`, `style:`, `refactor:` or `build:` (`feat!:` for a breaking change). The PR title check fails otherwise.

If you are stuck, ask Labiba. Small questions are fine.
