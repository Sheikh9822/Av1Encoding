from datetime import timedelta
import time
from pyrogram import enums
from pyrogram.errors import FloodWait
import asyncio

def generate_progress_bar(percentage):
    total_segments = 15
    completed = int((max(0, min(100, percentage)) / 100) * total_segments)
    return "[" + "▰" * completed + "▱" * (total_segments - completed) + "]"

def format_time(seconds):
    return str(timedelta(seconds=int(seconds))).zfill(8)

async def upload_progress(current, total, app, chat_id, status_msg, file_name, upload_state):
    now = time.time()
    
    if now - upload_state['last_update'] < 4:
        return
        
    percent = (current / total) * 100
    bar = generate_progress_bar(percent)
    cur_mb = current / (1024 * 1024)
    tot_mb = total / (1024 * 1024)
    
    scifi_up_ui = (
        f"<code>┌─── 🛰️ [ SYSTEM.UPLINK.ACTIVE ] ───┐\n"
        f"│                                    \n"
        f"│ 📂 FILE: {file_name}\n"
        f"│ 📊 PROG: {bar} {percent:.1f}%\n"
        f"│ 📦 SIZE: {cur_mb:.2f} / {tot_mb:.2f} MB\n"
        f"│ 📡 STATUS: Transmitting to Orbit... \n"
        f"│                                    \n"
        f"└────────────────────────────────────┘</code>"
    )
    
    try:
        await app.edit_message_text(chat_id, status_msg.id, scifi_up_ui, parse_mode=enums.ParseMode.HTML)
        upload_state['last_update'] = now
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception:
        pass

async def vmaf_progress(percent, speed, eta, app, chat_id, status_msg, vmaf_state):
    now = time.time()
    
    if now - vmaf_state['last_update'] > 5:
        bar = generate_progress_bar(percent)
        ui_text = (
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
            await app.edit_message_text(chat_id, status_msg.id, ui_text, parse_mode=enums.ParseMode.HTML)
            vmaf_state['last_update'] = now
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            pass
