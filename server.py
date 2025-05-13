#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Camera‑backend: Flask + Flask‑SocketIO (async_mode="threading") + OpenCV.
"""

import os, time, json, logging, threading

import cv2  # ← OpenCV
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO

# -------------------- ЛОГИРОВАНИЕ -----------------------------------------
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
LOG = logging.getLogger(__name__)

# -------------------- FLASK / SOCKETIO ------------------------------------
app = Flask(__name__, static_folder="static")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

FILES_DIR = "./demo_files"
SETTINGS_FILE = "./settings.json"

# -------------------- ИНИЦИАЛИЗАЦИЯ КАМЕРЫ --------------------------------
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)  # при необходимости смените индекс
if not cap.isOpened():
    raise RuntimeError("Web‑камера не обнаружена")

CURRENT = {
    "brightness": 50,  # диапазон 0‑100 (UI)
    "contrast": 50,
    "focus": 0,  # 0 = автофокус
    "exposure": -4,  # auto‑exposure V4L2
}


def apply_settings():
    cap.set(cv2.CAP_PROP_BRIGHTNESS, CURRENT["brightness"] / 100)
    cap.set(cv2.CAP_PROP_CONTRAST, CURRENT["contrast"] / 100)
    cap.set(cv2.CAP_PROP_FOCUS, CURRENT["focus"])
    cap.set(cv2.CAP_PROP_EXPOSURE, CURRENT["exposure"])
    LOG.debug("Applied settings: %s", CURRENT)


apply_settings()


# -------------------- УТИЛИТЫ ---------------------------------------------
def list_files():
    out = []
    for name in os.listdir(FILES_DIR):
        path = os.path.join(FILES_DIR, name)
        if os.path.isfile(path):
            out.append({"name": name, "thumb": f"/files/{name}"})
    return out


# -------------------- HTTP‑МАРШРУТЫ ---------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/files/<fname>")
def files(fname):
    return send_from_directory(FILES_DIR, fname)


@app.route("/api/files")
def api_files():
    return jsonify(list_files())


@app.route("/api/delete", methods=["POST"])
def api_delete():
    data = request.json or {}
    for f in data.get("files", []):
        try:
            os.remove(os.path.join(FILES_DIR, f))
        except FileNotFoundError:
            pass
    socketio.emit("files_updated", list_files(), broadcast=True)
    return "", 204


# ---------- длительная обработка ------------------------------------------
@app.route("/api/process", methods=["POST"])
def api_process():
    files = (request.json or {}).get("files", [])
    socketio.start_background_task(long_process, files)
    return "", 202


def long_process(files):
    total = max(len(files), 1)
    for i, f in enumerate(files, 1):
        socketio.sleep(1.5)  # non‑blocking
        progress = int(i / total * 100)
        socketio.emit(
            "process_progress", {"file": f, "progress": progress}, broadcast=True
        )
        LOG.debug("process_progress %s → %s%%", f, progress)
    socketio.emit("process_done", broadcast=True)
    LOG.debug("process_done")


# ---------- настройки ------------------------------------------------------
@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        try:
            with open(SETTINGS_FILE) as fh:
                CURRENT.update(json.load(fh))
                apply_settings()
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return jsonify(CURRENT)
    else:
        new = request.json or {}
        CURRENT.update(
            {
                "brightness": int(
                    new.get("contrast", 50)
                ),  # из формы приходит 'contrast'
                "contrast": int(new.get("focus", 50)),  # и т.д. (пример)
                "focus": int(new.get("focus", 0)),
                "exposure": int(new.get("exposure", -4)),
            }
        )
        apply_settings()
        with open(SETTINGS_FILE, "w") as fh:
            json.dump(CURRENT, fh)
        LOG.debug("settings updated %s", CURRENT)
        return "", 204


# ---------- MJPEG‑ПОТОК ----------------------------------------------------
@app.route("/mjpeg")
def mjpeg():
    def generate():
        while True:
            ok, frame = cap.read()
            if not ok:
                socketio.sleep(0.04)
                continue
            ret, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ret:
                continue
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
            )
            socketio.sleep(0.04)  # ≈25 fps

    return app.response_class(
        generate(), mimetype="multipart/x-mixed-replace; boundary=frame"
    )


# ---------- WebSocket‑СОБЫТИЯ ---------------------------------------------
@socketio.on("set_brightness")
def set_brightness(val):
    CURRENT["brightness"] = int(val)
    cap.set(cv2.CAP_PROP_BRIGHTNESS, CURRENT["brightness"] / 100)
    LOG.debug("Brightness %s", CURRENT["brightness"])


@socketio.on("set_contrast")
def ws_contrast(val):
    CURRENT["contrast"] = int(val)
    cap.set(cv2.CAP_PROP_CONTRAST, CURRENT["contrast"] / 100)
    LOG.debug("Contrast %s", CURRENT["contrast"])


@socketio.on("request_new_file")
def new_file_dummy():
    name = f"new_{int(time.time())}.jpg"
    open(os.path.join(FILES_DIR, name), "wb").write(b"")
    socketio.emit("files_updated", list_files(), broadcast=True)
    socketio.emit("popup", f"Появился новый файл: {name}", broadcast=True)


# -------------------- MAIN -------------------------------------------------
if __name__ == "__main__":
    os.makedirs(FILES_DIR, exist_ok=True)
    try:
        socketio.run(app, host="0.0.0.0", port=5000)
    finally:
        cap.release()
