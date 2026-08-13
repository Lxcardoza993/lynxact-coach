"""Unit tests for coach.stream — SSE timeline engine.

replay_events is a generator that interleaves baked transcript + card items,
sorts by (t, kind), sleeps scaled gaps, and emits meta/.../done SSE frames.
load_baked + time.sleep are monkeypatched so the generator runs fast and
deterministically against a synthetic baked dict.
"""


from coach import stream

# --- sse (pure) ---

def test_sse_formats_event_frame():
    frame = stream.sse("card", {"a": 1, "b": "x"})
    assert frame == 'event: card\ndata: {"a": 1, "b": "x"}\n\n'


def test_sse_keeps_unicode_unescaped():
    frame = stream.sse("meta", {"title": "进球"})
    assert "进球" in frame          # ensure_ascii=False
    assert frame.endswith("\n\n")


# --- replay_events ---

_BAKED = {
    "clip": "c1", "title": "Goal", "duration": 10,
    "cv_context": {"home": "red"},
    "transcript": [{"t": 0.0, "text": "kickoff"}, {"t": 4.0, "text": "shot"}],
    "events": [{"t": 4.0, "card": "shot"}],
}


def _kind(frame):
    return frame.split("data:", 1)[0].strip().split(" ", 1)[1]


def test_replay_events_interleaves_and_sleeps(monkeypatch):
    monkeypatch.setattr(stream, "load_baked", lambda cid: _BAKED)
    sleeps = []
    monkeypatch.setattr(stream.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setenv("REPLAY_SPEED", "2.0")
    frames = list(stream.replay_events("c1"))
    # meta first, done last
    assert _kind(frames[0]) == "meta"
    assert '"clip": "c1"' in frames[0]
    assert _kind(frames[-1]) == "done"
    # middle: sorted by (t, kind) — transcript before card at same t
    assert [_kind(f) for f in frames[1:-1]] == ["transcript", "transcript", "card"]
    # delays = (t-last)/speed: t0→0(skip), t4→4/2=2.0, t4→0(skip)
    assert sleeps == [2.0]


def test_replay_events_missing_baked_yields_nothing(monkeypatch):
    monkeypatch.setattr(stream, "load_baked", lambda cid: None)
    assert list(stream.replay_events("nope")) == []


def test_replay_events_zero_speed_falls_back_to_1(monkeypatch):
    # float("0") or 1.0 → 0.0 is falsy → speed 1.0 (guards div-by-zero)
    monkeypatch.setattr(stream, "load_baked", lambda cid: _BAKED)
    monkeypatch.setattr(stream.time, "sleep", lambda s: None)
    monkeypatch.setenv("REPLAY_SPEED", "0")
    frames = list(stream.replay_events("c1"))
    assert len(frames) >= 3               # meta + 2 items + done
    assert _kind(frames[-1]) == "done"


# --- sse_stream (dispatch) ---

def test_sse_stream_default_replay_mode(monkeypatch):
    def fake(cid):
        yield "evt1"
        yield "evt2"
    monkeypatch.setattr(stream, "replay_events", fake)
    monkeypatch.delenv("COACH_MODE", raising=False)
    assert list(stream.sse_stream("c1")) == ["evt1", "evt2"]


def test_sse_stream_explicit_mode_param_wins_over_env(monkeypatch):
    def fake(cid):
        yield "r"
    monkeypatch.setattr(stream, "replay_events", fake)
    monkeypatch.setenv("COACH_MODE", "live")          # env says live...
    assert list(stream.sse_stream("c1", mode="replay")) == ["r"]   # ...param wins


def test_sse_stream_live_mode_delegates_to_claude(monkeypatch):
    from coach import claude
    def fake_live(cid):
        yield "live1"
    monkeypatch.setattr(claude, "live_events", fake_live)
    out = list(stream.sse_stream("c1", mode="live"))
    assert out == ["live1"]
