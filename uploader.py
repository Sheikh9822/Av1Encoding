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
            r = requests.get(VMAF_MODEL_URL)
            with open(VMAF_MODEL_PATH, "wb") as f:
                f.write(r.content)
        except: pass

def get_video_info():
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", SOURCE]
    res = json.loads(subprocess.check_output(cmd).decode())
    video_stream = next(s for s in res['streams'] if s['codec_type'] == 'video')
    duration = float(res['format'].get('duration', 0))
    height = int(video_stream.get('height', 0))
    
    # Detect if file is likely animation for tuning
    title = res.get('format', {}).get('tags', {}).get('title', '').lower()
    is_animation = any(tag in title for tag in ['staff', 'episode', 'opus', 'season'])
    
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
    download_vmaf_model()
    if not os.path.exists(VMAF_MODEL_PATH): return "Model N/A"
    # Basic VMAF calculation via ffmpeg
    cmd = [
        "ffmpeg", "-i", output_file, "-i", SOURCE, 
        "-filter_complex", f"libvmaf=model_path={VMAF_MODEL_PATH}", 
        "-f", "null", "-"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        for line in res.stderr.split('\n'):
            if "VMAF score:" in line: return line.split("VMAF score:")[1].strip()
    except: return "N/A"

def select_params(height):
    if height >= 2000: return 32, 10
    elif height >= 1000: return 42, 6
    elif height >= 700: return 24, 6
    return 22, 4

# ---------- UPLOAD CALLBACK ----------

last_up_update = 0

async def upload_progress(current, total, app, chat_id, status_msg, file_name):
    global last_up_update
    now = time.time()
    if now - last_up_update < 10: return
        
    percent = (current / total) * 100
    bar = generate_progress_bar(percent)
    cur_mb, tot_mb = current / (1024 * 1024), total / (1024 * 1024)
    
    scifi_up_ui = (
        f"<code>┌─── 🛰️ [ SYSTEM.UPLINK.ACTIVE ] ───┐\n"
        f"│ 📂 FILE: {file_name}\n"
        f"│ 📊 PROG: {bar} {percent:.1f}%\n"
        f"│ 📦 SIZE: {cur_mb:.2f} / {tot_mb:.2f} MB\n"
        f"│ 📡 STATUS: Transmitting to Orbit... \n"
        f"└────────────────────────────────────┘</code>"
    )
    try:
        await app.edit_message_text(chat_id, status_msg.id, scifi_up_ui, parse_mode=enums.ParseMode.HTML)
        last_up_update = now
    except: pass

# ---------- MAIN PROCESS ----------

async def main():
    global CANCELLED, PROCESS

    api_id, api_hash = int(os.getenv("API_ID")), os.getenv("API_HASH")
    bot_token, chat_id = os.getenv("BOT_TOKEN"), int(os.getenv("CHAT_ID"))
    file_name = os.getenv("FILE_NAME")
    
    u_res = os.getenv("USER_RES")
    u_crf_raw, u_preset_raw = os.getenv("USER_CRF"), os.getenv("USER_PRESET")
    u_audio, u_bitrate = os.getenv("AUDIO_MODE", "opus"), os.getenv("AUDIO_BITRATE", "128k")
    run_vmaf = os.getenv("RUN_VMAF", "true").lower() == "true"

    try:
        duration, height, is_hdr, total_frames, is_anime = get_video_info()
    except Exception as e:
        print(f"Metadata error: {e}")
        return

    def_crf, def_preset = select_params(height)
    final_crf = u_crf_raw if (u_crf_raw and u_crf_raw.strip()) else def_crf
    final_preset = u_preset_raw if (u_preset_raw and u_preset_raw.strip()) else def_preset
    
    res_label = u_res if u_res else f"{height}p"
    hdr_label = "HDR10" if is_hdr else "SDR"
    tune_val = "1" if is_anime else "0"
    
    scale_filter = ["-vf", f"scale=-2:{u_res}"] if u_res else []
    audio_cmd = ["-c:a", "libopus", "-b:a", u_bitrate] if u_audio == "opus" else ["-c:a", "copy"]
    hdr_params = ":enable-hdr=1" if is_hdr else ""

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        status = await app.send_message(chat_id, "📡 <b>[ SYSTEM BOOT ] Initializing Satellite Link...</b>", parse_mode=enums.ParseMode.HTML)
        grid_task = asyncio.create_task(async_generate_grid(duration))

        # ENHANCED: Includes attachments mapping and tune logic
        cmd = [
            "ffmpeg", "-i", SOURCE, 
            "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?",
            *scale_filter,
            "-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le",
            "-crf", str(final_crf), "-preset", str(final_preset),
            "-svtav1-params", f"tune={tune_val}:aq-mode=2:enable-overlays=1:scd=1:enable-tpl-la=1{hdr_params}",
            *audio_cmd, "-c:s", "copy", "-c:t", "copy",
            "-max_muxing_queue_size", "1024",
            "-progress", "pipe:1", "-nostats", "-y", file_name
        ]

        start_time, last_update = time.time(), 0
        with open(LOG_FILE, "w") as f_log:
            PROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            for line in PROCESS.stdout:
                f_log.write(line)
                if "out_time_ms" in line:
                    try:
                        curr_sec = int(line.split("=")[1]) / 1_000_000
                        percent = (curr_sec / duration) * 100
                        elapsed = time.time() - start_time
                        speed = curr_sec / elapsed if elapsed > 0 else 0
                        fps = (percent / 100 * total_frames) / elapsed if elapsed > 0 else 0
                        eta = (elapsed / percent) * (100 - percent) if percent > 0 else 0
                        
                        if time.time() - last_update > 25:
                            bar = generate_progress_bar(percent)
                            size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0
                            scifi_ui = (
                                f"<code>┌─── 🛰️ [ SYSTEM.ENCODE.PROCESS ] ───┐\n"
                                f"│ 📂 FILE: {file_name}\n"
                                f"│ ⚡ SPEED: {speed:.1f}x ({int(fps)} FPS)\n"
                                f"│ ⏳ TIME: {format_time(elapsed)} / ETA: {format_time(eta)}\n"
                                f"│ 📊 PROG: {bar} {percent:.1f}% \n"
                                f"│ 🛠️ SETTINGS: CRF {final_crf} | P {final_preset}\n"
                                f"│ 🎞️ VIDEO: {res_label} | {hdr_label}\n"
                                f"│ 📦 SIZE: {size:.2f} MB\n"
                                f"└────────────────────────────────────┘</code>"
                            )
                            await app.edit_message_text(chat_id, status.id, scifi_ui, parse_mode=enums.ParseMode.HTML)
                            last_update = time.time()
                    except: continue

        PROCESS.wait()
        total_mission_time = time.time() - start_time
        await grid_task

        if PROCESS.returncode != 0:
            await app.send_document(chat_id, LOG_FILE, caption="❌ <b>CRITICAL ERROR: Core Failure</b>")
            return

        # VMAF Calculation
        await app.edit_message_text(chat_id, status.id, "🛠️ <b>[ SYSTEM.QUALITY ] Analyzing VMAF Score...</b>")
        vmaf_val = get_vmaf_score(file_name) if run_vmaf else "N/A"
        final_size = os.path.getsize(file_name)/(1024*1024)

        if os.path.exists(SCREENSHOT):
            await app.send_photo(chat_id, SCREENSHOT, caption=f"🖼 <b>PROXIMITY GRID:</b> <code>{file_name}</code>")
            os.remove(SCREENSHOT)

        report = (
            f"✅ <b>MISSION ACCOMPLISHED</b>\n\n"
            f"📄 <b>FILE:</b> <code>{file_name}</code>\n"
            f"⏱ <b>ENCODE TIME:</b> <code>{format_time(total_mission_time)}</code>\n"
            f"📦 <b>FINAL SIZE:</b> <code>{final_size:.2f} MB</code>\n"
            f"📊 <b>VMAF SCORE:</b> <code>{vmaf_val}</code>\n\n"
            f"🛠 <b>ENCODE SPECS:</b>\n"
            f"└ <b>P:</b> {final_preset} | <b>CRF:</b> {final_crf} | <b>Tune:</b> {tune_val}\n"
            f"└ <b>Video:</b> {res_label} | {hdr_label}\n"
            f"└ <b>Audio:</b> Opus @ {u_bitrate}"
        )

        await app.send_document(
            chat_id=chat_id, document=file_name, caption=report,
            parse_mode=enums.ParseMode.HTML, progress=upload_progress,
            progress_args=(app, chat_id, status, file_name)
        )
        
        await status.delete()
        for f in [SOURCE, file_name, LOG_FILE, VMAF_MODEL_PATH]:
            if os.path.exists(f): os.remove(f)

if __name__ == "__main__":
    asyncio.run(main())
