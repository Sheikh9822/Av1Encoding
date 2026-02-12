import asyncio
import os
import subprocess
import time
import signal
import sys
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

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

def select_params(height):
    """
    Optimized SVT-AV1 parameters:
    Higher resolution needs higher presets/CRF to finish within GH Action limits.
    """
    if height >= 2000:    # 4K
        return 32, 10
    elif height >= 1000:  # 1080p
        return 28, 8
    elif height >= 700:   # 720p
        return 24, 6
    else:                 # SD
        return 22, 4

# ---------- UTILS ----------

async def safe_edit(chat_id, message_id, text, app):
    """Prevents script crashes due to Telegram FloodWait or network blips."""
    try:
        await app.edit_message_text(chat_id, message_id, text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception as e:
        print(f"Failed to update status: {e}")

# ---------- MAIN ----------

async def main():
    global CANCELLED, PROCESS

    # Load environment variables
    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    bot_token = os.getenv("BOT_TOKEN")
    chat_id = int(os.getenv("CHAT_ID"))
    file_name = os.getenv("FILE_NAME")

    try:
        duration = get_duration()
        height = get_height()
        crf, preset = select_params(height)
    except Exception as e:
        print(f"Metadata extraction failed: {e}")
        return

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:

        @app.on_message(filters.command("cancel"))
        async def cancel_handler(client, message):
            global CANCELLED, PROCESS
            CANCELLED = True
            if PROCESS:
                PROCESS.send_signal(signal.SIGINT)
            await message.reply("⚠️ **Encoding Cancelled by User.**")

        status: Message = await app.send_message(
            chat_id,
            f"🎬 **Encoding Initialized**\nTarget: `{file_name}`\nResolution: {height}p\nCRF: {crf} | Preset: {preset}"
        )

        attempt = 0
        success = False

        while attempt < 2 and not success and not CANCELLED:
            attempt += 1
            start_time = time.time()

            # Enhanced FFmpeg command with standardized AV1 parameters
            cmd = [
                "ffmpeg", "-i", SOURCE,
                "-map", "0",
                "-c:v", "libsvtav1",
                "-pix_fmt", "yuv420p10le",
                "-crf", str(crf),
                "-preset", str(preset),
                "-svtav1-params", "tune=0:aq-mode=2:enable-overlays=1",
                "-c:a", "libopus", "-b:a", "128k",
                "-c:s", "copy",
                "-map_metadata", "0",
                "-progress", "pipe:1",
                "-nostats", "-y",
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
                if CANCELLED: break

                if "out_time_ms" in line:
                    try:
                        out_time = int(line.split("=")[1]) / 1_000_000
                        percent = (out_time / duration) * 100
                        elapsed = time.time() - start_time
                        speed = out_time / elapsed if elapsed > 0 else 0
                        fps = (percent / 100 * duration * 24) / elapsed if elapsed > 0 else 0 # Rough estimate

                        if time.time() - last_update > 15: # 15s interval to avoid TG flood
                            size_mb = os.path.getsize(file_name) / (1024 * 1024) if os.path.exists(file_name) else 0
                            
                            progress_text = (
                                f"🎬 **Encoding AV1...**\n"
                                f"━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 **Progress:** {percent:.2f}%\n"
                                f"⚡ **Speed:** {speed:.2f}x\n"
                                f"📦 **Current Size:** {size_mb:.2f} MB\n"
                                f"⏳ **Elapsed:** {int(elapsed // 60)}m {int(elapsed % 60)}s"
                            )
                            await safe_edit(chat_id, status.id, progress_text, app)
                            last_update = time.time()
                    except:
                        continue

            PROCESS.wait()

            if PROCESS.returncode == 0:
                success = True
            elif not CANCELLED:
                await safe_edit(chat_id, status.id, f"⚠️ Encode failed. Retrying... ({attempt}/2)", app)

        if CANCELLED:
            if os.path.exists(file_name): os.remove(file_name)
            return

        await safe_edit(chat_id, status.id, "✅ **Encoding Complete**\n📤 *Starting Upload...*", app)

        async def upload_progress(current, total):
            nonlocal last_update
            if time.time() - last_update > 10:
                percent = current * 100 / total
                await safe_edit(chat_id, status.id, f"📤 **Uploading...**\n`{percent:.2f}%`", app)
                last_update = time.time()

        await app.send_document(
            chat_id=chat_id,
            document=file_name,
            caption=f"✅ **AV1 Encode Finished**\n📄 `{file_name}`\n🎞 {height}p | CRF {crf}",
            progress=upload_progress
        )

        if os.path.exists(SOURCE): os.remove(SOURCE)
        if os.path.exists(file_name): os.remove(file_name)

        await safe_edit(chat_id, status.id, "🎉 **Process Finished Successfully!**", app)

if __name__ == "__main__":
    asyncio.run(main())
        
