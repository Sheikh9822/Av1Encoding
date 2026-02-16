import asyncio
import os
import subprocess
import time
from pyrogram import Client, enums
from pyrogram.errors import FloodWait

# Local Imports
from config import SOURCE_FILE, LOG_FILE, SCREENSHOT_FILE
from helpers import select_params, format_time, calculate_stats
from media import get_video_info, get_crop_params, async_generate_grid
from metrics import get_vmaf
from ui import make_status_ui, upload_to_cloud, upload_progress

async def main():
    # 1. SETUP ENV & CONSTANTS
    api_id, api_hash = int(os.getenv("API_ID")), os.getenv("API_HASH")
    bot_token, chat_id = os.getenv("BOT_TOKEN"), int(os.getenv("CHAT_ID"))
    file_name = os.getenv("FILE_NAME", "output.mkv")
    
    u_res = os.getenv("USER_RES")
    u_crf_raw, u_preset_raw = os.getenv("USER_CRF"), os.getenv("USER_PRESET")
    u_grain_raw = os.getenv("USER_GRAIN", "0")
    u_audio, u_bitrate = os.getenv("AUDIO_MODE", "opus"), os.getenv("AUDIO_BITRATE", "128k")
    run_vmaf = os.getenv("RUN_VMAF", "true").lower() == "true"

    # 2. ANALYZE SOURCE
    try:
        duration, width, height, is_hdr, total_frames, channels, fps_val = get_video_info()
    except Exception as e:
        print(f"Metadata error: {e}")
        return

    # 3. PREPARE ENCODER SETTINGS
    def_crf, def_preset = select_params(height)
    final_crf = u_crf_raw if (u_crf_raw and u_crf_raw.strip()) else def_crf
    final_preset = u_preset_raw if (u_preset_raw and u_preset_raw.strip()) else def_preset
    
    try: grain_val = int(u_grain_raw)
    except: grain_val = 0
    
    res_label = u_res if u_res else f"{height}p"
    hdr_label = "HDR10" if is_hdr else "SDR"
    grain_label = f" | Grain: {grain_val}" if grain_val > 0 else ""
    
    crop_val = get_crop_params()
    vf_filters = []
    if crop_val: vf_filters.append(f"crop={crop_val}")
    if u_res: vf_filters.append(f"scale=-2:{u_res}")
    
    video_filters = ["-vf", ",".join(vf_filters)] if vf_filters else []
    
    if channels == 0: audio_cmd = []
    elif u_audio == "opus":
        calc_bitrate = u_bitrate if channels <= 2 else "256k"
        audio_cmd = ["-c:a", "libopus", "-b:a", calc_bitrate]
    else: audio_cmd = ["-c:a", "copy"]

    hdr_params = ":enable-hdr=1" if is_hdr else ""
    grain_params = f":film-grain={grain_val}:film-grain-denoise=0" if grain_val > 0 else ""
    svtav1_tune = f"tune=0:aq-mode=2:enable-overlays=1:scd=1:enable-tpl-la=1:tile-columns=1{hdr_params}{grain_params}"

    # 4. START PROCESS
    async with Client("tg_dl_session", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:

        try:
            status = await app.send_message(chat_id, "📡 <b>[ SYSTEM BOOT ] Initializing Satellite Link...</b>", parse_mode=enums.ParseMode.HTML)
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
            status = await app.send_message(chat_id, "📡 <b>[ SYSTEM RECOVERY ] Link Re-established...</b>", parse_mode=enums.ParseMode.HTML)

        cmd = [
            "ffmpeg", "-i", SOURCE_FILE, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
            *video_filters,
            "-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le",
            "-crf", str(final_crf), "-preset", str(final_preset),
            "-svtav1-params", svtav1_tune,
            "-threads", "0",
            *audio_cmd, "-c:s", "copy",
            "-progress", "pipe:1", "-nostats", "-y", file_name
        ]

        start_time, last_update = time.time(), 0
        process = None

        with open(LOG_FILE, "w") as f_log:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            for line in process.stdout:
                f_log.write(line)
                if "out_time_ms" in line:
                    stats = calculate_stats(line, start_time, duration, total_frames)
                    if stats and (time.time() - last_update > 8):
                        curr_sec, percent, speed, fps, eta = stats
                        size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0
                        crop_label_str = f" | Cropped" if crop_val else ""
                        
                        ui_text = make_status_ui(
                            file_name, speed, fps, time.time() - start_time, eta, curr_sec, duration, percent, 
                            final_crf, final_preset, res_label, crop_label_str, hdr_label, grain_label, u_audio, u_bitrate, size
                        )
                        try:
                            await app.edit_message_text(chat_id, status.id, ui_text, parse_mode=enums.ParseMode.HTML)
                            last_update = time.time()
                        except FloodWait as e:
                            await asyncio.sleep(e.value)
                            last_update = time.time() + e.value
                        except: continue

        process.wait()
        total_mission_time = time.time() - start_time

        if process.returncode != 0:
            await app.send_document(chat_id, LOG_FILE, caption="❌ <b>CRITICAL ERROR: Core Failure</b>", parse_mode=enums.ParseMode.HTML)
            return

        # 5. POST-PROCESSING (Remux & Metrics)
        await app.edit_message_text(chat_id, status.id, "🛠️ <b>[ SYSTEM.OPTIMIZE ] Finalizing Metadata & Attachments...</b>", parse_mode=enums.ParseMode.HTML)
        fixed_file = f"FIXED_{file_name}"
        
        remux_cmd = ["mkvmerge", "-o", fixed_file, file_name, "--no-video", "--no-audio", "--no-subtitles", SOURCE_FILE]
        subprocess.run(remux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(fixed_file):
            os.remove(file_name)
            os.rename(fixed_file, file_name)

        final_size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0
        
        grid_task = asyncio.create_task(async_generate_grid(duration, file_name))
        
        if run_vmaf:
            vmaf_val, ssim_val = await get_vmaf(file_name, crop_val, width, height, duration, fps_val, app, chat_id, status)
        else:
            vmaf_val, ssim_val = "N/A", "N/A"
            
        await grid_task
        
        # 6. HANDLING UPLOAD
        if final_size > 1990:
            await app.edit_message_text(chat_id, status.id, "⚠️ <b>[ SYSTEM.WARNING ] SIZE OVERFLOW. Rerouting to Cloud Storage...</b>", parse_mode=enums.ParseMode.HTML)
            cloud_url = await upload_to_cloud(file_name)
            
            overflow_report = (
                f"⚠️ <b>MISSION PARTIALLY SUCCESSFUL (OVERFLOW)</b>\n\n"
                f"📄 <b>FILE:</b> <code>{file_name}</code>\n"
                f"📦 <b>SIZE:</b> <code>{final_size:.2f} MB</code> (Exceeds Telegram limit)\n"
                f"📊 <b>QUALITY:</b> VMAF: <code>{vmaf_val}</code> | SSIM: <code>{ssim_val}</code>\n\n"
                f"☁️ <b>EXTERNAL UPLINK (Valid 3 days):</b>\n{cloud_url}\n\n"
                f"<i>Sending process logs below.</i>"
            )
            await app.send_message(chat_id, overflow_report, disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
            await app.send_document(chat_id, LOG_FILE)
            return
        
        photo_msg = None
        if os.path.exists(SCREENSHOT_FILE):
            photo_msg = await app.send_photo(chat_id, SCREENSHOT_FILE, caption=f"🖼 <b>PROXIMITY GRID:</b> <code>{file_name}</code>", parse_mode=enums.ParseMode.HTML)
            os.remove(SCREENSHOT_FILE)

        report = (
            f"✅ <b>MISSION ACCOMPLISHED</b>\n\n"
            f"📄 <b>FILE:</b> <code>{file_name}</code>\n"
            f"⏱ <b>ENCODE TIME:</b> <code>{format_time(total_mission_time)}</code>\n"
            f"📦 <b>FINAL SIZE:</b> <code>{final_size:.2f} MB</code>\n"
            f"📊 <b>QUALITY:</b> VMAF: <code>{vmaf_val}</code> | SSIM: <code>{ssim_val}</code>\n\n"
            f"🛠 <b>ENCODE SPECS:</b>\n"
            f"└ <b>Preset:</b> {final_preset} | <b>CRF:</b> {final_crf}\n"
            f"└ <b>Video:</b> {res_label}{crop_label_str if 'crop_label_str' in locals() else ''} | {hdr_label} | 10-bit{grain_label}\n"
            f"└ <b>Audio:</b> {u_audio.upper()} @ {u_bitrate}"
        )

        await app.edit_message_text(chat_id, status.id, "🚀 <b>[ SYSTEM.UPLINK ] Transmitting Final Video to Telegram...</b>", parse_mode=enums.ParseMode.HTML)

        await app.send_document(
            chat_id=chat_id, 
            document=file_name, 
            caption=report,
            parse_mode=enums.ParseMode.HTML,
            reply_to_message_id=photo_msg.id if photo_msg else None,
            progress=upload_progress,
            progress_args=(app, chat_id, status, file_name)
        )
        
        try: await status.delete()
        except: pass

        for f in [SOURCE_FILE, file_name, LOG_FILE]:
            if os.path.exists(f): os.remove(f)

if __name__ == "__main__":
    asyncio.run(main())