import asyncio
import os
import subprocess
import time
import signal
from pyrogram import Client, filters
from pyrogram.types import Message

SOURCE = "source.mkv"
CANCELLED = False
PROCESS = None

# ---------- FFPROBE ----------

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
    global CANCELLED, PROCESS

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    bot_token = os.getenv("BOT_TOKEN")
    chat_id = int(os.getenv("CHAT_ID"))
    file_name = os.getenv("FILE_NAME")

    duration = get_duration()
    height = get_height()
    crf, preset = select_crf(height)

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:

        # Cancel command
        @app.on_message(filters.command("cancel"))
        async def cancel_handler(client, message):
            global CANCELLED, PROCESS
            CANCELLED = True
            if PROCESS:
                PROCESS.send_signal(signal.SIGINT)
            await message.reply("❌ Encoding cancelled.")

        status: Message = await app.send_message(
            chat_id,
            f"🎬 Encoding Started\nResolution: {height}p\nCRF: {crf}"
        )

        attempt = 0
        success = False

        while attempt < 2 and not success and not CANCELLED:
            attempt += 1
            start_time = time.time()

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

            PROCESS = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )

            last_update = 0

            for line in PROCESS.stdout:
                if CANCELLED:
                    break

                if "out_time_ms" in line:
                    out_time = int(line.split("=")[1]) / 1_000_000
                    percent = (out_time / duration) * 100

                    elapsed = time.time() - start_time
                    fps = out_time / elapsed if elapsed > 0 else 0
                    speed = out_time / elapsed if elapsed > 0 else 0

                    eta = (duration - out_time) / speed if speed > 0 else 0
                    eta_min = int(eta // 60)
                    eta_sec = int(eta % 60)

                    size_mb = 0
                    if os.path.exists(file_name):
                        size_mb = os.path.getsize(file_name) / (1024 * 1024)

                    if time.time() - last_update > 5:
                        await app.edit_message_text(
                            chat_id,
                            status.id,
                            f"🎬 Encoding...\n"
                            f"{percent:.2f}%\n\n"
                            f"📦 Size: {size_mb:.2f} MB\n"
                            f"⚡ FPS: {fps:.2f}\n"
                            f"🚀 Speed: {speed:.2f}x\n"
                            f"⏳ ETA: {eta_min}m {eta_sec}s"
                        )
                        last_update = time.time()

            PROCESS.wait()

            if PROCESS.returncode == 0:
                success = True
            elif not CANCELLED:
                await app.edit_message_text(
                    chat_id,
                    status.id,
                    f"⚠️ Encode failed. Retrying... (Attempt {attempt}/2)"
                )

        if CANCELLED:
            return

        await app.edit_message_text(chat_id, status.id, "✅ Encoding Complete\n📤 Uploading...")

        # Upload with progress
        async def upload_progress(current, total):
            percent = current * 100 / total
            await app.edit_message_text(
                chat_id,
                status.id,
                f"📤 Uploading...\n{percent:.2f}%"
            )

        await app.send_document(
            chat_id=chat_id,
            document=file_name,
            caption=f"✅ Encoding Complete!\n📄 `{file_name}`",
            progress=upload_progress
        )

        # Auto delete source
        if os.path.exists(SOURCE):
            os.remove(SOURCE)

        await app.edit_message_text(chat_id, status.id, "🎉 Upload Finished Successfully!")

if __name__ == "__main__":
    asyncio.run(main())
