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


# --- agent._clip_cards (reads persisted cards jsonl via shared CARDS_DIR) ---

def test_clip_cards_reads_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "CARDS_DIR", str(tmp_path))
    (tmp_path / "clip-1.jsonl").write_text(
        '{"t": 1.0, "type": "pass", "analysis": "x"}\n'
        '{"t": 2.0, "type": "shot", "analysis": "y"}\n',
        encoding="utf-8",
    )
    cards = agent._clip_cards("clip-1")
    assert len(cards) == 2
    assert cards[0]["type"] == "pass"
    assert cards[1]["type"] == "shot"


def test_clip_cards_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "CARDS_DIR", str(tmp_path))
    assert agent._clip_cards("nope") is None


# --- agent.chat (malformed model tool_calls must be skipped, not crash) ---

def test_chat_skips_malformed_tool_calls(monkeypatch):
    from coach import claude
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    calls = {"n": 0}

    def fake_model(cfg, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": None, "tool_calls": [
                {"id": "good", "function": {"name": "list_players",
                                            "arguments": '{"technique":"stepover"}'}},
                {"id": "bad", "function": {}},        # malformed: no name
                {"no_function": True},                # malformed: no function
            ]}
        return {"content": "done", "tool_calls": []}

    monkeypatch.setattr(agent, "_call_tool_model", fake_model)
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: (True, {"name": name}))
    res = agent.chat("", "q", [])
    assert res["reply"] == "done"
    assert len(res["tool_trace"]) == 1
    assert res["tool_trace"][0]["tool"] == "list_players"


def test_chat_happy_path_runs_tool_then_replies(monkeypatch):
    """Normal loop: one tool call dispatched, then a final text reply."""
    from coach import claude
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    calls = {"n": 0}

    def fake_model(cfg, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "let me check", "tool_calls": [
                {"id": "t1", "function": {"name": "query_technique",
                                           "arguments": '{"technique":"stepover"}'}}]}
        return {"content": "final answer", "tool_calls": []}

    monkeypatch.setattr(agent, "_call_tool_model", fake_model)
    monkeypatch.setattr(
        agent, "_run_tool",
        lambda name, args: (True, {"name": "stepover", "difficulty": 2, "key_points": ["a", "b"]}),
    )
    res = agent.chat("", "how to stepover?", [])
    assert res["reply"] == "final answer"
    assert len(res["tool_trace"]) == 1
    assert res["tool_trace"][0]["tool"] == "query_technique"
    assert res["tool_trace"][0]["ok"] is True


def test_template_report_top_moments_only_high_rating():
    cards = [
        {"t": 1, "type": "goal", "title": "Top", "rating": 10, "analysis": "x"},
        {"t": 2, "type": "pass", "title": "Low", "rating": 3, "analysis": "y"},
    ]
    md = template_report("M", cards, None)
    assert "## Top moments" in md
    assert "Top" in md  # rating 10 -> top moment


def test_template_report_string_t_rating_no_crash():
    # LLM cards sometimes emit t/rating as strings (malformed). Sort keys +
    # :.1f formats must coerce rather than raise TypeError → 500 on /api/report.
    cards = [
        {"t": "1", "type": "goal", "title": "StrT", "rating": "9", "analysis": "a"},
        {"t": 2.5, "type": "pass", "title": "Num", "rating": 3, "analysis": "b"},
        {"t": "garbage", "type": "burst", "title": "Bad", "rating": "excellent", "analysis": "c"},
    ]
    md = template_report("M", cards, None)
    assert "## Event timeline" in md
    assert "StrT" in md and "Num" in md and "Bad" in md  # all rendered, no crash


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


# --- path-traversal defense (external clip_id / technique slug → must not
#     escape the target dir; basename strips path components, like video.py) ---

def test_load_baked_traversal_stays_in_dir(tmp_path, monkeypatch):
    from coach import clips
    baked = tmp_path / "baked"
    baked.mkdir()
    monkeypatch.setattr(clips, "BAKED_DIR", str(baked))
    (tmp_path / "escape.json").write_text('{"pwned": true}', encoding="utf-8")
    # "../escape" from baked resolves to tmp_path/escape.json (the pwned file)
    assert clips.load_baked("../escape") is None   # basename → baked/escape.json (absent)


