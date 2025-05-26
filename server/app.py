#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import cv2
import flask
from flask_socketio import SocketIO
import utils
import settings

LOG = logging.getLogger("camera")
APP = flask.Flask(__name__, static_folder="static")
WS = SocketIO(APP, cors_allowed_origins="*", async_mode="threading")


@APP.route("/")
def index():
    return flask.render_template("index.html")


@APP.route("/files/<path:fname>")
def files(fname):
    """return file for client"""
    resp = flask.make_response(
        flask.send_from_directory(
            settings.FILES_DIR,
            fname,
            conditional=True,
        )
    )
    resp.cache_control.public = True
    resp.cache_control.max_age = settings.MAX_AGE
    return resp


@APP.route("/thumbs/<path:fname>")
def thumbs(fname):
    orig_file = settings.FILES_DIR / fname
    if not orig_file.is_file():
        flask.abort(404)

    thumb_file = settings.TMP_DIR / fname

    if thumb_file.is_file():
        return flask.send_file(
            thumb_file, mimetype="image/jpeg", max_age=settings.MAX_AGE
        )

    img = cv2.imread(str(orig_file))

    if img is None:
        flask.abort(415)  # не картинка

    h, w = img.shape[:2]

    if w > settings.THUMB_MAX_W:
        new_h = int(h * settings.THUMB_MAX_W / w)
        img = cv2.resize(
            img, (settings.THUMB_MAX_W, new_h), interpolation=cv2.INTER_AREA
        )

    cv2.imwrite(str(thumb_file), img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])

    return flask.send_file(thumb_file, mimetype="image/jpeg", max_age=settings.MAX_AGE)


@APP.route("/api/files")
def api_files():
    return flask.jsonify(utils.list_files())


@APP.route("/api/delete", methods=["POST"])
def api_delete():
    for f in (flask.request.json or {}).get("files", []):
        try:
            (settings.FILES_DIR / f).unlink(missing_ok=True)
        except Exception as e:
            LOG.warning("Delete %s: %s", f, e)
    WS.emit("files_updated", utils.list_files())
    return "", 204


@APP.route("/api/process", methods=["POST"])
def api_process():
    files = (flask.request.json or {}).get("files", [])
    WS.start_background_task(utils.create_view, files, WS)
    return "", 202


@APP.route("/api/videos")
def api_videos():
    videos = utils.list_videos()
    return flask.jsonify({"data": videos, "total_count": len(videos)})


@APP.route("/api/videos/delete", methods=["POST"])
def api_videos_delete():
    for f in (flask.request.json or {}).get("files", []):
        try:
            (settings.VIDEOS_DIR / f).unlink(missing_ok=True)
        except Exception as e:
            LOG.warning("Delete video %s: %s", f, e)
    WS.emit("videos_updated", utils.list_videos())
    return "", 204


@APP.route("/videos/<path:fname>")
def serve_video(fname):
    f = settings.VIDEOS_DIR / fname
    if not f.exists():
        flask.abort(404)
    return flask.send_file(f, as_attachment=False)


@APP.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if flask.request.method == "GET":
        cur_settitngs = settings.SETTINGS.copy()
        cur_settitngs["resolution"] = "x".join(str(i) for i in cur_settitngs["resolution"])
        LOG.debug(cur_settitngs)
        return flask.jsonify(cur_settitngs)

    new = flask.request.json or {}
    LOG.debug("settings:%s", new)
    utils.update_settings(new)
    broadcast_settings(origin=None)
    return "", 204


@APP.route("/mjpeg")
def mjpeg():
    def gen():
        while True:
            with utils.OUTPUT_FILE.condition:
                utils.OUTPUT_FILE.condition.wait()
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                    + utils.OUTPUT_FILE.frame
                    + b"\r\n"
                )

    return APP.response_class(
        gen(), mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@WS.on("set_brightness")
def ws_brightness(val):
    utils.update_settings({"brightness": int(val)})
    broadcast_settings(origin=flask.request.sid)


@WS.on("set_contrast")
def ws_contrast(val):
    utils.update_settings({"contrast": int(val)})
    broadcast_settings(origin=flask.request.sid)


def broadcast_settings(origin=None):
    WS.emit(
        "settings_changed",
        {"settings": settings.SETTINGS, "origin": origin},
        skip_sid=origin,  # исключаем только WebSocket-инициаторов
    )


def main():
    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    utils.init_resources()
    utils.start_camera()
    WS.run(APP, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
