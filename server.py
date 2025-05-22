#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time, json, logging, threading
from pathlib import Path
from datetime import datetime
import subprocess
import io
import cv2
import time
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput
from libcamera import Transform
from threading import Condition

from picamera2 import Picamera2
from io import BytesIO
from PIL import Image

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory,
    make_response,
    abort,
    send_file,
)
from flask_socketio import SocketIO

# ───────────────────────  LOGGING  ────────────────────────────
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
LOG = logging.getLogger("camera")

# ───────────────────────  FLASK / SIO  ────────────────────────
app = Flask(__name__, static_folder="static")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

FILES_DIR = Path("./demo_files")
SETTINGS_FILE = Path("./settings.json")

FILES_DIR.mkdir(exist_ok=True)

TMP_DIR = Path("./tmp_thumbs")
TMP_DIR.mkdir(parents=True, exist_ok=True)

THUMB_MAX_W = 160  # ширина миниатюры (px)
MAX_AGE = 60 * 60 * 24 * 30

VIDEOS_DIR = Path("./videos")
VIDEOS_DIR.mkdir(exist_ok=True)


# чистим содержимое при запуске
for f in TMP_DIR.iterdir():
    if f.is_file():
        f.unlink()

# ───────────────────────  GLOBAL STATE  ───────────────────────
CURRENT = {  # UI‑диапазон: 0‑100
    "resolution": "1920x1080",
    "brightness": 50,
    "contrast": 50,
    "focus": 0,
    "exposure": -4,
    "fps": 5,
    "tl_start": "",
    "tl_stop": "",
    "tl_interval_sec": 60,
    "tl_mode": 0
}

picam2 = Picamera2()

FrameDurationLimits = 1000000 // CURRENT['fps']
# Конфигурация камеры с двумя потоками: lores для MJPEG и main для фотосъёмки
video_config = picam2.create_video_configuration(
    main={"size": picam2.sensor_resolution, "format": "RGB888"},
    lores={"size": list(int(i) for i in CURRENT["resolution"].split('x')), "format": "YUV420"},
    transform=Transform(hflip=1, vflip=1),
    display=None,
    controls={"FrameDurationLimits": (FrameDurationLimits, FrameDurationLimits) }
)
picam2.configure(video_config)

encoder = MJPEGEncoder()
output = FileOutput()
encoder.output = output
picam2.start_encoder(encoder, name="lores")

# Запуск камеры
picam2.start()

# Буфер MJPEG-потока
class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

output_file = StreamingOutput()
output.fileoutput = output_file

# ───────────────────────  HTTP ROUTES  ────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/files/<path:fname>")
def files(fname):
    """Отдаём файл с агрессивным кешем + ETag/Last‑Modified."""
    resp = make_response(
        send_from_directory(
            FILES_DIR,
            fname,
            conditional=True,  # включает ETag + 304
        )
    )
    resp.cache_control.public = True
    resp.cache_control.max_age = 60 * 60 * 24 * 30  # 30 дней
    return resp


@app.route("/thumbs/<path:fname>")
def thumbs(fname):
    orig_file = FILES_DIR / fname
    if not orig_file.is_file():
        abort(404)

    thumb_file = TMP_DIR / fname

    # если уже есть готовый эскиз → сразу отдаём
    if thumb_file.is_file():
        return send_file(thumb_file, mimetype="image/jpeg", max_age=MAX_AGE)

    # иначе создаём
    img = cv2.imread(str(orig_file))

    if img is None:
        abort(415)  # не картинка

    h, w = img.shape[:2]

    if w > THUMB_MAX_W:
        new_h = int(h * THUMB_MAX_W / w)
        img = cv2.resize(img, (THUMB_MAX_W, new_h), interpolation=cv2.INTER_AREA)

    cv2.imwrite(str(thumb_file), img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])

    return send_file(thumb_file, mimetype="image/jpeg", max_age=MAX_AGE)


@app.route("/api/files")
def api_files():
    return jsonify(_list_files())


@app.route("/api/delete", methods=["POST"])
def api_delete():
    for f in (request.json or {}).get("files", []):
        try:
            (FILES_DIR / f).unlink(missing_ok=True)
        except Exception as e:
            LOG.warning("Delete %s: %s", f, e)
    socketio.emit("files_updated", _list_files())
    return "", 204


# ───── long process dummy ─────────────────────────────────────
@app.route("/api/process", methods=["POST"])
def api_process():
    files = (request.json or {}).get("files", [])
    socketio.start_background_task(_long_process, files)
    return "", 202

