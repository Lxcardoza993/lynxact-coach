"""mp4 Range streaming (borrowed pattern from LynxMove /video route)."""
import os
import re

from flask import Response, abort, request

from .clips import VAULT_ROOT

CHUNK = 1 << 16  # 64 KiB


def range_stream(fname):
    path = os.path.join(VAULT_ROOT, os.path.basename(fname))
    if not os.path.exists(path):
        abort(404)
    size = os.path.getsize(path)
    range_header = request.headers.get("Range")
    start, end = 0, size - 1
    status = 200
    if range_header:
        m = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            status = 206
    length = end - start + 1

    def generate():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return Response(generate(), status=status, headers=headers, mimetype="video/mp4")