def test_get_clip_vault_traversal_stays_in_dir(tmp_path, monkeypatch):
    from coach import clips
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(clips, "VAULT_ROOT", str(vault))
    monkeypatch.setattr(clips, "BAKED_DIR", str(tmp_path / "baked"))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path / "audio"))
    (tmp_path / "escape.mp4").write_bytes(b"x")    # outside vault
    assert clips.get_clip("../escape") is None      # basename → vault/escape.mp4 (absent)


def test_read_technique_traversal_stays_in_dir(tmp_path, monkeypatch):
    tech = tmp_path / "tech"
    tech.mkdir()
    monkeypatch.setattr(agent, "TECH_DIR", str(tech))
    (tmp_path / "escape.md").write_text("---\nname: Pwned\n---\nbody", encoding="utf-8")
    assert agent._read_technique("../escape") is None   # basename → tech/escape.md (absent)


def test_clip_cards_traversal_stays_in_dir(tmp_path, monkeypatch):
    cards = tmp_path / "cards"
    cards.mkdir()                                   # base dir must exist so ../ resolves through it
    monkeypatch.setattr(agent, "CARDS_DIR", str(cards))
    (tmp_path / "escape.jsonl").write_text('{"pwned": true}\n', encoding="utf-8")
    assert agent._clip_cards("../escape") is None      # basename → cards/escape.jsonl (absent)


def test_run_tool_analyze_clip_baked_without_transcript(tmp_path, monkeypatch):
    # baked data missing the "transcript" key must not KeyError the tool loop
    from coach import clips
    monkeypatch.setattr(clips, "load_baked", lambda cid: {"title": "X", "duration": 5})
    monkeypatch.setattr(agent, "_clip_cards", lambda cid: None)
    ok, payload = agent._run_tool("analyze_clip", {"clip_id": "c1"})
    assert ok is True
    assert payload["transcript"] == []   # .get default, not KeyError


# --- agent._read_technique: file without frontmatter ---

def test_read_technique_file_without_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "TECH_DIR", str(tmp_path))
    (tmp_path / "plain.md").write_text("just prose, no frontmatter", encoding="utf-8")
    assert agent._read_technique("plain") is None   # _parse_frontmatter {} -> None


# --- agent._parse_frontmatter: continuation branches ---

def test_frontmatter_list_item_reassigns_str_to_list():
    # a "- item" continuation under a string-valued key resets it to a list
    txt = "---\nsummary: a string\n- becomes a list\n---\n"
    assert _parse_frontmatter(txt)["summary"] == ["becomes a list"]


def test_frontmatter_plain_continuation_into_list():
    # a non-dash line under a list key appends as a list item
    txt = "---\nkey_points:\n- a\nplain continuation\n---\n"
    assert _parse_frontmatter(txt)["key_points"] == ["a", "plain continuation"]


# --- agent._run_tool: remaining branches ---

def test_run_tool_query_technique_found(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "TECH_DIR", str(tmp_path))
    (tmp_path / "body-feint.md").write_text(
        "---\nname: Body Feint\ndifficulty: 2\n---\nx", encoding="utf-8")
    ok, payload = agent._run_tool("query_technique", {"technique": "body-feint"})
    assert ok is True
    assert payload["name"] == "Body Feint"


def test_run_tool_query_technique_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "TECH_DIR", str(tmp_path))
    ok, payload = agent._run_tool("query_technique", {"technique": "nope"})
    assert ok is False
    assert "not in knowledge base" in payload["error"]


def test_run_tool_list_players(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "TECH_DIR", str(tmp_path))
    (tmp_path / "elastico.md").write_text(
        "---\nname: Elastico\nrepresentative_players:\n- Messi\n- Ronaldo\n---\nx",
        encoding="utf-8")
    ok, payload = agent._run_tool("list_players", {"technique": "elastico"})
    assert ok is True
    assert payload["players"] == ["Messi", "Ronaldo"]


