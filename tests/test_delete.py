"""Delete-clip tests — store cleanup breadth, registry removal, traversal
confinement, and the ADMIN_TOKEN-guarded endpoint.

Hermetic: every directory is monkeypatched into tmp_path (same pattern as
test_clips.py / test_annotations.py); no real data files are touched.
"""
import json

import pytest

from app import app
from coach import clips


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Point every clips.* data dir at tmp_path and return the dirs (plus
    TMP_DIR itself) so tests can seed files."""
    dirs = {"TMP_DIR": tmp_path}
    for name in ("VAULT_ROOT", "BAKED_DIR", "POSTER_DIR", "ANNOT_DIR",
                 "CARDS_DIR", "AUDIO_DIR", "UPLOAD_DIR"):
        d = tmp_path / name.lower()
        d.mkdir()
        dirs[name] = d
        monkeypatch.setattr(clips, name, str(d))
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    return dirs


def _seed_vault_clip(vault, clip_id="step-over_neymar_2015"):
    (vault / (clip_id + ".mp4")).write_bytes(b"x")
    return clip_id


def _seed_derived(dirs, clip_id):
    (dirs["BAKED_DIR"] / (clip_id + ".json")).write_text("{}", encoding="utf-8")
    (dirs["POSTER_DIR"] / (clip_id + ".jpg")).write_bytes(b"j")
    (dirs["CARDS_DIR"] / (clip_id + ".jsonl")).write_text("", encoding="utf-8")
    (dirs["AUDIO_DIR"] / (clip_id + ".wav")).write_bytes(b"w")
    (dirs["ANNOT_DIR"] / (clip_id + ".json")).write_text("[]", encoding="utf-8")
    (dirs["ANNOT_DIR"] / (clip_id + ".json.lock")).write_text("", encoding="utf-8")
    (dirs["ANNOT_DIR"] / (clip_id + ".offset.json")).write_text(
        json.dumps({"offset": 5.0}), encoding="utf-8"
    )


def test_delete_vault_clip_removes_everything(isolated):
    vault = isolated["VAULT_ROOT"]
    clip_id = _seed_vault_clip(vault)
    _seed_derived(isolated, clip_id)
    assert clips.delete_clip(clip_id) is True
    assert not (vault / (clip_id + ".mp4")).exists()
    for d in ("BAKED_DIR", "POSTER_DIR", "CARDS_DIR", "AUDIO_DIR", "ANNOT_DIR"):
        assert list(isolated[d].iterdir()) == []
    assert clips.get_clip(clip_id) is None
    assert clips.list_clips() == []


def test_delete_upload_clip_unregisters(isolated):
    # register_upload moves the src into UPLOAD_DIR and writes the registry.
    src = isolated["TMP_DIR"] / "raw.mp4"
    src.write_bytes(b"x")
    clip_id = clips.register_upload(str(src), "My Clip.mp4", 9.0)
    (isolated["AUDIO_DIR"] / (clip_id + ".wav")).write_bytes(b"w")
    assert clips._reg().get(clip_id) is not None
    assert clips.delete_clip(clip_id) is True
    assert clips._reg() == {}
    assert list(isolated["UPLOAD_DIR"].iterdir()) == []
    assert list(isolated["AUDIO_DIR"].iterdir()) == []


def test_delete_unknown_clip_returns_false(isolated):
    assert clips.delete_clip("no-such-clip") is False


def test_delete_missing_derived_files_still_succeeds(isolated):
    # Only the mp4 + a couple of side files exist — cleanup must not fail.
    vault = isolated["VAULT_ROOT"]
    clip_id = _seed_vault_clip(vault)
    (isolated["ANNOT_DIR"] / (clip_id + ".json.lock")).write_text("", encoding="utf-8")
    assert clips.delete_clip(clip_id) is True
    assert not (vault / (clip_id + ".mp4")).exists()


def test_delete_clip_id_is_baselined(isolated, tmp_path):
    # '../victim' resolves inside the data dirs only; the real victim file
    # outside them must survive, and no data-dir artifact may be touched.
    victim = tmp_path / "victim.mp4"
    victim.write_bytes(b"untouchable")
    assert clips.delete_clip("../victim") is False
    assert victim.exists()


def test_poster_url_present_and_absent(isolated):
    clip_id = _seed_vault_clip(isolated["VAULT_ROOT"])
    assert clips.poster_url(clip_id) is None          # no poster yet
    (isolated["POSTER_DIR"] / (clip_id + ".jpg")).write_bytes(b"j")
    assert clips.poster_url(clip_id) == f"/posters/{clip_id}.jpg"


# --- endpoint ---

def test_delete_endpoint_requires_admin_token(isolated, monkeypatch):
    clip_id = _seed_vault_clip(isolated["VAULT_ROOT"])
    client = app.test_client()
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)          # disabled
    assert client.delete(f"/api/clips/{clip_id}").status_code == 403
    monkeypatch.setenv("ADMIN_TOKEN", "sekret")
    assert client.delete(f"/api/clips/{clip_id}").status_code == 403      # missing header
    assert client.delete(f"/api/clips/{clip_id}",
                         headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert (isolated["VAULT_ROOT"] / (clip_id + ".mp4")).exists()          # untouched


def test_delete_endpoint_removes_with_token(isolated, monkeypatch):
    clip_id = _seed_vault_clip(isolated["VAULT_ROOT"])
    monkeypatch.setenv("ADMIN_TOKEN", "sekret")
    client = app.test_client()
    r = client.delete(f"/api/clips/{clip_id}", headers={"X-Admin-Token": "sekret"})
    assert r.status_code == 200
    assert r.get_json() == {"deleted": clip_id}
    assert not (isolated["VAULT_ROOT"] / (clip_id + ".mp4")).exists()
    # already gone -> 404 on repeat
    assert client.delete(f"/api/clips/{clip_id}",
                         headers={"X-Admin-Token": "sekret"}).status_code == 404


def test_delete_endpoint_traversal_not_deleteable(isolated, monkeypatch):
    # A clip id that isn't a real clip is 404 — never a delete.
    monkeypatch.setenv("ADMIN_TOKEN", "sekret")
    client = app.test_client()
    r = client.delete("/api/clips/..victim", headers={"X-Admin-Token": "sekret"})
    assert r.status_code == 404
