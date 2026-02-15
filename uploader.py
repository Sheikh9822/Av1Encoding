import asyncio
import os
import subprocess
import time
import json
import requests
from datetime import timedelta
from pyrogram import Client, enums
from pyrogram.errors import FloodWait

SOURCE = "source.mkv"
SCREENSHOT = "grid_preview.jpg"
LOG_FILE = "encode_log.txt"
VMAF_MODEL_URL = "https://github.com/Netflix/vmaf/raw/master/model/vmaf_v0.6.1.json"
VMAF_MODEL_PATH = "vmaf_v0.6.1.json"
CANCELLED = False
PROCESS = None

# ---------- TOOLS & ANALYTICS ----------

def download_vmaf_model():
    if not os.path.exists(VMAF_MODEL_PATH):
        try:
            r = requests.get(VMAF_MODEL_URL, timeout=10)
            with open(VMAF_MODEL_PATH, "wb") as f:
                f.write(r.content)
        except: pass

def check_vmaf_support():
    try:
        res = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True)
        return "libvmaf" in res.stdout
    except: return False

def get_video_info():
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", SOURCE]
    res = json.loads(subprocess.check_output(cmd).decode())
    video_stream = next(s for s in res['streams'] if s['codec_type'] == 'video')
    duration = float(res['format'].get('duration', 0))
    height = int(video_stream.get('height', 0))
    is_animation = any(tag in res.get('format', {}).get('tags', {}).get('title', '').lower() for tag in ['staff', 'episode', 'opus'])
    fps_raw = video_stream.get('r_frame_rate', '24/1')
    fps_val = eval(fps_raw) if '/' in fps_raw else float(fps_raw)
    total_frames = int(video_stream.get('nb_frames', duration * fps_val))
    is_hdr = 'bt2020' in video_stream.get('color_primaries', 'bt709')
    return duration, height, is_hdr, total_frames, is_animation

def generate_progress_bar(percentage):
    total_segments = 15
    completed = int((max(0, min(100, percentage)) / 100) * total_segments)
    return "[" + "▰" * completed + "▱" * (total_segments - completed) + "]"

def format_time(seconds):
    return str(timedelta(seconds=int(seconds))).zfill(8)

async def async_generate_grid(duration):
    loop = asyncio.get_event_loop()
    def sync_grid():
        interval = duration / 10
        select_filter = "select='" + "+".join([f"between(t,{i*interval}-0.1,{i*interval}+0.1)" for i in range(1, 10)]) + "',setpts=N/FRAME_RATE/TB"
        cmd = ["ffmpeg", "-i", SOURCE, "-vf", f"{select_filter},scale=480:-1,tile=3x3", "-frames:v", "1", "-q:v", "3", SCREENSHOT, "-y"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await loop.run_in_executor(None, sync_grid)

def get_vmaf_score(output_file):
    if not check_vmaf_support(): return "FFmpeg VMAF missing"
    download_vmaf_model()
    cmd = ["ffmpeg", "-i", output_file, "-i", SOURCE, "-filter_complex", f"libvmaf=model_path={VMAF_MODEL_PATH}", "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        for line in res.stderr.split('\n'):
            if "VMAF score:" in line: return line.split("VMAF score:")[1].strip()
    except: return "N/A"

# ---------- MAIN PROCESS ----------

async def main():
    global CANCELLED, PROCESS
    api_id, api_hash = int(os.getenv("API_ID")), os.getenv("API_HASH")
    bot_token, chat_id = os.getenv("BOT_TOKEN"), int(os.getenv("CHAT_ID"))
    file_name = os.getenv("FILE_NAME")
    u_res, u_crf, u_preset = os.getenv("USER_RES"), os.getenv("USER_CRF"), os.getenv("USER_PRESET")
    u_audio, u_bitrate = os.getenv("AUDIO_MODE", "opus"), os.getenv("AUDIO_BITRATE", "128k")
    run_vmaf = os.getenv("RUN_VMAF", "true").lower() == "true"

    duration, height, is_hdr, total_frames, is_anime = get_video_info()
    scale_filter = ["-vf", f"scale=-2:{u_res}"] if u_res else []
    audio_cmd = ["-c:a", "libopus", "-b:a", u_bitrate] if u_audio == "opus" else ["-c:a", "copy"]
    hdr_params = ":enable-hdr=1" if is_hdr else ""
    tune_val = "1" if is_anime else "0"

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        status = await app.send_message(chat_id, "📡 <b>[ SYSTEM BOOT ]</b> Initializing...", parse_mode=enums.ParseMode.HTML)
        grid_task = asyncio.create_task(async_generate_grid(duration))

        cmd = [
            "ffmpeg", "-i", SOURCE, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?",
            *scale_filter, "-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le",
            "-crf", str(u_crf), "-preset", str(u_preset),
            "-svtav1-params", f"tune={tune_val}:aq-mode=2:enable-overlays=1:scd=1:enable-tpl-la=1{hdr_params}",
            *audio_cmd, "-c:s", "copy", "-c:t", "copy", "-max_muxing_queue_size", "1024",
            "-progress", "pipe:1", "-nostats", "-y", file_name
        ]

        start_time, last_update = time.time(), 0
        PROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        for line in PROCESS.stdout:
            if "out_time_ms" in line:
                curr_sec = int(line.split("=")[1]) / 1_000_000
                percent = (curr_sec / duration) * 100
                if time.time() - last_update > 25:
                    bar = generate_progress_bar(percent)
                    ui = f"<code>┌─── 🛰️ [ SYSTEM.ENCODE ] ───┐\n│ 📊 PROG: {bar} {percent:.1f}%\n└──────────────────────────┘</code>"
                    await app.edit_message_text(chat_id, status.id, ui, parse_mode=enums.ParseMode.HTML)
                    last_update = time.time()

        PROCESS.wait()
        await grid_task
        vmaf_val = get_vmaf_score(file_name) if run_vmaf else "N/A"
        
        if os.path.exists(SCREENSHOT):
            await app.send_photo(chat_id, SCREENSHOT, caption=f"🖼 <code>{file_name}</code>")
            os.remove(SCREENSHOT)

        await app.send_document(
            chat_id=chat_id, document=file_name, 
            caption=f"✅ <b>MISSION ACCOMPLISHED</b>\n📦 <b>VMAF:</b> <code>{vmaf_val}</code>",
            parse_mode=enums.ParseMode.HTML, progress=upload_progress,
            progress_args=(app, chat_id, status, file_name)
        )
        await status.delete()
        for f in [SOURCE, file_name, LOG_FILE, VMAF_MODEL_PATH]:
            if os.path.exists(f): os.remove(f)

if __name__ == "__main__":
    asyncio.run(main())
