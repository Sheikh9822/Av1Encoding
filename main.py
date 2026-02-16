import asyncio
import os
import subprocess
import time
from pyrogram import Client, enums
from pyrogram.errors import FloodWait

import config
from media import get_video_info, get_crop_params, select_params, async_generate_grid, get_vmaf, upload_to_cloud
from ui import get_encode_ui, format_time, upload_progress

async def main():
    try:
        duration, width, height, is_hdr, total_frames, channels, fps_val = get_video_info()
    except Exception as e:
        print(f"Metadata error: {e}")
        return

    def_crf, def_preset = select_params(height)
    final_crf = config.USER_CRF if (config.USER_CRF and config.USER_CRF.strip()) else def_crf
    final_preset = config.USER_PRESET if (config.USER_PRESET and config.USER_PRESET.strip()) else def_preset
    
    try: grain_val = int(config.USER_GRAIN)
    except: grain_val = 0
    
    res_label = config.USER_RES if config.USER_RES else f"{height}p"
    hdr_label = "HDR10" if is_hdr else "SDR"
    grain_label = f" | Grain: {grain_val}" if grain_val > 0 else ""
    
    crop_val = get_crop_params()
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
    grain_params = f":film-grain={grain_val}:film-grain-denoise=0" if grain_val > 0 else ""
    svtav1_tune = f"tune=0:aq-mode=2:enable-overlays=1:scd=1:enable-tpl-la=1:tile-columns=1{hdr_params}{grain_params}"

    async with Client(":memory:", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN) as app:
        try:
            status = await app.send_message(config.CHAT_ID, "📡 <b>[ SYSTEM BOOT ] Initializing Satellite Link...</b>", parse_mode=enums.ParseMode.HTML)
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
            status = await app.send_message(config.CHAT_ID, "📡 <b>[ SYSTEM RECOVERY ] Link Re-established...</b>", parse_mode=enums.ParseMode.HTML)

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

        start_time, last_update = time.time(), 0

        with open(config.LOG_FILE, "w") as f_log:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            for line in process.stdout:
                f_log.write(line)
                if config.CANCELLED: break
                if "out_time_ms" in line:
                    try:
                        curr_sec = int(line.split("=")[1]) / 1_000_000
                        percent = (curr_sec / duration) * 100
                        elapsed = time.time() - start_time
                        speed = curr_sec / elapsed if elapsed > 0 else 0
                        fps = (percent / 100 * total_frames) / elapsed if elapsed > 0 else 0
                        eta = (elapsed / percent) * (100 - percent) if percent > 0 else 0
                        
                        if time.time() - last_update > 8:
                            size = os.path.getsize(config.FILE_NAME)/(1024*1024) if os.path.exists(config.FILE_NAME) else 0
                            crop_label = f" | Cropped" if crop_val else ""
                            
                            scifi_ui = get_encode_ui(
                                config.FILE_NAME, speed, fps, elapsed, eta, curr_sec, duration, percent, 
                                final_crf, final_preset, res_label, crop_label, hdr_label, grain_label, 
                                config.AUDIO_MODE, config.AUDIO_BITRATE, size
                            )
                            try:
                                await app.edit_message_text(config.CHAT_ID, status.id, scifi_ui, parse_mode=enums.ParseMode.HTML)
                                last_update = time.time()
                            except FloodWait as e:
                                await asyncio.sleep(e.value)
                                last_update = time.time() + e.value
                            except: continue
                    except: continue

        process.wait()
        total_mission_time = time.time() - start_time

        if process.returncode != 0:
            await app.send_document(config.CHAT_ID, config.LOG_FILE, caption="❌ <b>CRITICAL ERROR: Core Failure</b>", parse_mode=enums.ParseMode.HTML)
            return

        await app.edit_message_text(config.CHAT_ID, status.id, "🛠️ <b>[ SYSTEM.OPTIMIZE ] Finalizing Metadata & Attachments...</b>", parse_mode=enums.ParseMode.HTML)
        fixed_file = f"FIXED_{config.FILE_NAME}"
        
        remux_cmd = [
            "mkvmerge", "-o", fixed_file, 
            config.FILE_NAME, 
            "--no-video", "--no-audio", "--no-subtitles", config.SOURCE
        ]
        
        subprocess.run(remux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(fixed_file):
            os.remove(config.FILE_NAME)
            os.rename(fixed_file, config.FILE_NAME)

        final_size = os.path.getsize(config.FILE_NAME)/(1024*1024) if os.path.exists(config.FILE_NAME) else 0
        
        grid_task = asyncio.create_task(async_generate_grid(duration, config.FILE_NAME))
        
        if config.RUN_VMAF:
            vmaf_val, ssim_val = await get_vmaf(config.FILE_NAME, crop_val, width, height, duration, fps_val, app, config.CHAT_ID, status)
        else:
            vmaf_val, ssim_val = "N/A", "N/A"
            
        await grid_task
        
        if final_size > 1990:
            await app.edit_message_text(config.CHAT_ID, status.id, "⚠️ <b>[ SYSTEM.WARNING ] SIZE OVERFLOW. Rerouting to Cloud Storage...</b>", parse_mode=enums.ParseMode.HTML)
            cloud_url = await upload_to_cloud(config.FILE_NAME)
            
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
            return
        
        photo_msg = None
        if os.path.exists(config.SCREENSHOT):
            photo_msg = await app.send_photo(config.CHAT_ID, config.SCREENSHOT, caption=f"🖼 <b>PROXIMITY GRID:</b> <code>{config.FILE_NAME}</code>", parse_mode=enums.ParseMode.HTML)
            os.remove(config.SCREENSHOT)

        report = (
            f"✅ <b>MISSION ACCOMPLISHED</b>\n\n"
            f"📄 <b>FILE:</b> <code>{config.FILE_NAME}</code>\n"
            f"⏱ <b>ENCODE TIME:</b> <code>{format_time(total_mission_time)}</code>\n"
            f"📦 <b>FINAL SIZE:</b> <code>{final_size:.2f} MB</code>\n"
            f"📊 <b>QUALITY:</b> VMAF: <code>{vmaf_val}</code> | SSIM: <code>{ssim_val}</code>\n\n"
            f"🛠 <b>ENCODE SPECS:</b>\n"
            f"└ <b>Preset:</b> {final_preset} | <b>CRF:</b> {final_crf}\n"
            f"└ <b>Video:</b> {res_label}{crop_label} | {hdr_label} | 10-bit{grain_label}\n"
            f"└ <b>Audio:</b> {config.AUDIO_MODE.upper()} @ {config.AUDIO_BITRATE}"
        )

        await app.edit_message_text(config.CHAT_ID, status.id, "🚀 <b>[ SYSTEM.UPLINK ] Transmitting Final Video to Telegram...</b>", parse_mode=enums.ParseMode.HTML)

        await app.send_document(
            chat_id=config.CHAT_ID, 
            document=config.FILE_NAME, 
            caption=report,
            parse_mode=enums.ParseMode.HTML,
            reply_to_message_id=photo_msg.id if photo_msg else None,
            progress=upload_progress,
            progress_args=(app, config.CHAT_ID, status, config.FILE_NAME)
        )
        
        try:
            await status.delete()
        except:
            pass

        for f in [config.SOURCE, config.FILE_NAME, config.LOG_FILE]:
            if os.path.exists(f): os.remove(f)

if __name__ == "__main__":
    asyncio.run(main())
