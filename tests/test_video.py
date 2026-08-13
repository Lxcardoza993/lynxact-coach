"""Unit tests for coach.video — HTTP Range (RFC 7233) streaming.

Builds a throwaway Flask app per test that delegates to range_stream, then
exercises byte-range parsing through the test client with real Range headers.
File contents are bytes(range(n)) so a byte's value equals its offset —
assertions can compare ranges directly.
"""
import pytest
from flask import Flask

from coach import video


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)

    @app.get("/v/<path:fname>")
    def serve(fname):
        return video.range_stream(fname, str(tmp_path))

    return app.test_client()


def _seed(tmp_path, name, n):
    (tmp_path / name).write_bytes(bytes(range(n)))   # byte i == value i


def test_no_range_returns_full_200(client, tmp_path):
    _seed(tmp_path, "clip.mp4", 200)
    r = client.get("/v/clip.mp4")
    assert r.status_code == 200
    assert r.data == bytes(range(200))
    assert r.headers["Content-Length"] == "200"


def test_start_end_range_206(client, tmp_path):
    _seed(tmp_path, "clip.mp4", 200)
    r = client.get("/v/clip.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.data == bytes(range(0, 100))
    assert r.headers["Content-Range"] == "bytes 0-99/200"
    assert r.headers["Content-Length"] == "100"


def test_open_end_range(client, tmp_path):
    _seed(tmp_path, "clip.mp4", 200)
    r = client.get("/v/clip.mp4", headers={"Range": "bytes=100-"})
    assert r.status_code == 206
    assert r.data == bytes(range(100, 200))
    assert r.headers["Content-Range"] == "bytes 100-199/200"


def test_suffix_range_last_n_bytes(client, tmp_path):
    """bytes=-100 per RFC 7233 §2.1 = LAST 100 bytes = [100, 199]."""
    _seed(tmp_path, "clip.mp4", 200)
    r = client.get("/v/clip.mp4", headers={"Range": "bytes=-100"})
    assert r.status_code == 206
    assert r.data == bytes(range(100, 200))
    assert r.headers["Content-Range"] == "bytes 100-199/200"
    assert r.headers["Content-Length"] == "100"


def test_suffix_range_larger_than_file_returns_whole(client, tmp_path):
    """Suffix longer than the file → entire representation (RFC 7233 §2.1)."""
    _seed(tmp_path, "clip.mp4", 200)
    r = client.get("/v/clip.mp4", headers={"Range": "bytes=-99999"})
    assert r.status_code == 206
    assert r.data == bytes(range(200))
    assert r.headers["Content-Range"] == "bytes 0-199/200"


def test_missing_file_404(client, tmp_path):
    r = client.get("/v/nope.mp4")
    assert r.status_code == 404


def test_path_components_collapsed_to_leaf(client, tmp_path):
    """basename() must reduce a multi-segment path to its leaf, so a request
    can only ever reach files that live in base_dir under their own name —
    it can never escape base_dir via extra path components."""
    _seed(tmp_path, "clip.mp4", 200)
    r = client.get("/v/sub/dir/clip.mp4")
    assert r.status_code == 200
    assert r.data == bytes(range(200))


def test_malformed_range_falls_back_to_full_200(client, tmp_path):
    # A Range header that doesn't match bytes=<digits>-<digits> (no hyphen /
    # non-numeric) must be ignored, not crash — serve the whole file as 200.
    _seed(tmp_path, "clip.mp4", 200)
    r = client.get("/v/clip.mp4", headers={"Range": "bytes=banana"})
    assert r.status_code == 200
    assert r.data == bytes(range(200))


def test_both_empty_range_is_whole_file_206(client, tmp_path):
    # "bytes=-" (start and end both empty) is a syntactically-valid Range that
    # resolves to the whole representation, returned as 206 (start=0, end=size-1).
    _seed(tmp_path, "clip.mp4", 200)
    r = client.get("/v/clip.mp4", headers={"Range": "bytes=-"})
    assert r.status_code == 206
    assert r.data == bytes(range(200))
    assert r.headers["Content-Range"] == "bytes 0-199/200"
