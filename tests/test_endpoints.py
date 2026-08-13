"""Integration tests for coach app endpoints beyond /api/upload.

Covers /api/health, /api/report (happy / 404-missing / 404-no-cards),
and /api/agent/chat (happy / 400-empty). Mocks the model + clip layer
so no CPA, ffmpeg, or real data is touched.
"""
import app as app_module

_client = app_module.app.test_client()


# --- /api/health ---

def test_health_ok():
    r = _client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


# --- /api/report ---

def test_report_returns_json(monkeypatch):
    monkeypatch.setattr(app_module, "get_clip", lambda cid: {"id": cid})
    monkeypatch.setattr(app_module, "_cfg", lambda: {})
    monkeypatch.setattr(
        app_module.report_mod, "build_report",
        lambda cid, mode, cfg: {"markdown": "# x", "generated_by": "template"},
    )
    r = _client.get("/api/report/c1")
    assert r.status_code == 200
    assert r.get_json()["markdown"] == "# x"


def test_report_404_missing_clip(monkeypatch):
    monkeypatch.setattr(app_module, "get_clip", lambda cid: None)
    r = _client.get("/api/report/c1")
    assert r.status_code == 404


def test_report_404_no_cards(monkeypatch):
    monkeypatch.setattr(app_module, "get_clip", lambda cid: {"id": cid})
    monkeypatch.setattr(app_module, "_cfg", lambda: {})
    monkeypatch.setattr(app_module.report_mod, "build_report", lambda cid, mode, cfg: None)
    r = _client.get("/api/report/c1")
    assert r.status_code == 404
    assert "no cards" in r.get_json()["error"]


# --- /api/agent/chat ---

def test_agent_chat_happy(monkeypatch):
    monkeypatch.setattr(
        app_module.agent, "chat",
        lambda cid, msg, hist: {"reply": "hi", "tool_trace": []},
    )
    r = _client.post("/api/agent/chat", json={"clip_id": "c1", "message": "hi"})
    assert r.status_code == 200
    assert r.get_json()["reply"] == "hi"


def test_agent_chat_empty_message_400():
    r = _client.post("/api/agent/chat", json={"message": "   "})
    assert r.status_code == 400
