"""
tg_rename.py — Download from Telegram, rename using structured format, upload back.

No re-encoding. Just:
  1. Download source file from Telegram
  2. ffprobe → detect quality, audio tracks, subtitle tracks
  3. Build structured output filename via rename.py logic
  4. mkvmerge remux (preserve metadata, apply new filename)
  5. Upload renamed file back to Telegram with a full track report

Environment variables (set by rename.yml):
  TG_API_ID, TG_API_HASH, TG_BOT_TOKEN, TG_CHAT_ID
  VIDEO_URL          — tg_file:<id>|<name>  or  https://t.me/...
  ANIME_NAME         — e.g. "Medalist"
  SEASON             — e.g. "2"
  EPISODE            — e.g. "7"
  AUDIO_TYPE         — Auto | Sub | Dual | Tri | Multi
  CONTENT_TYPE       — Anime | Donghua | Hentai | HMV | AMV | custom
  SUB_TRACKS         — user-supplied subtitle labels e.g. "English, Arabic"
  AUDIO_TRACKS       — user-supplied audio labels   e.g. "Japanese, English (Dub)"
  GITHUB_RUN_NUMBER  — for lane resolution
"""

import asyncio
import os
import subprocess
import sys
import time
import traceback

from pyrogram import Client, enums
from pyrogram.errors import FloodWait

from rename import (
    get_track_info, detect_audio_type, detect_quality,
    build_output_name, format_track_report
)
from ui import get_download_ui, upload_progress, format_time
import ui as _ui

# ── ENV ───────────────────────────────────────────────────────────────────────

API_ID       = int(os.getenv("TG_API_ID",   "0").strip())
API_HASH     = os.getenv("TG_API_HASH",     "").strip()
BOT_TOKEN    = os.getenv("TG_BOT_TOKEN",    "").strip()
CHAT_ID      = int(os.getenv("TG_CHAT_ID",  "0").strip())
VIDEO_URL    = os.getenv("VIDEO_URL",        "").strip()

ANIME_NAME   = os.getenv("ANIME_NAME",      "").strip()
SEASON       = os.getenv("SEASON",          "1").strip()
EPISODE      = os.getenv("EPISODE",         "1").strip()
AUDIO_TYPE   = os.getenv("AUDIO_TYPE",      "Auto").strip()
CONTENT_TYPE = os.getenv("CONTENT_TYPE",    "Anime").strip()
SUB_TRACKS   = os.getenv("SUB_TRACKS",      "").strip()
AUDIO_TRACKS = os.getenv("AUDIO_TRACKS",    "").strip()

SOURCE_FILE  = "rename_source.mkv"
SCREENSHOT   = "grid_preview.jpg"

# ── LANE RESOLUTION ───────────────────────────────────────────────────────────

def resolve_lane() -> str:
    run_number = int(os.getenv("GITHUB_RUN_NUMBER", "0"))
    d = run_number % 10
    if d in (0, 3, 6, 9): return "A"
    if d in (1, 4, 7):    return "B"
    return "C"

# ── TELEGRAM HELPERS ──────────────────────────────────────────────────────────

async def tg_edit(app, chat_id, msg_id, text, reply_markup=None):
    try:
        kwargs = dict(parse_mode=enums.ParseMode.HTML)
        if reply_markup:
            kwargs["reply_markup"] = reply_markup
        await app.edit_message_text(chat_id, msg_id, text, **kwargs)
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
    except Exception:
        pass

