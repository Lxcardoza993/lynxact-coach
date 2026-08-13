"""Unit tests for coach.clips — filename parsing + baked/registry I/O.

Uses tmp_path + monkeypatch to isolate from the real data/baked and
data/tmp directories so tests are hermetic.
"""
import json

from coach import clips

# --- parse_stem (pure) ---

def test_parse_stem_three_part():
    technique, player, year = clips.parse_stem("la-croqueta_lionel-messi_2015")
    assert (technique, player, year) == ("la croqueta", "lionel messi", "2015")


def test_parse_stem_hyphens_inside_tokens():
    technique, player, year = clips.parse_stem("body-feint_diego-maradona_1986")
    assert technique == "body feint"
    assert player == "diego maradona"
    assert year == "1986"


def test_parse_stem_single_token_no_player():
    assert clips.parse_stem("solo_2020") == ("solo", "", "2020")


# --- load_baked ---

def test_load_baked_reads_json(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "BAKED_DIR", str(tmp_path))
    (tmp_path / "body-feint.json").write_text(
        json.dumps({"title": "Body Feint", "duration": 10}), encoding="utf-8"
    )
    data = clips.load_baked("body-feint")
    assert data["title"] == "Body Feint"
    assert data["duration"] == 10


def test_load_baked_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "BAKED_DIR", str(tmp_path))
    assert clips.load_baked("nope") is None


# --- registry round-trip ---

def test_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    clips._save_reg({"clip-1": {"title": "Goal.mp4", "duration": 5}})
    assert clips._reg() == {"clip-1": {"title": "Goal.mp4", "duration": 5}}


def test_reg_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "missing.json"))
    assert clips._reg() == {}


# --- get_upload ---

def test_get_upload_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path))
    clips._save_reg({"clip-1": {"title": "Goal.mp4", "duration": 5}})
    (tmp_path / "clip-1.mp4").write_bytes(b"fake")
    up = clips.get_upload("clip-1")
    assert up is not None
    assert up["id"] == "clip-1"
    assert up["title"] == "Goal.mp4"
    assert up["duration"] == 5
    assert up["source"] == "upload"


def test_get_upload_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path))
    assert clips.get_upload("nope") is None


# --- get_clip ---

def test_get_clip_upload_source(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "BAKED_DIR", str(tmp_path / "baked"))
    monkeypatch.setattr(clips, "VAULT_ROOT", str(tmp_path / "vault"))
    clips._save_reg({"clip-1": {"title": "Goal.mp4", "duration": 5}})
    (tmp_path / "clip-1.mp4").write_bytes(b"fake")
    clip = clips.get_clip("clip-1")
    assert clip["source"] == "upload"
    assert clip["baked"] is False
    assert clip["title"] == "Goal.mp4"


def test_get_clip_vault_source(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(clips, "VAULT_ROOT", str(vault))
    monkeypatch.setattr(clips, "BAKED_DIR", str(tmp_path / "baked"))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path / "audio"))
    (vault / "body-feint_diego-maradona_1986.mp4").write_bytes(b"fake")
    clip = clips.get_clip("body-feint_diego-maradona_1986")
    assert clip["source"] == "vault"
    assert clip["technique"] == "body feint"
    assert clip["player"] == "diego maradona"
    assert clip["year"] == "1986"
    assert clip["baked"] is False  # no baked json in isolated BAKED_DIR


def test_get_clip_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path / "audio"))
    assert clips.get_clip("nope") is None
