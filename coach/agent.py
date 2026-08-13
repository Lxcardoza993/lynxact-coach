"""Coach Agent — 教练对话式 Agent(GOAI Boundless Agents 赛道形态)。

把单向分析流水线升级成多轮对话 Agent:教练看完分析后可以追问
("这个动作怎么改进""谁擅长这个""给我 3 天训练计划"),Agent 通过
工具调用(analyze_clip / query_technique / list_players / generate_training_plan)
完成闭环任务。工具调用走 OpenAI 兼容 function calling(本地网关实测可用)。

闭环演示(评审点):上传 clip → 分析出事件卡 → 追问改进 → 查知识库要领
→ 查代表球员 → 生成训练计划 → 导出。多轮交互 + 工具调用 + 知识增强 + 结果交付。
"""
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TECH_DIR = os.environ.get(
    "VAULT_TECH_DIR",
    os.path.join(BASE_DIR, "data", "techniques"),
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_clip",
            "description": (
                "Analyze a football clip: return its tactical event cards (if the "
                "stream already ran), CV fusion context, and commentary transcript. "
                "Use this when the coach asks about a specific clip's analysis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string", "description": "clip id, e.g. body-feint_diego-maradona_1986"}
                },
                "required": ["clip_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_technique",
            "description": (
                "Look up a dribbling technique's knowledge card: key points, quick "
                "grasp cue, common mistakes, difficulty. Use this when the coach "
                "asks how to perform or improve a technique."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "technique": {
                        "type": "string",
                        "description": "technique slug, e.g. body-feint, elastico, cruyff-turn",
                    },
                },
                "required": ["technique"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_players",
            "description": (
                "List famous players known for a technique. Use this when the coach "
                "asks who is famous for a move or wants example footage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "technique": {"type": "string", "description": "technique slug, e.g. body-feint"}
                },
                "required": ["technique"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_training_plan",
            "description": (
                "Generate a structured 3-day training plan for a technique, given "
                "the player's weaknesses. Returns drills with sets/reps and "
                "coaching cues."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "technique": {"type": "string", "description": "technique slug, e.g. body-feint"},
                    "weaknesses": {"type": "string", "description": "what the player struggles with, from the coach"},
                    "days": {"type": "integer", "description": "plan length in days (default 3)"},
                },
                "required": ["technique", "weaknesses"],
            },
        },
    },
]


def _parse_frontmatter(txt):
    """Minimal YAML frontmatter parser: handles `key: value`, `key:` + `- item`
    lists, and multi-line continuation values. Good enough for the vault cards."""
    m = re.match(r"^---\n(.*?)\n---", txt, re.S)
    if not m:
        return {}
    data, cur = {}, None
    for ln in m.group(1).splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        fm = re.match(r"^([\w-]+):\s*(.*?)\s*$", ln)
        if fm:
            cur = fm.group(1)
            val = fm.group(2)
            if val.startswith("-"):
                data[cur] = [x.strip().strip("'\"") for x in val.split("-")[1:] if x.strip()]
            elif val:
                data[cur] = val
            else:
                data[cur] = []
            continue
        if cur is not None:
            if ln.lstrip().startswith("-"):
                if not isinstance(data[cur], list):
                    data[cur] = []
                data[cur].append(ln.lstrip()[1:].strip().strip("'\""))
            elif isinstance(data[cur], list):
                data[cur].append(ln.strip())
            elif isinstance(data[cur], str):
                data[cur] += " " + ln.strip()
    return data


def _read_technique(slug):
    """Read one technique card from the vault knowledge base."""
    p = os.path.join(TECH_DIR, f"{slug}.md")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        txt = f.read()
    data = _parse_frontmatter(txt)
    if not data:
        return None
    return {
        "slug": slug,
        "name": data.get("name") or slug,
        "category": data.get("category"),
        "difficulty": data.get("difficulty"),
        "summary": data.get("summary"),
        "key_points": data.get("key_points") or [],
        "quick_grasp": data.get("quick_grasp"),
        "common_mistakes": data.get("common_mistakes") or [],
        "representative_players": data.get("representative_players") or [],
    }