def test_run_tool_unknown_tool():
    ok, payload = agent._run_tool("nope", {})
    assert ok is False
    assert "unknown tool" in payload["error"]


# --- agent._jsonl_call + _call_tool_model (mocked requests) ---

class _FakeLLMResp:
    def __init__(self, content, fail=False):
        self._content, self._fail = content, fail

    def raise_for_status(self):
        if self._fail:
            import requests
            raise requests.HTTPError("upstream 503")

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _patch_post(monkeypatch, content, *, fail=False):
    import requests
    captured = {}

    def fake_post(url, *, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _FakeLLMResp(content, fail=fail)

    monkeypatch.setattr(requests, "post", fake_post)
    return captured


def test_jsonl_call_parses_object_lines(monkeypatch):
    cap = _patch_post(monkeypatch, '{"day":1,"drill":"cone"}\nnoise\n{"day":2,"drill":"gate"}')
    rows = agent._jsonl_call({"base": "x", "key": "k", "model": "m"}, "prompt")
    assert len(rows) == 2
    assert rows[0]["drill"] == "cone"
    assert cap["timeout"] == 90


def test_call_tool_model_returns_message(monkeypatch):
    _patch_post(monkeypatch, "hi")        # model message content is a plain string
    msg = agent._call_tool_model(
        {"base": "x", "key": "k", "model": "m"},
        [{"role": "user", "content": "q"}], agent.TOOLS)
    assert msg["content"] == "hi"


def test_run_tool_training_plan_happy(monkeypatch):
    from coach import claude
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    monkeypatch.setattr(agent, "TECH_DIR", "/nonexistent")   # _read_technique -> None
    _patch_post(monkeypatch,
                '{"day":1,"drill":"cone","focus":"speed"}\n{"day":2,"drill":"gate","focus":"turn"}')
    ok, payload = agent._run_tool(
        "generate_training_plan", {"technique": "x", "weaknesses": "slow", "days": 2})
    assert ok is True
    assert len(payload) == 2


def test_run_tool_training_plan_model_error(monkeypatch):
    from coach import claude
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    monkeypatch.setattr(agent, "TECH_DIR", "/nonexistent")
    _patch_post(monkeypatch, "", fail=True)
    ok, payload = agent._run_tool(
        "generate_training_plan", {"technique": "x", "weaknesses": "y"})
    assert ok is False
    assert "plan generation failed" in payload["error"]


# --- agent.chat: clip-context injection + edge branches ---

def test_chat_injects_baked_clip_context(monkeypatch):
    from coach import claude, clips
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    monkeypatch.setattr(clips, "load_baked",
                        lambda cid: {"title": "Goal", "duration": 90, "cv_context": {"fusion_label": "vote"}})
    seen = {}

    def fake_model(cfg, messages, tools):
        seen["messages"] = messages
        return {"content": "ok", "tool_calls": []}

    monkeypatch.setattr(agent, "_call_tool_model", fake_model)
    agent.chat("clip1", "hi", [])
    assert any(m.get("content", "").startswith("Current clip: Goal") for m in seen["messages"])


def test_chat_injects_uploaded_clip_context(monkeypatch):
    from coach import claude, clips
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    monkeypatch.setattr(clips, "load_baked", lambda cid: None)
    monkeypatch.setattr(clips, "get_upload", lambda cid: {"title": "Upload", "duration": 5})
    seen = {}

    def fake_model(cfg, messages, tools):
        seen["messages"] = messages
        return {"content": "ok", "tool_calls": []}

    monkeypatch.setattr(agent, "_call_tool_model", fake_model)
    agent.chat("up1", "hi", [])
    assert any("Current clip: Upload" in m.get("content", "") for m in seen["messages"])


def test_chat_malformed_tool_arguments_falls_back_to_empty(monkeypatch):
    from coach import claude
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    n = {"i": 0}

    def fake_model(cfg, messages, tools):
        n["i"] += 1
        if n["i"] == 1:
            return {"content": None, "tool_calls": [
                {"id": "t1", "function": {"name": "query_technique", "arguments": "not json{"}}]}
        return {"content": "ok", "tool_calls": []}

    monkeypatch.setattr(agent, "_call_tool_model", fake_model)
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: (True, {"name": name}))
    res = agent.chat("", "q", [])
    assert res["tool_trace"][0]["args"] == {}   # malformed arguments -> {} fallback


