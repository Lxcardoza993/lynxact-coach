"""Speechmatics 实时转录客户端(D3)。

把 16k 单声道 wav 按真实语速流式推给 Speechmatics Real-time API,
产出 {"t": 秒, "text": 句子} 的生成器,喂给 claude.live_events 的窗口化分析。

需要 .env:
  SPEECHMATICS_API_KEY=   (赛方合作额度,未配置时上传 clip 走不了 live)
  SPEECHMATICS_LANG=en    (中文解说试 cmn;D1 核验:历史支持中文,需实测)

协议:wss://eu2.rt.speechmatics.com/v2
  → StartRecognition(JSON) → 二进制音频帧 → AddTranscript(最终句)
  → 结束发 EndOfStream → 收 EndOfTranscript。
"""
import json
import logging
import os
import queue
import threading
import time
from collections.abc import Iterator

RT_URL = "wss://eu2.rt.speechmatics.com/v2"
CHUNK_MS = 200  # 每帧音频时长,控制推送节奏≈真实语速

logger = logging.getLogger(__name__)


def stream_wav(wav_path: str, lang: str | None = None) -> Iterator[dict]:
    """Yield {"t": start_seconds, "text": sentence} as finals arrive."""
    import websocket  # websocket-client,延迟 import,无 key 场景不硬依赖

    key = os.environ.get("SPEECHMATICS_API_KEY")
    if not key:
        raise RuntimeError("SPEECHMATICS_API_KEY not set")
    lang = lang or os.environ.get("SPEECHMATICS_LANG", "en")
    rate = 16000
    bytes_per_chunk = rate * 2 * CHUNK_MS // 1000  # pcm_s16le

    ws = websocket.create_connection(
        RT_URL, header=[f"Authorization: Bearer {key}"], timeout=30
    )
    ws.send(json.dumps({
        "message": "StartRecognition",
        "audio_format": {"type": "raw", "encoding": "pcm_s16le", "sample_rate": rate},
        "transcription_config": {
            "language": lang,
            "enable_partials": False,
            "max_delay": 2,
        },
    }))

    finals = queue.Queue()

    def reader():
        try:
            while True:
                msg = json.loads(ws.recv())
                kind = msg.get("message")
                if kind == "AddTranscript":
                    words = [
                        (r.get("start_time", 0), r["alternatives"][0]["content"])
                        for r in msg.get("results", [])
                        if r.get("alternatives")
                    ]
                    if words:
                        finals.put(words)
                elif kind == "EndOfTranscript":
                    finals.put(None)
                    return
                elif kind == "Error":
                    finals.put(RuntimeError(str(msg)))
                    return
        except Exception as exc:
            # Preserve the reader-thread traceback in the logs: the exception
            # is re-raised from the main thread below (raise pending), which
            # loses this frame. logger.exception captures the full stack here.
            logger.exception("speechmatics reader thread failed")
            finals.put(exc)

    threading.Thread(target=reader, daemon=True).start()

    buf, buf_start, pending = [], None, None
    try:
        with open(wav_path, "rb") as f:
            f.seek(44)  # 跳过 wav 头
            while True:
                chunk = f.read(bytes_per_chunk)
                if not chunk:
                    break
                ws.send_binary(chunk)
                time.sleep(CHUNK_MS / 1000 * 0.95)  # 按真实语速推送,对齐视频播放
                # 非阻塞收集已到的最终句
                while True:
                    try:
                        item = finals.get_nowait()
                    except queue.Empty:
                        break
                    if item is None or isinstance(item, Exception):
                        pending = item
                        break
                    for t, w in item:
                        if buf_start is None:
                            buf_start = t
                        buf.append(w)
                    # 句读:词间隔大或结尾标点 → 出一句
                    if buf and (buf[-1].endswith((".", "?", "!", "。")) or len(buf) >= 25):
                        yield {"t": buf_start or 0.0, "text": " ".join(buf)}
                        buf, buf_start = [], None
        ws.send(json.dumps({"message": "EndOfStream", "last_seq_no": 0}))
    finally:
        try:
            ws.close()
        except Exception as exc:
            logger.warning("ws.close failed: %s", exc)
    if buf:
        yield {"t": buf_start or 0.0, "text": " ".join(buf)}
    if isinstance(pending, Exception):
        raise pending
