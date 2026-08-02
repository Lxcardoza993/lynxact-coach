"""live 模式(D2 接线):转录窗口 + CV 融合上下文 → Claude 流式事件卡。

走 OpenAI 兼容端点:本地开发用 CPA(127.0.0.1:8317/v1),比赛时换成
赛方 Claude key 对应的网关。事件形状与 replay 完全一致,前端无感。

D2 待办:
- Speechmatics 实时转录喂进 transcript 窗口(当前用 baked 转录顶着)
- 事件卡的 JSON schema 校验 + 重试
- grounding:卡片必须引用 cv_context,无支撑的判断标 speculative
"""
import json
import os

import requests

from .clips import load_baked
from .stream import sse

SYSTEM_PROMPT = """You are LynxAct Coach, an elite football tactical analyst.
You receive: (1) a live commentary transcript window, (2) computer-vision
annotations from a multi-model fusion pipeline (with confidence and per-source
votes). Produce tactical event cards as STRICT JSON lines, one per event:
{"t": <seconds>, "type": <event type>, "title": <short>, "analysis": <2-3
sentences>, "players": [...], "rating": <1-10>, "speculative": <bool>}
Rules: ground every claim in the transcript or the CV context. If a judgment
has no supporting evidence, set speculative=true. Never invent player names."""


def _client():
    return {
        "base": os.environ.get("COACH_API_BASE", "http://127.0.0.1:8317/v1"),
        "key": os.environ.get("COACH_API_KEY", ""),
        "model": os.environ.get("COACH_MODEL", "claude-sonnet-5"),
    }


def build_window_prompt(data, upto_t):
    """Assemble one analysis window: transcript so far + CV context."""
    lines = [f"[{x['t']:5.1f}s] {x['text']}" for x in data["transcript"] if x["t"] <= upto_t]
    return (
        f"CV context (fusion pipeline):\n{json.dumps(data.get('cv_context'), ensure_ascii=False)}\n\n"
        f"Transcript window (0-{upto_t}s):\n" + "\n".join(lines)
    )


def live_events(clip_id):
    """Stream event cards generated live by the model. Falls back to replay
    if no API key is configured (demo can never die)."""
    cfg = _client()
    data = load_baked(clip_id)
    if not cfg["key"] or data is None:
        from .stream import replay_events
        yield from replay_events(clip_id)
        return
    yield sse("meta", {
        "clip": data["clip"], "title": data["title"],
        "duration": data["duration"], "cv_context": data.get("cv_context"),
    })
    # D2: replace baked transcript with Speechmatics stream; one Claude call
    # per window, parsing JSON lines off the streamed response.
    resp = requests.post(
        cfg["base"] + "/chat/completions",
        headers={"Authorization": f"Bearer {cfg['key']}"},
        json={
            "model": cfg["model"],
            "stream": True,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_window_prompt(data, data["duration"])},
            ],
        },
        stream=True,
        timeout=120,
    )
    buf = ""
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        chunk = raw[5:].strip()
        if chunk == "[DONE]":
            break
        try:
            delta = json.loads(chunk)["choices"][0]["delta"].get("content", "")
        except (KeyError, IndexError, json.JSONDecodeError):
            continue
        buf += delta
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                card = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield sse("card", card)
    yield sse("done", {"clip": data["clip"]})
