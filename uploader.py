import asyncio
import os
import subprocess
import time
import json
from datetime import timedelta
from pyrogram import Client, enums
from pyrogram.errors import FloodWait

[span_0](start_span)SOURCE = "source.mkv"[span_0](end_span)
[span_1](start_span)SCREENSHOT = "grid_preview.jpg"[span_1](end_span)
[span_2](start_span)LOG_FILE = "encode_log.txt"[span_2](end_span)
[span_3](start_span)CANCELLED = False[span_3](end_span)
[span_4](start_span)PROCESS = None[span_4](end_span)

# ---------- TOOLS & ANALYTICS ----------

def get_video_info():
    [span_5](start_span)cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", SOURCE][span_5](end_span)
    [span_6](start_span)res = json.loads(subprocess.check_output(cmd).decode())[span_6](end_span)
    [span_7](start_span)video_stream = next(s for s in res['streams'] if s['codec_type'] == 'video')[span_7](end_span)
    [span_8](start_span)duration = float(res['format'].get('duration', 0))[span_8](end_span)
    [span_9](start_span)height = int(video_stream.get('height', 0))[span_9](end_span)
    [span_10](start_span)fps_raw = video_stream.get('r_frame_rate', '24/1')[span_10](end_span)
    [span_11](start_span)fps_val = eval(fps_raw) if '/' in fps_raw else float(fps_raw)[span_11](end_span)
    [span_12](start_span)total_frames = int(video_stream.get('nb_frames', duration * fps_val))[span_12](end_span)
    [span_13](start_span)is_hdr = 'bt2020' in video_stream.get('color_primaries', 'bt709')[span_13](end_span)
    [span_14](start_span)return duration, height, is_hdr, total_frames[span_14](end_span)

def generate_progress_bar(percentage):
    [span_15](start_span)total_segments = 15[span_15](end_span)
    [span_16](start_span)completed = int((max(0, min(100, percentage)) / 100) * total_segments)[span_16](end_span)
    [span_17](start_span)return "[" + "▰" * completed + "▱" * (total_segments - completed) + "]"[span_17](end_span)

def format_time(seconds):
    [span_18](start_span)return str(timedelta(seconds=int(seconds))).zfill(8)[span_18](end_span)

async def async_generate_grid(duration):
    [span_19](start_span)loop = asyncio.get_event_loop()[span_19](end_span)
    def sync_grid():
        [span_20](start_span)interval = duration / 10[span_20](end_span)
        [span_21](start_span)select_filter = "select='" + "+".join([f"between(t,{i*interval}-0.1,{i*interval}+0.1)" for i in range(1, 10)]) + "',setpts=N/FRAME_RATE/TB"[span_21](end_span)
        [span_22](start_span)cmd = ["ffmpeg", "-i", SOURCE, "-vf", f"{select_filter},scale=480:-1,tile=3x3", "-frames:v", "1", "-q:v", "3", SCREENSHOT, "-y"][span_22](end_span)
        [span_23](start_span)subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)[span_23](end_span)
    [span_24](start_span)await loop.run_in_executor(None, sync_grid)[span_24](end_span)

def get_ssim(output_file):
    [span_25](start_span)cmd = ["ffmpeg", "-i", output_file, "-i", SOURCE, "-filter_complex", "ssim", "-f", "null", "-"][span_25](end_span)
    try:
        [span_26](start_span)res = subprocess.run(cmd, capture_output=True, text=True)[span_26](end_span)
        [span_27](start_span)for line in res.stderr.split('\n'):[span_27](end_span)
            [span_28](start_span)if "All:" in line: return line.split("All:")[1].split(" ")[0][span_28](end_span)
    [span_29](start_span)except: return "N/A"[span_29](end_span)

def select_params(height):
    [span_30](start_span)if height >= 2000: return 32, 10[span_30](end_span)
    [span_31](start_span)elif height >= 1000: return 42, 6[span_31](end_span)
    [span_32](start_span)elif height >= 700: return 24, 6[span_32](end_span)
    [span_33](start_span)return 22, 4[span_33](end_span)

# ---------- UPLOAD CALLBACK ----------

[span_34](start_span)last_up_update = 0[span_34](end_span)

async def upload_progress(current, total, app, chat_id, status_msg, file_name):
    [span_35](start_span)global last_up_update[span_35](end_span)
    [span_36](start_span)now = time.time()[span_36](end_span)
    
    [span_37](start_span)if now - last_up_update < 10:[span_37](end_span)
        return
        
    [span_38](start_span)percent = (current / total) * 100[span_38](end_span)
    [span_39](start_span)bar = generate_progress_bar(percent)[span_39](end_span)
    [span_40](start_span)cur_mb = current / (1024 * 1024)[span_40](end_span)
    [span_41](start_span)tot_mb = total / (1024 * 1024)[span_41](end_span)
    
    scifi_up_ui = (
        f"<code>┌─── 🛰️ [ SYSTEM.UPLINK.ACTIVE ] ───┐\n"
        f"│                                    \n"
        f"│ 📂 FILE: {file_name}\n"
        f"│ 📊 PROG: {bar} {percent:.1f}%\n"
        f"│ 📦 SIZE: {cur_mb:.2f} / {tot_mb:.2f} MB\n"
        f"│ 📡 STATUS: Transmitting to Orbit... \n"
        f"│                                    \n"
        f"└────────────────────────────────────┘</code>"
    [span_42](start_span))
    
    try:
        await app.edit_message_text(chat_id, status_msg.id, scifi_up_ui, parse_mode=enums.ParseMode.HTML)[span_42](end_span)
        [span_43](start_span)last_up_update = now[span_43](end_span)
    except:
        [span_44](start_span)pass[span_44](end_span)

