import asyncio
import os
import sys
import time
from pyrogram import Client, enums
from ui import get_download_ui

async def progress(current, total, app, chat_id, message, start_time):
    now = time.time()
    if not hasattr(progress, "last_update"): progress.last_update = 0
    if now - progress.last_update < 5: return
    progress.last_update = now
    elapsed = now - start_time
    percent = (current / total) * 100
    speed = current / elapsed / (1024 * 1024) if elapsed > 0 else 0
    size_mb = total / (1024 * 1024)
    try:
        await app.edit_message_text(chat_id, message.id, get_download_ui(percent, speed, size_mb), parse_mode=enums.ParseMode.HTML)
    except: pass

async def main():
    api_id = int(os.environ.get("TG_API_ID", 0))
    api_hash = os.environ.get("TG_API_HASH", "")
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = int(os.environ.get("TG_CHAT_ID", "0"))
    url = os.environ.get("VIDEO_URL", "")
    
    session_dir = "tg_session_dir"
    os.makedirs(session_dir, exist_ok=True)
    session_path = os.path.join(session_dir, "tg_dl_session")

    async with Client(session_path, api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        status = await app.send_message(chat_id, "📡 <b>[ SYSTEM.INIT ] Establishing Downlink...</b>", parse_mode=enums.ParseMode.HTML)
        start_time = time.time()
        
        if "t.me/" in url:
            link = url.rstrip("/")
            parts = link.split("/")
            msg_id = int(parts[-1].split("?")[0])
            c_id = int(f"-100{parts[-2]}") if parts[-3] == "c" else parts[-2]
            try: await app.get_chat(c_id)
            except: pass
            msg = await app.get_messages(c_id, msg_id)
            media = msg.video or msg.document
            await app.download_media(msg, file_name="./source.mkv", progress=progress, progress_args=(app, chat_id, status, start_time))
            fn = media.file_name or "video.mkv"
        elif url.startswith("tg_file:"):
            parts = url.replace("tg_file:", "").split("|")
            await app.download_media(parts[0], file_name="./source.mkv", progress=progress, progress_args=(app, chat_id, status, start_time))
            fn = parts[1] if len(parts) > 1 else "video.mkv"

        await app.edit_message_text(chat_id, status.id, "✅ <b>[ DOWNLOAD.COMPLETE ] Transferring to Encoder...</b>", parse_mode=enums.ParseMode.HTML)
        with open("tg_fname.txt", "w") as f: f.write(fn)

if __name__ == "__main__":
    asyncio.run(main())