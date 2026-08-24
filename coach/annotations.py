"""Telestration (战术画板) annotations store.

A coach pauses the video and draws arrow / freehand / rect strokes over a
frame; each stroke is anchored to a clip timestamp and persisted per clip
in ``data/annotations/<clip_id>.json``. Points are normalized to the video
display area ([0,1] × [0,1]) so they stay aligned when the player resizes.

Boundary discipline mirrors ``clips.py``:
- clip_id is basenamed (external ids cannot escape ``ANNOT_DIR``);
- every payload is validated at the store boundary (tool/color whitelists,
  finite normalized points, per-clip item cap);
- writes are atomic (tmp file + ``os.replace``);
- corrupt files degrade to an empty list rather than raising.
"""
import json
import math
import os
import re
import uuid

try:
    import fcntl
except ImportError:          # non-POSIX (dev-only): fall back to unlocked writes  # pragma: no cover
    fcntl = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANNOT_DIR = os.path.join(BASE, "data", "annotations")

TOOLS = frozenset({"arrow", "freehand", "rect"})
COLORS = frozenset({"#58a6ff", "#f2cc60", "#3fb950", "#f85149"})

MAX_ITEMS = 200        # per clip
MAX_POINTS = 600       # per stroke
MAX_LABEL = 120        # chars
MAX_T = 24 * 3600      # seconds — generous clip-length ceiling

_UID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _path(clip_id: str) -> str:
    """Path of the annotations file for clip_id — the id cannot escape ANNOT_DIR."""
    return os.path.join(ANNOT_DIR, os.path.basename(clip_id) + ".json")


class _locked:
    """Per-clip exclusive lock around read-modify-write cycles.

    Guards add/delete/clear against interleaving (flask threaded dev server,
    multi-worker gunicorn): without it, two concurrent adds both load the old
    list and the last save wins, silently dropping a stroke. The lock lives on
    a dedicated *.lock file that never gets os.replace'd, so flock stays valid
    across writers. Non-POSIX platforms run unlocked (demo-grade fallback).
    """

    def __init__(self, clip_id: str):
        self._path = _path(clip_id) + ".lock"

    def __enter__(self):
        os.makedirs(ANNOT_DIR, exist_ok=True)
        self._fh = open(self._path, "a", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if fcntl is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()
        return False


def _valid_item(it) -> bool:
    """One persisted item is structurally valid (skips corrupt entries on load)."""
    if not isinstance(it, dict):
        return False
    if not _UID_RE.match(str(it.get("uid", ""))) or not _ok_t(it.get("t")):
        return False
    if it.get("tool") not in TOOLS or it.get("color") not in COLORS:
        return False
    return _ok_points(it.get("points"))


def _ok_t(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= MAX_T


def _ok_points(points) -> bool:
    """List of ≥2 normalized [x, y] pairs; every coordinate finite and in [0,1]."""
    if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
        return False
    for pt in points:
        if (not isinstance(pt, list | tuple) or len(pt) != 2
                or not _ok_coord(pt[0]) or not _ok_coord(pt[1])):
            return False
    return True


def _ok_coord(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1


def _load(clip_id: str) -> list[dict]:
    p = _path(clip_id)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []   # truncated write / disk error — degrade to empty, never raise
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [it for it in items if _valid_item(it)]


def _save(clip_id: str, items: list[dict]) -> None:
    """Atomic write: dump to a tmp file, then os.replace onto the real path."""
    os.makedirs(ANNOT_DIR, exist_ok=True)
    tmp = _path(clip_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False)
    os.replace(tmp, _path(clip_id))


def list_annotations(clip_id: str) -> list[dict]:
    """All annotations for a clip, oldest first. Empty list for unknown clips."""
    return _load(clip_id)


def add_annotation(clip_id: str, payload) -> dict:
    """Validate and append one stroke; returns the stored item (with uid).

    Raises ValueError with a terse reason when the payload fails validation
    or the per-clip cap would be exceeded — callers map that to HTTP 400.
    """
    if not isinstance(payload, dict):
        raise ValueError("body must be a JSON object")
    t = payload.get("t")
    if not _ok_t(t):
        raise ValueError("t must be a finite number of seconds (0–86400)")
    tool = payload.get("tool")
    if tool not in TOOLS:
        raise ValueError(f"tool must be one of {sorted(TOOLS)}")
    color = payload.get("color")
    if color not in COLORS:
        raise ValueError(f"color must be one of {sorted(COLORS)}")
    points = payload.get("points")
    if not _ok_points(points):
        raise ValueError("points must be 2–600 normalized [x, y] pairs in [0,1]")
    label = payload.get("label")
    if label is not None:
        if not isinstance(label, str):
            raise ValueError("label must be a string")
        label = label.strip()[:MAX_LABEL]
    with _locked(clip_id):
        items = _load(clip_id)
        if len(items) >= MAX_ITEMS:
            raise ValueError(f"annotation cap reached ({MAX_ITEMS} per clip)")
        item = {
            "uid": uuid.uuid4().hex[:12],
            "t": float(t),
            "tool": tool,
            "color": color,
            "points": [[float(x), float(y)] for x, y in points],
        }
        if label:
            item["label"] = label
        items.append(item)
        _save(clip_id, items)
    return item
    return item


def delete_annotation(clip_id: str, uid: str) -> bool:
    """Remove one stroke by uid. Returns True if it was removed, False otherwise."""
    if not isinstance(uid, str) or not _UID_RE.match(uid):
        return False
    with _locked(clip_id):
        items = _load(clip_id)
        kept = [it for it in items if it.get("uid") != uid]
        if len(kept) == len(items):
            return False
        _save(clip_id, kept)
    return True


def clear_annotations(clip_id: str) -> int:
    """Remove every stroke for a clip; returns how many were removed."""
    with _locked(clip_id):
        items = _load(clip_id)
        if items:
            _save(clip_id, [])
    return len(items)


# ---- clip alignment offset (视频片头/掐头去尾 → 事件时间轴平移) ----

OFFSET_MAX_ABS = 3600  # ±1h ceiling — offsets beyond this are nonsense for clips


def _offset_path(clip_id: str) -> str:
    return os.path.join(ANNOT_DIR, os.path.basename(clip_id) + ".offset.json")


def get_offset(clip_id: str) -> float:
    """Alignment offset for clip_id in seconds; 0.0 when unset/corrupt/invalid."""
    path = _offset_path(clip_id)
    if not os.path.exists(path):
        return 0.0
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        x = float(raw.get("offset", 0.0))
        if not math.isfinite(x) or abs(x) > OFFSET_MAX_ABS:
            return 0.0
        return x
    except (OSError, ValueError, TypeError):
        return 0.0


def set_offset(clip_id: str, offset: object) -> float:
    """Persist the alignment offset. Raises ValueError on non-finite/oversized input."""
    try:
        x = float(offset)
    except (TypeError, ValueError):
        raise ValueError("offset must be a number") from None
    if not math.isfinite(x) or abs(x) > OFFSET_MAX_ABS:
        raise ValueError("offset must be finite and within ±3600s")
    with _locked(clip_id):
        os.makedirs(ANNOT_DIR, exist_ok=True)
        tmp = _offset_path(clip_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"offset": x}, fh)
        os.replace(tmp, _offset_path(clip_id))
    return x
