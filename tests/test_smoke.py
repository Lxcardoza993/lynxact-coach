"""LynxAct Coach smoke tests.

Verify the Flask app constructs, routes are registered, and the health
endpoint responds — all without touching external services (CPA /
Speechmatics / ffmpeg), so they stay fast and hermetic.
"""
from app import app


def test_app_is_flask():
    assert app.name == "app"


def test_routes_registered():
    rules = {r.rule for r in app.url_map.iter_rules()}
    expected = {
        "/",
        "/api/health",
        "/api/upload",
        "/api/agent/chat",
        "/api/annotations/<clip_id>",
        "/api/annotations/<clip_id>/<uid>",
        "/api/report/<clip_id>",
        "/api/stream/<clip_id>",
        "/coach/<clip_id>",
        "/video/<path:fname>",
    }
    missing = expected - rules
    assert not missing, f"missing routes: {missing}"


def test_health_ok():
    resp = app.test_client().get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
