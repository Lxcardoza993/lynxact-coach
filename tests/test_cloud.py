"""Cloud-mode integration tests — clips.py + app.py with a fake Drive
(no network). The autouse fixture in conftest.py keeps Drive off for the
rest of the suite; these tests turn the fakes on explicitly.
"""
import json
import os

import pytest

import app as app_module
from coach import clips, storage


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Point every clips.* data dir at tmp_path (same as test_delete.py)."""
    dirs = {"TMP_DIR": tmp_path}
    for name in ("VAULT_ROOT", "BAKED_DIR", "POSTER_DIR", "ANNOT_DIR",
                 "CARDS_DIR", "AUDIO_DIR", "UPLOAD_DIR"):
        d = tmp_path / name.lower()
        d.mkdir()
        dirs[name] = d
        monkeypatch.setattr(clips, name, str(d))
    monkeypatch.setattr(clips, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(clips, "UPLOADS_REG", str(tmp_path / "uploads.json"))
    # app.py bound these names at import time — retarget them too
    monkeypatch.setattr(app_module, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "AUDIO_DIR", str(dirs["AUDIO_DIR"]))
    monkeypatch.setattr(app_module, "POSTER_DIR", str(dirs["POSTER_DIR"]))
    return dirs


@pytest.fixture()
def cloud(monkeypatch):
    """Fake cloud: canned catalog + scriptable Drive ops, enabled."""
    state = {
        "catalog": {"vault": [], "uploads": []},
        "deleted": [],
        "posters": {},
        "fetch": None,
        "catalog_errors": False,
    }
    monkeypatch.setattr(storage, "enabled", lambda: True)
    monkeypatch.setattr(storage, "get_catalog", lambda: state["catalog"])
    monkeypatch.setattr(storage, "folder_id",
                        lambda name: {"uploads": "updir"}.get(name))
    monkeypatch.setattr(storage, "delete_file",
                        lambda fid: state["deleted"].append(fid) or True)
    monkeypatch.setattr(storage, "poster_file_id",
                        lambda name: state["posters"].get(name))
    monkeypatch.setattr(storage, "invalidate_posters", lambda: None)

    def fake_update(mutate):
        mutate(state["catalog"])
    monkeypatch.setattr(storage, "update_catalog", fake_update)
    return state


def _seed_catalog_entry(cloud_state, clip_id="step-over_neymar_2015",
                        section="vault", file_id="file-1", title="Neymar Step-over"):
    cloud_state["catalog"][section].append({
        "id": clip_id, "title": title, "duration": 12.3, "file_id": file_id,
    })
    return clip_id


# --- clips layer ------------------------------------------------------------

def test_list_clips_merges_catalog_and_local(isolated, cloud):
    a = _seed_catalog_entry(cloud)
    cid = "local-only_leo-messi_2010"                       # local vault file
    (isolated["VAULT_ROOT"] / (cid + ".mp4")).write_bytes(b"x")
    ids = {c["id"] for c in clips.list_clips()}
    assert ids == {a, cid}
    cloud_clip = next(c for c in clips.list_clips() if c["id"] == a)
    assert cloud_clip["title"] == "Neymar Step-over"
    assert cloud_clip["_file_id"] == "file-1"
    local_clip = next(c for c in clips.list_clips() if c["id"] == cid)
    assert "_file_id" not in local_clip


def test_get_clip_cloud_entry_poster_assumed(isolated, cloud):
    a = _seed_catalog_entry(cloud)
    clip = clips.get_clip(a)
    assert clip["_file_id"] == "file-1"
    assert clip["duration"] == 12.3
    assert clip["poster"] == f"/posters/{a}.jpg"            # cloud poster claim
    assert clips.poster_url(a) == f"/posters/{a}.jpg"


def test_get_clip_cloud_entry_baked_from_local(isolated, cloud, tmp_path):
    a = _seed_catalog_entry(cloud)
    (isolated["BAKED_DIR"] / f"{a}.json").write_text(
        json.dumps({"duration": 9.0, "cv_context": "t"}), encoding="utf-8")
    clip = clips.get_clip(a)
    assert clip["baked"] is True and clip["duration"] == 12.3  # entry wins
    assert clip["cv_context"] == "t"


def test_delete_cloud_only_clip_removes_drive_objects(isolated, cloud):
    a = _seed_catalog_entry(cloud)                          # no local mp4 at all
    cloud["posters"][f"{a}.jpg"] = "poster-1"
    assert clips.delete_clip(a) is True
    assert cloud["deleted"] == ["file-1", "poster-1"]
    assert cloud["catalog"] == {"vault": [], "uploads": []}


def test_delete_cloud_failure_keeps_catalog(isolated, cloud, monkeypatch):
    a = _seed_catalog_entry(cloud)
    def boom(_fid):
        raise storage.StorageError("drive down")
    monkeypatch.setattr(storage, "delete_file", boom)
    assert clips.delete_clip(a) is False
    assert cloud["catalog"]["vault"]                      # truth not destroyed


def test_delete_local_clip_with_cloud_disabled_unchanged(isolated, monkeypatch):
    # regression: local-delete path is byte-for-byte the old behavior when a
    # clip isn't in the catalog
    monkeypatch.setattr(storage, "enabled", lambda: True)
    monkeypatch.setattr(storage, "get_catalog", lambda: {})
    cid = "step-over_neymar_2015"
    (isolated["VAULT_ROOT"] / (cid + ".mp4")).write_bytes(b"x")
    assert clips.delete_clip(cid) is True
    assert clips.list_clips() == []


# --- app routes -------------------------------------------------------------

class FakeResp:
    def __init__(self, status_code=200, headers=None, content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._content = content
        self.closed = False

    def iter_content(self, _n):
        yield self._content

    def close(self):
        self.closed = True


def test_video_route_cloud_206_proxy(isolated, cloud, monkeypatch):
    a = _seed_catalog_entry(cloud)
    resp = FakeResp(206,
                    headers={"Content-Range": "bytes 0-3/1000",
                             "Content-Type": "video/mp4",
                             "Content-Length": "4"},
                    content=b"0123")
    monkeypatch.setattr(storage, "fetch_range", lambda fid, s, e: (206, resp.headers, resp))
    client = app_module.app.test_client()
    r = client.get(f"/video/{a}.mp4", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.headers.get("Accept-Ranges") == "bytes"
    assert r.headers.get("Content-Range") == "bytes 0-3/1000"
    assert r.data == b"0123"
    assert resp.closed is True


def test_video_route_cloud_200_range_ignored_materializes(isolated, cloud, monkeypatch, tmp_path):
    a = _seed_catalog_entry(cloud)
    resp = FakeResp(200, headers={"Content-Type": "video/mp4"}, content=b"0123456789")
    closed = {"v": False}

    def fake_fetch(fid, s, e):
        return 200, resp.headers, resp

    def fake_materialize(fid, fname):
        p = tmp_path / "cloud-cache" / os.path.basename(fname)
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"0123456789")
        return str(p)

    def fake_close():
        closed["v"] = True

    resp.close = fake_close
    monkeypatch.setattr(storage, "fetch_range", fake_fetch)
    monkeypatch.setattr(storage, "materialize", fake_materialize)
    client = app_module.app.test_client()
    r = client.get(f"/video/{a}.mp4", headers={"Range": "bytes=0-"})
    assert closed["v"] is True                        # spool then local range
    assert r.status_code == 206
    assert r.data == b"0123456789"


def test_video_route_falls_back_local_no_file_id(isolated, cloud):
    cid = "local-only_leo-messi_2010"
    (isolated["VAULT_ROOT"] / (cid + ".mp4")).write_bytes(b"0123456789")
    client = app_module.app.test_client()
    r = client.get(f"/video/{cid}.mp4")
    assert r.status_code == 200
    assert r.data == b"0123456789"


def test_poster_route_cloud_bytes(isolated, cloud, monkeypatch):
    monkeypatch.setattr(storage, "poster_bytes", lambda name: b"JPG")
    client = app_module.app.test_client()
    r = client.get("/posters/any-clip.jpg")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.headers.get("Cache-Control") == "public, max-age=86400"
    assert r.data == b"JPG"


def test_poster_route_cloud_failure_falls_back_local(isolated, cloud, monkeypatch):
    def boom(_name):
        raise storage.StorageError("drive down")
    monkeypatch.setattr(storage, "poster_bytes", boom)
    (isolated["POSTER_DIR"] / "c1.jpg").write_bytes(b"LOCALJPG")
    client = app_module.app.test_client()
    r = client.get("/posters/c1.jpg")
    assert r.status_code == 200 and r.data == b"LOCALJPG"


def test_api_library_lists_catalog(isolated, cloud):
    a = _seed_catalog_entry(cloud)
    _seed_catalog_entry(cloud, clip_id="elastico_ronaldinho_2006",
                        file_id="file-2", title="Ronaldinho Elastico")
    client = app_module.app.test_client()
    r = client.get("/api/library")
    assert r.status_code == 200
    items = r.get_json()["clips"]
    assert {c["id"] for c in items} == {a, "elastico_ronaldinho_2006"}
    top = next(c for c in items if c["id"] == a)
    assert top["title"] == "Neymar Step-over"
    assert top["poster"] == f"/posters/{a}.jpg"


def test_upload_route_pushes_to_cloud(isolated, cloud, monkeypatch):
    pushed = {}

    def fake_put_file(path, folder):
        pushed["path"] = path
        pushed["folder"] = folder
        return "fup-1"

    monkeypatch.setattr(storage, "put_file", fake_put_file)
    monkeypatch.setattr(storage, "folder_id",
                        lambda name: "updir" if name == storage.UPLOAD_FOLDER else None)

    class FakeRun:
        def __init__(self, stdout, **kw):
            self.stdout = stdout
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **k: FakeRun("3.5\n" if a[0][0].endswith("ffprobe") else ""))
    client = app_module.app.test_client()
    r = client.post("/api/upload",
                    data={"file": (__import__("io").BytesIO(b"mp4bytes"), "my clip.mp4")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    clip_id = r.headers["Location"].rsplit("/", 1)[1].split("?", 1)[0]
    assert pushed["folder"] == "updir"
    assert os.path.exists(pushed["path"])
    uploads = cloud["catalog"]["uploads"]
    assert len(uploads) == 1
    assert uploads[0]["id"] == clip_id and uploads[0]["file_id"] == "fup-1"
    assert uploads[0]["duration"] == 3.5


def test_upload_route_cloud_failure_still_works(isolated, cloud, monkeypatch):
    def boom(_path, _folder):
        raise storage.StorageError("drive down")
    monkeypatch.setattr(storage, "put_file", boom)
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **k: type("R", (), {"stdout": "1.0"})())
    client = app_module.app.test_client()
    r = client.post("/api/upload",
                    data={"file": (__import__("io").BytesIO(b"mp4bytes"), "x.mp4")},
                    content_type="multipart/form-data")
    assert r.status_code == 302                         # local upload proceeds
    assert cloud["catalog"] == {"vault": [], "uploads": []}
