import stat
from datetime import date

import pytest
from typer.testing import CliRunner

from liber.cli import app
from liber.errors import LiberError
from liber.server.tokens import TokenInfo, TokenStore

TODAY = date(2026, 10, 1)


def test_create_list_match_revoke(tmp_path):
    store = TokenStore(tmp_path / "tokens.json")
    token = store.create("voice-backend", TODAY)
    assert len(token) >= 40
    assert store.list() == [TokenInfo("voice-backend", "2026-10-01")]
    assert store.match(token) == "voice-backend"
    assert store.match(token + "x") is None
    assert token not in (tmp_path / "tokens.json").read_text(encoding="utf-8")
    assert stat.S_IMODE((tmp_path / "tokens.json").stat().st_mode) == 0o600
    store.revoke("voice-backend")
    assert store.match(token) is None
    assert store.list() == []


def test_match_sees_changes_made_by_another_store_instance(tmp_path):
    path = tmp_path / "tokens.json"
    reader = TokenStore(path)
    token = TokenStore(path).create("laptop", TODAY)
    assert reader.match(token) == "laptop"
    TokenStore(path).revoke("laptop")
    assert reader.match(token) is None


@pytest.mark.parametrize("name", ["", "Has Space", "UPPER", "-lead", "a" * 41, "x/y"])
def test_bad_names(tmp_path, name):
    with pytest.raises(LiberError, match="lowercase"):
        TokenStore(tmp_path / "t.json").create(name, TODAY)


def test_duplicate_and_unknown(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    store.create("laptop", TODAY)
    with pytest.raises(LiberError, match="already exists"):
        store.create("laptop", TODAY)
    with pytest.raises(LiberError, match="no token named"):
        store.revoke("nope")


def test_empty_store(tmp_path):
    store = TokenStore(tmp_path / "missing.json")
    assert store.list() == [] and store.match("anything") is None


def test_corrupt_store(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(LiberError, match="unreadable"):
        TokenStore(path).list()


def test_default_location(isolated_env):
    assert TokenStore.default().path == isolated_env / ".local" / "share" / "liber" / "service-tokens.json"


def test_cli_token_lifecycle():
    runner = CliRunner()
    created = runner.invoke(app, ["token", "create", "laptop"])
    assert created.exit_code == 0, created.output
    token = created.stdout.strip().splitlines()[0]
    assert TokenStore.default().match(token) == "laptop"
    listed = runner.invoke(app, ["token", "list"])
    assert listed.exit_code == 0 and "laptop" in listed.output
    assert runner.invoke(app, ["token", "revoke", "laptop"]).exit_code == 0
    again = runner.invoke(app, ["token", "revoke", "laptop"])
    assert again.exit_code == 1 and "error:" in again.output
    assert "no service tokens" in runner.invoke(app, ["token", "list"]).output


def test_save_replaces_a_wide_mode_file(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text('{"tokens": []}', encoding="utf-8")
    path.chmod(0o644)
    store = TokenStore(path)
    store.create("test-token", TODAY)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    tmp_files = list(tmp_path.glob(".*tmp"))
    assert not tmp_files, f"Expected no temp files, but found: {tmp_files}"
