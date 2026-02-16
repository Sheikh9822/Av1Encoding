import os

# Files
SOURCE_FILE = "source.mkv"
OUTPUT_FILE_NAME = os.getenv("FILE_NAME", "encoded_video.mkv") # Fallback if env missing
SCREENSHOT_FILE = "grid_preview.jpg"
LOG_FILE = "encode_log.txt"

# Flags
CANCELLED = False