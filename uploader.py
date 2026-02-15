import asyncio
import os
import subprocess
import time
import json
import uvloop
from datetime import timedelta
from pyrogram import Client, enums
from pyrogram.errors import FloodWait

# High-performance event loop for Linux runners
uvloop.install()

SOURCE = "source.mkv"
SCREENSHOT = "grid_preview.jpg"
LOG_FILE = "encode_log.txt"
CANCELLED = False
PROCESS = None

# ---------- TOOLS & ANALYTICS ----------

def get_video_info():
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", SOURCE]
    res = json.loads(subprocess.check_output(cmd).decode())
    video_stream = next(s for s in res['streams'] if s['codec_type'] == 'video')
    audio_stream = next((s for s in res['streams'] if s['codec_type'] == 'audio'), {})
    
    # ENHANCEMENT: Default to 0 channels to properly detect audio-less videos
    channels = int(audio_stream.get('channels', 0))
    
    duration = float(res['format'].get('duration', 0))
    height = int(video_stream.get('height', 0))
    fps_raw = video_stream.get('r_frame_rate', '24/1')
    fps_val = eval(fps_raw) if '/' in fps_raw else float(fps_raw)
    total_frames = int(video_stream.get('nb_frames', duration * fps_val))
    is_hdr = 'bt2020' in video_stream.get('color_primaries', 'bt709')
    return duration, height, is_hdr, total_frames, channels

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

