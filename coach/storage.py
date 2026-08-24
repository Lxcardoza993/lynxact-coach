"""Google Drive storage backend for clip media (videos + posters + catalog).

The cloud switch: when ``DRIVE_REFRESH_TOKEN`` is present in the environment
the app treats Drive as the source of truth for clip media; without it every
call here is a no-op and the app keeps serving from its local disks (existing
dev/tests unchanged). Files stay private on the user's Drive — the site
proxies them through the VPS instead of exposing Drive links.

Deps: only ``requests`` + stdlib. The OAuth client id/secret are rclone's
public defaults (the configured [gdrive] remote uses them), so the app can
refresh tokens issued to that client without further setup.
"""
import json
import os
import threading
import time

import requests

try:
    import fcntl
except ImportError:  # pragma: no cover — non-POSIX dev fallback
    fcntl = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE, "data", "tmp", "cloud-cache")
CATALOG_LOCK = os.path.join(BASE, "data", "tmp", "cloud-catalog.lock")

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3"
OAUTH_URL = "https://oauth2.googleapis.com/token"

# rclone's public default OAuth client (documented in rclone source) — the
# refresh token in the user's rclone.conf was issued to this client.
RCLONE_CLIENT_ID = "202264815644.apps.googleusercontent.com"
RCLONE_CLIENT_SECRET = "X4Z3ca8xfWDb1Voo-F9a7ZxJ"  # noqa: S105 — rclone's public default

ROOT_FOLDER_NAME = "lynxact-coach"
VAULT_FOLDER, POSTER_FOLDER, UPLOAD_FOLDER = "vault", "posters", "uploads"
CATALOG_NAME = "catalog.json"
CHUNK = 8 * 1024 * 1024        # resumable-upload chunk size
CATALOG_TTL = 60.0             # seconds — listing is one Drive API call
ID_TTL = 3600.0                # folder/file-id lookups stay stable


class StorageError(RuntimeError):
    """Drive is enabled but an operation failed — surfaced as 502 by routes."""


_state_lock = threading.Lock()
_token = {"access": None, "expires": 0.0}
_ids = {"ts": 0.0, "root": None, "folders": {}, "catalog": None}
_catalog = {"ts": 0.0, "data": None}


def enabled() -> bool:
    return bool(os.environ.get("DRIVE_REFRESH_TOKEN"))


def _now() -> float:
    return time.time()


