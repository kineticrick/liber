import json
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from liber.interview import prompts
from liber.interview.brain import Brain, Opening
from liber.interview.brief import build_brief
from liber.interview.live import LiveError
from liber.interview.session import InterviewSession
from liber.interview.settings import InterviewSettings
from liber.interview.web import create_app
from voice_fakes import FakeLiveClient, FakeLLM, text_reply

TOKEN = "tok123"
H = {"X-Liber-Token": TOKEN}


def responder(call):
    if call["system"] == prompts.NOTES_SYSTEM:
        return text_reply("## New facts\n- x [00:01]")
    return text_reply(json.dumps({"hint": "h", "coverage": "c"}))


def make(vault, tmp_path, live=None):
    settings = InterviewSettings("Rick")
    brain = Brain(FakeLLM(responder), settings, vault, build_brief(vault, "my career"))
    live = live or FakeLiveClient()
    session = InterviewSession(vault=vault, settings=settings, brain=brain, live=live,
                               opening=Opening("my career", "", "Q?"), workdir=tmp_path / "w",
                               now=lambda: datetime(2026, 10, 2, 9, 0), watch_interval_s=3600, close_wait_s=0.2)
    return session, live


def test_static_assets_without_token(vault, tmp_path):
    session, _ = make(vault, tmp_path)
    with TestClient(create_app(session, TOKEN)) as c:
        assert "liber interview" in c.get("/").text
        assert c.get("/app.js").headers["content-type"].startswith("text/javascript")
        assert c.get("/style.css").status_code == 200


@pytest.mark.parametrize("method, path", [("post", "/api/session"), ("post", "/api/resume"), ("post", "/api/note"),
                                          ("post", "/api/hold"), ("get", "/api/status"), ("post", "/api/end")])
def test_api_requires_token(vault, tmp_path, method, path):
    session, _ = make(vault, tmp_path)
    with TestClient(create_app(session, TOKEN)) as c:
        assert getattr(c, method)(path).status_code == 403
        assert getattr(c, method)(path, headers={"X-Liber-Token": "wrong"}).status_code == 403


def test_session_flow(vault, tmp_path):
    session, live = make(vault, tmp_path)
    with TestClient(create_app(session, TOKEN)) as c:
        r = c.post("/api/session", json={"sdp": "OFFER"}, headers=H)
        assert r.status_code == 201 and r.json() == {"sdp": "ANSWER-live_1"}
        assert live.created[0]["sdp"] == "OFFER"
        assert c.post("/api/session", json={"sdp": ""}, headers=H).status_code == 400
        assert c.post("/api/note", json={"text": "typed"}, headers=H).json() == {"ok": True}
        assert any(t.text == "typed" for t in session.assembler.turns())
        assert c.post("/api/note", json={"text": " "}, headers=H).status_code == 409
        status = c.get("/api/status", headers=H).json()
        assert status["state"] == "live" and status["topic"] == "my career"
        assert c.post("/api/resume", json={"sdp": "X"}, headers=H).status_code == 409
        end = c.post("/api/end", headers=H).json()
        assert end["transcript"] is None  # no speech, so no files
        assert c.get("/api/status", headers=H).json()["state"] == "done"


def test_openai_failure_is_502_and_key_never_leaks(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    session, _ = make(vault, tmp_path, FakeLiveClient(fail=LiveError("OpenAI refused the voice session (403): no access")))
    with TestClient(create_app(session, TOKEN)) as c:
        r = c.post("/api/session", json={"sdp": "OFFER"}, headers=H)
        assert r.status_code == 502 and "403" in r.json()["error"]
        for path in ("/", "/app.js"):
            assert "sk-secret-value" not in c.get(path).text
        assert "sk-secret-value" not in r.text


def test_page_has_controls(vault, tmp_path):
    session, _ = make(vault, tmp_path)
    with TestClient(create_app(session, TOKEN)) as c:
        html = c.get("/").text
        for element in ('id="start"', 'id="hold"', 'id="end"', 'id="resume"', 'id="note-form"', 'id="captions"'):
            assert element in html
        js = c.get("/app.js").text
        for needle in ("echoCancellation: true", '"oai-events"', "/api/status", "X-Liber-Token", "history.replaceState"):
            assert needle in js


def test_non_ascii_token_is_403_not_500(vault, tmp_path):
    session, _ = make(vault, tmp_path)
    with TestClient(create_app(session, TOKEN)) as c:
        r = c.get("/api/status", headers={"X-Liber-Token": "tök".encode("latin-1")})
        assert r.status_code == 403


def test_untrusted_host_rejected(vault, tmp_path):
    session, _ = make(vault, tmp_path)
    with TestClient(create_app(session, TOKEN)) as c:
        assert c.get("/", headers={"Host": "evil.example"}).status_code == 400
        assert c.get("/", headers={"Host": "localhost:8000"}).status_code == 200
