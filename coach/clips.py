"""Clip discovery + pre-baked registry + uploaded clips.

Vault filenames carry gold labels: <technique>_<player>_<year>.mp4
(technique and player may both contain hyphens; year is the trailing token).
Uploaded clips live in data/tmp with a small JSON registry.
"""
import json
import os
import re
import uuid

from coach import storage

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAKED_DIR = os.path.join(BASE, "data", "baked")
POSTER_DIR = os.path.join(BASE, "data", "posters")
ANNOT_DIR = os.path.join(BASE, "data", "annotations")
TMP_DIR = os.path.join(BASE, "data", "tmp")
UPLOAD_DIR = os.path.join(TMP_DIR, "uploads")
AUDIO_DIR = os.path.join(TMP_DIR, "audio")
CARDS_DIR = os.path.join(TMP_DIR, "cards")
UPLOADS_REG = os.path.join(TMP_DIR, "uploads.json")
VAULT_ROOT = os.environ.get(
    "VAULT_ROOT",
    os.path.join(BASE, "data", "vault"),
)


def _num(v, default=0.0) -> float:
    """Coerce a card field to float for sort keys / :.1f formats; LLM cards
    sometimes emit t/rating as strings. Returns default on non-numeric values."""
    if isinstance(v, int | float):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_stem(stem: str) -> tuple[str, str, str]:
    """'la-croqueta_lionel-messi_2015' -> (technique, player, year)."""
    rest, _, year = stem.rpartition("_")
    technique, _, player = rest.partition("_")
    return technique.replace("-", " "), player.replace("-", " "), year


def load_baked(clip_id: str) -> dict | None:
    """Load the pre-baked analysis JSON for clip_id, or None if absent/corrupt.

    A baked file that fails to parse (truncated pull, disk corruption) or isn't
    a JSON object degrades to None — clip shown as unbaked — rather than raising
    through get_clip -> list_clips and 500-ing the clip index. Mirrors _reg.
    """
    path = os.path.join(BAKED_DIR, os.path.basename(clip_id) + ".json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _reg() -> dict:
    if not os.path.exists(UPLOADS_REG):
        return {}
    try:
        with open(UPLOADS_REG, encoding="utf-8") as f:
            reg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return reg if isinstance(reg, dict) else {}


def _save_reg(reg: dict) -> None:
    os.makedirs(TMP_DIR, exist_ok=True)
    tmp = UPLOADS_REG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, UPLOADS_REG)


def register_upload(src_path: str, orig_name: str, duration: float) -> str:
    """Move an uploaded mp4 into UPLOAD_DIR, register title/duration, return its clip_id."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(orig_name)[0].lower()).strip("-")
    clip_id = f"{slug[:40]}-{uuid.uuid4().hex[:6]}"
    dest = os.path.join(UPLOAD_DIR, clip_id + ".mp4")
    os.replace(src_path, dest)
    reg = _reg()
    reg[clip_id] = {"title": orig_name, "duration": duration}
    _save_reg(reg)
    return clip_id


def poster_url(clip_id: str) -> str | None:
    """Preview-poster URL for a clip ('/posters/<id>.jpg'), or None when no
    poster is known (index card then falls back to a placeholder). In cloud
    mode the poster may live only on Drive, so a catalog entry claims one."""
    clip_id = os.path.basename(clip_id)
    if os.path.exists(os.path.join(POSTER_DIR, clip_id + ".jpg")):
        return f"/posters/{clip_id}.jpg"
    if _cloud_entry(clip_id) is not None:
        return f"/posters/{clip_id}.jpg"
    return None


# --- cloud mode (Drive catalog) -------------------------------------------------

def _cloud_catalog() -> dict:
    """The cloud clip catalog ({vault:[...], uploads:[...]}) or {} — a Drive
    failure degrades to local discovery rather than raising out of a page."""
    if not storage.enabled():
        return {}
    try:
        return storage.get_catalog()
    except storage.StorageError:
        return {}


_CLOUD_SOURCE = {"uploads": "upload", "vault": "vault"}


def _cloud_entry(clip_id: str) -> dict | None:
    for section in ("uploads", "vault"):
        for entry in _cloud_catalog().get(section, []):
            if entry.get("id") == clip_id:
                return {**entry, "source": _CLOUD_SOURCE[section]}
    return None


def _cloud_clip_dict(clip_id: str, entry: dict) -> dict:
    """Shape a catalog entry into the standard clip dict. Baked analysis and
    annotations stay local — the catalog only defines the video asset."""
    baked = load_baked(clip_id)
    technique, player, year = parse_stem(clip_id)
    title = entry.get("title")
    if not title and entry.get("source") != "upload":
        title = f"{player.title()} — {technique.title()} ({year})"
    return {
        "id": clip_id,
        "file": clip_id + ".mp4",
        "title": title or clip_id,
        "technique": technique,
        "player": player,
        "year": year,
        "baked": baked is not None,
        "duration": entry.get("duration") or (baked or {}).get("duration"),
        "cv_context": (baked or {}).get("cv_context"),
        "poster": f"/posters/{clip_id}.jpg",
        "source": entry.get("source", "vault"),
        "_file_id": entry.get("file_id"),
    }


def get_upload(clip_id: str) -> dict | None:
    """Return the uploaded-clip dict, or None if unregistered or its mp4 is missing."""
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
        "title": entry.get("title", clip_id),
        "duration": entry.get("duration"),
        "audio_wav": audio if os.path.exists(audio) else None,
        "poster": poster_url(clip_id),
        "source": "upload",
    }


def get_clip(clip_id: str) -> dict | None:
    """Return the clip dict (cloud catalog entry, upload, or vault), or None."""
    clip_id = os.path.basename(clip_id)   # external id can't escape its dirs
    cloud = _cloud_entry(clip_id)
    if cloud is not None:
        return _cloud_clip_dict(clip_id, cloud)
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
        "poster": poster_url(clip_id),
        "source": "vault",
    }


def video_dir(clip_id: str) -> str:
    """Return the directory holding clip_id's mp4 (UPLOAD_DIR for uploads, else VAULT_ROOT)."""
    return UPLOAD_DIR if get_upload(clip_id) else VAULT_ROOT