def _clip_cards(clip_id):
    """Event cards persisted for a clip (data/tmp/cards/<id>.jsonl)."""
    p = os.path.join(os.path.dirname(__file__), "..", "data", "tmp", "cards", f"{clip_id}.jsonl")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _jsonl_call(cfg, prompt):
    """Generic JSON Lines call (no event-card schema filter): parses every line
    that is a JSON object. Used by generate_training_plan."""
    import requests
    resp = requests.post(
        cfg["base"] + "/chat/completions",
        headers={"Authorization": f"Bearer {cfg['key']}"},
        json={
            "model": cfg["model"],
            "temperature": 0.4,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
    )
    resp.raise_for_status()
    rows = []
    for line in resp.json()["choices"][0]["message"]["content"].splitlines():
        line = line.strip().strip("`")
        if not line.startswith("{"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _run_tool(name, args):
    """Execute a tool call; returns (ok, payload)."""
    if name == "analyze_clip":
        clip_id = args.get("clip_id", "")
        from .clips import get_upload, load_baked
        data = load_baked(clip_id)
        if data is not None:
            cards = _clip_cards(clip_id)
            return True, {
                "clip": clip_id,
                "title": data["title"],
                "duration": data["duration"],
                "cv_context": data.get("cv_context"),
                "transcript": data["transcript"][:12],
                "event_cards": cards or "no cards yet — run the analysis stream first (press play)",
            }
        up = get_upload(clip_id)
        if up:
            return True, {
                "clip": clip_id,
                "title": up["title"],
                "duration": up.get("duration", 0),
                "note": "uploaded clip — run the live stream first to generate cards",
                "event_cards": _clip_cards(clip_id) or None,
            }
        return False, {"error": f"clip {clip_id} not found"}

    if name == "query_technique":
        card = _read_technique(args.get("technique", ""))
        if not card:
            return False, {"error": f"technique not in knowledge base: {args.get('technique')}"}
        return True, card

    if name == "list_players":
        card = _read_technique(args.get("technique", ""))
        if not card:
            return False, {"error": f"technique not found: {args.get('technique')}"}
        return True, {"technique": card["name"], "players": card["representative_players"]}

    if name == "generate_training_plan":
        from .claude import _cfg
        cfg = _cfg()
        card = _read_technique(args.get("technique", ""))
        key = json.dumps(card.get("key_points", []), ensure_ascii=False) if card else "[]"
        prompt = (
            f"Technique: {args.get('technique')} (knowledge keys: {key})\n"
            f"Player weaknesses: {args.get('weaknesses')}\n"
            f"Days: {args.get('days', 3)}\n\n"
            "Return ONLY JSON Lines — one object per line, no markdown, no "
            "prose, no code fences. Each line: "
            '{"day": <int>, "drill": "<name>", "focus": "<what it fixes>", '
            '"sets": "<sets x reps>", "cue": "<one coaching cue>"} '
            "Emit at least one line per day."
        )
        try:
            rows = _jsonl_call(cfg, prompt)
            if not rows:
                rows = _jsonl_call(cfg, prompt + "\nReturn JSON Lines only.")
            return True, rows or []
        except Exception as exc:
            return False, {"error": f"plan generation failed: {exc}"}

    return False, {"error": f"unknown tool {name}"}


SYSTEM_PROMPT = """You are Coach, a football tactical coach assistant inside LynxAct Coach.
You help coaches analyze footage and improve their players' dribbling technique.
You have tools: analyze_clip (clip analysis + event cards), query_technique
(knowledge base: key points, common mistakes, quick-grasp cue),
list_players (famous players for a technique), generate_training_plan
(3-day structured plan with drills/sets/cues).
Rules:
- Always answer in the coach's language (Chinese if they write Chinese).
- Ground every claim in tool output or the clip's own data. Never invent
  players, techniques, or statistics.
- Use tools when the coach asks for something a tool provides; do not guess.
- Keep replies coaching-grade: concrete, actionable, honest about uncertainty.
- If a tool returns an error, say what happened and offer the next step."""


def _call_tool_model(cfg, messages, tools):
    """One model call with tools; returns (message, tool_calls)."""
    import requests
    resp = requests.post(
        cfg["base"] + "/chat/completions",
        headers={"Authorization": f"Bearer {cfg['key']}"},
        json={
            "model": cfg["model"],
            "temperature": 0.4,
            "messages": messages,
            "tools": tools,
        },
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def chat(clip_id, message, history, max_tool_rounds=3):
    """Run the agent loop. Returns {reply, tool_trace}."""
    from .claude import _cfg
    cfg = _cfg()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or [])[-8:]:
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    clip_ctx = ""
    if clip_id:
        from .clips import get_upload, load_baked
        data = load_baked(clip_id)
        if data is not None:
            cv_ctx = json.dumps(data.get('cv_context'), ensure_ascii=False)
            clip_ctx = f"Current clip: {data['title']} ({data['duration']}s, CV fusion: {cv_ctx})."
        else:
            up = get_upload(clip_id)
            if up:
                clip_ctx = f"Current clip: {up['title']}."
    if clip_ctx:
        messages.append({"role": "system", "content": clip_ctx})
    messages.append({"role": "user", "content": message})

    trace = []
    for _ in range(max_tool_rounds):
        msg = _call_tool_model(cfg, messages, TOOLS)
        if not msg.get("tool_calls"):
            return {"reply": msg.get("content") or "...", "tool_trace": trace}
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg["tool_calls"]})
        for tc in msg["tool_calls"]:
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            ok, payload = _run_tool(fn["name"], args)
            trace.append({
                "tool": fn["name"],
                "args": args,
                "ok": ok,
                "summary": _summarize(fn["name"], payload),
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(payload, ensure_ascii=False)[:6000],
            })
    return {
        "reply": "I could not finish that in the allowed rounds — ask me again, or narrow the question.",
        "tool_trace": trace,
    }


def _summarize(name, payload):
    """Short human-readable summary of a tool result for the UI trace."""
    if name in ("query_technique", "list_players"):
        return {
            "query_technique": (
                f"{payload.get('name')} · {payload.get('difficulty')} · "
                f"{len(payload.get('key_points', []))} key points"
            ),
            "list_players": f"{len(payload.get('players', []))} players",
        }.get(name, "ok")
    if name == "analyze_clip":
        cards = payload.get("event_cards")
        return f"{payload.get('title')} · {len(cards) if isinstance(cards, list) else 'no'} cards"
    if name == "generate_training_plan":
        return f"{len(payload) if isinstance(payload, list) else 0} drills"
    return "ok"
