# Northern Lights

A self-hosted film and TV app for a household. It finds and downloads titles, plays them on
a phone, laptop, Android TV or Chromecast at original quality, remembers what each person
watched and how they liked it, and recommends what to watch from a typed wish, alone or
together.

It runs on your own computer at home, with [Jellyfin](https://jellyfin.org/) playing the
files. Recommendations use free, local AI models; nothing costs money unless you choose a
paid tagging run.

## Read next

- **[How it works](docs/guides/README.md)**: plain-language guides (Wishmatch, playback, the
  TV app, training, deployment).
- **[Knowledge map](docs/KNOWLEDGE_MAP.md)**: where each feature lives and what to edit to
  change it.
- **[CONTRIBUTING.md](CONTRIBUTING.md)**: set up, run the tests, open a PR.

## Quick start

You need Python 3.12, [uv](https://docs.astral.sh/uv/), a free
[TMDB API key](https://www.themoviedb.org/settings/api) and, to play anything, Jellyfin.

```bash
git clone https://github.com/rupaai/smart-recommender-player.git
cd smart-recommender-player
uv sync --group dev

# Secrets: never commit this file
cat > .env <<'EOF'
TMDB_API_KEY=your-key
JELLYFIN_URL=http://localhost:8096
JELLYFIN_API_KEY=your-jellyfin-key
EOF

# Your own setup: people, media folders (git ignores this file)
cp config.local.example.yaml config.local.yaml   # then edit it

uv run python server.py
```

Open http://localhost:8000 (the original app), http://localhost:8000/nl/ (Northern Lights on
a phone or browser), or http://localhost:8000/nl/tv/ (the TV pages).

The app uses `catalogue_full.db` (about 50,000 titles from TMDB). Build it once with
`uv run python utils/build_full_catalogue.py`. To just try the app, use the small test
catalogue that ships with the repo: `DATABASE_PATH=catalogue.db uv run python server.py`.

Optional: **Prowlarr** for wider download search (`PROWLARR_URL`, `PROWLARR_API_KEY` in
`.env`; `utils/add_prowlarr_indexers.py` adds safe indexers), and **Ollama** for reading
typed wishes (`OLLAMA_URL`).

## Settings

| File | For |
|---|---|
| `config.yaml` | shared defaults, no personal values |
| `config.local.yaml` | your people, folders and choices, laid over `config.yaml` (git ignores it) |
| `recommender_rules.yaml` | every recommender word list and weight |
| `homelab/config.yaml` | the settings of our own server (mars) |
| `.env` | secrets (API keys) |

## Checks

`make check` runs the linter, every test script and the Mac shell tests. Run it before every PR.

## License

MIT.
