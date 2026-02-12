import asyncio
import os
import subprocess
import time
import signal
import json
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

SOURCE = "source.mkv"
SCREENSHOT = "grid_preview.jpg"
LOG_FILE = "encode_log.txt"
CANCELLED = False
PROCESS = None

# ---------- METADATA & TOOLS ----------

def get_video_info():
    """Fetches duration, height, and HDR metadata using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json", 
        "-show_streams", "-show_format", SOURCE
    ]
    res = json.loads(subprocess.check_output(cmd).decode())
    video_stream = next(s for s in res['streams'] if s['codec_type'] == 'video')
    
    duration = float(res['format'].get('duration', 0))
    height = int(video_stream.get('height', 0))
    
    color_primaries = video_stream.get('color_primaries', 'bt709')
    is_hdr = 'bt2020' in color_primaries
    
    return duration, height, is_hdr

def generate_grid(duration):
    """Creates a 3x3 thumbnail grid across the video duration."""
    interval = duration / 10
    select_filter = "select='" + "+".join([f"between(t,{i*interval}-0.1,{i*interval}+0.1)" for i in range(1, 10)]) + "',setpts=N/FRAME_RATE/TB"
    cmd = [
        "ffmpeg", "-i", SOURCE, "-vf", f"{select_filter},scale=480:-1,tile=3x3",
        "-frames:v", "1", "-q:v", "3", SCREENSHOT, "-y"
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def get_ssim(output_file):
    """Calculates SSIM as a quality proxy."""
    cmd = [
        "ffmpeg", "-i", output_file, "-i", SOURCE, 
        "-filter_complex", "ssim", "-f", "null", "-"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        for line in res.stderr.split('\n'):
            if "All:" in line: return line.split("All:")[1].split(" ")[0]
    except: return "N/A"

def select_params(height):
    if height >= 2000: return 32, 10
    elif height >= 1000: return 28, 8
    elif height >= 700: return 24, 6
    else: return 22, 4

async def safe_edit(chat_id, message_id, text, app):
    try:
        await app.edit_message_text(chat_id, message_id, text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception: pass

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
    u_audio = os.getenv("AUDIO_MODE", "opus")
    u_bitrate = os.getenv("AUDIO_BITRATE", "128k")
    run_vmaf = os.getenv("RUN_VMAF", "false").lower() == "true"

    try:
        duration, height, is_hdr = get_video_info()
    except Exception as e:
        print(f"Error reading file: {e}")
        return
    
    def_crf, def_preset = select_params(height)
    final_crf = u_crf if u_crf else def_crf
    final_preset = u_preset if u_preset else def_preset
    
    scale_filter = ["-vf", f"scale=-2:{u_res}"] if u_res else []
    audio_cmd = ["-c:a", "libopus", "-b:a", u_bitrate] if u_audio == "opus" else ["-c:a", "copy"]
    hdr_params = ":enable-hdr=1" if is_hdr else ""

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:

        @app.on_message(filters.command("cancel"))
        async def cancel_handler(client, message):
            global CANCELLED, PROCESS
            CANCELLED = True
            if PROCESS: PROCESS.send_signal(signal.SIGINT)
            await message.reply("❌ **Encoding Terminated.**")

        status = await app.send_message(chat_id, f"🎬 **Encoding Started**\nOutput: `{file_name}`\nCRF: {final_crf} | P: {final_preset} | HDR: {is_hdr}")

        generate_grid(duration)

        start_time = time.time()
        cmd = [
            "ffmpeg", "-i", SOURCE, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
            *scale_filter,
            "-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le",
            "-crf", str(final_crf), "-preset", str(final_preset),
            "-svtav1-params", f"tune=0:aq-mode=2{hdr_params}",
            *audio_cmd, "-c:s", "copy",
            "-map_metadata", "0", "-progress", "pipe:1", "-nostats", "-y", file_name
        ]

        last_update = 0
        with open(LOG_FILE, "w") as f_log:
            PROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            for line in PROCESS.stdout:
                f_log.write(line)
                if CANCELLED: break
                if "out_time_ms" in line:
                    try:
                        out_time = int(line.split("=")[1]) / 1_000_000
                        percent = (out_time / duration) * 100
                        elapsed = time.time() - start_time
                        speed = out_time / elapsed if elapsed > 0 else 0
                        if time.time() - last_update > 20:
                            size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0
                            msg = f"🎬 **Encoding...**\n`{percent:.2f}%` | Speed: {speed:.2f}x\n📦 Size: {size:.2f} MB"
                            await safe_edit(chat_id, status.id, msg, app)
                            last_update = time.time()
                    except: continue

        PROCESS.wait()
        if CANCELLED: return

        if PROCESS.returncode != 0:
            await app.send_document(chat_id, LOG_FILE, caption="❌ **Encode Failed.** Check logs.")
            return

        ssim_score = get_ssim(file_name) if run_vmaf else "Skipped"
        await safe_edit(chat_id, status.id, f"✅ **Encode Finished!**\n📊 SSIM: `{ssim_score}`\n📤 *Uploading...*", app)

        if os.path.exists(SCREENSHOT):
            try:
                await app.send_photo(chat_id, SCREENSHOT, caption=f"🖼 **Preview Grid:** `{file_name}`")
                os.remove(SCREENSHOT)
            except: pass

        async def upload_progress(current, total):
            nonlocal last_update
            if time.time() - last_update > 10:
                await safe_edit(chat_id, status.id, f"📤 **Uploading:** {current*100/total:.2f}%", app)
                last_update = time.time()

        await app.send_document(
            chat_id=chat_id, 
            document=file_name, 
            caption=f"✅ **AV1 Done**\n📄 `{file_name}`\n🛠 CRF: {final_crf} | SSIM: {ssim_score}",
            progress=upload_progress
        )
        
        for f in [SOURCE, file_name, LOG_FILE]:
            if os.path.exists(f): os.remove(f)
        await safe_edit(chat_id, status.id, "🎉 **Task Successfully Completed!**", app)

if __name__ == "__main__":
    asyncio.run(main())
    
