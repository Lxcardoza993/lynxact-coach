"""LynxAct Coach — Flask entry. http://127.0.0.1:6901"""
import os

from flask import Flask, Response, abort, jsonify, render_template

from coach.clips import get_clip, list_clips
from coach.stream import sse_stream
from coach.video import range_stream

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", clips=list_clips())


@app.route("/coach/<clip_id>")
def coach_view(clip_id):
    clip = get_clip(clip_id)
    if not clip:
        abort(404)
    return render_template("coach.html", clip=clip)


@app.route("/video/<path:fname>")
def video(fname):
    return range_stream(fname)


@app.route("/api/stream/<clip_id>")
def api_stream(clip_id):
    if not get_clip(clip_id):
        abort(404)
    return Response(
        sse_stream(clip_id),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/health")
def health():
    return jsonify(ok=True, mode=os.environ.get("COACH_MODE", "replay"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "6901")), threaded=True)