async def dl_progress(current, total, app, chat_id, status_msg, start_time):
    if total <= 0: return
    pct = (current / total) * 100
    milestone = int(pct // 5) * 5
    if not hasattr(dl_progress, "last_pct"): dl_progress.last_pct = -1
    if milestone <= dl_progress.last_pct: return
    dl_progress.last_pct = milestone
    elapsed    = time.time() - start_time
    speed_mb   = (current / elapsed / 1_048_576) if elapsed > 0 else 0
    size_mb    = total / 1_048_576
    eta        = (total - current) / (current / elapsed) if current > 0 and elapsed > 0 else 0
    await tg_edit(app, chat_id, status_msg.id,
                  get_download_ui(pct, speed_mb, size_mb, elapsed, eta))

# ── DOWNLOAD FROM TELEGRAM ────────────────────────────────────────────────────

async def download_from_tg(app, status_msg) -> str:
    """Download the source file. Returns the original filename."""
    start   = time.time()
    dl_progress.last_pct = -1

    if VIDEO_URL.startswith("tg_file:"):
        raw = VIDEO_URL.replace("tg_file:", "")
        file_id, orig_name = (raw.split("|", 1) if "|" in raw else (raw, "source.mkv"))
        await app.download_media(
            message=file_id.strip(), file_name=SOURCE_FILE,
            progress=dl_progress, progress_args=(app, CHAT_ID, status_msg, start)
        )
        return orig_name

    if "t.me/" in VIDEO_URL:
        parts = VIDEO_URL.rstrip("/").split("/")
        msg_id = int(parts[-1].split("?")[0])
        target_chat = int(f"-100{parts[-2]}") if len(parts) >= 4 and parts[-3] == "c" else parts[-2]
        msg  = await app.get_messages(target_chat, msg_id)
        media = getattr(msg, "video", None) or getattr(msg, "document", None)
        orig_name = getattr(media, "file_name", "source.mkv") if media else "source.mkv"
        await app.download_media(
            msg, file_name=SOURCE_FILE,
            progress=dl_progress, progress_args=(app, CHAT_ID, status_msg, start)
        )
        return orig_name

    raise ValueError(f"Unsupported URL format: {VIDEO_URL}")

# ── PROBE + RENAME ────────────────────────────────────────────────────────────

def probe_and_build_name() -> tuple[str, str, list, list]:
    """
    ffprobe the source, build the structured filename.
    Returns (output_filename, audio_type_label, audio_tracks, sub_tracks).
    """
    audio_tracks, sub_tracks = get_track_info(SOURCE_FILE)

    # Audio type — use override unless "Auto"
    if AUDIO_TYPE and AUDIO_TYPE.lower() != "auto":
        audio_type_label = AUDIO_TYPE.strip().capitalize()
    else:
        audio_type_label = detect_audio_type(audio_tracks)

    # Quality — read from actual video height via ffprobe
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", SOURCE_FILE
    ]
    import json
    try:
        raw  = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
        data = json.loads(raw)
        height = int(data["streams"][0].get("height", 1080))
    except Exception:
        height = 1080

    quality  = detect_quality(height)
    filename = build_output_name(
        anime_name   = ANIME_NAME or "Unknown",
        season       = SEASON,
        episode      = EPISODE,
        quality      = quality,
        audio_type   = audio_type_label,
        content_type = CONTENT_TYPE or "Anime",
        ext          = "mkv",
    )
    return filename, audio_type_label, audio_tracks, sub_tracks

# ── REMUX (apply new name + clean metadata) ───────────────────────────────────

def remux(output_name: str) -> bool:
    """
    mkvmerge: copy all streams into a new container with the structured filename.
    No transcoding — pure stream copy. Returns True on success.
    """
    fixed = f"FIXED_{output_name}"
    ret = subprocess.run(
        ["mkvmerge", "-o", fixed, SOURCE_FILE],
        capture_output=True
    )
    if os.path.exists(fixed) and os.path.getsize(fixed) > 0:
        os.rename(fixed, output_name)
        return True
    # Fallback: simple rename if mkvmerge fails (e.g. non-MKV source)
    os.rename(SOURCE_FILE, output_name)
    return ret.returncode == 0

# ── MAIN ──────────────────────────────────────────────────────────────────────

