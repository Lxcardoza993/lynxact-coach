"""Unit tests for coach.claude — live event-card generation.

_call_model is mocked at the requests layer; live_events is driven through
mocked load_baked / get_upload / _call_model / speechmatics.stream_wav /
report.persist_card / time.sleep so every branch runs hermetically.
"""

from coach import claude

_BAKED = {
    "title": "Final", "duration": 20,
    "cv_context": {"fusion_label": "vote"},
    "transcript": [{"t": 0.0, "text": "kickoff"}, {"t": 7.0, "text": "shot"}],
}


def _kind(frame):
    return frame.split("data:", 1)[0].strip().split(" ", 1)[1]


# --- _cfg ---

def test_cfg_reads_env(monkeypatch):
    monkeypatch.setenv("COACH_API_BASE", "https://gw.example.com/v1")
    monkeypatch.setenv("COACH_API_KEY", "sk-x")
    monkeypatch.setenv("COACH_MODEL", "gpt-z")
    assert claude._cfg() == {
        "base": "https://gw.example.com/v1", "key": "sk-x", "model": "gpt-z"
    }


def test_cfg_defaults(monkeypatch):
    for v in ("COACH_API_BASE", "COACH_API_KEY", "COACH_MODEL"):
        monkeypatch.delenv(v, raising=False)
    cfg = claude._cfg()
    assert cfg["base"] == "http://127.0.0.1:8317/v1"
    assert cfg["key"] == ""
    assert cfg["model"] == "gpt-5.5"


# --- _parse_cards: an object-shaped line that is NOT valid JSON ---

def test_parse_cards_skips_invalid_json_object_line():
    text = '{"t":1,"type":"goal","analysis":"x"}\n{ broken\n{"t":2,"type":"save","analysis":"y"}'
    assert len(claude._parse_cards(text)) == 2   # middle { broken skipped, not crash


# --- _call_model (mocked http) ---

class _FakeResp:
    def __init__(self, content, status=200):
        self._content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise claude.requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_call_model_parses_cards(monkeypatch):
    captured = {}

    def fake_post(url, *, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _FakeResp('{"t":1,"type":"goal","analysis":"top corner"}\nnoise')

    monkeypatch.setattr(claude.requests, "post", fake_post)
    cards = claude._call_model({"base": "https://gw/v1", "key": "k", "model": "m"}, "prompt")
    assert len(cards) == 1
    assert cards[0]["type"] == "goal"
    assert captured["json"]["messages"][0]["role"] == "system"        # SYSTEM_PROMPT
    assert captured["json"]["messages"][1]["content"] == "prompt"
    assert captured["timeout"] == 90


# --- _emit_window_cards ---

def test_emit_window_cards_retries_on_empty_and_sets_defaults(monkeypatch):
    calls = {"n": 0}

    def fake_call(cfg, prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return []                       # empty first → retry once
        return [{"type": "burst", "analysis": "accel"}]   # no t / speculative

    monkeypatch.setattr(claude, "_call_model", fake_call)
    emitted = set()
    cards = claude._emit_window_cards(
        {"base": "x", "key": "k", "model": "m"}, "Final", 90, None,
        10.0, 16.0, [{"t": 10.0, "text": "go"}], emitted,
    )
    assert calls["n"] == 2
    assert cards[0]["t"] == 16.0                 # setdefault t = win_end
    assert cards[0]["speculative"] is True      # setdefault speculative
    assert "burst" in emitted


# --- live_events ---

def test_live_events_baked_happy(monkeypatch):
    monkeypatch.setattr(claude, "load_baked", lambda cid: _BAKED)
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    monkeypatch.setattr(claude, "_call_model", lambda cfg, p: [{"type": "goal", "analysis": "x", "t": 5.0}])
    monkeypatch.setattr(claude.time, "sleep", lambda s: None)
    import coach.report as report
    persisted = []
    monkeypatch.setattr(report, "persist_card", lambda cid, c: persisted.append(c))
    frames = list(claude.live_events("clip1"))
    kinds = [_kind(f) for f in frames]
    assert kinds[0] == "meta"
    assert kinds[-1] == "done"
    assert "transcript" in kinds and "card" in kinds
    assert persisted                       # cards persisted to disk


def test_live_events_no_key_baked_falls_back_to_replay(monkeypatch):
    monkeypatch.setattr(claude, "load_baked", lambda cid: _BAKED)
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "", "model": "m"})
    from coach import stream
    def fake_replay(cid):
        yield stream.sse("meta", {"clip": cid})
    monkeypatch.setattr(stream, "replay_events", fake_replay)
    frames = list(claude.live_events("clip1"))
    assert any("meta" in f for f in frames)


def test_live_events_uploaded_no_audio(monkeypatch):
    monkeypatch.setattr(claude, "load_baked", lambda cid: None)
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    from coach import clips
    monkeypatch.setattr(clips, "get_upload", lambda cid: {"title": "up"})   # no audio_wav
    frames = list(claude.live_events("clip1"))
    assert any("audio not extracted" in f for f in frames)


