"""Integration tests for the /api/upload endpoint.

Covers input validation (no file / non-mp4) and the happy path — the
happy path mocks subprocess (ffprobe/ffmpeg) and register_upload so it
touches no real binaries and writes only to tmp_path.
"""
import io
from subprocess import CompletedProcess

import app as app_module

_client = app_module.app.test_client()


def test_upload_rejects_no_file():
    resp = _client.post("/api/upload", content_type="multipart/form-data")
    assert resp.status_code in (301, 302, 303)
    assert "no+file" in resp.headers["Location"]


def test_upload_rejects_non_mp4():
    resp = _client.post("/api/upload", data={
        "file": (io.BytesIO(b"x"), "clip.avi"),
    }, content_type="multipart/form-data")
    assert resp.status_code in (301, 302, 303)
    assert "only+mp4" in resp.headers["Location"]


class _FakeSubprocess:
    """Stand-in for the subprocess module — app only calls .run."""

    def run(self, *args, **kwargs):
        return CompletedProcess(args=[], returncode=0, stdout="10.5\n", stderr="")


def test_upload_happy_path_redirects_to_coach(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "AUDIO_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "subprocess", _FakeSubprocess())
    monkeypatch.setattr(
        app_module, "register_upload", lambda src, name, dur: "testclip-abc"
    )

    resp = _client.post("/api/upload", data={
        "file": (io.BytesIO(b"fake mp4 bytes"), "goal.mp4"),
    }, content_type="multipart/form-data")
    assert resp.status_code in (301, 302, 303)
    loc = resp.headers["Location"]
    assert "/coach/testclip-abc" in loc
    assert "mode=live" in loc


class _FailingProbe:
    """subprocess stand-in: the ffprobe call raises, the ffmpeg call
    succeeds — exercises the ffprobe except branch where duration falls
    back to 0."""

    def run(self, *args, **kwargs):
        first = args[0][0] if args and args[0] else ""
        if "ffprobe" in first:
            raise RuntimeError("probe boom")
        return CompletedProcess(args=[], returncode=0, stdout="0", stderr="")


def test_upload_ffprobe_failure_sets_duration_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "AUDIO_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "subprocess", _FailingProbe())
    captured = {}

    def cap_register(src, name, dur):
        captured["dur"] = dur
        return "testclip-abc"

    monkeypatch.setattr(app_module, "register_upload", cap_register)
    resp = _client.post("/api/upload", data={
        "file": (io.BytesIO(b"fake mp4"), "goal.mp4"),
    }, content_type="multipart/form-data")
    assert resp.status_code in (301, 302, 303)
    assert captured["dur"] == 0


class _FailingFfmpeg:
    """subprocess stand-in: ffprobe succeeds, the ffmpeg audio-extraction
    call raises — exercises that except branch; the route still redirects."""

    def run(self, *args, **kwargs):
        first = args[0][0] if args and args[0] else ""
        if "ffmpeg" in first:
            raise RuntimeError("ffmpeg boom")
        return CompletedProcess(args=[], returncode=0, stdout="12\n", stderr="")


def test_upload_ffmpeg_failure_still_redirects(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "AUDIO_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "subprocess", _FailingFfmpeg())
    monkeypatch.setattr(
        app_module, "register_upload", lambda src, name, dur: "testclip-abc"
    )
    resp = _client.post("/api/upload", data={
        "file": (io.BytesIO(b"fake mp4"), "goal.mp4"),
    }, content_type="multipart/form-data")
    assert resp.status_code in (301, 302, 303)
    assert "/coach/testclip-abc" in resp.headers["Location"]


def test_upload_too_large_413(monkeypatch):
    monkeypatch.setitem(app_module.app.config, "MAX_CONTENT_LENGTH", 10)
    resp = _client.post("/api/upload", data={
        "file": (io.BytesIO(b"x" * 50), "big.mp4"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 413
