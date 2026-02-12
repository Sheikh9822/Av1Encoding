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
SCREENSHOT = "preview.jpg"
CANCELLED = False
PROCESS = None

# ---------- METADATA & TOOLS ----------

def get_duration():
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", SOURCE]
    return float(subprocess.check_output(cmd).decode().strip())

def get_height():
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", SOURCE]
    return int(subprocess.check_output(cmd).decode().strip())

def generate_screenshot(duration):
    ss_time = duration / 2
    cmd = ["ffmpeg", "-ss", str(ss_time), "-i", SOURCE, "-frames:v", "1", "-q:v", "2", SCREENSHOT, "-y"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def select_params(height):
    # Default fallbacks if user doesn't provide inputs
    if height >= 2000: return 32, 10
    elif height >= 1000: return 28, 8
    else: return 24, 6

# ---------- UTILS ----------

async def safe_edit(chat_id, message_id, text, app):
    try:
        await app.edit_message_text(chat_id, message_id, text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception:
        pass

# ---------- MAIN ----------

async def main():
    global CANCELLED, PROCESS

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    bot_token = os.getenv("BOT_TOKEN")
    chat_id = int(os.getenv("CHAT_ID"))
    file_name = os.getenv("FILE_NAME")
    
    u_res = os.getenv("USER_RES")
    u_crf = os.getenv("USER_CRF")
    u_preset = os.getenv("USER_PRESET")

    duration = get_duration()
    height = get_height()
    
    # Target size: 256KB (with 10% buffer for container overhead)
    # Bitrate (bps) = (Size in Bytes * 8) / Duration
    target_size_bytes = 256 * 1024
    calc_bitrate = int((target_size_bytes * 8 * 0.9) / duration)
    
    # Use user Preset/CRF or our defaults
    _, def_preset = select_params(height)
    final_preset = u_preset if u_preset else def_preset
    scale_filter = ["-vf", f"scale=-2:{u_res}"] if u_res else []

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:

        status = await app.send_message(chat_id, f"🎬 **Encoding Logic:** 2-Pass Bitrate Control\n🎯 **Target Size:** 256 KB\n📉 **Bitrate:** {calc_bitrate // 1000} kbps")

        generate_screenshot(duration)

        # --- PASS 1 ---
        await safe_edit(chat_id, status.id, "📊 **Running Analysis Pass...**", app)
        pass1 = [
            "ffmpeg", "-i", SOURCE, "-map", "0:v:0", *scale_filter,
            "-c:v", "libsvtav1", "-preset", str(final_preset),
            "-svtav1-params", f"rc=1:tbr={calc_bitrate}:pass=1",
            "-an", "-f", "null", "/dev/null", "-y"
        ]
        subprocess.run(pass1, check=True)

        # --- PASS 2 ---
        start_time = time.time()
        pass2 = [
            "ffmpeg", "-i", SOURCE, "-map", "0", *scale_filter,
            "-c:v", "libsvtav1", "-preset", str(final_preset),
            "-svtav1-params", f"rc=1:tbr={calc_bitrate}:pass=2",
            "-c:a", "libopus", "-b:a", "32k", # Low bitrate audio to fit size
            "-c:s", "copy", "-progress", "pipe:1", "-nostats", "-y", file_name
        ]

        PROCESS = subprocess.Popen(pass2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        
        last_update = 0
        for line in PROCESS.stdout:
            if "out_time_ms" in line:
                out_time = int(line.split("=")[1]) / 1_000_000
                percent = (out_time / duration) * 100
                if time.time() - last_update > 15:
                    await safe_edit(chat_id, status.id, f"🎬 **Encoding Pass 2:** `{percent:.2f}%`", app)
                    last_update = time.time()

        PROCESS.wait()

        # --- UPLOAD ---
        await safe_edit(chat_id, status.id, "✅ **Target Size Met!**\n📤 *Uploading...*", app)
        
        if os.path.exists(SCREENSHOT):
            await app.send_photo(chat_id, SCREENSHOT, caption=f"🖼 Preview: `{file_name}`")
            os.remove(SCREENSHOT)

        async def upload_progress(current, total):
            nonlocal last_update
            if time.time() - last_update > 10:
                await safe_edit(chat_id, status.id, f"📤 **Uploading:** {current*100/total:.2f}%", app)
                last_update = time.time()

        await app.send_document(
            chat_id=chat_id, 
            document=file_name, 
            caption=f"🏁 **AV1 Compressed**\n📄 `{file_name}`\n📦 Size: {os.path.getsize(file_name)/1024:.2f} KB",
            progress=upload_progress
        )
        
        for f in [SOURCE, file_name, "svtav1_2pass.log"]: # Cleanup
            if os.path.exists(f): os.remove(f)
        await safe_edit(chat_id, status.id, "🎉 **Done!**", app)

if __name__ == "__main__":
    asyncio.run(main())
        
