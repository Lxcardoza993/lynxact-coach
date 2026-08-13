"""Unit tests for coach.report — build_report, get_cards, persistence.

Hermetic: monkeypatches CARDS_DIR + load_baked + model_report so no real
baked data, CPA model call, or shared filesystem state is touched.
"""
import pytest

from coach import report


def _card(t=1.0, ctype="goal", title="Strike", rating=9, analysis="top corner"):
    return {"t": t, "type": ctype, "title": title, "rating": rating, "analysis": analysis}


# --- get_cards (persisted path when no baked) ---

def test_get_cards_from_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "CARDS_DIR", str(tmp_path))
    monkeypatch.setattr(report, "load_baked", lambda cid: None)
    report.persist_card("my_clip", _card())
    title, cards, cv = report.get_cards("my_clip")
    assert title == "my clip"          # clip_id _ -> space
    assert len(cards) == 1
    assert cards[0]["type"] == "goal"
    assert cv is None


# --- persist_card / _persisted_cards round-trip ---

def test_persist_card_accumulates(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "CARDS_DIR", str(tmp_path))
    report.persist_card("c1", _card(t=1.0))
    report.persist_card("c1", _card(t=2.0, ctype="save", title="Dive"))
    cards = report._persisted_cards("c1")
    assert len(cards) == 2            # append mode accumulates
    assert cards[0]["type"] == "goal"
    assert cards[1]["type"] == "save"


def test_persisted_cards_skips_bad_json(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "CARDS_DIR", str(tmp_path))
    (tmp_path / "c1.jsonl").write_text(
        '{"t": 1, "type": "goal"}\n{ broken\n{"t": 2, "type": "save"}\n',
        encoding="utf-8",
    )
    cards = report._persisted_cards("c1")
    assert len(cards) == 2            # corrupt middle line skipped


# --- build_report ---

def test_build_report_replay_uses_template(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "CARDS_DIR", str(tmp_path))
    monkeypatch.setattr(report, "load_baked", lambda cid: None)
    report.persist_card("c1", _card())
    res = report.build_report("c1", "replay", {})
    assert res["generated_by"] == "template"
    assert "# Tactical Report — c1" in res["markdown"]
    assert "Strike" in res["markdown"]


def test_build_report_no_cards_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "CARDS_DIR", str(tmp_path))
    monkeypatch.setattr(report, "load_baked", lambda cid: None)
    assert report.build_report("nope", "replay", {}) is None


def test_build_report_live_falls_back_on_model_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "CARDS_DIR", str(tmp_path))
    monkeypatch.setattr(report, "load_baked", lambda cid: None)
    report.persist_card("c1", _card())

    def boom(cfg, title, cards, cv):
        raise RuntimeError("model down")
    monkeypatch.setattr(report, "model_report", boom)
    res = report.build_report("c1", "live", {"key": "k", "model": "m", "base": "x"})
    assert res["generated_by"] == "template"   # fallback, not crash


def test_build_report_live_uses_model_when_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "CARDS_DIR", str(tmp_path))
    monkeypatch.setattr(report, "load_baked", lambda cid: None)
    report.persist_card("c1", _card())

    monkeypatch.setattr(report, "model_report", lambda cfg, title, cards, cv: "MODEL MD")
    res = report.build_report("c1", "live", {"key": "k", "model": "m", "base": "x"})
    assert res["generated_by"] == "m"
    assert res["markdown"] == "MODEL MD"


# --- get_cards (baked path) ---

def test_get_cards_uses_baked_events_when_present(monkeypatch):
    baked = {"title": "Final", "events": [_card()], "cv_context": {"fusion_label": "vote"}}
    monkeypatch.setattr(report, "load_baked", lambda cid: baked)
    title, cards, cv = report.get_cards("any")
    assert title == "Final"
    assert cards == [_card()]
    assert cv == {"fusion_label": "vote"}


# --- model_report (real body, mocked HTTP) ---

class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise report.requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def test_model_report_posts_and_returns_content(monkeypatch):
    captured = {}

    def fake_post(url, *, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _FakeResp({"choices": [{"message": {"content": "## Mock Report"}}]})

    monkeypatch.setattr(report.requests, "post", fake_post)
    cfg = {"base": "https://api.example.com", "key": "sk-test", "model": "gpt-x"}
    out = report.model_report(cfg, "My Clip", [{"t": 1, "title": "shot"}], {"fusion_label": "vote"})
    assert out == "## Mock Report"
    assert captured["url"] == "https://api.example.com/chat/completions"
    assert captured["headers"] == {"Authorization": "Bearer sk-test"}
    assert captured["json"]["model"] == "gpt-x"
    assert captured["json"]["temperature"] == 0.4
    assert captured["json"]["messages"][0]["role"] == "user"
    prompt = captured["json"]["messages"][0]["content"]
    assert "My Clip" in prompt and "shot" in prompt
    assert captured["timeout"] == 120


def test_model_report_propagates_http_error(monkeypatch):
    # raise_for_status on a 5xx must surface (build_report's try/except catches it).
    monkeypatch.setattr(
        report.requests, "post",
        lambda *a, **k: _FakeResp({}, status=503),
    )
    with pytest.raises(report.requests.HTTPError):
        report.model_report({"base": "x", "key": "k", "model": "m"}, "T", [], None)


# --- template_report (cv block) ---

def test_template_report_includes_cv_context():
    md = report.template_report(
        "Clip", [_card()],
        {"fusion_label": "vote", "confidence": 0.8, "gold": "src1"},
    )
    assert "**CV fusion**: vote (conf 0.8)" in md
    assert "gold=src1" in md
