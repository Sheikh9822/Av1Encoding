import asyncio
import os
import subprocess
import time
import signal
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
    """Captures a frame from the middle of the video."""
    ss_time = duration / 2
    cmd = [
        "ffmpeg", "-ss", str(ss_time), "-i", SOURCE,
        "-frames:v", "1", "-q:v", "2", SCREENSHOT, "-y"
    ]
    subprocess.run(cmd, stdout=subprocess.DEV_NULL, stderr=subprocess.DEV_NULL)

def select_params(height):
    if height >= 2000: return 32, 10
    elif height >= 1000: return 28, 8
    elif height >= 700: return 24, 6
    else: return 22, 4

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
    
    def_crf, def_preset = select_params(height)
    final_crf = u_crf if u_crf else def_crf
    final_preset = u_preset if u_preset else def_preset
    
    scale_filter = ["-vf", f"scale=-2:{u_res}"] if u_res else []

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:

        @app.on_message(filters.command("cancel"))
        async def cancel_handler(client, message):
            global CANCELLED, PROCESS
            CANCELLED = True
            if PROCESS: PROCESS.send_signal(signal.SIGINT)
            await message.reply("❌ **Encoding Terminated.**")

        status = await app.send_message(chat_id, f"🎬 **Encoding Started**\nOutput: `{file_name}`\nCRF: {final_crf} | Preset: {final_preset}")

        # Capture screenshot from source
        generate_screenshot(duration)

        start_time = time.time()
        cmd = [
            "ffmpeg", "-i", SOURCE, "-map", "0",
            *scale_filter,
            "-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le",
            "-crf", str(final_crf), "-preset", str(final_preset),
            "-svtav1-params", "tune=0:aq-mode=2",
            "-c:a", "libopus", "-b:a", "128k", "-c:s", "copy",
            "-map_metadata", "0", "-progress", "pipe:1", "-nostats", "-y", file_name
        ]

        PROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        last_update = 0

        for line in PROCESS.stdout:
            if CANCELLED: break
            if "out_time_ms" in line:
                out_time = int(line.split("=")[1]) / 1_000_000
                percent = (out_time / duration) * 100
                elapsed = time.time() - start_time
                speed = out_time / elapsed if elapsed > 0 else 0
                
                if time.time() - last_update > 15:
                    size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0
                    msg = f"🎬 **Encoding...**\n`{percent:.2f}%` | Speed: {speed:.2f}x\n📦 Size: {size:.2f} MB"
                    await safe_edit(chat_id, status.id, msg, app)
                    last_update = time.time()

        PROCESS.wait()
        if CANCELLED: return

        await safe_edit(chat_id, status.id, "✅ **Encode Finished!**\n📤 *Uploading...*", app)

        # Send Screenshot first
        if os.path.exists(SCREENSHOT):
            await app.send_photo(chat_id, SCREENSHOT, caption=f"🖼 **Preview:** `{file_name}`")
            os.remove(SCREENSHOT)

        async def upload_progress(current, total):
            nonlocal last_update
            if time.time() - last_update > 10:
                await safe_edit(chat_id, status.id, f"📤 **Uploading:** {current*100/total:.2f}%", app)
                last_update = time.time()

        await app.send_document(
            chat_id=chat_id, 
            document=file_name, 
            caption=f"✅ **AV1 Done**\n📄 `{file_name}`\n🛠 CRF: {final_crf} | P: {final_preset}",
            progress=upload_progress
        )
        
        for f in [SOURCE, file_name]:
            if os.path.exists(f): os.remove(f)
        await safe_edit(chat_id, status.id, "🎉 **Task Successfully Completed!**", app)

if __name__ == "__main__":
    asyncio.run(main())
