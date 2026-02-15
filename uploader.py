import asyncio
import os
import subprocess
import time
import json
import uvloop
from datetime import timedelta
from pyrogram import Client, enums
from pyrogram.errors import FloodWait

uvloop.install()

SOURCE = "source.mkv"
SCREENSHOT = "grid_preview.jpg"
LOG_FILE = "encode_log.txt"
CANCELLED = False
PROCESS = None
START_T = time.time()

# ---------- TOOLS & ANALYTICS ----------

def get_video_info():
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", SOURCE]
    res = json.loads(subprocess.check_output(cmd).decode())
    video_stream = next(s for s in res['streams'] if s['codec_type'] == 'video')
    audio_stream = next((s for s in res['streams'] if s['codec_type'] == 'audio'), {})
    channels = int(audio_stream.get('channels', 2))
    
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
        # Enhanced: Uses thumbnail filter to pick the best frames (avoiding black frames)
        interval = duration / 10
        select_filter = f"select='thumbnail',scale=480:-1,tile=3x3"
        cmd = ["ffmpeg", "-i", SOURCE, "-vf", select_filter, "-frames:v", "1", "-q:v", "3", SCREENSHOT, "-y"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await loop.run_in_executor(None, sync_grid)

# ---------- UPLOAD CALLBACK ----------

last_up_update = 0

async def upload_progress(current, total, app, chat_id, status_msg, file_name):
    global last_up_update
    now = time.time()
    if now - last_up_update < 8: return
        
    percent = (current / total) * 100
    bar = generate_progress_bar(percent)
    cur_mb, tot_mb = current / (1024**2), total / (1024**2)
    
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
    except FloodWait as e: await asyncio.sleep(e.value)
    except: pass

# ---------- MAIN PROCESS ----------

async def main():
    global PROCESS, START_T

    api_id, api_hash = int(os.getenv("API_ID")), os.getenv("API_HASH")
    bot_token, chat_id = os.getenv("BOT_TOKEN"), int(os.getenv("CHAT_ID"))
    file_name = os.getenv("FILE_NAME")
    u_res = os.getenv("USER_RES")
    u_crf, u_preset = os.getenv("USER_CRF", "42"), os.getenv("USER_PRESET", "6")
    u_bitrate = os.getenv("AUDIO_BITRATE", "128k")

    duration, height, is_hdr, total_frames, channels = get_video_info()
    res_label = u_res if u_res else f"{height}p"
    hdr_label = "HDR10" if is_hdr else "SDR"
    hdr_params = ":enable-hdr=1" if is_hdr else ""

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        status = await app.send_message(chat_id, "📡 <b>[ SYSTEM BOOT ] Initializing Satellite Link...</b>", parse_mode=enums.ParseMode.HTML)
        grid_task = asyncio.create_task(async_generate_grid(duration))

        # Checkpoint Seek logic
        seek = os.getenv("SEEK_TIME", "00:00:00")
        
        cmd = [
            "ffmpeg", "-ss", seek, "-i", SOURCE, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
            "-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le", "-crf", str(u_crf), "-preset", str(u_preset),
            "-svtav1-params", f"tune=0:aq-mode=2:scd=1:tile-columns=1{hdr_params}",
            "-c:a", "libopus", "-b:a", u_bitrate, "-c:s", "copy",
            "-metadata", "title=AV1 Satellite Encode",
            "-metadata:s:v:0", f"title=SVT-AV1 ({res_label})",
            "-metadata:s:a:0", f"title=Opus ({u_bitrate})",
            "-progress", "pipe:1", "-nostats", "-y", file_name
        ]

        start_time, last_update = time.time(), 0
        stasis_mode = False

        PROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        for line in PROCESS.stdout:
            # Checkpoint: 5.5 hours limit (19800 seconds)
            if time.time() - START_T > 19800:
                stasis_mode = True
                PROCESS.terminate()
                break

            if "out_time_ms" in line:
                try:
                    curr_sec = int(line.split("=")[1]) / 1_000_000
                    percent = (curr_sec / duration) * 100
                    elapsed = time.time() - start_time
                    fps = (percent / 100 * total_frames) / elapsed if elapsed > 0 else 0
                    eta = (elapsed / percent) * (100 - percent) if percent > 0 else 0
                    
                    if time.time() - last_update > 8:
                        bar = generate_progress_bar(percent)
                        scifi_ui = (
                            f"<code>┌─── 🛰️ [ SYSTEM.ENCODE.PROCESS ] ───┐\n"
                            f"│                                    \n"
                            f"│ 📂 FILE: {file_name}\n"
                            f"│ ⚡ SPEED: {int(fps)} FPS\n"
                            f"│ ⏳ TIME: {format_time(elapsed)} / ETA: {format_time(eta)}\n"
                            f"│ 🕒 DONE: {format_time(curr_sec)} / {format_time(duration)}\n"
                            f"│                                    \n"
                            f"│ 📊 PROG: {bar} {percent:.1f}% \n"
                            f"│                                    \n"
                            f"│ 🛠️ SETTINGS: CRF {u_crf} | Preset {u_preset}\n"
                            f"│ 🎞️ VIDEO: {res_label} | 10-bit | {hdr_label}\n"
                            f"│                                    \n"
                            f"└────────────────────────────────────┘</code>"
                        )
                        await app.edit_message_text(chat_id, status.id, scifi_ui, parse_mode=enums.ParseMode.HTML)
                        last_update = time.time()
                except: continue

        PROCESS.wait()
        await grid_task

        if stasis_mode:
            stasis_msg = (
                f"🛰️ <b>[ SYSTEM.STASIS ] LIMIT REACHED</b>\n\n"
                f"Mission paused to prevent timeout. Partial file saved.\n"
                f"🕒 <b>RESUME AT:</b> <code>{format_time(curr_sec)}</code>"
            )
            await app.send_document(chat_id, file_name, caption=stasis_msg)
            return

        # Sequential Output Flow
        if os.path.exists(SCREENSHOT):
            await app.send_photo(chat_id, SCREENSHOT, caption=f"🖼 <b>PROXIMITY GRID:</b> <code>{file_name}</code>")
            os.remove(SCREENSHOT)

        final_size = os.path.getsize(file_name)/(1024**2)
        report = (
            f"✅ <b>MISSION ACCOMPLISHED</b>\n\n"
            f"📄 <b>FILE:</b> <code>{file_name}</code>\n"
            f"⏱ <b>ENCODE TIME:</b> <code>{format_time(time.time()-start_time)}</code>\n"
            f"📦 <b>SIZE:</b> <code>{final_size:.2f} MB</code>\n\n"
            f"🛠 <b>ENCODE SPECS:</b>\n"
            f"└ <b>Preset:</b> {u_preset} | <b>CRF:</b> {u_crf}\n"
            f"└ <b>Video:</b> {res_label} | {hdr_label} | 10-bit\n"
            f"└ <b>Audio:</b> OPUS @ {u_bitrate}"
        )

        await app.send_document(chat_id, file_name, caption=report, progress=upload_progress, progress_args=(app, chat_id, status, file_name))
        await status.delete()

if __name__ == "__main__":
    asyncio.run(main())