# ENHANCEMENT: Made get_ssim fully async to prevent blocking the event loop
async def get_ssim(output_file):
    cmd = ["ffmpeg", "-threads", "0", "-i", output_file, "-i", SOURCE, "-filter_complex", "ssim", "-f", "null", "-"]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, stderr = await proc.communicate()
        for line in stderr.decode().split('\n'):
            if "All:" in line: return line.split("All:")[1].split(" ")[0]
    except: 
        pass
    return "N/A"

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
    
    # Refresh every 8 seconds for a snappier UI
    if now - last_up_update < 8:
        return
        
    percent = (current / total) * 100
    bar = generate_progress_bar(percent)
    cur_mb = current / (1024 * 1024)
    tot_mb = total / (1024 * 1024)
    
    scifi_up_ui = (
        f"<code>┌─── 🛰️ [ SYSTEM.UPLINK.ACTIVE ] ───┐\n"
        f"│                                    \n"
        f"│ 📂 FILE: {file_name}\n"
        f"│ 📊 PROG: {bar} {percent:.1f}%\n"
        f"│ 📦 SIZE: {cur_mb:.2f} / {tot_mb:.2f} MB\n"
        f"│ 📡 STATUS: Transmitting to Orbit... \n"
        f"│                                    \n"
        f"└────────────────────────────────────┘</code>"
    )
    
    try:
        await app.edit_message_text(chat_id, status_msg.id, scifi_up_ui, parse_mode=enums.ParseMode.HTML)
        last_up_update = now
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except:
        pass

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
        duration, height, is_hdr, total_frames, channels = get_video_info()
    except Exception as e:
        print(f"Metadata error: {e}")
        return

    def_crf, def_preset = select_params(height)
    final_crf = u_crf_raw if (u_crf_raw and u_crf_raw.strip()) else def_crf
    final_preset = u_preset_raw if (u_preset_raw and u_preset_raw.strip()) else def_preset
    
    res_label = u_res if u_res else f"{height}p"
    hdr_label = "HDR10" if is_hdr else "SDR"
    
    scale_filter = ["-vf", f"scale=-2:{u_res}"] if u_res else []
    
    # ENHANCEMENT: Check for 0 channels to prevent audio-less video crashes
    if channels == 0:
        audio_cmd = []
    elif u_audio == "opus":
        calc_bitrate = u_bitrate if channels <= 2 else "256k"
        audio_cmd = ["-c:a", "libopus", "-b:a", calc_bitrate]
    else:
        audio_cmd = ["-c:a", "copy"]

    hdr_params = ":enable-hdr=1" if is_hdr else ""

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        try:
            status = await app.send_message(chat_id, "📡 <b>[ SYSTEM BOOT ] Initializing Satellite Link...</b>", parse_mode=enums.ParseMode.HTML)
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
            status = await app.send_message(chat_id, "📡 <b>[ SYSTEM RECOVERY ] Link Re-established...</b>", parse_mode=enums.ParseMode.HTML)

        grid_task = asyncio.create_task(async_generate_grid(duration))

        # Optimized AV1 Command for GH Actions Runners
        cmd = [
            "ffmpeg", "-i", SOURCE, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
            *scale_filter,
            "-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le",
            "-crf", str(final_crf), "-preset", str(final_preset),
            "-svtav1-params", f"tune=0:aq-mode=2:enable-overlays=1:scd=1:enable-tpl-la=1:tile-columns=1{hdr_params}",
            "-threads", "0",
            *audio_cmd, "-c:s", "copy",
            "-progress", "pipe:1", "-nostats", "-y", file_name
        ]

        start_time, last_update = time.time(), 0

        with open(LOG_FILE, "w") as f_log:
            PROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            for line in PROCESS.stdout:
                f_log.write(line)
                if CANCELLED: break
                if "out_time_ms" in line:
                    try:
                        curr_sec = int(line.split("=")[1]) / 1_000_000
                        percent = (curr_sec / duration) * 100
                        elapsed = time.time() - start_time
                        speed = curr_sec / elapsed if elapsed > 0 else 0
                        fps = (percent / 100 * total_frames) / elapsed if elapsed > 0 else 0
                        eta = (elapsed / percent) * (100 - percent) if percent > 0 else 0
                        
                        if time.time() - last_update > 8:
                            bar = generate_progress_bar(percent)
                            size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0
                            scifi_ui = (
                                f"<code>┌─── 🛰️ [ SYSTEM.ENCODE.PROCESS ] ───┐\n"
                                f"│                                    \n"
                                f"│ 📂 FILE: {file_name}\n"
                                f"│ ⚡ SPEED: {speed:.1f}x ({int(fps)} FPS)\n"
                                f"│ ⏳ TIME: {format_time(elapsed)} / ETA: {format_time(eta)}\n"
                                f"│ 🕒 DONE: {format_time(curr_sec)} / {format_time(duration)}\n"
                                f"│                                    \n"
                                f"│ 📊 PROG: {bar} {percent:.1f}% \n"
                                f"│                                    \n"
                                f"│ 🛠️ SETTINGS: CRF {final_crf} | Preset {final_preset}\n"
                                f"│ 🎞️ VIDEO: {res_label} | 10-bit | {hdr_label}\n"
                                f"│ 🔊 AUDIO: {u_audio.upper()} @ {u_bitrate}\n"
                                f"│ 📦 SIZE: {size:.2f} MB\n"
                                f"│                                    \n"
                                f"└────────────────────────────────────┘</code>"
                            )
                            try:
                                await app.edit_message_text(chat_id, status.id, scifi_ui, parse_mode=enums.ParseMode.HTML)
                                last_update = time.time()
                            except FloodWait as e:
                                await asyncio.sleep(e.value)
                                last_update = time.time() + e.value
                            except: continue
                    except: continue

        PROCESS.wait()
        total_mission_time = time.time() - start_time
        await grid_task

        if PROCESS.returncode != 0:
            await app.send_document(chat_id, LOG_FILE, caption="❌ <b>CRITICAL ERROR: Core Failure</b>", parse_mode=enums.ParseMode.HTML)
            return

        # ENHANCEMENT: Swapped FFmpeg for mkvmerge to preserve attachments/fonts perfectly
        await app.edit_message_text(chat_id, status.id, "🛠️ <b>[ SYSTEM.OPTIMIZE ] Finalizing Metadata & Attachments...</b>", parse_mode=enums.ParseMode.HTML)
        fixed_file = f"FIXED_{file_name}"
        
        remux_cmd = [
            "mkvmerge", "-o", fixed_file, 
            file_name, 
            "--no-video", "--no-audio", "--no-subtitles", SOURCE
        ]
        
        remux_proc = await asyncio.create_subprocess_exec(*remux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await remux_proc.communicate()

        if os.path.exists(fixed_file):
            os.remove(file_name)
            os.rename(fixed_file, file_name)

        final_size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0
        
        # ENHANCEMENT: Lowered 2000 to 1990 for a safer buffer against Telegram limits
        if final_size > 1990:
            await app.send_message(chat_id, "⚠️ <b>SIZE OVERFLOW:</b> File exceeds 1.95GB limit. Sending Log only.")
            await app.send_document(chat_id, LOG_FILE)
            return

        # ENHANCEMENT: Awaited the new async SSIM function
        ssim_val = await get_ssim(file_name) if run_vmaf else "N/A"
        
        # --- SEQUENTIAL OUTPUT START ---
        
        # 1. Send Grid First
        if os.path.exists(SCREENSHOT):
            await app.send_photo(chat_id, SCREENSHOT, caption=f"🖼 <b>PROXIMITY GRID:</b> <code>{file_name}</code>", parse_mode=enums.ParseMode.HTML)
            os.remove(SCREENSHOT)

        # 2. Compile Report
        report = (
            f"✅ <b>MISSION ACCOMPLISHED</b>\n\n"
            f"📄 <b>FILE:</b> <code>{file_name}</code>\n"
            f"⏱ <b>ENCODE TIME:</b> <code>{format_time(total_mission_time)}</code>\n"
            f"📦 <b>FINAL SIZE:</b> <code>{final_size:.2f} MB</code>\n"
            f"📊 <b>SSIM:</b> <code>{ssim_val}</code>\n\n"
            f"🛠 <b>ENCODE SPECS:</b>\n"
            f"└ <b>Preset:</b> {final_preset} | <b>CRF:</b> {final_crf}\n"
            f"└ <b>Video:</b> {res_label} | {hdr_label} | 10-bit\n"
            f"└ <b>Audio:</b> {u_audio.upper()} @ {u_bitrate}"
        )

        # 3. Send Document with Progress
        await app.send_document(
            chat_id=chat_id, 
            document=file_name, 
            caption=report,
            parse_mode=enums.ParseMode.HTML,
            progress=upload_progress,
            progress_args=(app, chat_id, status, file_name)
        )
        
        # --- SEQUENTIAL OUTPUT END ---

        try:
            await status.delete()
        except:
            pass

        for f in [SOURCE, file_name, LOG_FILE]:
            if os.path.exists(f): os.remove(f)

if __name__ == "__main__":
    asyncio.run(main())
