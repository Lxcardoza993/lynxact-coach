"""LynxAct Coach — Flask entry. http://127.0.0.1:6901"""
import os

from dotenv import load_dotenv

load_dotenv()

import subprocess
import uuid

from flask import Flask, Response, abort, jsonify, redirect, render_template, request

from coach import report as report_mod
from coach.claude import _cfg
from coach.clips import AUDIO_DIR, TMP_DIR, get_clip, list_clips, register_upload, video_dir
from coach.stream import sse_stream
from coach.video import range_stream

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300MB 上传上限


def _err_page(code, msg):
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>LynxAct Coach</title>"
        "<style>body{background:#0d1117;color:#e6edf3;font:15px sans-serif;display:flex;"
        "min-height:100vh;align-items:center;justify-content:center;margin:0}"
        "a{color:#58a6ff}</style></head><body><div style='text-align:center'>"
        f"<h1 style='font-size:64px;margin:0'>{code}</h1><p>{msg}</p>"
        "<p><a href='/'>← back to clips</a></p></div></body></html>"
    ), code


@app.errorhandler(404)
def not_found(_):
    return _err_page(404, "clip or page not found")


@app.errorhandler(413)
def too_large(_):
    return _err_page(413, "file too large — 300MB max")


@app.route("/")
def index():
    return render_template("index.html", clips=list_clips(), err=request.args.get("err"))


@app.route("/coach/<clip_id>")
def coach_view(clip_id):
    clip = get_clip(clip_id)
    if not clip:
        abort(404)
    return render_template("coach.html", clip=clip)


@app.route("/video/<path:fname>")
def video(fname):
    clip_id = fname.rsplit(".", 1)[0]
    return range_stream(fname, video_dir(clip_id))


@app.route("/api/report/<clip_id>")
def api_report(clip_id):
    if not get_clip(clip_id):
        abort(404)
    mode = request.args.get("mode") or os.environ.get("COACH_MODE", "replay")
    result = report_mod.build_report(clip_id, mode, _cfg())
    if result is None:
        return jsonify(error="no cards yet — run the stream first"), 404
    return jsonify(result)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect("/?err=no+file+selected")
    if not f.filename.lower().endswith(".mp4"):
        return redirect("/?err=only+mp4+files+supported")
    os.makedirs(TMP_DIR, exist_ok=True)
    tmp_path = os.path.join(TMP_DIR, "up-" + uuid.uuid4().hex[:8] + ".mp4")
    f.save(tmp_path)
    ffmpeg = os.environ.get("FFMPEG", "/home/li/.local/bin/ffmpeg")
    ffprobe = os.environ.get("FFPROBE", ffmpeg.replace("ffmpeg", "ffprobe"))
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", tmp_path],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(out.stdout.strip() or 0)
    except Exception:
        duration = 0
    clip_id = register_upload(tmp_path, f.filename, duration)
    # 抽 16k 单声道音轨给 Speechmatics
    os.makedirs(AUDIO_DIR, exist_ok=True)
    wav = os.path.join(AUDIO_DIR, clip_id + ".wav")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", os.path.join(video_dir(clip_id), clip_id + ".mp4"),
             "-ac", "1", "-ar", "16000", "-f", "wav", wav],
            capture_output=True, timeout=120,
        )
    except Exception:
        pass  # 音轨抽取失败不挡路,live 时会报明确错误
    return redirect(f"/coach/{clip_id}?mode=live")


@app.route("/api/stream/<clip_id>")
def api_stream(clip_id):
    if not get_clip(clip_id):
        abort(404)
    # ?mode=live|replay 可覆盖环境变量,演示时一个服务两种模式
    mode = request.args.get("mode") or os.environ.get("COACH_MODE", "replay")
    return Response(
        sse_stream(clip_id, mode),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/health")
def health():
    return jsonify(ok=True, mode=os.environ.get("COACH_MODE", "replay"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "6901")), threaded=True)
