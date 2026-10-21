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
