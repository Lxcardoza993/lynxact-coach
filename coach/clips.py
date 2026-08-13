"""Clip discovery + pre-baked registry + uploaded clips.

Vault filenames carry gold labels: <technique>_<player>_<year>.mp4
(technique and player may both contain hyphens; year is the trailing token).
Uploaded clips live in data/tmp with a small JSON registry.
"""
import json
import os
import re
import uuid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAKED_DIR = os.path.join(BASE, "data", "baked")
TMP_DIR = os.path.join(BASE, "data", "tmp")
UPLOAD_DIR = os.path.join(TMP_DIR, "uploads")
AUDIO_DIR = os.path.join(TMP_DIR, "audio")
UPLOADS_REG = os.path.join(TMP_DIR, "uploads.json")
VAULT_ROOT = os.environ.get(
    "VAULT_ROOT",
    os.path.join(BASE, "data", "vault"),
)


def parse_stem(stem):
    """'la-croqueta_lionel-messi_2015' -> (technique, player, year)."""
    rest, _, year = stem.rpartition("_")
    technique, _, player = rest.partition("_")
    return technique.replace("-", " "), player.replace("-", " "), year


def load_baked(clip_id):
    path = os.path.join(BAKED_DIR, clip_id + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _reg():
    if not os.path.exists(UPLOADS_REG):
        return {}
    try:
        with open(UPLOADS_REG, encoding="utf-8") as f:
            reg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return reg if isinstance(reg, dict) else {}


def _save_reg(reg):
    os.makedirs(TMP_DIR, exist_ok=True)
    tmp = UPLOADS_REG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, UPLOADS_REG)


def register_upload(src_path, orig_name, duration):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(orig_name)[0].lower()).strip("-")
    clip_id = f"{slug[:40]}-{uuid.uuid4().hex[:6]}"
    dest = os.path.join(UPLOAD_DIR, clip_id + ".mp4")
    os.replace(src_path, dest)
    reg = _reg()
    reg[clip_id] = {"title": orig_name, "duration": duration}
    _save_reg(reg)
    return clip_id


def get_upload(clip_id):
    entry = _reg().get(clip_id)
    if not entry:
        return None
    path = os.path.join(UPLOAD_DIR, clip_id + ".mp4")
    if not os.path.exists(path):
        return None
    audio = os.path.join(AUDIO_DIR, clip_id + ".wav")
    return {
        "id": clip_id,
        "file": clip_id + ".mp4",
        "title": entry["title"],
        "duration": entry.get("duration"),
        "audio_wav": audio if os.path.exists(audio) else None,
        "source": "upload",
    }


def get_clip(clip_id):
    up = get_upload(clip_id)
    if up:
        return {**up, "technique": None, "player": None, "year": None,
                "baked": False, "cv_context": None}
    path = os.path.join(VAULT_ROOT, clip_id + ".mp4")
    if not os.path.exists(path):
        return None
    technique, player, year = parse_stem(clip_id)
    baked = load_baked(clip_id)
    return {
        "id": clip_id,
        "file": clip_id + ".mp4",
        "title": f"{player.title()} — {technique.title()} ({year})",
        "technique": technique,
        "player": player,
        "year": year,
        "baked": baked is not None,
        "duration": (baked or {}).get("duration"),
        "cv_context": (baked or {}).get("cv_context"),
        "source": "vault",
    }


def video_dir(clip_id):
    return UPLOAD_DIR if get_upload(clip_id) else VAULT_ROOT


def list_clips():
    clips = []
    for clip_id in _reg():
        clip = get_clip(clip_id)
        if clip:
            clips.append(clip)
    if os.path.isdir(VAULT_ROOT):
        for fname in sorted(os.listdir(VAULT_ROOT)):
            if fname.lower().endswith(".mp4"):
                clip = get_clip(fname[:-4])
                if clip:
                    clips.append(clip)
    clips.sort(key=lambda c: (c["source"] != "upload", not c["baked"], c["id"]))
    return clips
