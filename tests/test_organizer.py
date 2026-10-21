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
    _clean_release_title,
    _parse_sxxeyy,
    _parse_subtitle_tags,
    _show_name_mismatch,
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


def test_same_title_copies_are_preserved():
    print("\n[3] same title copies keep separate media files")
    tmp = fresh_tmp("copies")
    try:
        movie_target = os.path.join(tmp, "Movies")
        tv_target = os.path.join(tmp, "Shows")

        class FakeDB:
            def __init__(self):
                self.sources = []

            def register_source_file(self, **source):
                self.sources.append(source)

        db = FakeDB()
        movie_paths = []
        movie_subtitles = []
        for index, suffix in enumerate(("copy-a", "copy-b")):
            staging = os.path.join(tmp, f"movie-{index}")
            make_file(os.path.join(staging, f"The.Film.2020.{index}.mkv"), bytes([index + 1]) * 20)
            make_file(os.path.join(staging, f"The.Film.2020.{index}.English.srt"), bytes([index + 5]) * 10)
            outputs = organize_media_files(
                staging, movie_target, tv_target, "The Film", 2020,
                db=db, copy_suffix=suffix,
            )
            movie_paths.extend(p for p in outputs if p.endswith(".mkv"))
            movie_subtitles.extend(p for p in outputs if p.endswith(".srt"))
        check("same-title movie releases have distinct paths", len(set(movie_paths)) == 2 and all(os.path.isfile(p) for p in movie_paths), movie_paths)
        check("movie subtitles follow their own versions", len(set(movie_subtitles)) == 2 and all(os.path.isfile(p) for p in movie_subtitles), movie_subtitles)
        check("both movie copies retain the same catalogue title", len(db.sources) == 2 and all(s["parsed_title"] == "The Film" for s in db.sources))
        check("the second movie filename keeps a recognizable version suffix", "copy-b" in os.path.basename(movie_paths[1]), movie_paths)

        episode_paths = []
        episode_subtitles = []
        for index, (suffix, release_tag) in enumerate((("episode-a", "WEB-DL"), ("episode-b", "1080p"))):
            staging = os.path.join(tmp, f"episode-{index}")
            make_file(os.path.join(staging, f"Example.Show.S01E01.{release_tag}.mkv"), bytes([index + 3]) * 20)
            make_file(os.path.join(staging, f"Example.Show.S01E01.{release_tag}.English.srt"), bytes([index + 7]) * 10)
            outputs = organize_media_files(
                staging, movie_target, tv_target, "Example Show", 2020,
                media_type="tv", copy_suffix=suffix,
            )
            episode_paths.extend(p for p in outputs if p.endswith(".mkv"))
            episode_subtitles.extend(p for p in outputs if p.endswith(".srt"))
        check("same-episode releases do not overwrite one another", len(set(episode_paths)) == 2 and all(os.path.isfile(p) for p in episode_paths), episode_paths)
        check("episode subtitles follow their own versions", len(set(episode_subtitles)) == 2 and all(os.path.isfile(p) for p in episode_subtitles), episode_subtitles)
        check("the episode tag remains parseable after the version suffix", all(_parse_sxxeyy(os.path.basename(p)) == (1, 1, None) for p in episode_paths), episode_paths)
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


