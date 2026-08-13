"""mp4 Range streaming (borrowed pattern from LynxMove /video route)."""
import os
import re

from flask import Response, abort, request

CHUNK = 1 << 16  # 64 KiB


def range_stream(fname: str, base_dir: str) -> Response:
    path = os.path.join(base_dir, os.path.basename(fname))
    if not os.path.exists(path):
        abort(404)
    size = os.path.getsize(path)
    range_header = request.headers.get("Range")
    start, end = 0, size - 1
    status = 200
    if range_header:
        m = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if m:
            s, e = m.group(1), m.group(2)
            if s:                       # bytes=start-end or bytes=start- (has start)
                start = int(s)
                end = min(int(e), size - 1) if e else size - 1
            elif e:                     # bytes=-suffix → last N bytes (RFC 7233 §2.1)
                start = max(0, size - int(e))
                end = size - 1
            # else bytes=- → whole file (start=0, end=size-1, already set)
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
