import asyncio
import os
import subprocess
import json
import time
from collections import Counter
from pyrogram import enums
from pyrogram.errors import FloodWait

import config
from ui import get_vmaf_ui

def get_video_info():
    """Extracts metadata from the source file using ffprobe."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", config.SOURCE]
    res = json.loads(subprocess.check_output(cmd).decode())
    video_stream = next(s for s in res['streams'] if s['codec_type'] == 'video')
    audio_streams = [s for s in res['streams'] if s['codec_type'] == 'audio']
    
    channels = int(audio_streams[0].get('channels', 0)) if audio_streams else 0
    duration = float(res['format'].get('duration', 0))
    width = int(video_stream.get('width', 0))
    height = int(video_stream.get('height', 0))
    fps_raw = video_stream.get('r_frame_rate', '24/1')
    fps_val = eval(fps_raw) if '/' in fps_raw else float(fps_raw)
    total_frames = int(video_stream.get('nb_frames', duration * fps_val))
    
    is_hdr = 'bt2020' in video_stream.get('color_primaries', 'bt709')
    return duration, width, height, is_hdr, total_frames, channels, fps_val

async def generate_thumbnail(target_file):
    """Generates a 320px thumbnail for Telegram preview."""
    thumb_path = "thumb.jpg"
    cmd = [
        "ffmpeg", "-i", target_file, "-ss", "00:00:05", 
        "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "2", thumb_path, "-y"
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return thumb_path if os.path.exists(thumb_path) else None

async def async_generate_grid(duration, target_file):
    """Generates a 3x3 grid of screenshots."""
    loop = asyncio.get_event_loop()
    def sync_grid():
        interval = duration / 10
        select_filter = "select='" + "+".join([f"between(t,{i*interval}-0.1,{i*interval}+0.1)" for i in range(1, 10)]) + "',setpts=N/FRAME_RATE/TB"
        cmd = ["ffmpeg", "-i", target_file, "-vf", f"{select_filter},scale=480:-1,tile=3x3", "-frames:v", "1", "-q:v", "3", config.SCREENSHOT, "-y"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await loop.run_in_executor(None, sync_grid)

def get_crop_params(duration):
    if duration < 10: return None
    test_points = [duration * 0.15, duration * 0.35, duration * 0.55, duration * 0.75]
    detected_crops = []
    for ts in test_points:
        time_str = time.strftime('%H:%M:%S', time.gmtime(ts))
        cmd = ["ffmpeg", "-skip_frame", "nokey", "-ss", time_str, "-i", config.SOURCE, "-vframes", "20", "-vf", "cropdetect=limit=24:round=2", "-f", "null", "-"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            found_at_ts = [line.split("crop=")[1].split(" ")[0] for line in res.stderr.split('\n') if "crop=" in line]
            if found_at_ts: detected_crops.append(Counter(found_at_ts).most_common(1)[0][0])
        except: continue
    if not detected_crops: return None
    occurence_count = Counter(detected_crops)
    most_common_crop, count = occurence_count.most_common(1)[0]
    if count >= 3:
        w, h, x, y = most_common_crop.split(':')
        if int(x) == 0 and int(y) == 0: return None
        return most_common_crop
    return None

async def get_vmaf(output_file, crop_val, width, height, duration, fps, app, chat_id, status_msg):
    ref_w, ref_h = width, height
    if crop_val:
        parts = crop_val.split(':')
        ref_w, ref_h = parts[0], parts[1]

    # Scene-aware sampling: 10 segments of 3 seconds distributed across file
    interval = duration / 10
    select_parts = [f"between(t,{(i*interval)+(interval/2)-1.5},{(i*interval)+(interval/2)+1.5})" for i in range(10)]
    select_filter = f"select='{'+'.join(select_parts)}',setpts=N/FRAME_RATE/TB"
    total_vmaf_frames = int(30 * fps)

    ref_filters = f"crop={crop_val},{select_filter}" if crop_val else select_filter
    dist_filters = f"{select_filter},scale={ref_w}:{ref_h}:flags=bicubic"

    filter_graph = f"[1:v]{ref_filters}[r];[0:v]{dist_filters}[d];[d]split=2[d1][d2];[r]split=2[r1][r2];[d1][r1]libvmaf;[d2][r2]ssim"
    cmd = ["ffmpeg", "-threads", "0", "-i", output_file, "-i", config.SOURCE, "-filter_complex", filter_graph, "-progress", "pipe:1", "-nostats", "-f", "null", "-"]
    
    vmaf_score, ssim_score = "N/A", "N/A"
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        last_update = 0
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
                        if time.time() - last_update > 5:
                            ui_text = get_vmaf_ui(percent, 0, 0) # Simplified
                            await app.edit_message_text(chat_id, status_msg.id, ui_text, parse_mode=enums.ParseMode.HTML)
                            last_update = time.time()
                    except: pass
        async def read_stderr():
            nonlocal vmaf_score, ssim_score
            while True:
                line = await proc.stderr.readline()
                if not line: break
                line_str = line.decode('utf-8', errors='ignore').strip()
                if "VMAF score:" in line_str: vmaf_score = line_str.split("VMAF score:")[1].strip()
                if "SSIM Y:" in line_str and "All:" in line_str: ssim_score = line_str.split("All:")[1].split(" ")[0]
        await asyncio.gather(read_progress(), read_stderr())
        await proc.wait()
        return vmaf_score, ssim_score
    except: return "N/A", "N/A"

def select_params(height):
    if height >= 2000: return 28, 8
    elif height >= 1000: return 42, 6
    return 32, 4

async def upload_to_cloud(filepath):
    cmd = ["curl", "-H", "Max-Days: 3", "--upload-file", filepath, f"https://transfer.sh/{os.path.basename(filepath)}"]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()