def test_two_shows_no_cross_contamination():
    print("\n[6] two shows in ONE staging dir -> files land in their own show folders")
    # Simulates the fixed per-torrent flow: each torrent's staging dir is
    # separate; organizer must only touch the staging dir it was given.
    tmp = fresh_tmp("twoshows")
    try:
        movie_target = os.path.join(tmp, "Movies")
        tv_target = os.path.join(tmp, "Shows")

        staging_new_girl = os.path.join(tmp, "downloads", "id_101")
        staging_got = os.path.join(tmp, "downloads", "id_102")
        make_file(os.path.join(staging_new_girl, "New.Girl.S01E01.1080p.mkv"), FAKE_VIDEO_BYTES)
        make_file(os.path.join(staging_new_girl, "New.Girl.S01E02.1080p.mkv"), FAKE_VIDEO_BYTES)
        make_file(os.path.join(staging_got, "Game.of.Thrones.S01E01.1080p.mkv"), FAKE_VIDEO_BYTES)

        issues: list = []
        organize_media_files(
            staging_new_girl, movie_target, tv_target, "New Girl", 2011, "tv", issues=issues
        )
        ng_s1 = os.path.join(tv_target, "New Girl (2011)", "Season 01")
        check("New Girl S01E01", os.path.isfile(os.path.join(ng_s1, "New Girl - S01E01.mkv")))
        check("New Girl S01E02", os.path.isfile(os.path.join(ng_s1, "New Girl - S01E02.mkv")))
        # GoT files must NOT have been swept into the New Girl staging call
        check("GoT untouched by New Girl run", os.path.isfile(os.path.join(staging_got, "Game.of.Thrones.S01E01.1080p.mkv")))
        check("no GoT folder created", not os.path.exists(os.path.join(tv_target, "Game of Thrones (2011)")))

        issues_got: list = []
        organize_media_files(
            staging_got, movie_target, tv_target, "Game of Thrones", 2011, "tv", issues=issues_got
        )
        got_s1 = os.path.join(tv_target, "Game of Thrones (2011)", "Season 01")
        check("GoT S01E01", os.path.isfile(os.path.join(got_s1, "Game of Thrones - S01E01.mkv")))
        check("GoT title matched (no issue)", issues_got == [], f"got {issues_got}")
        check("GoT staging removed", not os.path.exists(staging_got))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mislabeled_warning():
    print("\n[7] mislabeled torrent detection")
    check(
        "GoT file vs 'New Girl' title",
        _show_name_mismatch("Game.of.Thrones.S01E01.1080p.mkv", "New Girl"),
    )
    check(
        "New Girl file vs 'New Girl' title",
        not _show_name_mismatch("New.Girl.S01E01.1080p.mkv", "New Girl"),
    )
    check(
        "movie: wrong title",
        _show_name_mismatch("The.Matrix.1999.1080p.mkv", "Big Buck Bunny"),
    )
    check(
        "movie: right title",
        not _show_name_mismatch("Big.Buck.Bunny.2010.1080p.BluRay.mkv", "Big Buck Bunny"),
    )
    # Conservative: unjudgeable names never fire
    check("no tokens -> no warning", not _show_name_mismatch("2160p.mkv", "New Girl"))
    # Seasons 5, 6 etc. with numeric noise tokens stripped
    check(
        "numeric noise ignored",
        not _show_name_mismatch("Show.5.1080p.S01E01.mkv", "Show"),
    )
    check("release noise ignored", _show_name_mismatch("YTS.Rip.S01E01.mkv", "Breaking Bad"))

    # End-to-end: issues list is populated via the issues kwarg
    tmp = fresh_tmp("mislabeled")
    try:
        staging = os.path.join(tmp, "staging")
        make_file(os.path.join(staging, "Game.of.Thrones.S01E01.1080p.mkv"), FAKE_VIDEO_BYTES)
        issues: list = []
        organize_media_files(
            staging, os.path.join(tmp, "Movies"), os.path.join(tmp, "Shows"),
            "New Girl", 2011, "tv", issues=issues,
        )
        check("issue reported", len(issues) == 1 and "mislabeled" in issues[0], f"got {issues}")
        # Never-delete policy: still organized under the requested title
        season = os.path.join(os.path.join(tmp, "Shows"), "New Girl (2011)", "Season 01")
        check(
            "organized anyway (never deleted)",
            os.path.isfile(os.path.join(season, "New Girl - S01E01.mkv")),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_clean_release_title():
    print("\n[8] _clean_release_title: torrent release names → clean titles")
    check(
        "TV with episode tag + tags",
        _clean_release_title("New Girl S05E12 D-Day 1080p DSNP WEB-DL DDP5 1 H 264-MiU") == "New Girl",
    )
    check(
        "TV with episode title",
        _clean_release_title("Game of Thrones S01E01 Winter Is Coming 1080p BluRay x264-OFT")
        == "Game of Thrones",
    )
    check(
        "movie dotted",
        _clean_release_title("They.Live.1988.REMASTERED.1080p.BluRay.H264.AAC-GoodFilms")
        == "They Live",
    )
    check(
        "plain title unchanged",
        _clean_release_title("Big Buck Bunny") == "Big Buck Bunny",
    )
    check(
        "dots cleaned",
        _clean_release_title("Big.Buck.Bunny.2010.1080p.BluRay") == "Big Buck Bunny",
    )
    check(
        "numeric sequel suffix remains part of the title",
        _clean_release_title("Sample.Film.2.2025.1080p.WEB-DL.DDP5.1-GROUP") == "Sample Film 2",
    )
    check(
        "audio codec tags still stop title parsing",
        _clean_release_title("A Movie DDP5.1-GROUP") == "A Movie",
    )
    check(
        "never empty (fallback)",
        _clean_release_title("1080p.x264") == "1080p.x264",
    )
    check(
        "1x02 style",
        _clean_release_title("Fargo 2x01 720p") == "Fargo",
    )


def test_numbered_title_organizes_under_its_own_title():
    print("\n[9] numbered title download and library folder")
    tmp = fresh_tmp("numbered_title")
    try:
        staging = os.path.join(tmp, "staging")
        movie_target = os.path.join(tmp, "Movies")
        title = _clean_release_title("Sample.Film.2.2025.1080p.WEB-DL.DDP5.1-GROUP")
        make_file(os.path.join(staging, "Sample.Film.2.2025.1080p.WEB-DL.mkv"), FAKE_VIDEO_BYTES)
        outputs = organize_media_files(
            staging, movie_target, os.path.join(tmp, "Shows"), title, 2025,
        )
        expected = os.path.join(movie_target, "Sample Film 2 (2025)", "Sample Film 2 (2025).mkv")
        check("the numeric sequel suffix is preserved in the folder and filename", title == "Sample Film 2" and expected in outputs and os.path.isfile(expected), outputs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_parsers()
    test_movie_organizer()
    test_same_title_copies_are_preserved()
    test_season_pack()
    test_multi_episode()
    test_no_videos()
    test_two_shows_no_cross_contamination()
    test_mislabeled_warning()
    test_clean_release_title()
    test_numbered_title_organizes_under_its_own_title()
    print()
    if failures:
        print(f"❌ {len(failures)} check(s) failed: {failures}")
        sys.exit(1)
    print("✅ All organizer tests passed.")
