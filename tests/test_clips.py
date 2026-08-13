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


def test_load_baked_corrupt_json_returns_none(tmp_path, monkeypatch):
    # A corrupt baked JSON (truncated pull / disk corruption) must degrade to
    # None (clip shown as unbaked), not raise through get_clip -> list_clips ->
    # 500 on the clip index. Mirrors _reg's corrupt-JSON guard.
    monkeypatch.setattr(clips, "BAKED_DIR", str(tmp_path))
    (tmp_path / "body-feint.json").write_text("{ broken", encoding="utf-8")
    assert clips.load_baked("body-feint") is None


def test_load_baked_non_dict_returns_none(tmp_path, monkeypatch):
    # A baked file that parses but isn't an object (e.g. a JSON list) must also
    # degrade to None — mirrors _reg's isinstance guard so a non-dict baked
    # payload can't leak through as a list into get_clip's (baked or {}) chain.
    monkeypatch.setattr(clips, "BAKED_DIR", str(tmp_path))
    (tmp_path / "x.json").write_text('["not", "a", "dict"]', encoding="utf-8")
    assert clips.load_baked("x") is None


# --- registry round-trip ---

def test_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    clips._save_reg({"clip-1": {"title": "Goal.mp4", "duration": 5}})
    assert clips._reg() == {"clip-1": {"title": "Goal.mp4", "duration": 5}}


def test_reg_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "missing.json"))
    assert clips._reg() == {}


def test_reg_corrupt_json_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    (tmp_path / "uploads.json").write_text("{ broken", encoding="utf-8")
    assert clips._reg() == {}


def test_reg_non_dict_returns_empty(tmp_path, monkeypatch):
    # A registry serialized as a JSON list (corruption) must not leak through
    # as a list — later reg[id] = ... would raise TypeError.
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    (tmp_path / "uploads.json").write_text('["not", "a", "dict"]', encoding="utf-8")
    assert clips._reg() == {}


def test_save_reg_no_tmp_left(tmp_path, monkeypatch):
    # Atomic write (temp + os.replace) must leave no orphan .tmp behind.
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    clips._save_reg({"a": 1})
    assert not (tmp_path / "uploads.json.tmp").exists()
    assert clips._reg() == {"a": 1}


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


def test_get_upload_entry_but_file_missing(tmp_path, monkeypatch):
    # Registry entry exists but the mp4 is gone (out-of-sync registry) —
    # get_upload returns None via the path-check branch rather than a
    # dict pointing at a non-existent file.
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path))
    clips._save_reg({"clip-1": {"title": "Goal.mp4", "duration": 5}})
    assert clips.get_upload("clip-1") is None


def test_get_upload_entry_missing_title_falls_back_to_id(tmp_path, monkeypatch):
    # A registry entry lacking "title" (partial corruption / hand-edit) must
    # fall back to the clip_id, not KeyError — get_upload already reads
    # "duration" defensively with .get; the title read must match that pattern.
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path))
    clips._save_reg({"clip-1": {"duration": 5}})   # no title key
    (tmp_path / "clip-1.mp4").write_bytes(b"fake")
    up = clips.get_upload("clip-1")
    assert up is not None
    assert up["title"] == "clip-1"          # slug fallback, not KeyError


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


# --- register_upload ---

def test_register_upload_moves_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    src = tmp_path / "raw.mp4"
    src.write_bytes(b"fake-mp4")
    clip_id = clips.register_upload(str(src), "My Goal Clip.mp4", 12.5)
    assert clip_id.startswith("my-goal-clip")          # slug from orig name
    # source file moved into UPLOAD_DIR
    assert (tmp_path / "uploads" / (clip_id + ".mp4")).exists()
    assert not src.exists()
    # registry records title + duration
    reg = clips._reg()
    assert reg[clip_id]["title"] == "My Goal Clip.mp4"
    assert reg[clip_id]["duration"] == 12.5


# --- list_clips ---

def test_list_clips_merges_uploads_and_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(clips, "VAULT_ROOT", str(vault))
    monkeypatch.setattr(clips, "BAKED_DIR", str(tmp_path / "baked"))
    src = tmp_path / "u.mp4"
    src.write_bytes(b"x")
    up_id = clips.register_upload(str(src), "Upload.mp4", 5)
    (vault / "stepover_lionel-messi_2015.mp4").write_bytes(b"x")
    result = clips.list_clips()
    ids = [c["id"] for c in result]
    assert up_id in ids
    assert "stepover_lionel-messi_2015" in ids
    assert result[0]["source"] == "upload"     # uploads sort before vault


def test_list_clips_survives_corrupt_baked(tmp_path, monkeypatch):
    # One vault clip with a corrupt baked JSON must NOT kill the whole list
    # with a 500 — it degrades to "unbaked" (analysis absent) and is still
    # listed. Blast-radius hardening: one bad entry -> shaped-as-unbaked, not
    # a crashed index page (same "skip malformed" rule as the speechmatics reader).
    vault = tmp_path / "vault"
    vault.mkdir()
    baked = tmp_path / "baked"
    baked.mkdir()
    monkeypatch.setattr(clips, "VAULT_ROOT", str(vault))
    monkeypatch.setattr(clips, "BAKED_DIR", str(baked))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path / "audio"))
    (vault / "good_lionel-messi_2015.mp4").write_bytes(b"x")
    (vault / "bad_lionel-messi_2016.mp4").write_bytes(b"x")
    (baked / "bad.json").write_text("{ broken", encoding="utf-8")
    result = clips.list_clips()                 # must not raise
    by_id = {c["id"]: c for c in result}
    assert "good_lionel-messi_2015" in by_id
    assert by_id["bad_lionel-messi_2016"]["baked"] is False   # corrupt -> unbaked


def test_list_clips_skips_non_mp4_and_missing_upload_files(tmp_path, monkeypatch):
    # list_clips must skip: (a) non-.mp4 files in the vault (a stray README/json),
    # (b) registry entries whose mp4 has vanished and whose clip_id matches no
    # vault file (out-of-sync registry). Neither should appear in the result.
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(clips, "VAULT_ROOT", str(vault))
    monkeypatch.setattr(clips, "BAKED_DIR", str(tmp_path / "baked"))
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    monkeypatch.setattr(clips, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(clips, "AUDIO_DIR", str(tmp_path / "audio"))
    (vault / "stepover_lionel-messi_2015.mp4").write_bytes(b"x")
    (vault / "notes.txt").write_bytes(b"ignore me")          # non-.mp4 -> skip
    clips._save_reg({"orphan-abc123": {"title": "Gone.mp4", "duration": 5}})  # mp4 missing
    result = clips.list_clips()
    ids = [c["id"] for c in result]
    assert "stepover_lionel-messi_2015" in ids
    assert "orphan-abc123" not in ids                        # missing mp4 -> skipped
