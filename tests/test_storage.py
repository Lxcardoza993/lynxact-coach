"""Storage layer tests — scriptable fake Drive transport, hermetic (no
network, no real tokens). Module caches are reset per test and CACHE_DIR /
CATALOG_LOCK are pointed into tmp_path, mirroring the other test modules.
"""
import json
import os

import pytest

from coach import storage

FAR_FUTURE = 1e15   # _ids ts stamp that never triggers a re-resolve


class FakeResp:
    """Minimal requests.Response stand-in (status/headers/json/iter/close)."""

    def __init__(self, status_code=200, headers=None, content=b"",
                 json_data=None, text=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._content = content
        self.content = content
        self._json = json_data
        # mirror requests: content is bytes, text is its decoded form —
        # json_data supplies the text for alt=media JSON payloads
        self.text = text if text is not None else (
            json.dumps(json_data) if json_data is not None
            else (content.decode("utf-8", "replace") if isinstance(content, bytes) else ""))
        self.closed = False

    def json(self):
        if self._json is not None:
            return self._json
        if not (self.text or "").strip():
            return {}
        return json.loads(self.text)

    def iter_content(self, _n):
        yield self._content

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeDrive:
    """Scriptable transport: queue of (method, url-substring, FakeResp),
    consumed in order; oauth refresh goes through post(), resumable PUTs live
    in the same queue with method "PUT"."""

    def __init__(self):
        self.handlers = []
        self.calls = []
        self.token = FakeResp(json_data={"access_token": "at", "expires_in": 3600})

    def add(self, method, contains, resp):
        self.handlers.append((method, contains, resp))
        return resp

    def _take(self, method, url, **kw):
        key = url + "||" + json.dumps(kw.get("params") or {}, sort_keys=True)
        self.calls.append((method, key, kw))
        for i, (m, contains, resp) in enumerate(self.handlers):
            if m == method and contains in key:
                self.handlers.pop(i)
                return resp
        pytest.fail(f"unhandled drive call {method} {key}")

    def request(self, method, url, **kw):
        return self._take(method, url, **kw)

    def post(self, url, **kw):
        self.calls.append(("OAUTH", json.dumps(kw.get("data") or {}, sort_keys=True), kw))
        return self.token

    def put(self, url, **kw):
        return self._take("PUT", url)

    def get_calls(self):
        """Drive requests only (drop OAUTH bookkeeping)."""
        return [c for c in self.calls if c[0] in ("GET", "POST", "PUT", "PATCH", "DELETE")]


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    """Reset module state, disable overrides, fake transport in place."""
    drive = FakeDrive()
    monkeypatch.setattr(storage.requests, "request", drive.request)
    monkeypatch.setattr(storage.requests, "post", drive.post)
    monkeypatch.setattr(storage.requests, "put", drive.put)
    monkeypatch.setattr(storage, "_token", {"access": None, "expires": 0.0})
    monkeypatch.setattr(storage, "_ids", {"ts": 0.0, "root": None,
                                          "folders": {}, "catalog": None})
    monkeypatch.setattr(storage, "_catalog", {"ts": 0.0, "data": None})
    monkeypatch.setattr(storage, "_posters", {"ts": 0.0, "map": {}})
    monkeypatch.setattr(storage, "_poster_bytes", {})
    monkeypatch.setattr(storage, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(storage, "CATALOG_LOCK", str(tmp_path / "catalog.lock"))
    monkeypatch.setenv("DRIVE_REFRESH_TOKEN", "rt")
    for key in ("DRIVE_ROOT_FOLDER_ID", "DRIVE_VAULT_FOLDER_ID",
                "DRIVE_POSTER_FOLDER_ID", "DRIVE_UPLOAD_FOLDER_ID",
                "DRIVE_CATALOG_FILE_ID", "DRIVE_CLIENT_ID", "DRIVE_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    return drive


def _queue_ids(drive, sub_ids=None, catalog_id=None):
    """Queue the standard id-resolution lookups: subfolder queries + catalog."""
    sub_ids = sub_ids or {"vault": "v1", "posters": "p1", "uploads": "u1"}
    for sub in ("vault", "posters", "uploads"):
        if sub in sub_ids and sub_ids[sub] is not None:
            drive.add("GET", f"name='{sub}'",
                      FakeResp(json_data={"files": [{"id": sub_ids[sub]}]}))
    drive.add("GET", "name='catalog.json'",
              FakeResp(json_data={"files": [] if not catalog_id
                                  else [{"id": catalog_id}]}))


# --- enabled / token ---------------------------------------------------------

def test_enabled_gated_by_env(monkeypatch):
    monkeypatch.delenv("DRIVE_REFRESH_TOKEN", raising=False)
    assert storage.enabled() is False
    monkeypatch.setenv("DRIVE_REFRESH_TOKEN", "rt")
    assert storage.enabled() is True


def test_access_token_cached_then_refreshed(fresh, monkeypatch):
    assert storage.access_token() == "at"
    assert storage.access_token() == "at"
    assert len([c for c in fresh.calls if c[0] == "OAUTH"]) == 1
    monkeypatch.setattr(storage, "_now", lambda: 9e15)          # way past expiry
    storage.access_token()
    assert len([c for c in fresh.calls if c[0] == "OAUTH"]) == 2


def test_drive_req_401_refreshes_and_retries(fresh, monkeypatch):
    monkeypatch.setattr(storage.time, "sleep", lambda _s: None)
    fresh.add("GET", "files", FakeResp(401))
    fresh.add("GET", "files", FakeResp(200, json_data={"files": []}))
    resp = storage.drive_req("GET", storage.DRIVE_API + "/files")
    assert resp.status_code == 200
    assert len(fresh.get_calls()) == 2                      # exactly one retry


def test_drive_req_403_shard_flake_retries(fresh, monkeypatch):
    """Transient Drive shard-consistency 403s self-heal on retry."""
    monkeypatch.setattr(storage.time, "sleep", lambda _s: None)
    fresh.add("GET", "files", FakeResp(403))
    fresh.add("GET", "files", FakeResp(200, json_data={"files": [{"id": "x"}]}))
    resp = storage.drive_req("GET", storage.DRIVE_API + "/files")
    assert resp.status_code == 200
    assert len(fresh.get_calls()) == 2


def test_drive_req_retries_exhausted_returns_last(fresh, monkeypatch):
    monkeypatch.setattr(storage.time, "sleep", lambda _s: None)
    for _ in range(4):                                    # retry=3 -> 4 attempts
        fresh.add("GET", "files", FakeResp(500))
    resp = storage.drive_req("GET", storage.DRIVE_API + "/files")
    assert resp.status_code == 500
    assert len(fresh.get_calls()) == 4


# --- id resolution / catalog -------------------------------------------------

def test_root_resolved_once_and_cached(fresh):
    fresh.add("GET", f"name='{storage.ROOT_FOLDER_NAME}'",
              FakeResp(json_data={"files": [{"id": "rootA"}]}))
    _queue_ids(fresh)
    assert storage.root_id() == "rootA"
    assert storage.folder_id("vault") == "v1"
    assert storage.folder_id("posters") == "p1"
    n = len(fresh.get_calls())
    assert storage.root_id() == "rootA"                     # all cached
    assert storage.folder_id("posters") == "p1"
    assert len(fresh.get_calls()) == n


def test_pinned_env_ids_skip_queries(fresh, monkeypatch):
    """With id pins in the env, resolution is offline — zero Drive calls."""
    monkeypatch.setenv("DRIVE_ROOT_FOLDER_ID", "r")
    monkeypatch.setenv("DRIVE_VAULT_FOLDER_ID", "v")
    monkeypatch.setenv("DRIVE_POSTER_FOLDER_ID", "p")
    monkeypatch.setenv("DRIVE_UPLOAD_FOLDER_ID", "u")
    monkeypatch.setenv("DRIVE_CATALOG_FILE_ID", "c")
    assert storage.root_id() == "r"
    assert storage.folder_id("vault") == "v"
    assert storage.folder_id("posts") is None
    assert storage._ensure_ids()["catalog"] == "c"
    assert fresh.get_calls() == []


def test_get_catalog_empty_when_missing(fresh):
    fresh.add("GET", f"name='{storage.ROOT_FOLDER_NAME}'",
              FakeResp(json_data={"files": [{"id": "rootA"}]}))
    _queue_ids(fresh, catalog_id=None)
    assert storage.get_catalog() == {}
    assert storage.get_catalog() == {}                      # cached too


def test_get_catalog_ttl(fresh, monkeypatch):
    monkeypatch.setattr(storage, "_ids",
                        {"ts": 1e16, "root": "rootA",
                         "folders": {}, "catalog": "cat1"})
    fresh.add("GET", "/files/cat1",
              FakeResp(json_data={"vault": [{"id": "x"}]}))
    assert storage.get_catalog() == {"vault": [{"id": "x"}]}
    assert storage.get_catalog() == {"vault": [{"id": "x"}]}
    assert len(fresh.get_calls()) == 1                      # TTL cache
    monkeypatch.setattr(storage, "_now", lambda: 9e15)      # expire TTL, not ids
    fresh.add("GET", "/files/cat1",
              FakeResp(json_data={"vault": [{"id": "y"}]}))
    assert storage.get_catalog() == {"vault": [{"id": "y"}]}
    assert len(fresh.get_calls()) == 2


def test_catalog_write_create_then_update(fresh, monkeypatch):
    monkeypatch.setattr(storage, "_ids",
                        {"ts": FAR_FUTURE, "root": "rootA",
                         "folders": {}, "catalog": None})
    fresh.add("POST", "/upload/drive/v3/files",
              FakeResp(201, json_data={"id": "catNew"}))
    storage.update_catalog(lambda cat: cat.update({"n": 1}))
    _m, _k, kw = fresh.calls[-1]
    assert json.loads(kw["files"]["media"][1]) == {"n": 1}   # payload went up
    assert storage._ids["catalog"] == "catNew"
    fresh.add("PATCH", "/upload/drive/v3/files/catNew", FakeResp(200))
    storage.update_catalog(lambda cat: cat.update({"n": 2}))
    assert storage._catalog["data"] == {"n": 2}
    assert len(fresh.get_calls()) == 2


# --- media I/O ---------------------------------------------------------------

def test_fetch_range_forwards_range_header(fresh):
    resp = FakeResp(206, headers={"Content-Range": "bytes 0-99/1000",
                                  "Content-Type": "video/mp4"},
                    content=b"0" * 100)
    fresh.add("GET", "/files/f1", resp)
    status, hdr, r = storage.fetch_range("f1", 0, 99)
    assert status == 206 and r is resp
    assert hdr.get("Content-Range") == "bytes 0-99/1000"
    _m, _k, kw = fresh.calls[-1]
    assert kw["headers"]["Range"] == "bytes=0-99"
    assert kw["params"]["alt"] == "media"


def test_materialize_writes_cache_basename(fresh):
    fresh.add("GET", "/files/f1", FakeResp(content=b"0123456789"))
    path = storage.materialize("f1", "sub/clip.mp4")
    assert path == os.path.join(storage.CACHE_DIR, "clip.mp4")
    with open(path, "rb") as f:
        assert f.read() == b"0123456789"


def test_put_file_resumable_chunked(fresh, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CHUNK", 8)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"0123456789abcdef")                    # 16 bytes -> 2 chunks
    fresh.add("POST", "/upload/drive/v3/files",
              FakeResp(201, headers={"Location": "https://up/sess1"}))
    fresh.add("PUT", "https://up/sess1", FakeResp(308))
    fresh.add("PUT", "https://up/sess1", FakeResp(200, json_data={"id": "fNew"}))
    assert storage.put_file(str(src), "updir") == "fNew"
    put_args = [c for c in fresh.calls if c[1].startswith("https://up/sess1")]
    assert len(put_args) == 2


def test_put_file_failure_raises(fresh, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CHUNK", 8)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"0123456789")
    fresh.add("POST", "/upload/drive/v3/files",
              FakeResp(201, headers={"Location": "https://up/sess1"}))
    fresh.add("PUT", "https://up/sess1", FakeResp(400))
    with pytest.raises(storage.StorageError):
        storage.put_file(str(src), "updir")


def test_delete_file_semantics(fresh):
    fresh.add("DELETE", "f1", FakeResp(204))
    assert storage.delete_file("f1") is True
    fresh.add("DELETE", "f1", FakeResp(404))
    assert storage.delete_file("f1") is False


# --- poster cache ------------------------------------------------------------

def test_poster_bytes_listed_and_cached(fresh, monkeypatch):
    monkeypatch.setattr(storage, "_ids",
                        {"ts": FAR_FUTURE, "root": "rootA",
                         "folders": {"posters": "pdir"}, "catalog": None})
    fresh.add("GET", "'pdir' in parents",
              FakeResp(json_data={"files": [{"id": "post1", "name": "a.jpg"}]}))
    fresh.add("GET", "/files/post1", FakeResp(content=b"JPGDATA"))
    assert storage.poster_bytes("a.jpg") == b"JPGDATA"
    assert storage.poster_bytes("a.jpg") == b"JPGDATA"      # memory cached
    assert len(fresh.get_calls()) == 2
    assert storage.poster_file_id("b.jpg") is None
    storage.invalidate_posters()                            # drop both caches
    fresh.add("GET", "'pdir' in parents",
              FakeResp(json_data={"files": [{"id": "post1", "name": "a.jpg"}]}))
    fresh.add("GET", "/files/post1", FakeResp(content=b"JPGDATA"))
    assert storage.poster_bytes("a.jpg") == b"JPGDATA"


# --- ping --------------------------------------------------------------------

def test_ping_ok_and_failure(fresh, monkeypatch):
    fresh.add("GET", f"name='{storage.ROOT_FOLDER_NAME}'",
              FakeResp(json_data={"files": [{"id": "rootA"}]}))
    _queue_ids(fresh)
    ok, msg = storage.ping()
    assert ok and msg == "ok"

    def raiser():
        raise storage.StorageError("boom")

    monkeypatch.setattr(storage, "_ids",
                        {"ts": 0.0, "root": None, "folders": {}, "catalog": None})
    monkeypatch.setattr(storage, "_refresh_ids_locked", raiser)
    ok, msg = storage.ping()
    assert not ok and "boom" in msg
