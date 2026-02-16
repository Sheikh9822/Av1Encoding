import os

# Telegram Credentials
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = int(os.getenv("CHAT_ID", 0))

# File Paths
FILE_NAME = os.getenv("FILE_NAME", "output.mkv").strip()
SOURCE = "source.mkv"
SCREENSHOT = "grid_preview.jpg"
LOG_FILE = "encode_log.txt"

# User Settings (Safely parsing potential empty strings from GitHub Actions)
USER_RES = os.getenv("USER_RES", "").strip()
USER_CRF = os.getenv("USER_CRF", "").strip()
USER_PRESET = os.getenv("USER_PRESET", "").strip()

_grain_raw = os.getenv("USER_GRAIN", "0").strip()
USER_GRAIN = int(_grain_raw) if _grain_raw.isdigit() else 0

AUDIO_MODE = os.getenv("AUDIO_MODE", "opus").strip()
AUDIO_BITRATE = os.getenv("AUDIO_BITRATE", "128k").strip()
RUN_VMAF = os.getenv("RUN_VMAF", "true").strip().lower() == "true"
