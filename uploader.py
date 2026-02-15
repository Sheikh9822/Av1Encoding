import asyncio
import os
import subprocess
import time
import json
from datetime import timedelta
from pyrogram import Client, enums
from pyrogram.errors import FloodWait

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
    
    channels = int(audio_stream.get('channels', 0))
    duration = float(res['format'].get('duration', 0))
    height = int(video_stream.get('height', 0))
    fps_raw = video_stream.get('r_frame_rate', '24/1')
    fps_val = eval(fps_raw) if '/' in fps_raw else float(fps_raw)
    total_frames = int(video_stream.get('nb_frames', duration * fps_val))
    is_hdr = 'bt2020' in video_stream.get('color_primaries', 'bt709')
    
    return duration, height, is_hdr, total_frames, channels, fps_val

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

async def get_vmaf(output_file, vf_string="", duration=0, fps=24, app=None, chat_id=None, status_msg=None):
    if duration > 30:
        interval = duration / 6
        select_parts = []
        for i in range(6):
            start_t = (i * interval) + (interval / 2) - 2.5
            end_t = start_t + 5
            select_parts.append(f"between(t,{start_t},{end_t})")
        
        select_expr = "+".join(select_parts)
        select_filter = f"select='{select_expr}',setpts=N/FRAME_RATE/TB"
        
        if vf_string:
            filter_graph = f"[1:v]{vf_string},{select_filter}[ref];[0:v]{select_filter}[dist];[dist][ref]libvmaf"
        else:
            filter_graph = f"[1:v]{select_filter}[ref];[0:v]{select_filter}[dist];[dist][ref]libvmaf"
            
        total_vmaf_frames = int(30 * fps)
    else:
        if vf_string:
            filter_graph = f"[1:v]{vf_string}[ref];[0:v][ref]libvmaf"
        else:
            filter_graph = "libvmaf"
        total_vmaf_frames = int(duration * fps)
        
    cmd = ["ffmpeg", "-threads", "0", "-i", output_file, "-i", SOURCE, "-filter_complex", filter_graph, "-progress", "pipe:1", "-nostats", "-f", "null", "-"]
    
    vmaf_score = "N/A"
    
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        start_time = time.time()
        last_update = 0
        
        async def read_progress():
            nonlocal last_update
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode().strip()
                if line_str.startswith("frame="):
                    try:
                        curr_frame = int(line_str.split("=")[1].strip())
                        percent = min(100, (curr_frame / total_vmaf_frames) * 100)
                        now = time.time()
                        
                        if now - last_update > 5 and app and status_msg:
                            elapsed = now - start_time
                            speed = curr_frame / elapsed if elapsed > 0 else 0
                            eta = (total_vmaf_frames - curr_frame) / speed if speed > 0 else 0
                            
                            bar = generate_progress_bar(percent)
                            ui = (
                                f"<code>┌─── 🧠 [ SYSTEM.ANALYSIS.VMAF ] ───┐\n"
                                f"│                                    \n"
                                f"│ 🔬 SCORING: Rapid 30s Sample\n"
                                f"│ 📊 PROG: {bar} {percent:.1f}%\n"
                                f"│ ⚡ SPEED: {speed:.1f} FPS\n"
                                f"│ ⏳ ETA: {format_time(eta)}\n"
                                f"│                                    \n"
                                f"└────────────────────────────────────┘</code>"
                            )
                            try:
                                await app.edit_message_text(chat_id, status_msg.id, ui, parse_mode=enums.ParseMode.HTML)
                                last_update = now
                            except FloodWait as e:
                                await asyncio.sleep(e.value)
                            except:
                                pass
                    except:
                        pass

        async def read_stderr():
            nonlocal vmaf_score
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                line_str = line.decode('utf-8', errors='ignore').strip()
                if "VMAF score:" in line_str:
                    vmaf_score = line_str.split("VMAF score:")[1].strip()

        await asyncio.gather(read_progress(), read_stderr())
        await proc.wait()
        return vmaf_score
        
    except Exception as e:
        print(f"VMAF Capture Error: {e}")
        return "N/A"

