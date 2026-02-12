import asyncio
import os
import subprocess
import time
import signal
import json
from datetime import timedelta
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

SOURCE = "source.mkv"
SCREENSHOT = "grid_preview.jpg"
LOG_FILE = "encode_log.txt"
CANCELLED = False
PROCESS = None

# ---------- TOOLS ----------

def get_video_info():
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", SOURCE]
    res = json.loads(subprocess.check_output(cmd).decode())
    v = next(s for s in res['streams'] if s['codec_type'] == 'video')
    duration = float(res['format'].get('duration', 0))
    height = int(v.get('height', 0))
    # Extracting FPS
    fps_eval = eval(v.get('r_frame_rate', '24/1'))
    total_frames = int(v.get('nb_frames', duration * fps_eval))
    is_hdr = 'bt2020' in v.get('color_primaries', 'bt709')
    return duration, height, is_hdr, total_frames

def generate_progress_bar(percentage):
    """Sleek Sci-Fi Bar using Power-Level symbols"""
    total_blocks = 15
    filled = int(percentage / (100 / total_blocks))
    return "▰" * filled + "▱" * (total_blocks - filled)

def format_time(seconds):
    return str(timedelta(seconds=int(seconds)))

def generate_grid(duration):
    """Creates a 3x3 thumbnail grid"""
    interval = duration / 10
    select_filter = "select='" + "+".join([f"between(t,{i*interval}-0.1,{i*interval}+0.1)" for i in range(1, 10)]) + "',setpts=N/FRAME_RATE/TB"
    cmd = ["ffmpeg", "-i", SOURCE, "-vf", f"{select_filter},scale=480:-1,tile=3x3", "-frames:v", "1", "-q:v", "3", SCREENSHOT, "-y"]
    subprocess.run(cmd, capture_output=True)

def get_ssim(output_file):
    cmd = ["ffmpeg", "-i", output_file, "-i", SOURCE, "-filter_complex", "ssim", "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        for line in res.stderr.split('\n'):
            if "All:" in line: return line.split("All:")[1].split(" ")[0]
    except: return "N/A"

# ---------- MAIN ----------

async def main():
    global CANCELLED, PROCESS

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    bot_token = os.getenv("BOT_TOKEN")
    chat_id = int(os.getenv("CHAT_ID"))
    file_name = os.getenv("FILE_NAME")
    
    u_res = os.getenv("USER_RES")
    u_crf = os.getenv("USER_CRF", "28")
    u_preset = os.getenv("USER_PRESET", "8")
    u_audio = os.getenv("AUDIO_MODE", "opus")
    u_bitrate = os.getenv("AUDIO_BITRATE", "128k")
    run_ssim = os.getenv("RUN_SSIM", "true").lower() == "true"

    duration, height, is_hdr, total_frames = get_video_info()
    hdr_label = "HDR10" if is_hdr else "SDR"
    res_label = u_res if u_res else f"{height}p"

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        
        @app.on_message(filters.command("cancel") & filters.chat(chat_id))
        async def cancel_handler(client, message):
            global CANCELLED, PROCESS
            CANCELLED = True
            if PROCESS: PROCESS.send_signal(signal.SIGINT)
            await message.reply("🛰️ **CORE SHUTDOWN: Encoding Terminated.**")

        status = await app.send_message(chat_id, "🛰️ **Initializing Satellite Link...**")
        generate_grid(duration)

        scale = f"scale=-2:{u_res}," if u_res else ""
        audio_cmd = ["-c:a", "libopus", "-b:a", u_bitrate] if u_audio == "opus" else ["-c:a", "copy"]
        
        cmd = [
            "ffmpeg", "-i", SOURCE, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
            "-vf", f"{scale}format=yuv420p10le",
            "-c:v", "libsvtav1", "-crf", str(u_crf), "-preset", str(u_preset),
            "-svtav1-params", f"tune=0:aq-mode=2:enable-hdr={1 if is_hdr else 0}",
            *audio_cmd, "-c:s", "copy", "-progress", "pipe:1", "-nostats", "-y", file_name
        ]

        start_time = time.time()
        last_update = 0

        with open(LOG_FILE, "w") as f_log:
            PROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            for line in PROCESS.stdout:
                f_log.write(line)
                if CANCELLED: break
                if "out_time_ms" in line:
                    try:
                        cur_sec = int(line.split("=")[1]) / 1_000_000
                        percent = (cur_sec / duration) * 100
                        elapsed = time.time() - start_time
                        fps = (percent / 100 * total_frames) / elapsed if elapsed > 0 else 0
                        speed = cur_sec / elapsed if elapsed > 0 else 0
                        eta = (elapsed / percent) * (100 - percent) if percent > 0 else 0

                        if time.time() - last_update > 15:
                            bar = generate_progress_bar(percent)
                            size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0
                            
                            msg = (
                                f"┌─── 🛰️ **[ SYSTEM.ENCODE.PROCESS ]** ───┐\n"
                                f"│\n"
                                f"│ 📂 **FILE:** `{file_name}`\n"
                                f"│ ⚡ **SPEED:** `{speed:.2x}` (`{int(fps)} FPS`)\n"
                                f"│ ⏳ **TIME:** `{format_time(elapsed)}` / **ETA:** `{format_time(eta)}`\n"
                                f"│\n"
                                f"│ 📊 **PROG:** `[{bar}] {percent:.1f}%` \n"
                                f"│\n"
                                f"│ 🎞️ **VIDEO:** `{res_label} | 10-bit | {hdr_label}`\n"
                                f"│ 🔊 **AUDIO:** `Opus @ {u_bitrate}`\n"
                                f"│ 📦 **SIZE:** `{size:.2f} MB`\n"
                                f"│\n"
                                f"└────────────────────────────────────┘"
                            )
                            await app.edit_message_text(chat_id, status.id, msg)
                            last_update = time.time()
                    except: continue

        PROCESS.wait()
        if CANCELLED: return

        if PROCESS.returncode != 0:
            await app.send_document(chat_id, LOG_FILE, caption="❌ **System Failure.** Check Log.")
            return

        score = get_ssim(file_name) if run_ssim else "Skipped"
        
        if os.path.exists(SCREENSHOT):
            await app.send_photo(chat_id, SCREENSHOT, caption=f"🖼 **Preview:** `{file_name}`")
            os.remove(SCREENSHOT)

        await app.send_document(
            chat_id=chat_id, document=file_name,
            caption=f"✅ **Transmission Complete**\n📄 `{file_name}`\n📊 SSIM: `{score}`",
            progress=lambda c, t: None # Simplified
        )
        
        for f in [SOURCE, file_name, LOG_FILE]:
            if os.path.exists(f): os.remove(f)

if __name__ == "__main__":
    asyncio.run(main())
