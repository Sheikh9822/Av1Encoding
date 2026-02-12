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
    except Exception:
        return 0.0

@app.on_message(filters.video | filters.document)
async def encode_handler(client, message):
    global active_process

    chat_id = message.chat.id
    status = await message.reply("📥 Downloading...")
    file_path = await message.download()
    
    duration = get_duration(file_path)

    # --- SKIP LOGIC FOR SHORT VIDEOS ---
    if duration < 10:
        await status.edit(f"⚠️ Video is only {duration:.1f}s. Skipping encode and uploading source...")
        await client.send_document(chat_id, file_path)
        if os.path.exists(file_path):
            os.remove(file_path)
        await status.delete()
        return
    # ----------------------------------

    await status.edit("🎬 Starting encode...")
    output = f"encoded_{os.path.basename(file_path)}"
    if not output.endswith(".mkv"):
        output += ".mkv"

    cmd = [
        "ffmpeg", "-i", file_path,
        "-c:v", "libsvtav1",
        "-preset", "6", 
        "-crf", "28",
        "-c:a", "libopus", "-b:a", "64k",
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
                
                # Update status every 8 seconds to avoid Telegram flood limits
                if time.time() - last_update > 8:
                    elapsed = time.time() - start_time
                    percent = (out_time / duration) * 100 if duration > 0 else 0
                    try:
                        await status.edit(f"🎬 **Encoding:** {percent:.1f}%")
                    except: pass
                    last_update = time.time()

        active_process.wait()
        
        await status.edit("📤 Uploading encoded file...")
        await client.send_document(chat_id, output)
        await status.delete()

    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output): os.remove(output)

app.run()
