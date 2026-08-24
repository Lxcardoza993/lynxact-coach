"""LynxAct Coach — Flask entry. http://127.0.0.1:6901"""
import hmac
import logging
import os

from dotenv import load_dotenv

load_dotenv()

# subprocess: only list-arg ffmpeg/ffprobe calls below — no shell injection
import subprocess  # nosec
import uuid

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
)

from coach import agent, storage
from coach import annotations as anno_mod
from coach import report as report_mod
from coach.claude import _cfg
from coach.clips import (
    AUDIO_DIR,
    POSTER_DIR,
    TMP_DIR,
    delete_clip,
    get_clip,
    list_clips,
    register_upload,
    video_dir,
)
from coach.stream import sse_stream
from coach.video import range_stream

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300MB 上传上限

logger = logging.getLogger(__name__)


def _err_page(code: int, msg: str) -> tuple[str, int]:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>LynxAct Coach</title>"
        "<style>body{background:#0d1117;color:#e6edf3;font:15px sans-serif;display:flex;"
        "min-height:100vh;align-items:center;justify-content:center;margin:0}"
        "a{color:#58a6ff}</style></head><body><div style='text-align:center'>"
        f"<h1 style='font-size:64px;margin:0'>{code}</h1><p>{msg}</p>"
        "<p><a href='/'>← back to clips</a></p></div></body></html>"
    ), code


@app.errorhandler(404)
def not_found(_: Exception) -> tuple[str, int]:
    return _err_page(404, "clip or page not found")


@app.errorhandler(413)
def too_large(_: Exception) -> tuple[str, int]:
    return _err_page(413, "file too large — 300MB max")


@app.route("/")
def index() -> str:
    return render_template("index.html", clips=list_clips(), err=request.args.get("err"))


@app.route("/coach/<clip_id>")
def coach_view(clip_id: str) -> str:
    clip = get_clip(clip_id)
    if not clip:
        abort(404)
    return render_template("coach.html", clip=clip)


def _cloud_video(fname: str, file_id: str) -> Response | None:
    """Serve <video> byte ranges from Drive; None means 'fall back to local'.

    Parses the request Range, forwards it to Drive and relays a 206/200
    stream. When Drive ignores the Range header (200 + full body) the file is
    spooled once into the local cloud cache and served from there — the next
    request drags the progress bar locally, no red screen either way."""
    rng = request.headers.get("Range")
    start = end = None
    if rng and rng.startswith("bytes="):
        spec = rng.split("=", 1)[1].split("-", 1)
        try:
            start = int(spec[0]) if spec[0] else None
            end = int(spec[1]) if len(spec) > 1 and spec[1] else None
        except ValueError:
            start = end = None
    status, hdr, resp = storage.fetch_range(file_id, start, end)
    try:
        if status == 200 and start is not None \
                and not hdr.get("Content-Range", "").startswith("bytes "):
            resp.close()                 # Range ignored — spool then serve locally
            path = storage.materialize(file_id, fname)
            return range_stream(os.path.basename(fname), os.path.dirname(path))
        if status not in (200, 206):
            return None
        headers = {"Accept-Ranges": "bytes"}
        for key in ("Content-Range", "Content-Length", "Content-Type"):
            if hdr.get(key) is not None:
                headers[key] = hdr[key]
        headers.setdefault("Content-Type", "video/mp4")

        def _body():
            try:
                yield from resp.iter_content(storage.CHUNK)
            finally:
                resp.close()

        return Response(_body(), status=status, headers=headers)
    except BaseException:
        resp.close()
        raise


@app.route("/video/<path:fname>")
def video(fname: str) -> Response:
    clip_id = fname.rsplit(".", 1)[0]
    clip = get_clip(clip_id)
    if clip and clip.get("_file_id"):
        try:
            resp = _cloud_video(fname, clip["_file_id"])
            if resp is not None:
                return resp
        except storage.StorageError as exc:
            logger.warning("cloud video failed for %s: %s", clip_id, exc)
    return range_stream(fname, video_dir(clip_id))


