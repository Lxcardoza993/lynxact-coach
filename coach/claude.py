"""live 模式:转录窗口 + CV 融合上下文 → LLM(OpenAI 兼容端点)流式事件卡。

走 OpenAI 兼容端点:本地开发用自建网关,比赛时换赛方 Claude key 的网关,
只改 .env 三个变量。事件形状与 replay 完全一致,前端无感。

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


def _emit_window_cards(cfg, data_title, duration, cv, win_start, win_end, win_lines, emitted_types):
    prompt = _window_prompt(
        {"title": data_title, "duration": duration, "cv_context": cv},
        win_start, win_end, win_lines, emitted_types,
    )
    cards = _call_model(cfg, prompt)
    if not cards:  # 整窗零卡 → 重试一次
        cards = _call_model(cfg, prompt + "\nReturn JSON Lines only.")
    for card in cards:
        card.setdefault("t", win_end)
        card.setdefault("speculative", True)
        emitted_types.add(card.get("type", "?"))
    return cards


def live_events(clip_id):
    """Window-by-window live generation; any failure falls back to replay so
    the demo can never die on stage. Baked clip → baked transcript pacing;
    uploaded clip → Speechmatics real-time transcript (D3)."""
    cfg = _cfg()
    data = load_baked(clip_id)
    from . import report

    if data is not None:
        transcript_source = "baked"
        title, duration, cv = data["title"], data["duration"], data.get("cv_context")
    else:
        from .clips import get_upload
        up = get_upload(clip_id)
        if not up or not up.get("audio_wav"):
            yield sse("error", {"msg": "clip not found or audio not extracted"})
            return
        if not os.environ.get("SPEECHMATICS_API_KEY"):
            yield sse("error", {"msg": "uploaded clips need SPEECHMATICS_API_KEY for live transcription"})
            return
        transcript_source = "speechmatics"
        title, duration, cv = up["title"], up.get("duration") or 0, None

    if not cfg["key"]:
        if data is not None:
            from .stream import replay_events
            yield from replay_events(clip_id)
        else:
            yield sse("error", {"msg": "COACH_API_KEY not set"})
        return

    speed = float(os.environ.get("REPLAY_SPEED", "2.0")) or 1.0
    yield sse("meta", {
        "clip": clip_id, "title": title, "duration": duration,
        "cv_context": cv, "live_model": cfg["model"],
        "transcript_source": transcript_source,
    })
    emitted_types, last_t = set(), 0.0
    try:
        if transcript_source == "baked":
            for win_start, win_end, win_lines in _windows(data):
                for line in win_lines:
                    delay = max(0.0, (line["t"] - last_t) / speed)
                    last_t = line["t"]
                    if delay:
                        time.sleep(delay)
                    yield sse("transcript", line)
                for card in _emit_window_cards(cfg, title, duration, cv, win_start, win_end, win_lines, emitted_types):
                    report.persist_card(clip_id, card)
                    yield sse("card", card)
        else:
            from . import speechmatics
            win_start, win_lines = 0.0, []
            for line in speechmatics.stream_wav(get_upload(clip_id)["audio_wav"]):
                win_lines.append(line)
                yield sse("transcript", line)
                if line["t"] - win_start >= 6.0:
                    cards = _emit_window_cards(
                        cfg, title, duration, cv, win_start, line["t"], win_lines, emitted_types
                    )
                    for card in cards:
                        report.persist_card(clip_id, card)
                        yield sse("card", card)
                    win_start, win_lines = line["t"], []
            if win_lines:
                cards = _emit_window_cards(
                    cfg, title, duration, cv, win_start, win_lines[-1]["t"], win_lines, emitted_types
                )
                for card in cards:
                    report.persist_card(clip_id, card)
                    yield sse("card", card)
    except Exception as exc:  # 任意异常 → 报错事件,前端可回退 replay
        yield sse("error", {"msg": f"live failed: {exc.__class__.__name__}: {exc}"})
    yield sse("done", {"clip": clip_id})
