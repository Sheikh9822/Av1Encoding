import asyncio
import os
import time
import logging
import subprocess
from pyrogram import Client, enums
from pyrogram.errors import FloodWait

import config
import ui
import media_tools as media


# Proper Python logging avoids printing directly to stdout incorrectly
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def run_encode():
    app = Client(":memory:", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN)
    
    try:
        await app.start()
        status = await app.send_message(config.CHAT_ID, "📡 <b>[ SYSTEM BOOT ] Initializing Satellite Link...</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed to start bot or send initial message: {e}")
        return

    try:
        duration, width, height, is_hdr, total_frames, channels, fps_val = await media.get_video_info(config.SOURCE)
    except Exception as e:
        logger.error(f"Metadata error: {e}")
        await app.edit_message_text(config.CHAT_ID, status.id, "❌ <b>Metadata Extraction Failed!</b>", parse_mode=enums.ParseMode.HTML)
        return

    def_crf, def_preset = media.select_params(height)
    final_crf = config.USER_CRF if (config.USER_CRF and config.USER_CRF.strip()) else def_crf
    final_preset = config.USER_PRESET if (config.USER_PRESET and config.USER_PRESET.strip()) else def_preset
    
    res_label = config.USER_RES if config.USER_RES else f"{height}p"
    hdr_label = "HDR10" if is_hdr else "SDR"
    grain_label = f" | Grain: {config.USER_GRAIN}" if config.USER_GRAIN > 0 else ""
    
    crop_val = await media.get_crop_params(config.SOURCE)
    vf_filters = []
    if crop_val: vf_filters.append(f"crop={crop_val}")
    if config.USER_RES: vf_filters.append(f"scale=-2:{config.USER_RES}")
    video_filters = ["-vf", ",".join(vf_filters)] if vf_filters else []
    
    if channels == 0: audio_cmd = []
    elif config.AUDIO_MODE == "opus":
        calc_bitrate = config.AUDIO_BITRATE if channels <= 2 else "256k"
        audio_cmd = ["-c:a", "libopus", "-b:a", calc_bitrate]
    else: audio_cmd = ["-c:a", "copy"]

    hdr_params = ":enable-hdr=1" if is_hdr else ""
    grain_params = f":film-grain={config.USER_GRAIN}:film-grain-denoise=0" if config.USER_GRAIN > 0 else ""
    svtav1_tune = f"tune=0:aq-mode=2:enable-overlays=1:scd=1:enable-tpl-la=1:tile-columns=1{hdr_params}{grain_params}"

    cmd = [
        "ffmpeg", "-i", config.SOURCE, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
        *video_filters,
        "-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le",
        "-crf", str(final_crf), "-preset", str(final_preset),
        "-svtav1-params", svtav1_tune,
        "-threads", "0",
        *audio_cmd, "-c:s", "copy",
        "-progress", "pipe:1", "-nostats", "-y", config.FILE_NAME
    ]

    logger.info("Starting encode process...")
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    
    start_time, last_update = time.time(), 0
    
    with open(config.LOG_FILE, "w") as f_log:
        while True:
            line = await proc.stdout.readline()
            if not line: break
            
            line_str = line.decode('utf-8', errors='ignore')
            f_log.write(line_str)
            
            if "out_time_ms" in line_str:
                try:
                    curr_sec = int(line_str.split("=")[1]) / 1_000_000
                    percent = (curr_sec / duration) * 100
                    elapsed = time.time() - start_time
                    speed = curr_sec / elapsed if elapsed > 0 else 0
                    fps = (percent / 100 * total_frames) / elapsed if elapsed > 0 else 0
                    eta = (elapsed / percent) * (100 - percent) if percent > 0 else 0
                    
                    if time.time() - last_update > 8:
                        bar = ui.generate_progress_bar(percent)
                        size = os.path.getsize(config.FILE_NAME)/(1024*1024) if os.path.exists(config.FILE_NAME) else 0
                        crop_label = f" | Cropped" if crop_val else ""
                        
                        scifi_ui = (
                            f"<code>┌─── 🛰️ [ SYSTEM.ENCODE.PROCESS ] ───┐\n"
                            f"│                                    \n"
                            f"│ 📂 FILE: {config.FILE_NAME}\n"
                            f"│ ⚡ SPEED: {speed:.1f}x ({int(fps)} FPS)\n"
                            f"│ ⏳ TIME: {ui.format_time(elapsed)} / ETA: {ui.format_time(eta)}\n"
                            f"│ 🕒 DONE: {ui.format_time(curr_sec)} / {ui.format_time(duration)}\n"
                            f"│                                    \n"
                            f"│ 📊 PROG: {bar} {percent:.1f}% \n"
                            f"│                                    \n"
                            f"│ 🛠️ SETTINGS: CRF {final_crf} | Preset {final_preset}\n"
                            f"│ 🎞️ VIDEO: {res_label}{crop_label} | 10-bit | {hdr_label}{grain_label}\n"
                            f"│ 🔊 AUDIO: {config.AUDIO_MODE.upper()} @ {config.AUDIO_BITRATE}\n"
                            f"│ 📦 SIZE: {size:.2f} MB\n"
                            f"│                                    \n"
                            f"└────────────────────────────────────┘</code>"
                        )
                        try:
                            await app.edit_message_text(config.CHAT_ID, status.id, scifi_ui, parse_mode=enums.ParseMode.HTML)
                            last_update = time.time()
                        except FloodWait as e:
                            await asyncio.sleep(e.value)
                        except Exception: 
                            continue
                except Exception: 
                    continue

    await proc.wait()
    total_mission_time = time.time() - start_time

    if proc.returncode != 0:
        await app.send_document(config.CHAT_ID, config.LOG_FILE, caption="❌ <b>CRITICAL ERROR: Core Failure</b>", parse_mode=enums.ParseMode.HTML)
        await app.stop()
        return

    await app.edit_message_text(config.CHAT_ID, status.id, "🛠️ <b>[ SYSTEM.OPTIMIZE ] Finalizing Metadata & Attachments...</b>", parse_mode=enums.ParseMode.HTML)
    
    # Non-blocking async Remux
    fixed_file = f"FIXED_{config.FILE_NAME}"
    remux_cmd = ["mkvmerge", "-o", fixed_file, config.FILE_NAME, "--no-video", "--no-audio", "--no-subtitles", config.SOURCE]
    remux_proc = await asyncio.create_subprocess_exec(*remux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await remux_proc.wait()

    if os.path.exists(fixed_file):
        os.remove(config.FILE_NAME)
        os.rename(fixed_file, config.FILE_NAME)

    # Grid Generation
    grid_task = asyncio.create_task(media.async_generate_grid(duration, config.FILE_NAME, config.SCREENSHOT))
    
    # Connect VMAF Logic smoothly to UI
    vmaf_val, ssim_val = "N/A", "N/A"
    if config.RUN_VMAF:
        vmaf_state = {'last_update': 0}
        
        async def handle_vmaf_progress(percent, speed, eta):
            await ui.vmaf_progress(percent, speed, eta, app, config.CHAT_ID, status, vmaf_state)
            
        vmaf_val, ssim_val = await media.get_vmaf(
            config.FILE_NAME, crop_val, width, height, duration, fps_val, 
            progress_callback=handle_vmaf_progress
        )

    await grid_task
    final_size = os.path.getsize(config.FILE_NAME) / (1024 * 1024) if os.path.exists(config.FILE_NAME) else 0

    if final_size > 1990:
        await app.edit_message_text(config.CHAT_ID, status.id, "⚠️ <b>[ SYSTEM.WARNING ] SIZE OVERFLOW. Rerouting to Cloud Storage...</b>", parse_mode=enums.ParseMode.HTML)
        cloud_url = await media.upload_to_cloud(config.FILE_NAME)
        
        overflow_report = (
            f"⚠️ <b>MISSION PARTIALLY SUCCESSFUL (OVERFLOW)</b>\n\n"
            f"📄 <b>FILE:</b> <code>{config.FILE_NAME}</code>\n"
            f"📦 <b>SIZE:</b> <code>{final_size:.2f} MB</code> (Exceeds Telegram limit)\n"
            f"📊 <b>QUALITY:</b> VMAF: <code>{vmaf_val}</code> | SSIM: <code>{ssim_val}</code>\n\n"
            f"☁️ <b>EXTERNAL UPLINK (Valid 3 days):</b>\n{cloud_url}\n\n"
            f"<i>Sending process logs below.</i>"
        )
        await app.send_message(config.CHAT_ID, overflow_report, disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
        await app.send_document(config.CHAT_ID, config.LOG_FILE)
        
        for f in [config.SOURCE, config.FILE_NAME, config.LOG_FILE, config.SCREENSHOT]:
            if os.path.exists(f): os.remove(f)
        await app.stop()
        return

    photo_msg = None
    if os.path.exists(config.SCREENSHOT):
        photo_msg = await app.send_photo(config.CHAT_ID, config.SCREENSHOT, caption=f"🖼 <b>PROXIMITY GRID:</b> <code>{config.FILE_NAME}</code>", parse_mode=enums.ParseMode.HTML)

    report = (
        f"✅ <b>MISSION ACCOMPLISHED</b>\n\n"
        f"📄 <b>FILE:</b> <code>{config.FILE_NAME}</code>\n"
        f"⏱ <b>ENCODE TIME:</b> <code>{ui.format_time(total_mission_time)}</code>\n"
        f"📦 <b>FINAL SIZE:</b> <code>{final_size:.2f} MB</code>\n"
        f"📊 <b>QUALITY:</b> VMAF: <code>{vmaf_val}</code> | SSIM: <code>{ssim_val}</code>\n\n"
        f"🛠 <b>ENCODE SPECS:</b>\n"
        f"└ <b>Preset:</b> {final_preset} | <b>CRF:</b> {final_crf}\n"
        f"└ <b>Video:</b> {res_label}{crop_label} | {hdr_label} | 10-bit{grain_label}\n"
        f"└ <b>Audio:</b> {config.AUDIO_MODE.upper()} @ {config.AUDIO_BITRATE}"
    )

    await app.edit_message_text(config.CHAT_ID, status.id, "🚀 <b>[ SYSTEM.UPLINK ] Transmitting Final Video to Telegram...</b>", parse_mode=enums.ParseMode.HTML)

    upload_state_tracker = {'last_update': 0}
    await app.send_document(
        chat_id=config.CHAT_ID, 
        document=config.FILE_NAME, 
        caption=report,
        parse_mode=enums.ParseMode.HTML,
        reply_to_message_id=photo_msg.id if photo_msg else None,
        progress=ui.upload_progress,
        progress_args=(app, config.CHAT_ID, status, config.FILE_NAME, upload_state_tracker)
    )

    try:
        await status.delete()
    except Exception:
        pass

    # Cleanup Files
    for f in [config.SOURCE, config.FILE_NAME, config.LOG_FILE, config.SCREENSHOT]:
        if os.path.exists(f): os.remove(f)

    await app.stop()

if __name__ == "__main__":
    asyncio.run(run_encode())
