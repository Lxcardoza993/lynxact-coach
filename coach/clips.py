"""Clip discovery + pre-baked registry.

Vault filenames carry gold labels: <technique>_<player>_<year>.mp4
(technique and player may both contain hyphens; year is the trailing token).
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAKED_DIR = os.path.join(BASE, "data", "baked")
VAULT_ROOT = os.environ.get(
    "VAULT_ROOT", "/home/li/football-dribbling-vault/sports/football/videos"
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


def get_clip(clip_id):
    path = os.path.join(VAULT_ROOT, clip_id + ".mp4")
    if not os.path.exists(path):
        return None
    technique, player, year = parse_stem(clip_id)
    baked = load_baked(clip_id)
    clip = {
        "id": clip_id,
        "file": clip_id + ".mp4",
        "title": f"{player.title()} — {technique.title()} ({year})",
        "technique": technique,
        "player": player,
        "year": year,
        "baked": baked is not None,
        "duration": (baked or {}).get("duration"),
        "cv_context": (baked or {}).get("cv_context"),
    }
    return clip


def list_clips():
    clips = []
    if not os.path.isdir(VAULT_ROOT):
        return clips
    for fname in sorted(os.listdir(VAULT_ROOT)):
        if not fname.lower().endswith(".mp4"):
            continue
        clip = get_clip(fname[:-4])
        if clip:
            clips.append(clip)
    clips.sort(key=lambda c: (not c["baked"], c["id"]))
    return clips
