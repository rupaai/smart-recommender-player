"""
Run the daily IMDb / Rotten Tomatoes lookup once, by hand (utils/fetch_title_scores.py)

The app does this by itself once a day on mars prod (services/title_scores.py). Use this to try it on a copy:

    DATABASE_PATH=/tmp/copy.db uv run python utils/fetch_title_scores.py           # today's share
    DATABASE_PATH=/tmp/copy.db uv run python utils/fetch_title_scores.py --status  # only show progress

Reads TMDB_API_KEY and OMDB_API_KEY from .env. It counts against the same daily limit as the app
when it runs on the same database. Free: no money is spent.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from database import DatabaseManager  # noqa: E402
from services.app_config import load_config  # noqa: E402
from services.title_scores import TitleScores  # noqa: E402


def main() -> None:
    load_dotenv()
    config = load_config()
    db_path = os.getenv("DATABASE_PATH") or config.get("database_path", "catalogue.db")
    DatabaseManager(db_path)  # adds the score columns if the copy is older
    job = TitleScores(config, db_path)
    if "--status" in sys.argv:
        print(job.progress(), "used today:", job.used_today())
        return
    job.run_once()


if __name__ == "__main__":
    main()
