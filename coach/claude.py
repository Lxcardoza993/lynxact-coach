"""live 模式:转录窗口 + CV 融合上下文 → Claude(或 CPA 替代模型)流式事件卡。

走 OpenAI 兼容端点:本地开发用 CPA(127.0.0.1:8317/v1),比赛时换赛方
Claude key 的网关,只改 .env 三个变量。事件形状与 replay 完全一致,前端无感。

D2 已落地:
- 按解说时间窗逐窗生成(模拟实时),卡片即来即推
- 严格 JSON Lines 输出 + 逐行解析,坏行跳过,整窗零卡重试一次
- grounding:卡片必须引用转录或 cv_context,无支撑判断标 speculative

D3 待办:Speechmatics 实时转录替换 baked 转录窗口。
"""
import json
import os
import time

import requests

from .clips import load_baked
from .stream import sse

SYSTEM_PROMPT = """You are LynxAct Coach, an elite football tactical analyst.
Input: (1) a live commentary transcript with timestamps, (2) computer-vision
annotations from a multi-model fusion pipeline (fusion_label, confidence,
per-source votes, gold label unknown to you).
Output: tactical event cards as STRICT JSON Lines — one JSON object per line,
no markdown fences, no commentary, no wrapping text:
{"t": <seconds>, "type": "reception|setup|dribble|burst|finish|defense",
 "title": "<=8 words", "analysis": "<=40 words, coaching-grade insight",
 "players": ["..."], "rating": <1-10>, "speculative": <bool>}
Rules:
- Ground every claim in the transcript or the CV context; if unsure,
  speculative=true. Never invent player names not present in the input.
- Emit cards ONLY for events inside the requested time window.
- If nothing notable happens in the window, output nothing (empty response)."""


def _cfg():
    return {
        "base": os.environ.get("COACH_API_BASE", "http://127.0.0.1:8317/v1"),
        "key": os.environ.get("COACH_API_KEY", ""),
        "model": os.environ.get("COACH_MODEL", "gpt-5.5"),
    }


def _windows(data):
    """Split baked transcript into windows ending near each notable beat.
    Groups transcript lines into ~6s buckets so cards trickle like live TV."""
    lines = sorted(data["transcript"], key=lambda x: x["t"])
    windows, cur, start = [], [], 0.0
    for line in lines:
        cur.append(line)
        if line["t"] - start >= 6.0:
            windows.append((start, line["t"], list(cur)))
            start, cur = line["t"], []
    if cur:
        windows.append((start, cur[-1]["t"], list(cur)))
    return windows


def _parse_cards(text):
    cards = []
    for line in text.splitlines():
        line = line.strip().strip("`")
        if not line.startswith("{"):
            continue
        try:
            card = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(card, dict) and "type" in card and "analysis" in card:
            cards.append(card)
    return cards


def _call_model(cfg, user_prompt):
    """One non-streaming call; returns parsed cards. Streaming per window adds
    little (windows are the live granularity) and complicates JSON repair."""
    resp = requests.post(
        cfg["base"] + "/chat/completions",
        headers={"Authorization": f"Bearer {cfg['key']}"},
        json={
            "model": cfg["model"],
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=90,
    )
    resp.raise_for_status()
    return _parse_cards(resp.json()["choices"][0]["message"]["content"])


def _window_prompt(data, win_start, win_end, win_lines, emitted_types):
    cv = json.dumps(data.get("cv_context"), ensure_ascii=False)
    transcript = "\n".join(f"[{x['t']:5.1f}s] {x['text']}" for x in win_lines)
    return (
        f"Clip: {data['title']} (total {data['duration']}s)\n"
        f"CV fusion context: {cv}\n"
        f"Event types already emitted earlier: {sorted(emitted_types) or 'none'}\n\n"
        f"NEW transcript window {win_start:.1f}s–{win_end:.1f}s:\n{transcript}\n\n"
        f"Emit cards ONLY for events in {win_start:.1f}s–{win_end:.1f}s."
    )


def live_events(clip_id):
    """Window-by-window live generation; any failure falls back to replay so
    the demo can never die on stage."""
    cfg = _cfg()
    data = load_baked(clip_id)
    if not cfg["key"] or data is None:
        from .stream import replay_events
        yield from replay_events(clip_id)
        return
    speed = float(os.environ.get("REPLAY_SPEED", "2.0")) or 1.0
    yield sse("meta", {
        "clip": data["clip"], "title": data["title"],
        "duration": data["duration"], "cv_context": data.get("cv_context"),
        "live_model": cfg["model"],
    })
    emitted_types, last_t = set(), 0.0
    try:
        for win_start, win_end, win_lines in _windows(data):
            # 按回放节奏先推解说词,再生成该窗卡片
            for line in win_lines:
                delay = max(0.0, (line["t"] - last_t) / speed)
                last_t = line["t"]
                if delay:
                    time.sleep(delay)
                yield sse("transcript", line)
            prompt = _window_prompt(data, win_start, win_end, win_lines, emitted_types)
            cards = _call_model(cfg, prompt)
            if not cards:  # 整窗零卡 → 重试一次
                cards = _call_model(cfg, prompt + "\nReturn JSON Lines only.")
            for card in cards:
                card.setdefault("t", win_end)
                card.setdefault("speculative", True)
                emitted_types.add(card.get("type", "?"))
                yield sse("card", card)
    except Exception as exc:  # 任意异常 → 剩余部分用回放兜底
        yield sse("error", {"msg": f"live failed, falling back: {exc.__class__.__name__}"})
    yield sse("done", {"clip": data["clip"]})