def test_chat_exhausts_rounds_falls_back_message(monkeypatch):
    from coach import claude
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    n = {"i": 0}

    def fake_model(cfg, messages, tools):
        n["i"] += 1
        return {"content": "thinking", "tool_calls": [
            {"id": str(n["i"]), "function": {"name": "query_technique",
                                              "arguments": '{"technique":"x"}'}}]}

    monkeypatch.setattr(agent, "_call_tool_model", fake_model)
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: (True, {"name": "x"}))
    res = agent.chat("", "q", [], max_tool_rounds=2)
    assert "could not finish" in res["reply"]
    assert len(res["tool_trace"]) == 2


# --- agent edges: remaining branches ---

def test_jsonl_call_skips_invalid_object_line(monkeypatch):
    _patch_post(monkeypatch, '{"day":1,"drill":"cone"}\n{ broken\n{"day":2,"drill":"gate"}')
    rows = agent._jsonl_call({"base": "x", "key": "k", "model": "m"}, "p")
    assert len(rows) == 2                     # { broken skipped, not crash


def test_run_tool_analyze_clip_uploaded_path(monkeypatch):
    from coach import clips
    monkeypatch.setattr(clips, "load_baked", lambda cid: None)        # not baked
    monkeypatch.setattr(clips, "get_upload", lambda cid: {"title": "Up", "duration": 5})
    monkeypatch.setattr(agent, "_clip_cards", lambda cid: [{"t": 1}])
    ok, payload = agent._run_tool("analyze_clip", {"clip_id": "up1"})
    assert ok is True
    assert payload["title"] == "Up"
    assert payload["event_cards"] == [{"t": 1}]
    assert "note" in payload


def test_run_tool_analyze_clip_not_found(monkeypatch):
    from coach import clips
    monkeypatch.setattr(clips, "load_baked", lambda cid: None)
    monkeypatch.setattr(clips, "get_upload", lambda cid: None)
    ok, payload = agent._run_tool("analyze_clip", {"clip_id": "ghost"})
    assert ok is False
    assert "not found" in payload["error"]


def test_run_tool_list_players_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "TECH_DIR", str(tmp_path))
    ok, payload = agent._run_tool("list_players", {"technique": "nope"})
    assert ok is False
    assert "technique not found" in payload["error"]


def test_run_tool_training_plan_retries_on_empty(monkeypatch):
    import requests

    from coach import claude
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    monkeypatch.setattr(agent, "TECH_DIR", "/nonexistent")
    state = {"n": 0}

    def fake_post(url, *, headers=None, json=None, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            return _FakeLLMResp("no json lines here")          # rows=[] -> retry
        return _FakeLLMResp('{"day":1,"drill":"cone"}')        # retry succeeds

    monkeypatch.setattr(requests, "post", fake_post)
    ok, payload = agent._run_tool(
        "generate_training_plan", {"technique": "x", "weaknesses": "y"})
    assert ok is True
    assert state["n"] == 2                    # retried once on empty
    assert len(payload) == 1


def test_chat_passes_history_context(monkeypatch):
    from coach import claude
    monkeypatch.setattr(claude, "_cfg", lambda: {"base": "x", "key": "k", "model": "m"})
    seen = {}

    def fake_model(cfg, messages, tools):
        seen["messages"] = messages
        return {"content": "ok", "tool_calls": []}

    monkeypatch.setattr(agent, "_call_tool_model", fake_model)
    agent.chat("", "now", [{"role": "user", "content": "before"},
                           {"role": "assistant", "content": "hi"}])
    roles = [m["role"] for m in seen["messages"]]
    assert "assistant" in roles               # history injected into messages
