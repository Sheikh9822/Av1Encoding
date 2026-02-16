import asyncio
import subprocess
import time
from pyrogram import enums
from pyrogram.errors import FloodWait
from config import SOURCE_FILE
from helpers import generate_progress_bar, format_time

async def get_vmaf(output_file, crop_val, width, height, duration=0, fps=24, app=None, chat_id=None, status_msg=None):
    """Calculates VMAF and SSIM scores."""
    
    ref_w, ref_h = width, height
    if crop_val:
        try:
            parts = crop_val.split(':')
            ref_w, ref_h = parts[0], parts[1]
        except: pass

    # Limit to 30 seconds to save time if video is long
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

    cmd = ["ffmpeg", "-threads", "0", "-i", output_file, "-i", SOURCE_FILE, "-filter_complex", filter_graph, "-progress", "pipe:1", "-nostats", "-f", "null", "-"]
    
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
                            bar = generate_progress_bar(percent)
                            
                            ui = (
                                f"<code>┌─── 🧠 [ SYSTEM.ANALYSIS ] ───┐\n"
                                f"│                                    \n"
                                f"│ 🔬 METRICS: VMAF + SSIM (30s)\n"
                                f"│ 📊 PROG: {bar} {percent:.1f}%\n"
                                f"│ ⚡ SPEED: {speed:.1f} FPS\n"
                                f"│ ⏳ ETA: {format_time(eta)}\n"
                                f"│                                    \n"
                                f"└────────────────────────────────────┘</code>"
                            )
                            try:
                                await app.edit_message_text(chat_id, status_msg.id, ui, parse_mode=enums.ParseMode.HTML)
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