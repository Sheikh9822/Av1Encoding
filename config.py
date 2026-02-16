import os

# Telegram Credentials
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = int(os.getenv("CHAT_ID", 0))

# File Paths
FILE_NAME = os.getenv("FILE_NAME", "output.mkv")
SOURCE = "source.mkv"
SCREENSHOT = "grid_preview.jpg"
LOG_FILE = "encode_log.txt"

# User Settings
USER_RES = os.getenv("USER_RES")
USER_CRF = os.getenv("USER_CRF")
USER_PRESET = os.getenv("USER_PRESET")
USER_GRAIN = int(os.getenv("USER_GRAIN", "0"))
AUDIO_MODE = os.getenv("AUDIO_MODE", "opus")
AUDIO_BITRATE = os.getenv("AUDIO_BITRATE", "128k")
RUN_VMAF = os.getenv("RUN_VMAF", "true").lower() == "true"
