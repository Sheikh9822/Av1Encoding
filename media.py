import asyncio
import os
import subprocess
import json
import time
from pyrogram import enums
from pyrogram.errors import FloodWait

import config
from ui import get_vmaf_ui

def get_video_info():
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", config.SOURCE]
    res = json.loads(subprocess.check_output(cmd).decode())
    video_stream = next(s for s in res['streams'] if s['codec_type'] == 'video')
    audio_stream = next((s for s in res['streams'] if s['codec_type'] == 'audio'), {})
    
    channels = int(audio_stream.get('channels', 0))
    duration = float(res['format'].get('duration', 0))
    width = int(video_stream.get('width', 0))
    height = int(video_stream.get('height', 0))
    fps_raw = video_stream.get('r_frame_rate', '24/1')
    fps_val = eval(fps_raw) if '/' in fps_raw else float(fps_raw)
    total_frames = int(video_stream.get('nb_frames', duration * fps_val))
    is_hdr = 'bt2020' in video_stream.get('color_primaries', 'bt709')
    
    return duration, width, height, is_hdr, total_frames, channels, fps_val

async def async_generate_grid(duration, target_file):
    loop = asyncio.get_event_loop()
    def sync_grid():
        interval = duration / 10
        select_filter = "select='" + "+".join([f"between(t,{i*interval}-0.1,{i*interval}+0.1)" for i in range(1, 10)]) + "',setpts=N/FRAME_RATE/TB"
        cmd = ["ffmpeg", "-i", target_file, "-vf", f"{select_filter},scale=480:-1,tile=3x3", "-frames:v", "1", "-q:v", "3", config.SCREENSHOT, "-y"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await loop.run_in_executor(None, sync_grid)

async def get_vmaf(output_file, crop_val, width, height, duration=0, fps=24, app=None, chat_id=None, status_msg=None):
    ref_w, ref_h = width, height
    if crop_val:
        try:
            parts = crop_val.split(':')
            ref_w, ref_h = parts[0], parts[1]
        except: pass

    if duration > 30:
        interval = duration / 6
        select_parts = []
        for i in range(6):
            start_t = (i * interval) + (interval / 2) - 2.5
            end_t = start_t + 5
            select_parts.append(f"between(t,{start_t},{end_t})")
        
        select_expr = "+".join(select_parts)
        select_filter = f"select='{select_expr}',setpts=N/FRAME_RATE/TB"
        total_vmaf_frames = int(30 * fps)
    else:
        select_filter = "setpts=N/FRAME_RATE/TB"
        total_vmaf_frames = int(duration * fps)

    ref_filters = f"crop={crop_val},{select_filter}" if crop_val else select_filter
    dist_filters = f"{select_filter},scale={ref_w}:{ref_h}:flags=bicubic"

    filter_graph = (
        f"[1:v]{ref_filters}[r];"
        f"[0:v]{dist_filters}[d];"
        f"[d]split=2[d1][d2];"
        f"[r]split=2[r1][r2];"
        f"[d1][r1]libvmaf;"
        f"[d2][r2]ssim"
    )

    cmd = ["ffmpeg", "-threads", "0", "-i", output_file, "-i", config.SOURCE, "-filter_complex", filter_graph, "-progress", "pipe:1", "-nostats", "-f", "null", "-"]
    
    vmaf_score, ssim_score = "N/A", "N/A"
    
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        start_time, last_update = time.time(), 0
        
        async def read_progress():
            nonlocal last_update
            while True:
                line = await proc.stdout.readline()
                if not line: break
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
                            
                            ui_text = get_vmaf_ui(percent, speed, eta)
                            
                            try:
                                await app.edit_message_text(chat_id, status_msg.id, ui_text, parse_mode=enums.ParseMode.HTML)
                                last_update = now
                            except FloodWait as e: await asyncio.sleep(e.value)
                            except: pass
                    except: pass

        async def read_stderr():
            nonlocal vmaf_score, ssim_score
            while True:
                line = await proc.stderr.readline()
                if not line: break
                line_str = line.decode('utf-8', errors='ignore').strip()
                
                if "VMAF score:" in line_str:
                    vmaf_score = line_str.split("VMAF score:")[1].strip()
                if "SSIM Y:" in line_str and "All:" in line_str:
                    try: ssim_score = line_str.split("All:")[1].split(" ")[0]
                    except: pass

        await asyncio.gather(read_progress(), read_stderr())
        await proc.wait()
        return vmaf_score, ssim_score
        
    except Exception as e:
        print(f"Metrics Capture Error: {e}")
        return "N/A", "N/A"

def get_crop_params():
    cmd = [
        "ffmpeg", "-skip_frame", "nokey", "-ss", "00:01:00", "-i", config.SOURCE,
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
