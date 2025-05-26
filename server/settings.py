from pathlib import Path

FILES_DIR = Path("./demo_files")
TMP_DIR = Path("./tmp_thumbs")
VIDEOS_DIR = Path("./videos")
SETTINGS_FILE = Path("./settings.json")
THUMB_MAX_W = 160
MAX_AGE = 60 * 60 * 24 * 30

# ───────────────────────  GLOBAL STATE  ───────────────────────
SETTINGS = {
    "resolution": (1920, 1080),
    "brightness": 50,
    "contrast": 50,
    "focus": 0,
    "exposure": -4,
    "fps": 10,
    "tl_start": "",
    "tl_stop": "",
    "tl_interval_sec": 60,
    "tl_mode": 0,
}
