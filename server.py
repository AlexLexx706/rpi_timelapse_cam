#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time, json, logging, threading
from pathlib import Path
from datetime import datetime
import subprocess
import cv2

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
    "brightness": 50,
    "contrast": 50,
    "focus": 0,
    "exposure": -4,
    "fps": 5,
}

# ───────────────────────  CAMERA SHARED LOOP  ─────────────────
latest_frame: bytes | None = None  # JPEG buffer
frame_event = threading.Event()  # set() when new frame ready
stop_capture = threading.Event()
STREAMERS = 0  # active /mjpeg clients
LOCK = threading.Lock()
capture_thread: threading.Thread | None = None


def _capture_loop():
    """Runs in a dedicated thread: grabs frames & encodes JPEG."""
    LOG.debug("Capture thread: opening camera")
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    if not cap.isOpened():
        LOG.error("Camera not available")
        return

    try:
        while not stop_capture.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.03)
                continue

            # ── software brightness / contrast (0‑100 → alpha/beta) ──
            alpha = 1 + (CURRENT["contrast"] - 50) / 50  # 0‑100 → 0‑2
            beta = (CURRENT["brightness"] - 50) * 2  # 0‑100 → −100..+100
            frame = cv2.convertScaleAbs(frame, alpha=alpha, beta=beta)

            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok:
                global latest_frame
                latest_frame = buf.tobytes()
                frame_event.set()
                frame_event.clear()

            time.sleep(0.03)  # ≈ 30 fps
    finally:
        cap.release()
        LOG.debug("Capture thread: camera released")


def _ensure_capture_running():
    """Spin up the capture loop if not alive."""
    global capture_thread
    if capture_thread is None or not capture_thread.is_alive():
        stop_capture.clear()
        capture_thread = threading.Thread(target=_capture_loop, daemon=True)
        capture_thread.start()


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


# def _long_process(files):
#     total = max(len(files), 1)
#     for i, f in enumerate(files, 1):
#         socketio.sleep(1.0)
#         p = int(i / total * 100)
#         socketio.emit("process_progress", {"file": f, "progress": p})
#         LOG.debug("process %s → %s%%", f, p)
#     socketio.emit("process_done")


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
    CURRENT.update(
        {
            "brightness": int(new.get("brightness", CURRENT["brightness"])),
            "contrast": int(new.get("contrast", CURRENT["contrast"])),
            "focus": int(new.get("focus", CURRENT["focus"])),
            "exposure": int(new.get("exposure", CURRENT["exposure"])),
            "fps": int(new.get("fps", CURRENT["fps"])),
        }
    )
    _save_settings_file()
    _broadcast_settings(origin=None)
    return "", 204


# ───── MJPEG ROUTE (shared frame) ─────────────────────────────
@app.route("/mjpeg")
def mjpeg():
    global STREAMERS
    with LOCK:
        STREAMERS += 1
        _ensure_capture_running()
        LOG.debug("Streamer +1 → %s", STREAMERS)

    def gen():
        try:
            while True:
                if not frame_event.wait(timeout=5):
                    continue
                if latest_frame:
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                        + latest_frame
                        + b"\r\n"
                    )
        finally:  # called when client disconnects
            global STREAMERS
            with LOCK:
                STREAMERS -= 1
                LOG.debug("Streamer −1 → %s", STREAMERS)
                if STREAMERS == 0:
                    stop_capture.set()

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