# ---------- MAIN PROCESS ----------

async def main():
    [span_45](start_span)global CANCELLED, PROCESS[span_45](end_span)

    [span_46](start_span)api_id, api_hash = int(os.getenv("API_ID")), os.getenv("API_HASH")[span_46](end_span)
    [span_47](start_span)bot_token, chat_id = os.getenv("BOT_TOKEN"), int(os.getenv("CHAT_ID"))[span_47](end_span)
    [span_48](start_span)file_name = os.getenv("FILE_NAME")[span_48](end_span)
    
    [span_49](start_span)u_res = os.getenv("USER_RES")[span_49](end_span)
    [span_50](start_span)u_crf_raw, u_preset_raw = os.getenv("USER_CRF"), os.getenv("USER_PRESET")[span_50](end_span)
    [span_51](start_span)u_audio, u_bitrate = os.getenv("AUDIO_MODE", "opus"), os.getenv("AUDIO_BITRATE", "128k")[span_51](end_span)
    [span_52](start_span)run_vmaf = os.getenv("RUN_VMAF", "true").lower() == "true"[span_52](end_span)

    try:
        [span_53](start_span)duration, height, is_hdr, total_frames = get_video_info()[span_53](end_span)
    except Exception as e:
        [span_54](start_span)print(f"Metadata error: {e}")[span_54](end_span)
        return

    [span_55](start_span)def_crf, def_preset = select_params(height)[span_55](end_span)
    [span_56](start_span)final_crf = u_crf_raw if (u_crf_raw and u_crf_raw.strip()) else def_crf[span_56](end_span)
    [span_57](start_span)final_preset = u_preset_raw if (u_preset_raw and u_preset_raw.strip()) else def_preset[span_57](end_span)
    
    [span_58](start_span)res_label = u_res if u_res else f"{height}p"[span_58](end_span)
    [span_59](start_span)hdr_label = "HDR10" if is_hdr else "SDR"[span_59](end_span)
    
    [span_60](start_span)scale_filter = ["-vf", f"scale=-2:{u_res}"] if u_res else [][span_60](end_span)
    [span_61](start_span)audio_cmd = ["-c:a", "libopus", "-b:a", u_bitrate] if u_audio == "opus" else ["-c:a", "copy"][span_61](end_span)
    [span_62](start_span)hdr_params = ":enable-hdr=1" if is_hdr else ""[span_62](end_span)

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        # --- ROBUST SATELLITE LINK ---
        try:
            [span_63](start_span)status = await app.send_message(chat_id, "📡 <b>[ SYSTEM BOOT ] Initializing Satellite Link...</b>", parse_mode=enums.ParseMode.HTML)[span_63](end_span)
        except FloodWait as e:
            print(f"⚠️ FloodWait: Waiting {e.value} seconds.")
            await asyncio.sleep(e.value + 2)
            status = await app.send_message(chat_id, "📡 <b>[ SYSTEM RECOVERY ] Link Re-established...</b>", parse_mode=enums.ParseMode.HTML)

        [span_64](start_span)grid_task = asyncio.create_task(async_generate_grid(duration))[span_64](end_span)

        cmd = [
            [span_65](start_span)"ffmpeg", "-i", SOURCE, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",[span_65](end_span)
            *[span_66](start_span)scale_filter,[span_66](end_span)
            [span_67](start_span)"-c:v", "libsvtav1", "-pix_fmt", "yuv420p10le",[span_67](end_span)
            [span_68](start_span)"-crf", str(final_crf), "-preset", str(final_preset),[span_68](end_span)
            [span_69](start_span)"-svtav1-params", f"tune=0:aq-mode=2:enable-overlays=1:scd=1:enable-tpl-la=1{hdr_params}",[span_69](end_span)
            *[span_70](start_span)audio_cmd, "-c:s", "copy",[span_70](end_span)
            [span_71](start_span)"-progress", "pipe:1", "-nostats", "-y", file_name[span_71](end_span)
        ]

        [span_72](start_span)start_time, last_update = time.time(), 0[span_72](end_span)

        [span_73](start_span)with open(LOG_FILE, "w") as f_log:[span_73](end_span)
            [span_74](start_span)PROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)[span_74](end_span)
            [span_75](start_span)for line in PROCESS.stdout:[span_75](end_span)
                [span_76](start_span)f_log.write(line)[span_76](end_span)
                [span_77](start_span)if CANCELLED: break[span_77](end_span)
                [span_78](start_span)if "out_time_ms" in line:[span_78](end_span)
                    try:
                        [span_79](start_span)curr_sec = int(line.split("=")[1]) / 1_000_000[span_79](end_span)
                        [span_80](start_span)percent = (curr_sec / duration) * 100[span_80](end_span)
                        [span_81](start_span)elapsed = time.time() - start_time[span_81](end_span)
                        [span_82](start_span)speed = curr_sec / elapsed if elapsed > 0 else 0[span_82](end_span)
                        [span_83](start_span)fps = (percent / 100 * total_frames) / elapsed if elapsed > 0 else 0[span_83](end_span)
                        [span_84](start_span)eta = (elapsed / percent) * (100 - percent) if percent > 0 else 0[span_84](end_span)
                        
                        [span_85](start_span)if time.time() - last_update > 25:[span_85](end_span)
                            [span_86](start_span)bar = generate_progress_bar(percent)[span_86](end_span)
                            [span_87](start_span)size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0[span_87](end_span)
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
                                f"│ 🔊 AUDIO: Opus @ {u_bitrate}\n"
                                f"│ 📦 SIZE: {size:.2f} MB\n"
                                f"│                                    \n"
                                f"└────────────────────────────────────┘</code>"
                            [span_88](start_span))
                            await app.edit_message_text(chat_id, status.id, scifi_ui, parse_mode=enums.ParseMode.HTML)[span_88](end_span)
                            [span_89](start_span)last_update = time.time()[span_89](end_span)
                    [span_90](start_span)except: continue[span_90](end_span)

        [span_91](start_span)PROCESS.wait()[span_91](end_span)
        [span_92](start_span)total_mission_time = time.time() - start_time[span_92](end_span)
        [span_93](start_span)await grid_task[span_93](end_span)

        [span_94](start_span)if PROCESS.returncode != 0:[span_94](end_span)
            [span_95](start_span)await app.send_document(chat_id, LOG_FILE, caption="❌ <b>CRITICAL ERROR: Core Failure</b>", parse_mode=enums.ParseMode.HTML)[span_95](end_span)
            return

        [span_96](start_span)await app.edit_message_text(chat_id, status.id, "🛠️ <b>[ SYSTEM.OPTIMIZE ] Finalizing Metadata...</b>", parse_mode=enums.ParseMode.HTML)[span_96](end_span)
        [span_97](start_span)fixed_file = f"FIXED_{file_name}"[span_97](end_span)
        [span_98](start_span)remux_cmd = ["ffmpeg", "-i", file_name, "-c", "copy", "-map_metadata", "0", fixed_file, "-y"][span_98](end_span)
        [span_99](start_span)subprocess.run(remux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)[span_99](end_span)
        [span_100](start_span)if os.path.exists(fixed_file):[span_100](end_span)
            [span_101](start_span)os.remove(file_name)[span_101](end_span)
            [span_102](start_span)os.rename(fixed_file, file_name)[span_102](end_span)

        [span_103](start_span)ssim_val = get_ssim(file_name) if run_vmaf else "N/A"[span_103](end_span)
        [span_104](start_span)final_size = os.path.getsize(file_name)/(1024*1024) if os.path.exists(file_name) else 0[span_104](end_span)
        
        [span_105](start_span)if os.path.exists(SCREENSHOT):[span_105](end_span)
            [span_106](start_span)await app.send_photo(chat_id, SCREENSHOT, caption=f"🖼 <b>PROXIMITY GRID:</b> <code>{file_name}</code>", parse_mode=enums.ParseMode.HTML)[span_106](end_span)
            [span_107](start_span)os.remove(SCREENSHOT)[span_107](end_span)

        report = (
            f"✅ <b>MISSION ACCOMPLISHED</b>\n\n"
            f"📄 <b>FILE:</b> <code>{file_name}</code>\n"
            f"⏱ <b>ENCODE TIME:</b> <code>{format_time(total_mission_time)}</code>\n"
            f"📦 <b>FINAL SIZE:</b> <code>{final_size:.2f} MB</code>\n"
            f"📊 <b>SSIM:</b> <code>{ssim_val}</code>\n\n"
            f"🛠 <b>ENCODE SPECS:</b>\n"
            f"└ <b>Preset:</b> {final_preset} | <b>CRF:</b> {final_crf}\n"
            f"└ <b>Video:</b> {res_label} | {hdr_label} | 10-bit\n"
            f"└ <b>Audio:</b> Opus @ {u_bitrate}"
        [span_108](start_span))

        await app.send_document(
            chat_id=chat_id, 
            document=file_name, 
            caption=report,
            parse_mode=enums.ParseMode.HTML,
            progress=upload_progress,
            progress_args=(app, chat_id, status, file_name)
        )[span_108](end_span)
        
        try:
            [span_109](start_span)await status.delete()[span_109](end_span)
        except:
            [span_110](start_span)pass[span_110](end_span)

        [span_111](start_span)for f in [SOURCE, file_name, LOG_FILE]:[span_111](end_span)
            [span_112](start_span)if os.path.exists(f): os.remove(f)[span_112](end_span)

if __name__ == "__main__":
    [span_113](start_span)asyncio.run(main())[span_113](end_span)
