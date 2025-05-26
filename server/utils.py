import json
import logging
import threading
import subprocess
import io
import settings
import datetime
import picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput
from libcamera import Transform

LOG = logging.getLogger(__name__)
PICAM = picamera2.Picamera2()


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


OUTPUT_FILE = StreamingOutput()


def init_resources():
    settings.FILES_DIR.mkdir(exist_ok=True)
    settings.TMP_DIR.mkdir(parents=True, exist_ok=True)
    for f in settings.TMP_DIR.iterdir():
        if f.is_file():
            f.unlink()
    settings.VIDEOS_DIR.mkdir(exist_ok=True)
    load_settings()


def start_camera():
    frame_duration_limits = 1000000 // settings.SETTINGS["fps"]
    video_config = PICAM.create_video_configuration(
        main={"size": PICAM.sensor_resolution, "format": "RGB888"},
        lores={
            "size": settings.SETTINGS["resolution"],
            "format": "YUV420",
        },
        transform=Transform(hflip=1, vflip=1),
        display=None,
        controls={
            "FrameDurationLimits": (frame_duration_limits, frame_duration_limits)
        },
    )

    PICAM.configure(video_config)
    encoder = MJPEGEncoder()
    output = FileOutput()
    output.fileoutput = OUTPUT_FILE
    encoder.output = output
    PICAM.start_encoder(encoder, name="lores")
    PICAM.start()


def list_videos():
    out = []
    for i, f in enumerate(
        sorted(settings.VIDEOS_DIR.glob("*.mp4"), key=lambda p: p.name.lower()), start=1
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


def list_files() -> list[dict]:
    out = []
    for f in sorted(settings.FILES_DIR.iterdir(), key=lambda p: p.name.lower()):
        if f.is_file():
            ver = int(f.stat().st_mtime)
            out.append({"name": f.name, "thumb": f"/thumbs/{f.name}?v={ver}"})
    return out


def load_settings():
    if settings.SETTINGS_FILE.exists():
        try:
            settings.SETTINGS.update(json.loads(settings.SETTINGS_FILE.read_text()))
        except Exception as e:
            LOG.warning("Settings load: %s", e)


def save_settings():
    try:
        settings.SETTINGS_FILE.write_text(json.dumps(settings.SETTINGS))
    except Exception as e:
        LOG.warning("Settings save: %s", e)


def create_view(files, socketio):
    if not files:
        return

    # 1. Создаём временный список файлов
    list_txt = settings.TMP_DIR / "list.txt"
    with list_txt.open("w") as fh:
        for fname in files:
            full_path = (settings.FILES_DIR / fname).resolve()
            fh.write(f"file '{full_path}'\n")

    # 2. Параметры
    fps = settings.SETTINGS["fps"]
    output_file = (
        settings.VIDEOS_DIR
        / f"{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mp4"
    )
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

    socketio.emit("process_done", {"video": output_file.name})
    socketio.emit("videos_updated", list_videos())


def update_settings(new):
    resolution = new.get("resolution")
    if resolution:
        settings.SETTINGS["resolution"] = tuple(int(n) for n in resolution.split("x"))

    settings.SETTINGS["brightness"] = int(
        new.get("brightness", settings.SETTINGS["brightness"])
    )
    settings.SETTINGS["contrast"] = int(
        new.get("contrast", settings.SETTINGS["contrast"])
    )
    settings.SETTINGS["focus"] = int(new.get("focus", settings.SETTINGS["focus"]))
    settings.SETTINGS["exposure"] = int(
        new.get("exposure", settings.SETTINGS["exposure"])
    )
    settings.SETTINGS["fps"] = int(new.get("fps", settings.SETTINGS["fps"]))
    settings.SETTINGS["tl_start"] = str(
        new.get("tl_start", settings.SETTINGS["tl_start"])
    )
    settings.SETTINGS["tl_stop"] = str(new.get("tl_stop", settings.SETTINGS["tl_stop"]))
    settings.SETTINGS["tl_interval_sec"] = int(
        new.get("tl_interval_sec", settings.SETTINGS["tl_interval_sec"])
    )
    settings.SETTINGS["tl_mode"] = int(new.get("tl_mode", settings.SETTINGS["tl_mode"]))

    save_settings()
