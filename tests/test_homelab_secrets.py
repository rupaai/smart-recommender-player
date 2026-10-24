import os

from services import homelab_secrets


def test_loads_secret_files(tmp_path, monkeypatch):
    (tmp_path / "TMDB_API_KEY").write_text("tmdb-value")
    (tmp_path / "ADMIN_TOKEN").write_text("admin-value")
    (tmp_path / ".hidden.tmp").write_text("x")
    (tmp_path / "bad-name").write_text("x")
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    assert homelab_secrets.load(str(tmp_path)) == ["ADMIN_TOKEN", "TMDB_API_KEY"]
    assert os.environ["TMDB_API_KEY"] == "tmdb-value"
    assert os.environ["ADMIN_TOKEN"] == "admin-value"
    assert "bad-name" not in os.environ


def test_environment_wins(tmp_path, monkeypatch):
    (tmp_path / "TMDB_API_KEY").write_text("from-file")
    monkeypatch.setenv("TMDB_API_KEY", "from-env")

    assert homelab_secrets.load(str(tmp_path)) == []
    assert os.environ["TMDB_API_KEY"] == "from-env"


def test_missing_folder_is_fine(tmp_path):
    assert homelab_secrets.load(str(tmp_path / "nope")) == []


def test_line_breaks_and_spaces_are_trimmed(tmp_path, monkeypatch):
    (tmp_path / "ADMIN_TOKEN").write_text("admin-value\n")
    (tmp_path / "TMDB_API_KEY").write_text("  tmdb-value \r\n")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)

    homelab_secrets.load(str(tmp_path))
    assert os.environ["ADMIN_TOKEN"] == "admin-value"
    assert os.environ["TMDB_API_KEY"] == "tmdb-value"


def test_admin_token_from_a_file_with_a_line_break_is_accepted(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from services.admin_api import require_admin

    class Req:
        def __init__(self, auth):
            self.headers = {"authorization": auth}

    monkeypatch.setenv("ADMIN_TOKEN", "admin-value\n")
    require_admin(Req("Bearer admin-value"))           # no error
    try:
        require_admin(Req("Bearer wrong"))
        raise AssertionError("a wrong token must be refused")
    except HTTPException as e:
        assert e.status_code == 401


if __name__ == "__main__":
    # `make test` runs each file as a script: run the tests with a small stand-in for pytest's fixtures.
    import tempfile
    from pathlib import Path

    class MonkeyPatch:
        def __init__(self):
            self.saved = {}

        def setenv(self, k, v):
            self.saved.setdefault(k, os.environ.get(k))
            os.environ[k] = v

        def delenv(self, k, raising=True):
            self.saved.setdefault(k, os.environ.get(k))
            os.environ.pop(k, None)

        def undo(self):
            for k, v in self.saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            mp = MonkeyPatch()
            try:
                args = fn.__code__.co_varnames[:fn.__code__.co_argcount]
                kwargs = {"tmp_path": Path(tempfile.mkdtemp()), "monkeypatch": mp}
                fn(**{a: kwargs[a] for a in args})
            finally:
                mp.undo()
            print(f"ok {name}")