def _derived_paths(clip_id: str) -> list[str]:
    """Every derived artifact a clip can leave behind (baked analysis, poster,
    event-card cache, extracted audio, telestration annotations + lock +
    alignment offset). All best-effort — their absence is never an error."""
    return [
        os.path.join(BAKED_DIR, clip_id + ".json"),
        os.path.join(POSTER_DIR, clip_id + ".jpg"),
        os.path.join(CARDS_DIR, clip_id + ".jsonl"),
        os.path.join(AUDIO_DIR, clip_id + ".wav"),
        os.path.join(ANNOT_DIR, clip_id + ".json"),
        os.path.join(ANNOT_DIR, clip_id + ".json.lock"),
        os.path.join(ANNOT_DIR, clip_id + ".offset.json"),
    ]


def _catalog_drop(cat: dict, clip_id: str) -> None:
    for section in ("vault", "uploads"):
        cat[section] = [e for e in cat.get(section, []) if e.get("id") != clip_id]


def delete_clip(clip_id: str) -> bool:
    """Delete a clip and every file derived from it, and (for uploads) drop
    its registry entry. In cloud mode also removes the Drive objects and the
    catalog entry. Returns True iff no mp4 is left — neither locally nor in
    the cloud catalog.

    clip_id is basenamed exactly like get_clip, so ids such as '../evil'
    cannot escape the data dirs. Side files are removed best-effort — the
    mp4/catalog removal alone decides success, matching the store discipline
    that a missing artifact degrades instead of raising.
    """
    clip_id = os.path.basename(clip_id)
    if not clip_id:
        return False
    cloud = _cloud_entry(clip_id)
    mp4 = os.path.join(video_dir(clip_id), clip_id + ".mp4")
    mp4_existed = os.path.exists(mp4)
    if not mp4_existed and not cloud:
        return False
    for path in [mp4] + _derived_paths(clip_id):
        try:
            os.remove(path)
        except OSError:
            pass                 # side file already gone / locked — continue cleanup
    if _reg().get(clip_id):      # upload source: unregister it
        reg = _reg()
        del reg[clip_id]
        _save_reg(reg)
    cloud_gone = not cloud
    if cloud:
        try:
            if cloud.get("file_id"):
                storage.delete_file(cloud["file_id"])
            poster_id = storage.poster_file_id(clip_id + ".jpg")
            if poster_id:
                storage.delete_file(poster_id)
                storage.invalidate_posters()
            storage.update_catalog(lambda cat: _catalog_drop(cat, clip_id))
            cloud_gone = True
        except storage.StorageError:
            cloud_gone = False        # keep catalog truth on partial cloud failures
    return (not os.path.exists(mp4)) and cloud_gone


def list_clips() -> list[dict]:
    """Return all clips — uploads first, then vault — sorted for display.

    Cloud mode merges catalog entries with local files by id (catalog wins),
    so a half-migrated instance still shows one coherent list."""
    seen: dict[str, dict] = {}
    for section in ("uploads", "vault"):
        for entry in _cloud_catalog().get(section, []):
            entry_id = entry.get("id")
            if entry_id and entry_id not in seen:
                seen[entry_id] = _cloud_clip_dict(
                    entry_id, {**entry, "source": _CLOUD_SOURCE[section]}
                )
    for clip_id in _reg():
        if clip_id not in seen:
            clip = get_clip(clip_id)
            if clip:
                seen[clip_id] = clip
    if os.path.isdir(VAULT_ROOT):
        for fname in sorted(os.listdir(VAULT_ROOT)):
            clip_id = fname[:-4]
            if fname.lower().endswith(".mp4") and clip_id not in seen:
                clip = get_clip(clip_id)
                if clip:
                    seen[clip_id] = clip
    clips = list(seen.values())
    clips.sort(key=lambda c: (c["source"] != "upload", not c["baked"], c["id"]))
    return clips
