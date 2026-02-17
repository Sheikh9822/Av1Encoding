import asyncio
import os
import subprocess
import time
import shutil
from pyrogram import Client, enums
from pyrogram.errors import FloodWait

import config
from media import get_video_info, get_crop_params, select_params, async_generate_grid, get_vmaf, upload_to_cloud, get_recommended_crf, extract_tg_thumb
from ui import get_encode_ui, format_time, upload_progress, get_failure_ui

async def main():
    if os.path.exists(config.SOURCE):
        total, used, free = shutil.disk_usage("/")
        if (os.path.getsize(config.SOURCE) * 2.2) > free:
            print("⚠️ DISK PRESSURE DETECTED.")

    try:
        duration, width, height, is_hdr, total_frames, channels, fps_val = get_video_info()
    except Exception as e:
        print(f"Metadata error: {e}"); return

    # 1. INTELLIGENT PRE-ANALYSIS
    async with Client(config.SESSION_NAME, api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN) as app:
        status = await app.send_message(config.CHAT_ID, f"📡 <b>[ SYSTEM.INIT ] Analyzing Source: {config.FILE_NAME}</b>", parse_mode=enums.ParseMode.HTML)
        
        final_crf = await get_recommended_crf(duration, height, fps_val, is_hdr)
        
        # PRESET SELECTION based on content
        if "movie" in config.FILE_NAME.lower() or height > 1080: final_preset = "4"
        else: final_preset = config.USER_PRESET if (config.USER_PRESET and config.USER_PRESET.strip()) else "6"

        crop_val = get_crop_params(duration)
        vf = []
        if crop_val: vf.append(f"crop={crop_val}")
        if config.USER_RES: vf.append(f"scale=-2:{config.USER_RES}")
        video_filters = ["-vf", ",".join(vf)] if vf else []

        # AUDIO: aformat fix + dynamic bitrate
        if channels == 0: audio_cmd = []
        else:
            calc_bitrate = config.AUDIO_BITRATE if channels <= 2 else "256k"
            audio_cmd = ["-c:a", "libopus", "-b:a", calc_bitrate, "-af", "aformat=channel_layouts=7.1|5.1|stereo"]

        # SVT-AV1 TUNING: Seekability (Keyint) + Metadata
        hdr_params = ":enable-hdr=1" if is_hdr else ""
        grain_val = int(config.USER_GRAIN) if config.USER_GRAIN.isdigit() else 0
        grain_params = f":film-grain={grain_val}" if grain_val > 0 else ""
        # keyint=10s of frames for better seeking in Telegram
        svtav1_tune = f"tune=0:aq-mode=2:scd=1:tile-columns=1:keyint={int(fps_val*10)}{hdr_params}{grain_params}"

        cmd = [
            "ffmpeg", "-i", config.SOURCE, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
            *video_filters,
            "-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le", "-crf", str(final_crf), "-preset", str(final_preset),
            "-svtav1-params", svtav1_tune, "-threads", "0",
            *audio_cmd, "-c:s", "copy",
            "-metadata", f"title=Encoded by AV1-Universal",
            "-metadata:s:v:0", f"title=AV1 (CRF {final_crf})",
            "-progress", "pipe:1", "-nostats", "-y", config.FILE_NAME
        ]

        # 2. ENCODE EXECUTION
        start_time, last_update = time.time(), 0
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        
        for line in process.stdout:
            if "out_time_ms" in line:
                curr_sec = int(line.split("=")[1]) / 1_000_000
                percent = (curr_sec / duration) * 100
                if time.time() - last_update > 8:
                    elapsed = time.time() - start_time
                    speed = curr_sec / elapsed if elapsed > 0 else 0
                    eta = (elapsed / percent) * (100 - percent) if percent > 0 else 0
                    fps = (percent / 100 * total_frames) / elapsed if elapsed > 0 else 0
                    ui = get_encode_ui(config.FILE_NAME, speed, fps, elapsed, eta, curr_sec, duration, percent, final_crf, final_preset, f"{height}p", " Cropped" if crop_val else "", "HDR" if is_hdr else "SDR", f" | G:{grain_val}" if grain_val else "", config.AUDIO_MODE, config.AUDIO_BITRATE, os.path.getsize(config.FILE_NAME)/1048576 if os.path.exists(config.FILE_NAME) else 0)
                    try: await app.edit_message_text(config.CHAT_ID, status.id, ui, parse_mode=enums.ParseMode.HTML); last_update = time.time()
                    except: pass
        process.wait()

        if process.returncode != 0:
            await app.edit_message_text(config.CHAT_ID, status.id, "❌ <b>MISSION FAILED.</b>", parse_mode=enums.ParseMode.HTML); return

        # 3. FINALIZING & THUMBNAIL
        await app.edit_message_text(config.CHAT_ID, status.id, "🛠️ <b>[ SYSTEM.OPTIMIZE ] Generating Metadata & Thumbnails...</b>", parse_mode=enums.ParseMode.HTML)
        
        thumb_file = await extract_tg_thumb(config.FILE_NAME, duration)
        grid_task = asyncio.create_task(async_generate_grid(duration, config.FILE_NAME))
        
        vmaf_val, _ = await get_vmaf(config.FILE_NAME, crop_val, width, height, duration, fps_val, app, config.CHAT_ID, status)
        await grid_task

        # 4. UPLINK
        final_size = os.path.getsize(config.FILE_NAME)/1048576
        report = (
            f"✅ <b>MISSION ACCOMPLISHED</b>\n\n"
            f"📄 <b>FILE:</b> <code>{config.FILE_NAME}</code>\n"
            f"📦 <b>SIZE:</b> <code>{final_size:.2f} MB</code>\n"
            f"📊 <b>QUALITY:</b> VMAF: <code>{vmaf_val}</code>\n"
            f"🛠 <b>SPECS:</b> AV1-P{final_preset} @ CRF {final_crf}"
        )

        await app.send_document(
            chat_id=config.CHAT_ID, document=config.FILE_NAME,
            thumb=thumb_file, caption=report,
            parse_mode=enums.ParseMode.HTML, progress=upload_progress,
            progress_args=(app, config.CHAT_ID, status, config.FILE_NAME)
        )
        
        # Cleanup
        for f in [config.SOURCE, config.FILE_NAME, thumb_file, config.SCREENSHOT]:
            if f and os.path.exists(f): os.remove(f)
        await status.delete()

if __name__ == "__main__":
    asyncio.run(main())