@app.route("/posters/<name>")
def poster(name: str) -> Response:
    """Generated preview posters (data/posters, gitignored). Cloud mode serves
    them from Drive first with a day-long public cache for the CDN edge."""
    name = os.path.basename(name)
    if storage.enabled():
        try:
            data = storage.poster_bytes(name)
            if data is not None:
                return Response(data, mimetype="image/jpeg",
                                headers={"Cache-Control": "public, max-age=86400"})
        except storage.StorageError as exc:
            logger.warning("cloud poster failed for %s: %s", name, exc)
    return send_from_directory(
        POSTER_DIR, os.path.basename(name), mimetype="image/jpeg"
    )


@app.route("/api/clips/<clip_id>", methods=["DELETE"])
def api_clip_delete(clip_id: str) -> Response:
    """Delete a clip and all its derived files. Guarded by ADMIN_TOKEN — the
    site is public, so without a matching X-Admin-Token header any visitor
    (or stray crawler) could wipe the library."""
    if not get_clip(clip_id):
        abort(404)
    expected = os.environ.get("ADMIN_TOKEN") or ""
    given = request.headers.get("X-Admin-Token", "")
    if not expected or not hmac.compare_digest(expected, given):
        return jsonify(error="delete disabled or bad admin token"), 403
    if not delete_clip(clip_id):
        return jsonify(error="deletion failed"), 500
    return jsonify(deleted=clip_id)


@app.route("/api/report/<clip_id>")
def api_report(clip_id: str) -> Response:
    if not get_clip(clip_id):
        abort(404)
    mode = request.args.get("mode") or os.environ.get("COACH_MODE", "replay")
    result = report_mod.build_report(clip_id, mode, _cfg())
    if result is None:
        return jsonify(error="no cards yet — run the stream first"), 404
    return jsonify(result)


