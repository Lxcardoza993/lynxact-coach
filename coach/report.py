"""全场报告引擎(D4)。

replay:模板聚合,零模型、永不失败(演示兜底)。
live:一次模型调用,把事件卡聚合成 markdown 战术报告。
卡片来源:baked events 优先;否则读 live 运行持久化的 jsonl。
"""
import json
import logging
import os

import requests

from .clips import BASE, _num, load_baked

logger = logging.getLogger(__name__)

CARDS_DIR = os.path.join(BASE, "data", "tmp", "cards")


def persist_card(clip_id: str, card: dict) -> None:
    """Append a tactical event card to the clip's cards jsonl."""
    os.makedirs(CARDS_DIR, exist_ok=True)
    with open(os.path.join(CARDS_DIR, clip_id + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(card, ensure_ascii=False) + "\n")


def _persisted_cards(clip_id: str) -> list[dict]:
    path = os.path.join(CARDS_DIR, clip_id + ".jsonl")
    if not os.path.exists(path):
        return []
    cards = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    cards.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return cards


def get_cards(clip_id: str) -> tuple[str, list[dict], dict | None]:
    """Return (title, cards, cv_context) from baked data, else persisted cards + None cv."""
    data = load_baked(clip_id)
    if data:
        return data["title"], data["events"], data.get("cv_context")
    title = clip_id.replace("_", " ")
    return title, _persisted_cards(clip_id), None


def template_report(title: str, cards: list[dict], cv: dict | None) -> str:
    """零模型模板报告:时间线 + 高光时刻 + 类型分布 + 教练要点。"""
    # LLM cards may emit t/rating as strings; coerce so sort keys + :.1f never raise.
    cards = [{**c, "t": _num(c.get("t")), "rating": _num(c.get("rating"))} for c in cards]
    lines = [f"# Tactical Report — {title}", ""]
    if cv:
        lines.append(
            f"**CV fusion**: {cv.get('fusion_label')} (conf {cv.get('confidence')})"
            f" · gold={cv.get('gold')}"
        )
        lines.append("")
    lines.append("## Event timeline")
    lines.append("")
    for c in sorted(cards, key=lambda x: x.get("t", 0)):
        spec = " ⚠speculative" if c.get("speculative") else ""
        lines.append(
            f"- **{c.get('t', 0):.1f}s** [{c.get('type', '?')}] "
            f"{c.get('title', '')} — ★{c.get('rating', '?')}{spec}"
        )
    top = [c for c in cards if isinstance(c.get("rating"), int | float) and c["rating"] >= 9]
    if top:
        lines += ["", "## Top moments"]
        lines.append("")
        for c in sorted(top, key=lambda x: -x["rating"]):
            lines.append(f"- ★{c['rating']} **{c.get('title', '')}** ({c.get('t', 0):.1f}s)")
    dist = {}
    for c in cards:
        dist[c.get("type", "?")] = dist.get(c.get("type", "?"), 0) + 1
    lines += ["", "## Event mix", ""]
    lines.append(" · ".join(f"{k}×{v}" for k, v in sorted(dist.items(), key=lambda x: -x[1])))
    takeaways = [c for c in sorted(cards, key=lambda x: -(x.get("rating") or 0))[:3]]
    lines += ["", "## Coaching takeaways", ""]
    for c in takeaways:
        lines.append(f"- {c.get('analysis', '')}")
    lines.append("")
    return "\n".join(lines)


def model_report(cfg: dict, title: str, cards: list[dict], cv: dict | None) -> str:
    """Call the LLM to turn event cards into a concise markdown tactical report."""
    prompt = (
        "You are LynxAct Coach. Turn these tactical event cards into a concise "
        "full-match markdown report: summary paragraph, event timeline, top 3 "
        "moments with reasons, player notes, and 3 concrete coaching takeaways. "
        "Ground everything in the cards; mark uncertain claims as speculative.\n\n"
        f"Clip: {title}\nCV context: {json.dumps(cv, ensure_ascii=False)}\n"
        f"Cards:\n{json.dumps(cards, ensure_ascii=False)}"
    )
    resp = requests.post(
        cfg["base"] + "/chat/completions",
        headers={"Authorization": f"Bearer {cfg['key']}"},
        json={
            "model": cfg["model"],
            "temperature": 0.4,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def build_report(clip_id: str, mode: str, cfg: dict) -> dict | None:
    """Build a clip's tactical report — LLM in live mode (when key set), else the zero-model template."""
    title, cards, cv = get_cards(clip_id)
    if not cards:
        return None
    if mode == "live" and cfg.get("key"):
        try:
            return {"markdown": model_report(cfg, title, cards, cv), "generated_by": cfg["model"]}
        except Exception as exc:
            logger.warning("model report failed for %s, falling back to template: %s", clip_id, exc)
    return {"markdown": template_report(title, cards, cv), "generated_by": "template"}
