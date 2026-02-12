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

def format_time(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds//60:02d}:{seconds%60:02d}"

def progress_bar(percent, width=25, frame=0):
    percent = min(100, max(0, percent))
    frames = ["█", "▓"]
    fill = frames[frame % len(frames)]
    filled = int(width * percent / 100)
    return fill * filled + "░" * (width - filled)

def get_duration(file):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", file],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        return float(result.stdout.strip())
    except:
        return 1.0 # Fallback to prevent division by zero

def get_resolution(file):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=height",
             "-of", "csv=p=0", file],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        return int(result.stdout.strip())
    except:
        return 720

def dynamic_crf(height):
    return 28 

@app.on_message(filters.command("cancel"))
async def cancel_handler(_, message):
    global active_process
    if active_process:
        active_process.kill()
        await message.reply("❌ Encoding cancelled.")
    else:
        await message.reply("Nothing running.")

@app.on_message(filters.video | filters.document)
async def encode_handler(client, message):
    global active_process

    chat_id = message.chat.id
    status = await message.reply("📥 Downloading...")
    file_path = await message.download()
    
    await status.edit("🎬 Starting encode...")

    duration = get_duration(file_path)
    resolution = get_resolution(file_path)
    crf = dynamic_crf(resolution)

    output = f"encoded_{os.path.basename(file_path)}"
    if not output.endswith(".mkv"):
        output += ".mkv"

    # SVT-AV1 Presets: 2 or 3 = High Compression, 10 = Fast/Low Compression
    cmd = [
        "ffmpeg", "-i", file_path,
        "-map", "0",
        "-c:v", "libsvtav1",
        "-pix_fmt", "yuv420p10le",
        "-preset", "6", # Balanced; use 3 for "Ultra" compression
        "-crf", str(crf),
        "-svtav1-params", "tune=0:aq-mode=2",
        "-c:a", "libopus", "-b:a", "64k", # Better compression than 'copy'
        "-c:s", "copy",
        "-progress", "pipe:1",
        "-nostats", "-y",
        output
    ]

    try:
        active_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True
        )

        start_time = time.time()
        last_update = 0

        while active_process.poll() is None:
            line = active_process.stdout.readline()
            if not line: break

            if "out_time_ms=" in line:
                out_time = int(line.split("=")[1]) / 1_000_000
                
                if time.time() - last_update > 8: # Throttle updates for Telegram
                    elapsed = time.time() - start_time
                    percent = (out_time / duration) * 100
                    speed = out_time / elapsed if elapsed > 0 else 0.01
                    eta = (duration - out_time) / speed if speed > 0 else 0
                    
                    bar = progress_bar(percent, 20, int(elapsed))
                    try:
                        await status.edit(
                            f"🎬 **Encoding:** `{os.path.basename(output)}`\n"
                            f"[{bar}] {percent:.1f}%\n"
                            f"⏳ {format_time(out_time)} / {format_time(duration)}\n"
                            f"⏱ ETA: {format_time(eta)}"
                        )
                    except: pass
                    last_update = time.time()

        active_process.wait()
        
        await status.edit("📤 Uploading...")
        await client.send_document(chat_id, output)
        await status.delete()

    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output): os.remove(output)

app.run()