@app.route("/api/upload", methods=["POST"])
def api_upload() -> Response:
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect("/?err=no+file+selected")
    if not f.filename.lower().endswith(".mp4"):
        return redirect("/?err=only+mp4+files+supported")
    os.makedirs(TMP_DIR, exist_ok=True)
    tmp_path = os.path.join(TMP_DIR, "up-" + uuid.uuid4().hex[:8] + ".mp4")
    f.save(tmp_path)
    ffmpeg = os.environ.get("FFMPEG", "ffmpeg")
    ffprobe = os.environ.get("FFPROBE", ffmpeg.replace("ffmpeg", "ffprobe"))
    try:
        # ffprobe via list args (path from trusted env) — no shell injection
        out = subprocess.run(  # nosec
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", tmp_path],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(out.stdout.strip() or 0)
    except Exception as exc:
        logger.warning("ffprobe failed for %s: %s", tmp_path, exc)
        duration = 0
    clip_id = register_upload(tmp_path, f.filename, duration)
    mp4_path = os.path.join(video_dir(clip_id), clip_id + ".mp4")
    if storage.enabled():
        try:
            file_id = storage.put_file(mp4_path, storage.folder_id(storage.UPLOAD_FOLDER))
        except storage.StorageError as exc:
            logger.warning("cloud upload failed for %s: %s", clip_id, exc)
        else:
            def _catalog_add(cat: dict) -> None:
                cat.setdefault("uploads", []).append({
                    "id": clip_id, "title": f.filename,
                    "duration": duration, "file_id": file_id,
                })

            try:
                storage.update_catalog(_catalog_add)
            except storage.StorageError as exc:
                logger.warning("cloud catalog update failed for %s: %s", clip_id, exc)
    # 抽 16k 单声道音轨给 Speechmatics
    os.makedirs(AUDIO_DIR, exist_ok=True)
    wav = os.path.join(AUDIO_DIR, clip_id + ".wav")
    try:
        # ffmpeg via list args (path from trusted env) — no shell injection
        subprocess.run(  # nosec
            [ffmpeg, "-y", "-i", mp4_path,
             "-ac", "1", "-ar", "16000", "-f", "wav", wav],
            capture_output=True, timeout=120,
        )
    except Exception as exc:
        logger.warning("audio extraction failed for %s: %s", clip_id, exc)
    return redirect(f"/coach/{clip_id}?mode=live")


@app.route("/api/stream/<clip_id>")
def api_stream(clip_id: str) -> Response:
    if not get_clip(clip_id):
        abort(404)
    # ?mode=live|replay 可覆盖环境变量,演示时一个服务两种模式
    mode = request.args.get("mode") or os.environ.get("COACH_MODE", "replay")
    return Response(
        sse_stream(clip_id, mode),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/agent/chat", methods=["POST"])
def api_agent_chat() -> Response:
    """教练对话 Agent:多轮追问 + 工具调用闭环(GOAI Track 2 形态)。"""
    d = request.get_json(force=True) or {}
    clip_id = d.get("clip_id") or ""
    message = d.get("message") or ""
    if not message.strip():
        return jsonify(error="empty message"), 400
    result = agent.chat(clip_id, message, d.get("history") or [])
    return jsonify(result)


@app.route("/api/annotations/<clip_id>")
def api_annotations(clip_id: str) -> Response:
    """List telestration strokes anchored to a clip (oldest first)."""
    if not get_clip(clip_id):
        abort(404)
    return jsonify(clip_id=clip_id, items=anno_mod.list_annotations(clip_id))


@app.route("/api/annotations/<clip_id>", methods=["POST"])
def api_annotations_add(clip_id: str) -> Response:
    """Add one stroke, validate at the store boundary. 400 on bad payload."""
    if not get_clip(clip_id):
        abort(404)
    payload = request.get_json(silent=True)
    try:
        item = anno_mod.add_annotation(clip_id, payload)
    except ValueError as exc:   # store-side validation gave a terse reason
        return jsonify(error=str(exc)), 400
    return jsonify(item), 201


@app.route("/api/annotations/<clip_id>/<uid>", methods=["DELETE"])
def api_annotations_del(clip_id: str, uid: str) -> Response:
    if not get_clip(clip_id):
        abort(404)
    if not anno_mod.delete_annotation(clip_id, uid):
        return jsonify(error="annotation not found"), 404
    return jsonify(deleted=uid)


@app.route("/api/clips/<clip_id>/offset")
def api_clip_offset(clip_id: str) -> Response:
    """Clip alignment offset in seconds (0 when unset)."""
    if not get_clip(clip_id):
        abort(404)
    return jsonify(clip_id=clip_id, offset=anno_mod.get_offset(clip_id))


@app.route("/api/clips/<clip_id>/offset", methods=["POST"])
def api_clip_offset_set(clip_id: str) -> Response:
    """Persist the alignment offset. 400 on non-finite/oversized input."""
    if not get_clip(clip_id):
        abort(404)
    payload = request.get_json(silent=True) or {}
    try:
        offset = anno_mod.set_offset(clip_id, payload.get("offset"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(clip_id=clip_id, offset=offset)


@app.route("/api/library")
def api_library() -> Response:
    """Clip list for the '☁ from library' picker on the index page."""
    items = [{
        "id": c["id"], "title": c["title"], "duration": c.get("duration"),
        "poster": c.get("poster"), "source": c["source"],
    } for c in list_clips()]
    return jsonify(clips=items)


@app.route("/api/health")
def health() -> Response:
    drive_ok, drive_msg = storage.ping()
    return jsonify(ok=True, mode=os.environ.get("COACH_MODE", "replay"),
                   drive={"enabled": storage.enabled(), "ok": drive_ok, "msg": drive_msg})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "6901")), threaded=True)
