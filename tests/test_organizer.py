"""
Offline organizer tests (tests/test_organizer.py)

Pure-Python: no network, no libtorrent session, no database.
Run standalone:

    uv run python tests/test_organizer.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.torrent_service import (  # noqa: E402
    _parse_sxxeyy,
    _parse_subtitle_tags,
    organize_media_files,
)

FAKE_VIDEO_BYTES = b"0" * 2048
FAKE_SUB_BYTES = b"1" * 64

failures = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


def make_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def fresh_tmp(tag):
    return tempfile.mkdtemp(prefix=f"cms_test_{tag}_")


def test_parsers():
    print("\n[1] _parse_sxxeyy / _parse_subtitle_tags")
    check("S01E02", _parse_sxxeyy("Show.S01E02.1080p.mkv") == (1, 2, None))
    check("1x02", _parse_sxxeyy("Show.1x02.mkv") == (1, 2, None))
    check("multi S01E02-E03", _parse_sxxeyy("Show.S01E02-E03.mkv") == (1, 2, 3))
    check("multi 1x02-03", _parse_sxxeyy("Show.1x02-03.mkv") == (1, 2, 3))
    check("no tag -> None", _parse_sxxeyy("Some.Movie.2020.mkv") is None)
    check("1920x1080 not misparsed", _parse_sxxeyy("Clip.1920x1080.mkv") is None)
    check(
        "eng + forced",
        _parse_subtitle_tags("Movie.2020.eng.forced.srt") == ("en", ["forced"]),
    )
    check(
        "fre + sdh",
        _parse_subtitle_tags("Show.S01E02.fre.sdh.ass") == ("fr", ["sdh"]),
    )
    check("default en", _parse_subtitle_tags("random.srt") == ("en", []))
    check(
        "english full name + SDH",
        _parse_subtitle_tags("Movie.English.SDH.srt") == ("en", ["sdh"]),
    )
    check("2-letter es", _parse_subtitle_tags("Movie.es.srt")[0] == "es")
    check("'hi' not a language", _parse_subtitle_tags("Movie.HI.srt") == ("en", []))


def test_movie_organizer():
    print("\n[2] movie + 2 subtitles")
    tmp = fresh_tmp("movie")
    try:
        staging = os.path.join(tmp, "staging")
        movie_target = os.path.join(tmp, "Movies")
        tv_target = os.path.join(tmp, "Shows")
        make_file(os.path.join(staging, "Big.Buck.Bunny.2010.1080p.BluRay.mkv"), FAKE_VIDEO_BYTES)
        make_file(os.path.join(staging, "Big.Buck.Bunny.2010.English.srt"), FAKE_SUB_BYTES)
        make_file(os.path.join(staging, "Big.Buck.Bunny.2010.eng.forced.srt"), FAKE_SUB_BYTES)

        result = organize_media_files(
            staging, movie_target, tv_target, "Big Buck Bunny", 2010, "movie"
        )
        base = os.path.join(movie_target, "Big Buck Bunny (2010)")
        check("video path", os.path.isfile(os.path.join(base, "Big Buck Bunny (2010).mkv")))
        check("en sub", os.path.isfile(os.path.join(base, "Big Buck Bunny (2010).en.srt")))
        check(
            "en.forced sub",
            os.path.isfile(os.path.join(base, "Big Buck Bunny (2010).en.forced.srt")),
        )
        check("staging removed", not os.path.exists(staging))
        check("3 organized paths", len(result) == 3, f"got {result}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_season_pack():
    print("\n[3] season pack: 3 episodes + subs + unpairable sub")
    tmp = fresh_tmp("tv")
    try:
        staging = os.path.join(tmp, "staging")
        movie_target = os.path.join(tmp, "Movies")
        tv_target = os.path.join(tmp, "Shows")
        for ep in ("S01E01", "S01E02", "S01E03"):
            make_file(os.path.join(staging, f"Breaking.Bad.{ep}.720p.mkv"), FAKE_VIDEO_BYTES)
        make_file(os.path.join(staging, "Breaking.Bad.S01E01.en.srt"), FAKE_SUB_BYTES)
        make_file(os.path.join(staging, "Breaking.Bad.S01E02.fre.srt"), FAKE_SUB_BYTES)
        make_file(os.path.join(staging, "totally.unpairable.srt"), FAKE_SUB_BYTES)

        result = organize_media_files(
            staging, movie_target, tv_target, "Breaking Bad", 2008, "tv"
        )
        season = os.path.join(tv_target, "Breaking Bad (2008)", "Season 01")
        for ep in ("S01E01", "S01E02", "S01E03"):
            check(
                f"episode {ep}",
                os.path.isfile(os.path.join(season, f"Breaking Bad - {ep}.mkv")),
            )
        check("en sub", os.path.isfile(os.path.join(season, "Breaking Bad - S01E01.en.srt")))
        check("fr sub", os.path.isfile(os.path.join(season, "Breaking Bad - S01E02.fr.srt")))
        # Never-delete policy: unpairable subs are skipped, not destroyed,
        # so the staging dir must still hold the original unpairable file.
        check(
            "unpairable sub preserved in staging",
            os.path.isfile(os.path.join(staging, "totally.unpairable.srt")),
        )
        check("staging kept (unpairable sub remains)", os.path.exists(staging))
        check("5 organized paths", len(result) == 5, f"got {result}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_multi_episode():
    print("\n[4] multi-episode file + subtitle")
    tmp = fresh_tmp("multi")
    try:
        staging = os.path.join(tmp, "staging")
        movie_target = os.path.join(tmp, "Movies")
        tv_target = os.path.join(tmp, "Shows")
        make_file(os.path.join(staging, "Fargo.S02E02-E03.1080p.mkv"), FAKE_VIDEO_BYTES)
        make_file(os.path.join(staging, "Fargo.S02E02-E03.eng.srt"), FAKE_SUB_BYTES)

        result = organize_media_files(staging, movie_target, tv_target, "Fargo", 2014, "tv")
        season = os.path.join(tv_target, "Fargo (2014)", "Season 02")
        check(
            "multi-ep video",
            os.path.isfile(os.path.join(season, "Fargo - S02E02-E03.mkv")),
        )
        check(
            "multi-ep sub",
            os.path.isfile(os.path.join(season, "Fargo - S02E02-E03.en.srt")),
        )
        check("staging removed", not os.path.exists(staging))
        check("2 organized paths", len(result) == 2, f"got {result}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_videos():
    print("\n[5] staging with no videos -> nothing organized, staging kept")
    tmp = fresh_tmp("empty")
    try:
        staging = os.path.join(tmp, "staging")
        make_file(os.path.join(staging, "notes.txt"), b"hello")
        result = organize_media_files(
            staging, os.path.join(tmp, "Movies"), os.path.join(tmp, "Shows"), "X", None, "movie"
        )
        check("returns empty list", result == [])
        check("staging kept", os.path.exists(staging))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_parsers()
    test_movie_organizer()
    test_season_pack()
    test_multi_episode()
    test_no_videos()
    print()
    if failures:
        print(f"❌ {len(failures)} check(s) failed: {failures}")
        sys.exit(1)
    print("✅ All organizer tests passed.")