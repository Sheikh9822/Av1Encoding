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
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json", 
        "-show_streams", "-show_format", config.SOURCE
    ]
    res = json.loads(subprocess.check_output(cmd).decode())
    
    # Extract Video Stream
    video_stream = next(s for s in res['streams'] if s['codec_type'] == 'video')
    
    # Extract Audio Stream (Handle silent videos)
    audio_stream = next((s for s in res['streams'] if s['codec_type'] == 'audio'), {})
    channels = int(audio_stream.get('channels', 0))
    
    duration = float(res['format'].get('duration', 0))
    width = int(video_stream.get('width', 0))
    height = int(video_stream.get('height', 0))
    
    # Handle FPS calculation
    fps_raw = video_stream.get('r_frame_rate', '24/1')
    if '/' in fps_raw:
        n, d = fps_raw.split('/')
        fps_val = float(n) / float(d) if float(d) != 0 else 23.976
    else:
        fps_val = float(fps_raw)
        
    total_frames = int(video_stream.get('nb_frames', duration * fps_val))
    
    # Check for HDR (BT2020 color space)
    is_hdr = 'bt2020' in video_stream.get('color_primaries', 'bt709')
    
    return duration, width, height, is_hdr, total_frames, channels, fps_val

async def async_generate_grid(duration, target_file):
    """Generates a 3x3 grid of screenshots from the encoded file."""
    loop = asyncio.get_event_loop()
    def sync_grid():
        interval = duration / 10
        # Select frames at intervals, scale down, and tile them 3x3
        select_filter = "select='" + "+".join([f"between(t,{i*interval}-0.1,{i*interval}+0.1)" for i in range(1, 10)]) + "',setpts=N/FRAME_RATE/TB"
        cmd = [
            "ffmpeg", "-i", target_file, 
            "-vf", f"{select_filter},scale=480:-1,tile=3x3", 
            "-frames:v", "1", "-q:v", "3", config.SCREENSHOT, "-y"
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await loop.run_in_executor(None, sync_grid)

def get_crop_params(duration):
    """
    Smarter Cropping: Checks 4 points in the video (15%, 35%, 55%, 75%).
    Only applies crop if at least 3 samples agree (Consensus).
    """
    if duration < 10: return None

    test_points = [duration * 0.15, duration * 0.35, duration * 0.55, duration * 0.75]
    detected_crops = []

    for ts in test_points:
        time_str = time.strftime('%H:%M:%S', time.gmtime(ts))
        cmd = [
            "ffmpeg", "-skip_frame", "nokey", "-ss", time_str, "-i", config.SOURCE,
            "-vframes", "20", "-vf", "cropdetect=limit=24:round=2", "-f", "null", "-"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            found_at_ts = []
            for line in res.stderr.split('\n'):
                if "crop=" in line:
                    found_at_ts.append(line.split("crop=")[1].split(" ")[0])
            
            if found_at_ts:
                most_common_at_ts = Counter(found_at_ts).most_common(1)[0][0]
                detected_crops.append(most_common_at_ts)
        except: continue

    if not detected_crops: return None

    occurence_count = Counter(detected_crops)
    most_common_crop, count = occurence_count.most_common(1)[0]

    # Consensus Check: At least 3 out of 4 samples must match exactly
    if count >= 3:
        w, h, x, y = most_common_crop.split(':')
        if int(x) == 0 and int(y) == 0: return None # No black bars detected
        return most_common_crop
    
    return None

async def get_vmaf(output_file, crop_val, width, height, duration, fps, app, chat_id, status_msg):
    """Calculates VMAF and SSIM scores by sampling 30 seconds of video."""
    ref_w, ref_h = width, height
    if crop_val:
        try:
            parts = crop_val.split(':')
            ref_w, ref_h = parts[0], parts[1]
        except: pass

    # Sampling Logic: Take 6 segments of 5 seconds each distributed across the file
    interval = duration / 6
    select_parts = [f"between(t,{(i*interval)+(interval/2)-2.5},{(i*interval)+(interval/2)+2.5})" for i in range(6)]
    select_filter = f"select='{'+'.join(select_parts)}',setpts=N/FRAME_RATE/TB"
    total_vmaf_frames = int(30 * fps)

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

    cmd = [
        "ffmpeg", "-threads", "0", "-i", output_file, "-i", config.SOURCE, 
        "-filter_complex", filter_graph, "-progress", "pipe:1", "-nostats", "-f", "null", "-"
    ]
    
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
                        if now - last_update > 5:
                            elapsed = now - start_time
                            speed = curr_frame / elapsed if elapsed > 0 else 0
                            eta = (total_vmaf_frames - curr_frame) / speed if speed > 0 else 0
                            ui_text = get_vmaf_ui(percent, speed, eta)
                            try:
                                await app.edit_message_text(chat_id, status_msg.id, ui_text, parse_mode=enums.ParseMode.HTML)
                                last_update = now
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