def get_crop_params():
    cmd = [
        "ffmpeg", "-skip_frame", "nokey", "-ss", "00:01:00", "-i", SOURCE,
        "-vframes", "100", "-vf", "cropdetect", "-f", "null", "-"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        for line in reversed(res.stderr.split('\n')):
            if "crop=" in line:
                return line.split("crop=")[1].split(" ")[0]
    except:
        pass
    return None

def select_params(height):
    if height >= 2000: return 32, 10
    elif height >= 1000: return 42, 6
    elif height >= 700: return 24, 6
    return 22, 4

async def upload_to_cloud(filepath):
    cmd = ["curl", "-H", "Max-Days: 3", "--upload-file", filepath, f"https://transfer.sh/{os.path.basename(filepath)}"]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()

# ---------- UPLOAD CALLBACK ----------

last_up_update = 0

async def upload_progress(current, total, app, chat_id, status_msg, file_name):
    global last_up_update
    now = time.time()
    
    # ENHANCEMENT: Dropped refresh limit to 4 seconds for smoother updates
    if now - last_up_update < 4:
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
    u_grain_raw = os.getenv("USER_GRAIN", "0")
    u_audio, u_bitrate = os.getenv("AUDIO_MODE", "opus"), os.getenv("AUDIO_BITRATE", "128k")
    run_vmaf = os.getenv("RUN_VMAF", "true").lower() == "true"

    try:
        duration, height, is_hdr, total_frames, channels, fps_val = get_video_info()
    except Exception as e:
        print(f"Metadata error: {e}")
        return

    def_crf, def_preset = select_params(height)
    final_crf = u_crf_raw if (u_crf_raw and u_crf_raw.strip()) else def_crf
    final_preset = u_preset_raw if (u_preset_raw and u_preset_raw.strip()) else def_preset
    
    try:
        grain_val = int(u_grain_raw)
    except:
        grain_val = 0
    
    res_label = u_res if u_res else f"{height}p"
    hdr_label = "HDR10" if is_hdr else "SDR"
    grain_label = f" | Grain: {grain_val}" if grain_val > 0 else ""
    
    crop_val = get_crop_params()
    vf_filters = []
    if crop_val:
        vf_filters.append(f"crop={crop_val}")
    if u_res:
        vf_filters.append(f"scale=-2:{u_res}")
        
    video_filters = ["-vf", ",".join(vf_filters)] if vf_filters else []
    
    if channels == 0:
        audio_cmd = []
    elif u_audio == "opus":
        calc_bitrate = u_bitrate if channels <= 2 else "256k"
        audio_cmd = ["-c:a", "libopus", "-b:a", calc_bitrate]
    else:
        audio_cmd = ["-c:a", "copy"]

    hdr_params = ":enable-hdr=1" if is_hdr else ""
    grain_params = f":film-grain={grain_val}" if grain_val > 0 else ""
    svtav1_tune = f"tune=0:aq-mode=2:enable-overlays=1:scd=1:enable-tpl-la=1:tile-columns=1{hdr_params}{grain_params}"

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        try:
            status = await app.send_message(chat_id, "📡 <b>[ SYSTEM BOOT ] Initializing Satellite Link...</b>", parse_mode=enums.ParseMode.HTML)
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
            status = await app.send_message(chat_id, "📡 <b>[ SYSTEM RECOVERY ] Link Re-established...</b>", parse_mode=enums.ParseMode.HTML)

        grid_task = asyncio.create_task(async_generate_grid(duration))

        cmd = [
            "ffmpeg", "-i", SOURCE, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
            *video_filters,
            "-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le",
            "-crf", str(final_crf), "-preset", str(final_preset),
            "-svtav1-params", svtav1_tune,
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
                            
                            crop_label = f" | Cropped" if crop_val else ""
                            
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
                                f"│ 🎞️ VIDEO: {res_label}{crop_label} | 10-bit | {hdr_label}{grain_label}\n"
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

        await app.edit_message_text(chat_id, status.id, "🛠️ <b>[ SYSTEM.OPTIMIZE ] Finalizing Metadata & Attachments...</b>", parse_mode=enums.ParseMode.HTML)
        fixed_file = f"FIXED_{file_name}"
        
        remux_cmd = [
            "mkvmerge", "-o", fixed_file, 
            file_name, 
            "--no-video", "--no-audio", "--no-subtitles", SOURCE
        ]
        
        subprocess.run(remux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(fixed_file):
            os.remove(file_name)
            os.rename(fixed_file, file_name)

        final_size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0
        
        vf_string = ",".join(vf_filters) if vf_filters else ""
        if run_vmaf:
            vmaf_val = await get_vmaf(file_name, vf_string, duration, fps_val, app, chat_id, status)
        else:
            vmaf_val = "N/A"
        
        if final_size > 1990:
            await app.edit_message_text(chat_id, status.id, "⚠️ <b>[ SYSTEM.WARNING ] SIZE OVERFLOW. Rerouting to Cloud Storage...</b>", parse_mode=enums.ParseMode.HTML)
            cloud_url = await upload_to_cloud(file_name)
            
            overflow_report = (
                f"⚠️ <b>MISSION PARTIALLY SUCCESSFUL (OVERFLOW)</b>\n\n"
                f"📄 <b>FILE:</b> <code>{file_name}</code>\n"
                f"📦 <b>SIZE:</b> <code>{final_size:.2f} MB</code> (Exceeds Telegram limit)\n"
                f"📊 <b>VMAF:</b> <code>{vmaf_val}</code>\n\n"
                f"☁️ <b>EXTERNAL UPLINK (Valid 3 days):</b>\n{cloud_url}\n\n"
                f"<i>Sending process logs below.</i>"
            )
            await app.send_message(chat_id, overflow_report, disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
            await app.send_document(chat_id, LOG_FILE)
            return
        
        if os.path.exists(SCREENSHOT):
            await app.send_photo(chat_id, SCREENSHOT, caption=f"🖼 <b>PROXIMITY GRID:</b> <code>{file_name}</code>", parse_mode=enums.ParseMode.HTML)
            os.remove(SCREENSHOT)

        report = (
            f"✅ <b>MISSION ACCOMPLISHED</b>\n\n"
            f"📄 <b>FILE:</b> <code>{file_name}</code>\n"
            f"⏱ <b>ENCODE TIME:</b> <code>{format_time(total_mission_time)}</code>\n"
            f"📦 <b>FINAL SIZE:</b> <code>{final_size:.2f} MB</code>\n"
            f"📊 <b>VMAF:</b> <code>{vmaf_val}</code>\n\n"
            f"🛠 <b>ENCODE SPECS:</b>\n"
            f"└ <b>Preset:</b> {final_preset} | <b>CRF:</b> {final_crf}\n"
            f"└ <b>Video:</b> {res_label}{crop_label} | {hdr_label} | 10-bit{grain_label}\n"
            f"└ <b>Audio:</b> {u_audio.upper()} @ {u_bitrate}"
        )

        # ENHANCEMENT: Pre-Upload UI Status
        await app.edit_message_text(chat_id, status.id, "🚀 <b>[ SYSTEM.UPLINK ] Transmitting Final Video to Telegram...</b>", parse_mode=enums.ParseMode.HTML)

        await app.send_document(
            chat_id=chat_id, 
            document=file_name, 
            caption=report,
            parse_mode=enums.ParseMode.HTML,
            progress=upload_progress,
            progress_args=(app, chat_id, status, file_name)
        )
        
        try:
            await status.delete()
        except:
            pass

        for f in [SOURCE, file_name, LOG_FILE]:
            if os.path.exists(f): os.remove(f)

if __name__ == "__main__":
    asyncio.run(main())