async def main():
    lane         = resolve_lane()
    session_dir  = "tg_session_dir"
    os.makedirs(session_dir, exist_ok=True)
    session_path = os.path.join(session_dir, f"tg_dl_session_{lane}")

    start_total = time.time()

    app = Client(session_path, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    for attempt in range(5):
        try:
            await app.start(); break
        except FloodWait as e:
            await asyncio.sleep(e.value + 5)
    else:
        print("❌ Could not authenticate with Telegram after 5 attempts."); sys.exit(1)

    try:
        status = await app.send_message(
            CHAT_ID,
            "<code>┌─── 🏷️  [ RENAME.MISSION ] ──────────┐\n"
            "│                                    \n"
            "│ 📡 Establishing Telegram downlink...\n"
            "│                                    \n"
            "└────────────────────────────────────┘</code>",
            parse_mode=enums.ParseMode.HTML
        )

        # ── 1. DOWNLOAD ────────────────────────────────────────────────────
        await tg_edit(app, CHAT_ID, status.id,
            "<code>┌─── 📥 [ DOWNLOADING ] ──────────────┐\n"
            "│ Fetching file from Telegram...     \n"
            "└────────────────────────────────────┘</code>")

        try:
            orig_name = await download_from_tg(app, status)
        except Exception as e:
            await tg_edit(app, CHAT_ID, status.id,
                f"<b>❌ DOWNLOAD FAILED:</b>\n<code>{e}</code>")
            sys.exit(1)

        dl_time = time.time() - start_total
        print(f"[rename] Downloaded in {dl_time:.1f}s → {SOURCE_FILE}")

        # ── 2. PROBE + BUILD NAME ──────────────────────────────────────────
        await tg_edit(app, CHAT_ID, status.id,
            "<code>┌─── 🔬 [ PROBING ] ──────────────────┐\n"
            "│ Reading track info...              \n"
            "└────────────────────────────────────┘</code>")

        if not ANIME_NAME:
            await tg_edit(app, CHAT_ID, status.id,
                "<b>⚠️ ANIME_NAME not set — aborting rename.</b>")
            sys.exit(1)

        output_name, audio_type_label, audio_tracks, sub_tracks = probe_and_build_name()
        print(f"[rename] Output filename: {output_name}")

        # ── 3. REMUX ───────────────────────────────────────────────────────
        await tg_edit(app, CHAT_ID, status.id,
            "<code>┌─── 🛠️  [ REMUXING ] ────────────────┐\n"
            f"│ {output_name[:34]}\n"
            "│ Repackaging streams...             \n"
            "└────────────────────────────────────┘</code>")

        remux(output_name)

        # ── 4. UPLOAD ──────────────────────────────────────────────────────
        final_size = os.path.getsize(output_name) / 1_048_576
        await tg_edit(app, CHAT_ID, status.id,
            "<b>🚀 [ UPLINK ] Transmitting renamed file...</b>")

        # Build track report
        track_report = format_track_report(audio_tracks, sub_tracks)
        user_notes   = ""
        if SUB_TRACKS:
            user_notes += f"\n🔤 <b>SUB LABELS:</b>  <code>{SUB_TRACKS}</code>"
        if AUDIO_TRACKS:
            user_notes += f"\n🔊 <b>AUDIO LABELS:</b> <code>{AUDIO_TRACKS}</code>"

        total_time = time.time() - start_total
        report = (
            f"✅ <b>RENAME COMPLETE</b>\n\n"
            f"📄 <b>ORIGINAL:</b> <code>{orig_name[:60]}</code>\n"
            f"🏷️  <b>RENAMED TO:</b> <code>{output_name}</code>\n\n"
            f"📦 <b>SIZE:</b> <code>{final_size:.2f} MB</code>\n"
            f"⏱ <b>TIME:</b> <code>{format_time(total_time)}</code>\n\n"
            f"📂 <b>TYPE:</b> {CONTENT_TYPE or 'Anime'}  |  "
            f"🔈 <b>AUDIO:</b> {audio_type_label}\n\n"
            f"{track_report}"
            f"{user_notes}"
        )

        _ui.last_up_pct = -1; _ui.last_up_update = 0; _ui.up_start_time = 0

        await app.send_document(
            chat_id=CHAT_ID,
            document=output_name,
            caption=report,
            parse_mode=enums.ParseMode.HTML,
            progress=upload_progress,
            progress_args=(app, CHAT_ID, status, output_name),
        )

        try: await status.delete()
        except: pass

        # Cleanup
        for f in [SOURCE_FILE, output_name, SCREENSHOT]:
            if os.path.exists(f): os.remove(f)

        print(f"[rename] Mission complete → {output_name}")

    except Exception as e:
        traceback.print_exc()
        try:
            await tg_edit(app, CHAT_ID, status.id,
                f"<b>❌ RENAME MISSION FAILED</b>\n<code>{e}</code>")
        except: pass
        sys.exit(1)
    finally:
        try: await app.stop()
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
