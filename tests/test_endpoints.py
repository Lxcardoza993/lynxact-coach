"""Integration tests for coach app endpoints beyond /api/upload.

Covers /api/health, /api/report (happy / 404-missing / 404-no-cards),
/api/agent/chat (happy / 400-empty), /video (200 / 206 range / 206 suffix
range / 404), and /api/stream (404 / replay SSE). Mocks the model + clip
layer so no CPA, ffmpeg, or real data is touched.
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


# --- /video/<path:fname> (mp4 range streaming via range_stream) ---
# range_stream basenames fname; video_dir returns a fixed dir chosen by
# registry lookup (get_upload basenames clip_id) — neither lets ../ escape.

def test_video_serves_clip(monkeypatch, tmp_path):
    (tmp_path / "clip1.mp4").write_bytes(b"abcdefghij")
    monkeypatch.setattr(app_module, "video_dir", lambda cid: str(tmp_path))
    r = _client.get("/video/clip1.mp4")
    assert r.status_code == 200
    assert r.mimetype == "video/mp4"
    assert r.data == b"abcdefghij"
    assert r.headers["Accept-Ranges"] == "bytes"


def test_video_range_206(monkeypatch, tmp_path):
    (tmp_path / "clip1.mp4").write_bytes(b"abcdefghij")
    monkeypatch.setattr(app_module, "video_dir", lambda cid: str(tmp_path))
    r = _client.get("/video/clip1.mp4", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.data == b"cdef"
    assert r.headers["Content-Range"] == "bytes 2-5/10"


def test_video_suffix_range_206(monkeypatch, tmp_path):
    # RFC 7233 suffix range bytes=-N returns the LAST N bytes (R33 fix),
    # exercised end-to-end through the real route + range_stream.
    (tmp_path / "clip1.mp4").write_bytes(b"abcdefghij")
    monkeypatch.setattr(app_module, "video_dir", lambda cid: str(tmp_path))
    r = _client.get("/video/clip1.mp4", headers={"Range": "bytes=-3"})
    assert r.status_code == 206
    assert r.data == b"hij"


def test_video_missing_404(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "video_dir", lambda cid: str(tmp_path))
    r = _client.get("/video/nope.mp4")
    assert r.status_code == 404


# --- /api/stream/<clip_id> (SSE event stream) ---

def test_stream_404_missing(monkeypatch):
    monkeypatch.setattr(app_module, "get_clip", lambda cid: None)
    r = _client.get("/api/stream/c1")
    assert r.status_code == 404


def test_stream_replay_ok(monkeypatch):
    monkeypatch.setattr(app_module, "get_clip", lambda cid: {"id": cid})

    def fake_stream(cid, mode):
        yield "data: hello\n\n"
        yield "data: done\n\n"

    monkeypatch.setattr(app_module, "sse_stream", fake_stream)
    r = _client.get("/api/stream/c1?mode=replay")
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    assert b"hello" in r.data
    assert r.headers["Cache-Control"] == "no-cache"
    assert r.headers["X-Accel-Buffering"] == "no"
