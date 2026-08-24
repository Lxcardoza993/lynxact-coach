"""Unit + endpoint tests for the telestration annotations store and API.

Store side: boundary validation (tool/color whitelists, finite normalized
points, per-clip cap), atomic persistence, corrupt-file degrade, basename
confinement. Endpoint side: 404 for unknown clips, 400 for bad payloads,
full CRUD roundtrip. Hermetic — tmp_path-backed stores, a dummy vault mp4,
no external services.
"""
import json
import os

import pytest

from app import app
from coach import annotations as anno


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Redirect the annotations dir to tmp_path for every test."""
    monkeypatch.setattr(anno, "ANNOT_DIR", str(tmp_path / "annotations"))
    return anno


@pytest.fixture()
def clip(store, tmp_path, monkeypatch):
    """A vault clip whose id passes get_clip (dummy mp4 file)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "probe_player_test_2020.mp4").write_bytes(b"\x00" * 64)
    monkeypatch.setattr("coach.clips.VAULT_ROOT", str(vault))
    return "probe_player_test_2020"


def good_payload(**overrides):
    payload = {"t": 3.5, "tool": "arrow", "color": "#f2cc60",
               "points": [[0.1, 0.2], [0.9, 0.8]]}
    payload.update(overrides)
    return payload


# ---- store unit tests ----

def test_add_returns_uid_and_roundtrips(store):
    item = store.add_annotation("c1", good_payload())
    assert isinstance(item["uid"], str) and len(item["uid"]) == 12
    assert item["t"] == 3.5
    assert item["points"] == [[0.1, 0.2], [0.9, 0.8]]
    assert store.list_annotations("c1") == [item]


def test_add_coerces_int_points_to_float(store):
    item = store.add_annotation("c1", good_payload(points=[[0, 0], [1, 1]]))
    assert item["points"] == [[0.0, 0.0], [1.0, 1.0]]


def test_label_saved_and_truncated(store):
    item = store.add_annotation("c1", good_payload(label="x" * 200))
    assert item["label"] == "x" * anno.MAX_LABEL


@pytest.mark.parametrize("bad", [
    {"t": -1},
    {"t": 86401},
    {"t": "12"},
    {"t": None},
    {"tool": "circle"},
    {"tool": None},
    {"color": "hotpink"},
    {"points": [[0.5, 0.5]]},
    {"points": [[0.5, 0.5, 0.5], [0.5, 0.5]]},
    {"points": [[1.5, 0.5], [0.5, 0.5]]},
    {"points": [[-0.1, 0.5], [0.5, 0.5]]},
    {"points": [["a", 0.5], [0.5, 0.5]]},
    {"points": "not-a-list"},
    {"label": 42},
])
def test_rejects_invalid_payloads(store, bad):
    with pytest.raises(ValueError):
        store.add_annotation("c1", good_payload(**bad))


def test_rejects_non_object_payload(store):
    with pytest.raises(ValueError):
        store.add_annotation("c1", ["arrow", 0.5])


def test_cap_enforced(store, monkeypatch):
    monkeypatch.setattr(anno, "MAX_ITEMS", 3)
    for i in range(3):
        store.add_annotation("c1", good_payload(t=float(i)))
    with pytest.raises(ValueError):
        store.add_annotation("c1", good_payload(t=9.0))
    assert len(store.list_annotations("c1")) == 3


def test_delete_annotation(store):
    uid = store.add_annotation("c1", good_payload())["uid"]
    assert store.delete_annotation("c1", uid) is True
    assert store.list_annotations("c1") == []
    assert store.delete_annotation("c1", uid) is False
    assert store.delete_annotation("c1", "../not-a-uid") is False


def test_clear_annotations(store):
    store.add_annotation("c1", good_payload(t=1.0))
    store.add_annotation("c1", good_payload(t=2.0))
    assert store.clear_annotations("c1") == 2
    assert store.list_annotations("c1") == []
    assert store.clear_annotations("c1") == 0


