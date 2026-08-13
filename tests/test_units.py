"""Unit tests for pure helper functions across the coach package.

These cover parsing / windowing / summarization / report-template logic
that has no external (CPA / Speechmatics / ffmpeg) dependencies, so they
stay fast and hermetic. They also pin down current behavior so later
refactors can't silently change it.
"""
from coach import agent
from coach.agent import _parse_frontmatter, _summarize
from coach.claude import _parse_cards, _windows
from coach.report import template_report

# --- agent._parse_frontmatter ---

def test_frontmatter_basic_key_value():
    txt = "---\nname: Body Feint\ndifficulty: 2\n---\nbody"
    data = _parse_frontmatter(txt)
    assert data["name"] == "Body Feint"
    assert data["difficulty"] == "2"


def test_frontmatter_inline_list():
    txt = "---\nkey_points: - low body - shift weight - explode\n---\n"
    data = _parse_frontmatter(txt)
    assert data["key_points"] == ["low body", "shift weight", "explode"]


def test_frontmatter_block_list_items():
    txt = "---\nkey_points:\n- low body\n- shift weight\n---\n"
    data = _parse_frontmatter(txt)
    assert data["key_points"] == ["low body", "shift weight"]


def test_frontmatter_multiline_continuation():
    txt = "---\nsummary: first part\n second continued\n---\n"
    data = _parse_frontmatter(txt)
    assert data["summary"] == "first part second continued"


def test_frontmatter_no_frontmatter_returns_empty():
    assert _parse_frontmatter("no frontmatter here") == {}


def test_frontmatter_skips_comments():
    txt = "---\n# a comment\nname: X\n---\n"
    assert _parse_frontmatter(txt) == {"name": "X"}


# --- agent._summarize ---

def test_summarize_query_technique():
    payload = {"name": "Body Feint", "difficulty": "2", "key_points": ["a", "b"]}
    s = _summarize("query_technique", payload)
    assert "Body Feint" in s
    assert "2 key points" in s


def test_summarize_list_players():
    s = _summarize("list_players", {"players": ["Maradona", "Messi"]})
    assert "2 players" in s


def test_summarize_analyze_clip():
    payload = {"title": "Goal 1", "event_cards": [{"t": 1}, {"t": 2}]}
    s = _summarize("analyze_clip", payload)
    assert "Goal 1" in s
    assert "2 cards" in s


def test_summarize_analyze_clip_no_cards():
    s = _summarize("analyze_clip", {"title": "X", "event_cards": None})
    assert "no cards" in s


def test_summarize_training_plan():
    s = _summarize("generate_training_plan", [{"day": 1}, {"day": 2}, {"day": 3}])
    assert "3 drills" in s


def test_summarize_unknown_tool():
    assert _summarize("nope", {}) == "ok"


# --- claude._windows ---

def test_windows_groups_by_six_second_buckets():
    data = {"transcript": [
        {"t": 0.0, "text": "a"},
        {"t": 6.5, "text": "b"},
        {"t": 13.0, "text": "c"},
    ]}
    wins = _windows(data)
    assert len(wins) == 2  # [a,b] closes at b (6.5-0>=6), [c] closes at c (13-6.5>=6)
    assert wins[0][2] == [{"t": 0.0, "text": "a"}, {"t": 6.5, "text": "b"}]
    assert wins[1][2] == [{"t": 13.0, "text": "c"}]


def test_windows_preserves_order_and_emits_trailing():
    lines = [{"t": float(i), "text": str(i)} for i in range(8)]
    data = {"transcript": lines}
    wins = _windows(data)
    flat = [item for _start, _end, items in wins for item in items]
    assert [entry["text"] for entry in flat] == [str(i) for i in range(8)]


# --- claude._parse_cards ---

def test_parse_cards_filters_non_json():
    text = 'noise\n{"t":1,"type":"goal","analysis":"x"}\nnot json\n{"t":2,"type":"save","analysis":"y"}'
    cards = _parse_cards(text)
    assert len(cards) == 2
    assert cards[0]["type"] == "goal"


def test_parse_cards_strips_backticks():
    text = '`{"t":1,"type":"goal","analysis":"x"}`'
    assert len(_parse_cards(text)) == 1


def test_parse_cards_requires_type_and_analysis():
    text = '{"t":1,"type":"goal"}\n{"t":2,"analysis":"y"}'
    assert _parse_cards(text) == []


def test_parse_cards_empty():
    assert _parse_cards("") == []


# --- report.template_report ---

def test_template_report_has_sections():
    cards = [{"t": 1.0, "type": "goal", "title": "Strike", "rating": 9, "analysis": "top corner"}]
    cv = {"fusion_label": "run", "confidence": 0.9, "gold": "run"}
    md = template_report("Big Match", cards, cv)
    assert "# Tactical Report — Big Match" in md
    assert "## Event timeline" in md
    assert "## Top moments" in md
    assert "## Coaching takeaways" in md
    assert "Strike" in md


def test_template_report_empty_cards_no_cv():
    md = template_report("Solo", [], None)
    assert "# Tactical Report — Solo" in md
    assert "## Event timeline" in md


def test_template_report_top_moments_only_high_rating():
    cards = [
        {"t": 1, "type": "goal", "title": "Top", "rating": 10, "analysis": "x"},
        {"t": 2, "type": "pass", "title": "Low", "rating": 3, "analysis": "y"},
    ]
    md = template_report("M", cards, None)
    assert "## Top moments" in md
    assert "Top" in md  # rating 10 -> top moment


# --- agent._read_technique ---

def test_read_technique_parses_card(tmp_path, monkeypatch):
    card = tmp_path / "body-feint.md"
    card.write_text(
        "---\nname: Body Feint\ncategory: feint\ndifficulty: 2\n"
        "summary: shift weight\nkey_points:\n- low body\n- explode\n---\nbody",
        encoding="utf-8",
    )
    monkeypatch.setattr(agent, "TECH_DIR", str(tmp_path))
    data = agent._read_technique("body-feint")
    assert data is not None
    assert data["slug"] == "body-feint"
    assert data["name"] == "Body Feint"
    assert data["difficulty"] == "2"
    assert data["key_points"] == ["low body", "explode"]


def test_read_technique_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "TECH_DIR", str(tmp_path))
    assert agent._read_technique("nope-not-here") is None