def test_live_events_uploaded_no_speechmatics_key(monkeypatch):
    monkeypatch.setattr(claude, "load_baked", lambda cid: None)
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    monkeypatch.delenv("SPEECHMATICS_API_KEY", raising=False)
    from coach import clips
    monkeypatch.setattr(clips, "get_upload", lambda cid: {"title": "up", "audio_wav": "/x.wav"})
    frames = list(claude.live_events("clip1"))
    assert any("SPEECHMATICS_API_KEY" in f for f in frames)


def test_live_events_uploaded_no_coach_key(monkeypatch):
    monkeypatch.setattr(claude, "load_baked", lambda cid: None)
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "", "model": "m"})
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "sm")
    from coach import clips
    monkeypatch.setattr(clips, "get_upload", lambda cid: {"title": "up", "audio_wav": "/x.wav"})
    frames = list(claude.live_events("clip1"))
    assert any("COACH_API_KEY" in f for f in frames)


def test_live_events_uploaded_speechmatics_path(monkeypatch):
    monkeypatch.setattr(claude, "load_baked", lambda cid: None)
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "sm")
    from coach import clips, speechmatics
    monkeypatch.setattr(clips, "get_upload", lambda cid: {"title": "up", "duration": 30, "audio_wav": "/x.wav"})
    def fake_stream(wav, lang=None):
        yield {"t": 0.0, "text": "kickoff"}
        yield {"t": 7.0, "text": "shot."}      # 7-0>=6 → flush window
        yield {"t": 9.0, "text": "goal"}       # 9-7<6 → trailing flush at loop end
    monkeypatch.setattr(speechmatics, "stream_wav", fake_stream)
    monkeypatch.setattr(claude, "_call_model", lambda cfg, p: [{"type": "goal", "analysis": "x"}])
    monkeypatch.setattr(claude.time, "sleep", lambda s: None)
    import coach.report as report
    monkeypatch.setattr(report, "persist_card", lambda cid, c: None)
    frames = list(claude.live_events("clip1"))
    kinds = [_kind(f) for f in frames]
    assert "meta" in kinds and "done" in kinds
    assert "transcript" in kinds and "card" in kinds


def test_live_events_model_failure_yields_error_then_done(monkeypatch):
    monkeypatch.setattr(claude, "load_baked", lambda cid: _BAKED)
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    def boom(cfg, p):
        raise RuntimeError("boom")
    monkeypatch.setattr(claude, "_call_model", boom)
    monkeypatch.setattr(claude.time, "sleep", lambda s: None)
    import coach.report as report
    monkeypatch.setattr(report, "persist_card", lambda cid, c: None)
    frames = list(claude.live_events("clip1"))
    assert any("live failed" in f for f in frames)
    assert frames[-1].startswith("event: done")


def test_live_events_non_numeric_speed_falls_back(monkeypatch):
    # Same guard gap as stream.replay_events (R93): a non-numeric REPLAY_SPEED
    # raised ValueError at the speed assignment, OUTSIDE live_events' try/except
    # (which starts later at the per-window loop), so /api/stream -> 500. The
    # docstring promises a demo that "can never die on stage"; float() is
    # wrapped -> 1.0 (matching the existing 0-guard fallback).
    monkeypatch.setattr(claude, "load_baked", lambda cid: _BAKED)
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    monkeypatch.setattr(claude, "_call_model", lambda cfg, p: [{"type": "goal", "analysis": "x", "t": 5.0}])
    monkeypatch.setattr(claude.time, "sleep", lambda s: None)
    import coach.report as report
    monkeypatch.setattr(report, "persist_card", lambda cid, c: None)
    monkeypatch.setenv("REPLAY_SPEED", "fast")
    frames = list(claude.live_events("clip1"))   # must not raise
    kinds = [_kind(f) for f in frames]
    assert kinds[0] == "meta"
    assert kinds[-1] == "done"


def test_live_events_speechmatics_ends_on_boundary_no_trailing(monkeypatch):
    # When the Speechmatics stream's last line completes a 6s window (flushed
    # inside the loop), win_lines is empty at the end -> the trailing-flush
    # branch is skipped (no spurious trailing card). Covers `if win_lines:` False.
    monkeypatch.setattr(claude, "load_baked", lambda cid: None)
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "sm")
    from coach import clips, speechmatics
    monkeypatch.setattr(clips, "get_upload", lambda cid: {"title": "up", "duration": 30, "audio_wav": "/x.wav"})

    def fake_stream(wav, lang=None):
        yield {"t": 0.0, "text": "kickoff"}
        yield {"t": 7.0, "text": "shot."}      # 7-0>=6 -> flush; loop ends -> win_lines=[]

    monkeypatch.setattr(speechmatics, "stream_wav", fake_stream)
    monkeypatch.setattr(claude, "_call_model", lambda cfg, p: [{"type": "goal", "analysis": "x"}])
    monkeypatch.setattr(claude.time, "sleep", lambda s: None)
    import coach.report as report
    monkeypatch.setattr(report, "persist_card", lambda cid, c: None)
    frames = list(claude.live_events("clip1"))
    kinds = [_kind(f) for f in frames]
    assert kinds[-1] == "done"
    assert kinds.count("card") == 1            # one flushed window, no trailing card