def access_token() -> str:
    """Cached OAuth access token, refreshed via the stored refresh token."""
    with _state_lock:
        if _token["access"] and _now() < _token["expires"] - 60:
            return _token["access"]
    resp = requests.post(
        OAUTH_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": os.environ.get("DRIVE_CLIENT_ID", RCLONE_CLIENT_ID),
            "client_secret": os.environ.get("DRIVE_CLIENT_SECRET", RCLONE_CLIENT_SECRET),
            "refresh_token": os.environ["DRIVE_REFRESH_TOKEN"],
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise StorageError(f"oauth refresh failed: {resp.status_code}")
    data = resp.json()
    with _state_lock:
        _token["access"] = data["access_token"]
        _token["expires"] = _now() + float(data.get("expires_in", 3600))
        return _token["access"]


def _headers(json_body: bool = False) -> dict:
    h = {"Authorization": f"Bearer {access_token()}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def drive_req(method: str, url: str, *, retry: int = 1, **kw) -> requests.Response:
    """Drive API call with one automatic token-refresh-and-retry on 401."""
    json_body = kw.pop("json_body", False)          # private flag for _headers
    headers = dict(kw.pop("headers", None) or {})
    headers.update(_headers(json_body=json_body))
    headers.update(_headers())
    try:
        resp = requests.request(method, url, headers=headers, timeout=60, **kw)
        if resp.status_code == 401 and retry:
            with _state_lock:                 # force a fresh token
                _token["access"] = None
            return drive_req(method, url, retry=retry - 1, **kw)
        return resp
    except requests.RequestException as exc:
        raise StorageError(str(exc)) from exc


def _refresh_ids_locked() -> None:
    """Resolve root/folder/catalog ids (env override or by-name lookup)."""
    env_root = os.environ.get("DRIVE_ROOT_FOLDER_ID")
    if env_root:
        root = env_root
    else:
        q = (f"name='{ROOT_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' "
             "and 'root' in parents and trashed=false")
        resp = drive_req("GET", DRIVE_API + "/files",
                         params={"q": q, "fields": "files(id)", "pageSize": 5})
        files = (resp.json().get("files") or []) if resp.status_code == 200 else []
        if files:
            root = files[0]["id"]
        else:                              # self-heal: create the root folder
            resp = drive_req("POST", DRIVE_API + "/files", json_body=True, json={
                "name": ROOT_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
            })
            resp.raise_for_status()
            root = resp.json()["id"]
    _ids["root"] = root
    _ids["folders"] = {}
    for sub in (VAULT_FOLDER, POSTER_FOLDER, UPLOAD_FOLDER):
        q = (f"name='{sub}' and mimeType='application/vnd.google-apps.folder' "
             f"and '{root}' in parents and trashed=false")
        resp = drive_req("GET", DRIVE_API + "/files",
                         params={"q": q, "fields": "files(id)", "pageSize": 5})
        files = (resp.json().get("files") or []) if resp.status_code == 200 else []
        if files:
            _ids["folders"][sub] = files[0]["id"]
    q = (f"name='{CATALOG_NAME}' and '{root}' in parents and trashed=false")
    resp = drive_req("GET", DRIVE_API + "/files",
                     params={"q": q, "fields": "files(id)", "pageSize": 5})
    files = (resp.json().get("files") or []) if resp.status_code == 200 else []
    _ids["catalog"] = files[0]["id"] if files else None
    _ids["ts"] = _now()


def _ensure_ids() -> dict:
    if not _ids["root"] or _now() > _ids["ts"] + ID_TTL:
        _refresh_ids_locked()
    return _ids


def root_id() -> str:
    return _ensure_ids()["root"]


def folder_id(name: str) -> str | None:
    ids = _ensure_ids()
    return ids["folders"].get(name)


def list_folder(folder: str | None) -> list[dict]:
    """Short file listing of a folder: [{id, name, size}]."""
    if not folder:
        return []
    resp = drive_req("GET", DRIVE_API + "/files", params={
        "q": f"'{folder}' in parents and trashed=false",
        "fields": "files(id,name,size)", "pageSize": 1000,
    })
    if resp.status_code != 200:
        raise StorageError(f"list failed: {resp.status_code}")
    return resp.json().get("files", [])


def get_catalog() -> dict:
    """The cloud clip catalog: {vault:[...], uploads:[...]}. {} when absent."""
    now = _now()
    if _catalog["data"] is not None and now < _catalog["ts"] + CATALOG_TTL:
        return _catalog["data"]
    file_id = _ensure_ids()["catalog"]
    if not file_id:
        _catalog["data"], _catalog["ts"] = {}, now
        return {}
    resp = drive_req("GET", DRIVE_API + f"/files/{file_id}", params={"alt": "media"})
    if resp.status_code != 200:
        raise StorageError(f"catalog fetch failed: {resp.status_code}")
    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError as exc:
        raise StorageError("catalog corrupt") from exc
    catalog = data if isinstance(data, dict) else {}
    _catalog["data"], _catalog["ts"] = catalog, now
    return catalog


def _catalog_write(data: dict) -> None:
    """Multipart update of catalog.json (create when missing); flock-guarded so
    concurrent delete/upload can't interleave read-modify-write cycles."""
    os.makedirs(os.path.dirname(CATALOG_LOCK), exist_ok=True)
    lock = open(CATALOG_LOCK, "a", encoding="utf-8")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        file_id = _ensure_ids()["catalog"]
        payload = json.dumps(data, ensure_ascii=False)
        if file_id:
            resp = drive_req("PATCH", DRIVE_UPLOAD + f"/files/{file_id}",
                             params={"uploadType": "multipart"},
                             data={"metadata": json.dumps({"name": CATALOG_NAME}),
                                   "media": ("catalog.json", payload, "application/json")})
        else:
            resp = drive_req("POST", DRIVE_UPLOAD + "/files",
                             params={"uploadType": "multipart"},
                             data={"metadata": json.dumps({
                                 "name": CATALOG_NAME,
                                 "parents": [root_id()],
                             }), "media": ("catalog.json", payload, "application/json")})
        if resp.status_code not in (200, 201):
            raise StorageError(f"catalog write failed: {resp.status_code}")
        with _state_lock:
            _ids["catalog"] = resp.json().get("id", _ids["catalog"])
            _catalog["data"], _catalog["ts"] = data, _now()
    finally:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def update_catalog(mutate) -> None:
    """Read-modify-write the catalog (single write under the cloud lock —
    a failed write raises so callers keep the old catalog as truth)."""
    data = get_catalog()
    mutate(data)
    _catalog_write(data)


def _file_url(file_id: str) -> str:
    return DRIVE_API + f"/files/{file_id}"


def fetch_range(file_id: str, start: int | None, end: int | None):
    """Stream a byte range of a Drive file. Returns (status, headers, response).

    The response object stays live on the caller's side — iterate it with
    ``resp.iter_content(CHUNK)`` and then ``resp.close()``. When Drive ignores
    the Range header it answers 200 with the whole body: callers must detect
    that (status, Content-Range) and decide whether to consume or materialize.
    """
    headers = {}
    if start is not None or end is not None:
        if start is not None and end is not None:
            headers["Range"] = f"bytes={start}-{end}"
        elif start is not None:
            headers["Range"] = f"bytes={start}-"
        else:
            headers["Range"] = f"bytes=-{end}"
    resp = drive_req("GET", _file_url(file_id), params={"alt": "media"},
                     headers=headers, stream=True)
    return resp.status_code, resp.headers, resp


def get_bytes(file_id: str) -> bytes:
    resp = drive_req("GET", _file_url(file_id), params={"alt": "media"})
    if resp.status_code != 200:
        raise StorageError(f"media fetch failed: {resp.status_code}")
    return resp.content


def materialize(file_id: str, cache_name: str) -> str:
    """Full-download a Drive file into the local cloud cache; return its path."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    dest = os.path.join(CACHE_DIR, os.path.basename(cache_name))
    resp = drive_req("GET", _file_url(file_id), params={"alt": "media"}, stream=True)
    if resp.status_code != 200:
        raise StorageError(f"materialize failed: {resp.status_code}")
    tmp = dest + ".part"
    with resp:
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(CHUNK):
                f.write(chunk)
    os.replace(tmp, dest)
    return dest


def put_file(path: str, folder: str, mime: str = "video/mp4") -> str:
    """Upload path into folder with a resumable session; returns the file id."""
    folder_ok = folder or root_id()
    size = os.path.getsize(path)
    init = drive_req("POST", DRIVE_UPLOAD + "/files",
                     params={"uploadType": "resumable"}, json_body=True,
                     json={"name": os.path.basename(path),
                           "parents": [folder_ok], "mimeType": mime})
    if init.status_code not in (200, 201):
        raise StorageError(f"resumable init failed: {init.status_code}")
    session = init.headers.get("Location")
    with open(path, "rb") as f:
        sent = 0
        while sent < size:
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            resp = requests.put(
                session, headers={
                    "Content-Range": f"bytes {sent}-{end}/{size}",
                    "Content-Length": str(len(chunk)),
                }, data=chunk, timeout=120,
            )
            if resp.status_code in (308,):
                sent += len(chunk)
                continue
            if resp.status_code in (200, 201):    # upload complete (early or final)
                return resp.json()["id"]
            raise StorageError(f"resumable put failed: {resp.status_code}")
    # Small files complete in the final chunk above; loop is empty otherwise.
    raise StorageError("upload ended unexpectedly")  # pragma: no cover


def delete_file(file_id: str) -> bool:
    resp = drive_req("DELETE", _file_url(file_id))
    if resp.status_code == 404:
        return False
    if resp.status_code not in (204, 200):
        raise StorageError(f"delete failed: {resp.status_code}")
    return True


POSTER_TTL = 300.0
_poster_bytes_cap = 256
_posters = {"ts": 0.0, "map": {}}
_poster_bytes: dict[str, bytes] = {}


def poster_file_id(name: str) -> str | None:
    """file_id for a poster by name (posters folder listing, cached 5 min).

    Kept separate from the per-file id cache because a homepage asks for ~29
    posters at once — one folder listing per 5 minutes, not 29 API calls.
    """
    now = _now()
    if _posters["ts"] and now < _posters["ts"] + POSTER_TTL:
        return _posters["map"].get(name)
    folder = folder_id(POSTER_FOLDER)
    if not folder:
        return None
    _posters["map"] = {f["name"]: f["id"] for f in list_folder(folder)}
    _posters["ts"] = now
    return _posters["map"].get(name)


def poster_bytes(name: str) -> bytes | None:
    """Poster bytes from Drive, memory-cached (browser/CDN cache on top)."""
    if name in _poster_bytes:
        return _poster_bytes[name]
    fid = poster_file_id(name)
    if not fid:
        return None
    data = get_bytes(fid)
    if len(_poster_bytes) >= _poster_bytes_cap:
        _poster_bytes.clear()
    _poster_bytes[name] = data
    return data


def invalidate_posters() -> None:
    """Drop the poster caches after a poster is created or deleted."""
    _posters["map"] = {}
    _posters["ts"] = 0.0
    _poster_bytes.clear()


def ping() -> tuple[bool, str]:
    """Cheap liveness probe for /api/health (best-effort, never raises)."""
    if not enabled():
        return False, "disabled"
    try:
        root_id()
        return True, "ok"
    except StorageError as exc:
        return False, str(exc)[:120]
