import os
import time
import asyncio
import psutil
import subprocess
from pyrogram import Client, filters

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

app = Client("encoder", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

active_process = None

# ---------------------------
# Helper Functions
# ---------------------------

def format_time(seconds):
    seconds = int(seconds)
    return f"{seconds//60:02d}:{seconds%60:02d}"

def progress_bar(percent, width=25, frame=0):
    frames = ["█", "▓"]
    fill = frames[frame % len(frames)]
    filled = int(width * percent / 100)
    return fill * filled + "░" * (width - filled)

def get_duration(file):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", file],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    return float(result.stdout)

def get_resolution(file):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=height",
         "-of", "csv=p=0", file],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    return int(result.stdout)

def dynamic_crf(height):
    # CRF30 for all main resolutions
    if height >= 480:
        return 30
    else:
        return 28  # for very small videos

# ---------------------------
# Cancel Command
# ---------------------------

@app.on_message(filters.command("cancel"))
async def cancel_handler(_, message):
    global active_process
    if active_process:
        active_process.kill()
        await message.reply("❌ Encoding cancelled.")
    else:
        await message.reply("Nothing running.")

# ---------------------------
# Main Handler
# ---------------------------

@app.on_message(filters.video | filters.document)
async def encode_handler(client, message):
    global active_process

    chat_id = message.chat.id
    file_path = await message.download()
    await message.reply("📥 Download complete.\nStarting encode...")

    duration = get_duration(file_path)
    resolution = get_resolution(file_path)
    crf = dynamic_crf(resolution)

    output = f"encoded_{os.path.basename(file_path)}"

    for attempt in range(2):  # Retry once if fails
        try:
            cmd = [
                "ffmpeg",
                "-i", file_path,
                "-map", "0",
                "-c:v", "libsvtav1",
                "-pix_fmt", "yuv420p10le",
                "-crf", str(crf),
                "-preset", "8",
                "-g", "240",
                "-svtav1-params", "tune=0:aq-mode=2",
                "-c:a", "libopus", "-b:a", "128k",
                "-c:s", "copy",
                "-c:t", "copy",
                "-map_metadata", "0",
                "-progress", "pipe:1",
                "-nostats",
                output
            ]

            active_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )

            status = await message.reply("🎬 Encoding started...")
            start_time = time.time()
            last_update = 0

            out_time = 0
            size_bytes = 0

            while True:
                line = active_process.stdout.readline()
                if not line:
                    break

                if "out_time_ms=" in line:
                    out_time = int(line.split("=")[1]) / 1_000_000

                if os.path.exists(output):
                    size_bytes = os.path.getsize(output)

                elapsed = time.time() - start_time
                percent = (out_time / duration) * 100 if duration else 0
                fps = out_time / elapsed if elapsed > 0 else 0
                speed = fps
                eta = (duration - out_time) / speed if speed > 0 else 0

                size_mb = size_bytes / (1024 * 1024)
                bitrate = (size_bytes * 8 / out_time / 1000) if out_time > 0 else 0

                predicted_size_mb = 0
                if out_time > 0:
                    growth_rate = size_mb / out_time
                    predicted_size_mb = growth_rate * duration

                cpu = psutil.cpu_percent()

                if time.time() - last_update > 5:
                    bar = progress_bar(percent, 25, int(elapsed))
                    await client.edit_message_text(
                        chat_id,
                        status.id,
                        f"🎬 **Encoding:** `{os.path.basename(output)}`\n\n"
                        f"[{bar}] {percent:.2f}%\n\n"
                        f"⏳ {format_time(out_time)} / {format_time(duration)}\n"
                        f"📦 {size_mb:.2f} MB / ~{predicted_size_mb:.2f} MB\n"
                        f"📊 Bitrate: {bitrate:.0f} kbps\n"
                        f"⚡ FPS: {fps:.2f}\n"
                        f"🚀 Speed: {speed:.2f}x\n"
                        f"🖥 CPU: {cpu:.1f}%\n"
                        f"⏱ Elapsed: {format_time(elapsed)}\n"
                        f"⏳ ETA: {format_time(eta)}"
                    )
                    last_update = time.time()

            active_process.wait()
            break

        except Exception:
            if attempt == 1:
                await message.reply("❌ Encoding failed.")
                return

    await message.reply("📤 Uploading...")
    await client.send_document(chat_id, output)

    if os.path.exists(file_path):
        os.remove(file_path)
    if os.path.exists(output):
        os.remove(output)

    await message.reply("✅ Done. Source deleted.")

app.run()
