"""SSE timeline engine.

COACH_MODE=replay: interleave baked transcript lines + event cards, scaled by
REPLAY_SPEED, and stream them as Server-Sent Events. This is the demo
fallback that can never die on stage.

COACH_MODE=live: delegate to coach.claude (D2 wiring) — same event shapes.
"""
import json
import os
import time
from collections.abc import Iterator

from .clips import load_baked


def sse(kind: str, payload: dict) -> str:
    """Format a Server-Sent Event string for the given kind + payload."""
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def replay_events(clip_id: str) -> Iterator[str]:
    """Replay baked transcript lines + event cards as SSE, paced by REPLAY_SPEED."""
    data = load_baked(clip_id)
    if data is None:
        return
    speed = float(os.environ.get("REPLAY_SPEED", "2.0")) or 1.0
    yield sse("meta", {
        "clip": data["clip"],
        "title": data["title"],
        "duration": data["duration"],
        "cv_context": data.get("cv_context"),
    })
    items = [{"t": x["t"], "kind": "transcript", "payload": x} for x in data["transcript"]]
    items += [{"t": x["t"], "kind": "card", "payload": x} for x in data["events"]]
    items.sort(key=lambda x: (x["t"], 0 if x["kind"] == "transcript" else 1))
    last = 0.0
    for item in items:
        delay = max(0.0, (item["t"] - last) / speed)
        last = item["t"]
        if delay:
            time.sleep(delay)
        yield sse(item["kind"], item["payload"])
    yield sse("done", {"clip": data["clip"]})


def sse_stream(clip_id: str, mode: str | None = None) -> Iterator[str]:
    """SSE timeline — live (claude) or replay mode, per COACH_MODE."""
    mode = mode or os.environ.get("COACH_MODE", "replay")
    if mode == "live":
        from . import claude
        yield from claude.live_events(clip_id)
    else:
        yield from replay_events(clip_id)
