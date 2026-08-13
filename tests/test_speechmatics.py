"""Unit tests for coach.speechmatics — real-time transcription client.

The websocket is faked via sys.modules injection (stream_wav does a lazy
`import websocket`); a scripted recv() feeds AddTranscript/Error frames to
the reader thread. speechmatics.time is swapped for a namespace whose sleep
calls the real sleep for ~20ms, so the reader thread runs ahead of the main
loop's non-blocking collect (deterministic without the real 190ms cadence).
"""
import json
import sys
import time
import types

import pytest

from coach import speechmatics


class _FakeWS:
    def __init__(self, messages):
        self._msgs = list(messages)
        self.sent = []
        self.closed = False

    def recv(self):
        if self._msgs:
            return self._msgs.pop(0)
        time.sleep(0.01)
        return '{"message":"EndOfTranscript"}'

    def send(self, data):
        self.sent.append(("text", data))

    def send_binary(self, data):
        self.sent.append(("bin", data))

    def close(self):
        self.closed = True


def _inject_ws(monkeypatch, ws):
    mod = types.ModuleType("websocket")
    mod.create_connection = lambda *a, **k: ws
    monkeypatch.setitem(sys.modules, "websocket", mod)


def _wav(tmp_path, pcm_bytes=6400):
    p = tmp_path / "clip.wav"
    p.write_bytes(b"\x00" * 44 + b"\x01" * pcm_bytes)   # 44-byte header + PCM
    return str(p)


def _transcript(*pairs):
    return json.dumps({"message": "AddTranscript", "results": [
        {"start_time": t, "alternatives": [{"content": w}]} for t, w in pairs
    ]})


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    real = time.sleep
    fake_time = types.SimpleNamespace(sleep=lambda s: real(0.02))
    monkeypatch.setattr(speechmatics, "time", fake_time)


def test_stream_wav_missing_key_raises(monkeypatch, tmp_path):
    _inject_ws(monkeypatch, _FakeWS([]))
    monkeypatch.delenv("SPEECHMATICS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SPEECHMATICS_API_KEY"):
        list(speechmatics.stream_wav(_wav(tmp_path)))


def test_stream_wav_yields_sentence_on_punctuation(monkeypatch, tmp_path):
    ws = _FakeWS([
        _transcript((0.0, "Goal"), (0.5, "scored.")),
        '{"message":"EndOfTranscript"}',
    ])
    _inject_ws(monkeypatch, ws)
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "test-key")
    out = list(speechmatics.stream_wav(_wav(tmp_path, 12800)))   # 2 chunks
    assert out == [{"t": 0.0, "text": "Goal scored."}]
    assert ws.closed is True


def test_stream_wav_flushes_trailing_buffer(monkeypatch, tmp_path):
    # words with no sentence-ending punctuation stay buffered until the end
    ws = _FakeWS([
        _transcript((1.0, "Nice"), (1.4, "pass")),
        '{"message":"EndOfTranscript"}',
    ])
    _inject_ws(monkeypatch, ws)
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "k")
    out = list(speechmatics.stream_wav(_wav(tmp_path)))
    assert out == [{"t": 1.0, "text": "Nice pass"}]
    assert ws.closed is True


def test_stream_wav_propagates_error_message(monkeypatch, tmp_path):
    ws = _FakeWS([json.dumps({"message": "Error", "error": "bad lang"})])
    _inject_ws(monkeypatch, ws)
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "k")
    with pytest.raises(RuntimeError):
        list(speechmatics.stream_wav(_wav(tmp_path)))
    assert ws.closed is True


def test_stream_wav_propagates_reader_exception(monkeypatch, tmp_path):
    class _BoomWS(_FakeWS):
        def recv(self):
            raise ValueError("recv boom")

    ws = _BoomWS([])
    _inject_ws(monkeypatch, ws)
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "k")
    with pytest.raises(ValueError, match="recv boom"):
        list(speechmatics.stream_wav(_wav(tmp_path)))
    assert ws.closed is True


def test_stream_wav_survives_close_failure(monkeypatch, tmp_path):
    # ws.close raising in the finally must be logged+swallowed, not crash the gen
    class _CloseBoomWS(_FakeWS):
        def close(self):
            raise OSError("close boom")

    ws = _CloseBoomWS([_transcript((0.0, "Hi.")), '{"message":"EndOfTranscript"}'])
    _inject_ws(monkeypatch, ws)
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "k")
    out = list(speechmatics.stream_wav(_wav(tmp_path)))
    assert out == [{"t": 0.0, "text": "Hi."}]


def test_stream_wav_skips_malformed_results(monkeypatch, tmp_path):
    # Remote result shape is untrusted: a result missing content (or with empty
    # alternatives) must be skipped, not KeyError the reader thread and kill the
    # whole transcription.
    mixed = json.dumps({"message": "AddTranscript", "results": [
        {"start_time": 0.0, "alternatives": [{"content": "Goal"}]},
        {"start_time": 0.5, "alternatives": [{}]},            # no content -> skip
        {"start_time": 0.7, "alternatives": []},              # empty -> skip
        {"start_time": 1.0, "alternatives": [{"content": "scored."}]},
    ]})
    ws = _FakeWS([mixed, '{"message":"EndOfTranscript"}'])
    _inject_ws(monkeypatch, ws)
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "k")
    out = list(speechmatics.stream_wav(_wav(tmp_path, 12800)))   # 2 chunks
    assert out == [{"t": 0.0, "text": "Goal scored."}]
    assert ws.closed is True


def test_stream_wav_ignores_unknown_message_kinds(monkeypatch, tmp_path):
    # The reader must ignore message kinds it doesn't handle (partials/info/etc),
    # not crash or hang — only AddTranscript/EndOfTranscript/Error are acted on.
    ws = _FakeWS([
        '{"message":"PartialTranscript","results":[]}',   # unknown -> ignored
        _transcript((0.0, "Goal.")),
        '{"message":"EndOfTranscript"}',
    ])
    _inject_ws(monkeypatch, ws)
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "k")
    out = list(speechmatics.stream_wav(_wav(tmp_path, 12800)))   # 2 chunks
    assert out == [{"t": 0.0, "text": "Goal."}]
    assert ws.closed is True


def test_stream_wav_skips_addtranscript_with_no_valid_words(monkeypatch, tmp_path):
    # An AddTranscript whose every result is malformed (no content) yields an
    # empty words list -> must be skipped (not queued as an empty batch) and the
    # reader continues to the next message.
    ws = _FakeWS([
        json.dumps({"message": "AddTranscript", "results": [
            {"start_time": 0.5, "alternatives": [{}]},   # no content
            {"start_time": 0.7, "alternatives": []},     # empty
        ]}),
        _transcript((0.0, "Goal.")),
        '{"message":"EndOfTranscript"}',
    ])
    _inject_ws(monkeypatch, ws)
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "k")
    out = list(speechmatics.stream_wav(_wav(tmp_path, 12800)))   # 2 chunks
    assert out == [{"t": 0.0, "text": "Goal."}]   # all-malformed msg ignored, next one yields
    assert ws.closed is True
