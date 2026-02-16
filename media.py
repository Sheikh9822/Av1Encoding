import subprocess
import json
import asyncio
from config import SOURCE_FILE, SCREENSHOT_FILE

def get_video_info():
    """Extracts metadata using ffprobe."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", SOURCE_FILE]
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

def get_crop_params():
    """Detects black bars."""
    cmd = [
        "ffmpeg", "-skip_frame", "nokey", "-ss", "00:01:00", "-i", SOURCE_FILE,
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

async def async_generate_grid(duration, target_file):
    """Generates a 3x3 screenshot grid."""
    loop = asyncio.get_event_loop()
    def sync_grid():
        interval = duration / 10
        select_filter = "select='" + "+".join([f"between(t,{i*interval}-0.1,{i*interval}+0.1)" for i in range(1, 10)]) + "',setpts=N/FRAME_RATE/TB"
        cmd = ["ffmpeg", "-i", target_file, "-vf", f"{select_filter},scale=480:-1,tile=3x3", "-frames:v", "1", "-q:v", "3", SCREENSHOT_FILE, "-y"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await loop.run_in_executor(None, sync_grid)