def test_corrupt_file_degrades_to_empty(store):
    import os
    os.makedirs(store.ANNOT_DIR, exist_ok=True)
    with open(store._path("c1"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert store.list_annotations("c1") == []


def test_non_dict_json_degrades_to_empty(store):
    import os
    os.makedirs(store.ANNOT_DIR, exist_ok=True)
    with open(store._path("c1"), "w", encoding="utf-8") as f:
        json.dump([1, 2, 3], f)
    assert store.list_annotations("c1") == []


def test_invalid_stored_items_are_skipped(store):
    item = store.add_annotation("c1", good_payload())
    corrupt = [
        "not-a-dict",                                                          # non-dict entry
        {"uid": "has space!", "t": 1.0, "tool": "arrow", "color": "#58a6ff",
         "points": [[0, 0], [1, 1]]},                                            # uid regex
        {"uid": "ok123", "t": 1.0, "tool": "circle", "color": "#58a6ff",
         "points": [[0, 0], [1, 1]]},                                           # tool whitelist
        {"uid": "ok456", "t": 1.0, "tool": "arrow", "color": "#58a6ff",
         "points": [[5, 5], [1, 1]]},                                           # points range
        {"uid": "ok789", "t": -5, "tool": "arrow", "color": "#58a6ff",
         "points": [[0, 0], [1, 1]]},                                           # bad t
    ]
    with open(store._path("c1"), "w", encoding="utf-8") as f:
        json.dump({"items": corrupt + [item]}, f)
    assert store.list_annotations("c1") == [item]


def test_clip_id_cannot_escape_dir(store):
    item = store.add_annotation("../evil", good_payload())
    saved = store._path("../evil")
    assert saved.startswith(store.ANNOT_DIR) and ".." not in saved[len(store.ANNOT_DIR):]
    assert item["uid"] in [x["uid"] for x in store.list_annotations("../evil")]


def test_concurrent_adds_lose_nothing(store):
    """flock around read-modify-write: 24 racing adds must all land."""
    import threading
    errors = []

    def add(i):
        try:
            store.add_annotation("c1", good_payload(t=float(i % 100)))
        except Exception as exc:   # pragma: no cover — collection, not control flow
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(store.list_annotations("c1")) == 24


def test_unlocked_fallback_when_no_fcntl(store, monkeypatch):
    """Non-POSIX fallback: writes complete without flock."""
    monkeypatch.setattr(anno, "fcntl", None)
    item = store.add_annotation("c1", good_payload())
    assert item["uid"] in [x["uid"] for x in store.list_annotations("c1")]


# ---- endpoint tests ----

def test_unknown_clip_404s():
    client = app.test_client()
    assert client.get("/api/annotations/no-such-clip").status_code == 404
    assert client.post("/api/annotations/no-such-clip", json=good_payload()).status_code == 404
    assert client.delete("/api/annotations/no-such-clip/abc123").status_code == 404


def test_crud_roundtrip(clip):
    client = app.test_client()
    r = client.get(f"/api/annotations/{clip}")
    assert r.status_code == 200
    assert r.get_json() == {"clip_id": clip, "items": []}

    r = client.post(f"/api/annotations/{clip}", json=good_payload(t=6.0, tool="freehand",
                                                                  points=[[0.2, 0.3], [0.4, 0.5], [0.6, 0.7]]))
    assert r.status_code == 201
    item = r.get_json()
    assert item["tool"] == "freehand" and item["t"] == 6.0

    items = client.get(f"/api/annotations/{clip}").get_json()["items"]
    assert items == [item]

    r = client.delete(f"/api/annotations/{clip}/{item['uid']}")
    assert r.status_code == 200 and r.get_json() == {"deleted": item["uid"]}
    assert client.delete(f"/api/annotations/{clip}/{item['uid']}").status_code == 404
    assert client.get(f"/api/annotations/{clip}").get_json()["items"] == []


def test_bad_payload_400s_with_reason(clip):
    client = app.test_client()
    r = client.post(f"/api/annotations/{clip}", json=good_payload(tool="circle"))
    assert r.status_code == 400
    assert "tool" in r.get_json()["error"]


def test_non_json_body_400s(clip):
    client = app.test_client()
    r = client.post(f"/api/annotations/{clip}", data="not json", content_type="application/json")
    assert r.status_code == 400


# ---- clip alignment offset ----

def test_offset_defaults_to_zero(store):
    assert store.get_offset("never-set") == 0.0


def test_offset_roundtrip(store):
    assert store.set_offset("c1", 5.25) == 5.25
    assert store.get_offset("c1") == 5.25
    store.set_offset("c1", -2)          # negative = trimmed video start
    assert store.get_offset("c1") == -2.0


def test_offset_accepts_numeric_strings(store):
    assert store.set_offset("c1", "5.5") == 5.5


@pytest.mark.parametrize("bad", ["abc", None, {}, 3601, -3601, float("nan"), float("inf")])
def test_offset_rejects_invalid(store, bad):
    with pytest.raises(ValueError):
        store.set_offset("c1", bad)


def test_offset_corrupt_file_degrades_to_zero(store):
    store.set_offset("c1", 3.0)
    with open(anno._offset_path("c1"), "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert store.get_offset("c1") == 0.0


def test_offset_endpoints_unknown_clip_404():
    client = app.test_client()
    assert client.get("/api/clips/ghost/offset").status_code == 404
    assert client.post("/api/clips/ghost/offset", json={"offset": 1}).status_code == 404


def test_offset_endpoints_roundtrip(clip):
    client = app.test_client()
    assert client.get(f"/api/clips/{clip}/offset").get_json()["offset"] == 0.0
    r = client.post(f"/api/clips/{clip}/offset", json={"offset": 5.0})
    assert r.status_code == 200 and r.get_json()["offset"] == 5.0
    assert client.get(f"/api/clips/{clip}/offset").get_json()["offset"] == 5.0


def test_offset_endpoints_bad_payload_400(clip):
    client = app.test_client()
    r = client.post(f"/api/clips/{clip}/offset", json={"offset": "NaN"})
    assert r.status_code == 400 and "offset" in r.get_json()["error"]


def test_offset_file_nonfinite_value_degrades_to_zero(store):
    store.set_offset("c1", 3.0)
    with open(anno._offset_path("c1"), "w", encoding="utf-8") as fh:
        json.dump({"offset": float("nan")}, fh)  # python json round-trips NaN
    assert store.get_offset("c1") == 0.0


def test_offset_file_non_dict_degrades_to_zero(store):
    store.set_offset("c1", 3.0)
    with open(anno._offset_path("c1"), "w", encoding="utf-8") as fh:
        json.dump([1, 2, 3], fh)
    assert store.get_offset("c1") == 0.0


def test_set_offset_leaves_no_tmp_residue(store):
    store.set_offset("c1", 5.0)
    assert not os.path.exists(anno._offset_path("c1") + ".tmp")
