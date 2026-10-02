import pytest
from datetime import date


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path_factory, request):
    """Keep every test away from the real home dir, user config, and git config."""
    if request.node.get_closest_marker("live"):
        return None  # live smoke tests deliberately use the owner's real config and keys
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("LIBER_VAULT", raising=False)
    gitconfig = home / ".gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("FASTMCP_CHECK_FOR_UPDATES", "off")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return home


@pytest.fixture
def fake_skills(tmp_path):
    root = tmp_path / "skills"
    for name in ("ingest", "review"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8")
    return root


@pytest.fixture
def vault(tmp_path, fake_skills):
    from liber.scaffold import init_vault

    path = tmp_path / "vault"
    init_vault(path, fake_skills, today=date(2026, 9, 30))
    return path


@pytest.fixture
def configured_vault(vault, monkeypatch):
    monkeypatch.setenv("LIBER_VAULT", str(vault))
    return vault


@pytest.fixture
def anyio_backend():
    return "asyncio"