def _long_process(files):
    if not files:
        return

    # 1. Создаём временный список файлов
    list_txt = TMP_DIR / "list.txt"
    with list_txt.open("w") as fh:
        for fname in files:
            full_path = (FILES_DIR / fname).resolve()
            fh.write(f"file '{full_path}'\n")

    # 2. Параметры
    fps = CURRENT.get("fps", 5)
    output_file = VIDEOS_DIR / f'{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}.mp4'
    cmd = [
        "ffmpeg",
        "-y",
        "-r",
        str(fps),
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_txt),
        "-vf",
        f"fps={fps}",
        "-pix_fmt",
        "yuv420p",
        str(output_file),
    ]

    LOG.info("Running ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True)

    if proc.returncode != 0:
        LOG.error("ffmpeg failed:\n%s", proc.stderr.decode())
    else:
        LOG.info("ffmpeg finished OK, saved: %s", output_file)

    socketio.emit("process_done", { "video": output_file.name })
    socketio.emit("videos_updated", _list_videos())


def _list_videos():
    out = []
    for i, f in enumerate(
        sorted(VIDEOS_DIR.glob("*.mp4"), key=lambda p: p.name.lower()), start=1
    ):
        out.append(
            {
                "id": i,
                "name": f.name,
                "url": f"/videos/{f.name}",
                "size": f.stat().st_size,
            }
        )
    return out


@app.route("/api/videos")
def api_videos():
    videos = _list_videos()
    return jsonify({"data": videos, "total_count": len(videos)})


@app.route("/api/videos/delete", methods=["POST"])
def api_videos_delete():
    for f in (request.json or {}).get("files", []):
        try:
            (VIDEOS_DIR / f).unlink(missing_ok=True)
        except Exception as e:
            LOG.warning("Delete video %s: %s", f, e)
    socketio.emit("videos_updated", _list_videos())
    return "", 204


@app.route("/videos/<path:fname>")
def serve_video(fname):
    f = VIDEOS_DIR / fname
    if not f.exists():
        abort(404)
    return send_file(f, as_attachment=False)


# ───── settings CRUD ──────────────────────────────────────────
@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    global CURRENT
    if request.method == "GET":
        _load_settings_file()
        return jsonify(CURRENT)

    new = request.json or {}
    LOG.debug("settings:%s", new)
    CURRENT.update(
        {
            "resolution": new.get("resolution", CURRENT["resolution"]),
            "brightness": int(new.get("brightness", CURRENT["brightness"])),
            "contrast": int(new.get("contrast", CURRENT["contrast"])),
            "focus": int(new.get("focus", CURRENT["focus"])),
            "exposure": int(new.get("exposure", CURRENT["exposure"])),
            "fps": int(new.get("fps", CURRENT["fps"])),
            "tl_start": new.get("tl_start", CURRENT["tl_start"]),
            "tl_stop": new.get("tl_stop", CURRENT["tl_stop"]),
            "tl_interval_sec": int(new.get("tl_interval_sec", CURRENT["tl_interval_sec"])),
            "tl_mode": int(new.get("tl_mode", CURRENT["tl_mode"])),
        }
    )
    _save_settings_file()
    _broadcast_settings(origin=None)
    return "", 204


# ───── MJPEG ROUTE (shared frame) ─────────────────────────────
@app.route("/mjpeg")
def mjpeg():
    def gen():
        while True:
            with output_file.condition:
                output_file.condition.wait()
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                    + output_file.frame
                    + b"\r\n"
                )
    return app.response_class(
        gen(), mimetype="multipart/x-mixed-replace; boundary=frame"
    )


# ───────────────────────  SOCKET EVENTS  ──────────────────────
@socketio.on("set_brightness")
def ws_brightness(val):
    CURRENT["brightness"] = int(val)
    _broadcast_settings(origin=request.sid)


@socketio.on("set_contrast")
def ws_contrast(val):
    CURRENT["contrast"] = int(val)
    _broadcast_settings(origin=request.sid)


@socketio.on("request_new_file")
def ws_new_file():
    name = f"new_{int(time.time())}.jpg"
    (FILES_DIR / name).touch()
    socketio.emit("files_updated", _list_files())
    socketio.emit("popup", f"Появился новый файл: {name}")


# ───────────────────────  HELPERS  ────────────────────────────
def _broadcast_settings(origin=None):
    socketio.emit(
        "settings_changed",
        {"settings": CURRENT, "origin": origin},
        skip_sid=origin,  # исключаем только WebSocket-инициаторов
    )


def _list_files() -> list[dict]:
    out = []
    for f in sorted(FILES_DIR.iterdir(), key=lambda p: p.name.lower()):
        if f.is_file():
            ver = int(f.stat().st_mtime)
            out.append({"name": f.name, "thumb": f"/thumbs/{f.name}?v={ver}"})
    return out


def _load_settings_file():
    return
    if SETTINGS_FILE.exists():
        try:
            CURRENT.update(json.loads(SETTINGS_FILE.read_text()))
        except Exception as e:
            LOG.warning("Settings load: %s", e)


def _save_settings_file():
    try:
        SETTINGS_FILE.write_text(json.dumps(CURRENT))
    except Exception as e:
        LOG.warning("Settings save: %s", e)


# ───────────────────────  MAIN  ───────────────────────────────
if __name__ == "__main__":
    _load_settings_file()
    socketio.run(app, host="0.0.0.0", port=5000)
