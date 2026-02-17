import asyncio
import os
import subprocess
import json
import time
from collections import Counter
from pyrogram import enums
import config

def get_video_info():
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", config.SOURCE]
    res = json.loads(subprocess.check_output(cmd).decode())
    video_stream = next(s for s in res['streams'] if s['codec_type'] == 'video')
    
    # Accurate Channel Detection for Metadata
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

async def get_recommended_crf(duration, height, fps_val, is_hdr):
    """Runs a 30s test encode to find CRF hitting ~95 VMAF."""
    if config.USER_CRF and config.USER_CRF.strip():
        return config.USER_CRF

    test_ss = duration * 0.4
    test_out = "probe_test.mkv"
    # Faster preset for the probe
    probe_preset = "8"
    base_crf = 32 if height >= 1080 else 28
    
    hdr_p = ":enable-hdr=1" if is_hdr else ""
    # Test Command
    cmd = [
        "ffmpeg", "-ss", str(test_ss), "-t", "30", "-i", config.SOURCE,
        "-c:v", "libsvtav1", "-preset", probe_preset, "-crf", str(base_crf),
        "-svtav1-params", f"tune=0:scd=1{hdr_p}", "-an", "-sn", "-y", test_out
    ]
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await proc.wait()
    
    # Calculate VMAF of the probe
    vmaf_score, _ = await get_vmaf(test_out, None, 0, 0, 30, fps_val, None, None, None, is_probe=True)
    
    try: os.remove(test_out)
    except: pass

    # Intelligent adjustment logic
    try:
        score = float(vmaf_score)
        if score > 97: return str(base_crf + 4) # Too high quality, save space
        if score > 95.5: return str(base_crf + 2)
        if score < 93: return str(base_crf - 3)  # Too low, boost quality
        return str(base_crf)
    except:
        return str(base_crf)

async def extract_tg_thumb(target_file, duration):
    """Extracts a 320px thumbnail for Telegram's video player."""
    thumb_path = "thumb.jpg"
    cmd = [
        "ffmpeg", "-ss", str(duration * 0.3), "-i", target_file,
        "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "4", thumb_path, "-y"
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await proc.wait()
    return thumb_path if os.path.exists(thumb_path) else None

async def get_vmaf(output_file, crop_val, width, height, duration, fps, app, chat_id, status_msg, is_probe=False):
    # Use simpler filter for the probe to save time
    if is_probe:
        filter_graph = "[0:v][1:v]libvmaf"
        ref_input = config.SOURCE
        # We need to seek the reference to match the 30s clip
        cmd = ["ffmpeg", "-i", output_file, "-ss", str(duration * 0.4), "-t", "30", "-i", ref_input, "-filter_complex", filter_graph, "-f", "null", "-"]
    else:
        # High precision scene sampling for the final report
        interval = duration / 8
        select_parts = [f"between(t,{(i*interval)+5},{(i*interval)+10})" for i in range(8)]
        select_filter = f"select='{'+'.join(select_parts)}',setpts=N/FRAME_RATE/TB"
        total_vmaf_frames = int(40 * fps)
        
        ref_filters = f"crop={crop_val},{select_filter}" if crop_val else select_filter
        dist_filters = f"{select_filter},scale={width}:{height}:flags=bicubic"
        filter_graph = f"[1:v]{ref_filters}[r];[0:v]{dist_filters}[d];[d][r]libvmaf"
        cmd = ["ffmpeg", "-i", output_file, "-i", config.SOURCE, "-filter_complex", filter_graph, "-nostats", "-f", "null", "-"]

    vmaf_score = "N/A"
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, stderr = await proc.communicate()
    
    for line in stderr.decode().split('\n'):
        if "VMAF score:" in line:
            vmaf_score = line.split("VMAF score:")[1].strip()
            
    return vmaf_score, "N/A"

# Existing helper functions (get_crop_params, select_params, etc) remain exactly as they were
def get_crop_params(duration):
    if duration < 10: return None
    test_points = [duration * 0.15, duration * 0.35, duration * 0.55, duration * 0.75]
    detected_crops = []
    for ts in test_points:
        time_str = time.strftime('%H:%M:%S', time.gmtime(ts))
        cmd = ["ffmpeg", "-skip_frame", "nokey", "-ss", time_str, "-i", config.SOURCE, "-vframes", "20", "-vf", "cropdetect=limit=24:round=2", "-f", "null", "-"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            for line in res.stderr.split('\n'):
                if "crop=" in line:
                    detected_crops.append(line.split("crop=")[1].split(" ")[0])
        except: continue
    if not detected_crops: return None
    occurence_count = Counter(detected_crops)
    most_common_crop, count = occurence_count.most_common(1)[0]
    return most_common_crop if count >= 3 else None

def select_params(height):
    if height >= 2000: return 28, 10
    elif height >= 1000: return 42, 6
    elif height >= 700: return 32, 6
    return 24, 4

async def upload_to_cloud(filepath):
    cmd = ["curl", "-H", "Max-Days: 3", "--upload-file", filepath, f"https://transfer.sh/{os.path.basename(filepath)}"]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()

async def async_generate_grid(duration, target_file):
    interval = duration / 10
    select_filter = "select='" + "+".join([f"between(t,{i*interval}-0.1,{i*interval}+0.1)" for i in range(1, 10)]) + "',setpts=N/FRAME_RATE/TB"
    cmd = ["ffmpeg", "-i", target_file, "-vf", f"{select_filter},scale=480:-1,tile=3x3", "-frames:v", "1", "-q:v", "3", config.SCREENSHOT, "-y"]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await proc.wait()