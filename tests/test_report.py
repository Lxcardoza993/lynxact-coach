"""Unit tests for coach.report — build_report, get_cards, persistence.

Hermetic: monkeypatches CARDS_DIR + load_baked + model_report so no real
baked data, CPA model call, or shared filesystem state is touched.
"""
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
