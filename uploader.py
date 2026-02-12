import asyncio
import os
import subprocess
import time
from pyrogram import Client
from pyrogram.types import Message

SOURCE = "source.mkv"

# ---------- FFPROBE HELPERS ----------

def get_duration():
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        SOURCE
    ]
    return float(subprocess.check_output(cmd).decode().strip())

def get_height():
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=height",
        "-of", "csv=p=0",
        SOURCE
    ]
    return int(subprocess.check_output(cmd).decode().strip())

def select_crf(height):
    if height >= 2000:
        return 30, 8
    elif height >= 1000:
        return 28, 8
    elif height >= 700:
        return 27, 8
    elif height >= 480:
        return 25, 7
    else:
        return 24, 7

# ---------- MAIN ----------

async def main():
    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    bot_token = os.getenv("BOT_TOKEN")
    chat_id = int(os.getenv("CHAT_ID"))
    file_name = os.getenv("FILE_NAME")

    duration = get_duration()
    height = get_height()
    crf, preset = select_crf(height)

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:

        status: Message = await app.send_message(
            chat_id,
            f"🎬 **Encoding Started**\n\n"
            f"Resolution: {height}p\n"
            f"CRF: {crf} | Preset: {preset}"
        )

        # FFmpeg command
        cmd = [
            "ffmpeg",
            "-i", SOURCE,
            "-map", "0",
            "-c:v", "libsvtav1",
            "-pix_fmt", "yuv420p10le",
            "-crf", str(crf),
            "-preset", str(preset),
            "-g", "240",
            "-svtav1-params", "tune=0:aq-mode=2",
            "-c:a", "libopus", "-b:a", "128k",
            "-c:s", "copy",
            "-c:t", "copy",
            "-map_metadata", "0",
            "-progress", "pipe:1",
            "-nostats",
            file_name
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )

        last_update = 0

        for line in process.stdout:
            if "out_time_ms" in line:
                out_time = int(line.split("=")[1].strip()) / 1_000_000
                percent = (out_time / duration) * 100

                if time.time() - last_update > 5:
                    await app.edit_message_text(
                        chat_id,
                        status.id,
                        f"🎬 **Encoding...**\n\n"
                        f"{percent:.2f}% Complete"
                    )
                    last_update = time.time()

        process.wait()

        await app.edit_message_text(
            chat_id,
            status.id,
            "✅ **Encoding Complete!**\n\n📤 Uploading..."
        )

        # ---------- Upload with Progress ----------

        async def upload_progress(current, total):
            percent = current * 100 / total
            await app.edit_message_text(
                chat_id,
                status.id,
                f"📤 **Uploading...**\n\n"
                f"{percent:.2f}% Complete"
            )

        await app.send_document(
            chat_id=chat_id,
            document=file_name,
            caption=f"✅ **Encoding Complete!**\n\n📄 `{file_name}`",
            progress=upload_progress
        )

        await app.edit_message_text(
            chat_id,
            status.id,
            "🎉 **Upload Finished Successfully!**"
        )

if __name__ == "__main__":
    asyncio.run(main())
