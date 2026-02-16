import asyncio
import os
import sys
import time
from pyrogram import Client, enums
from ui import get_download_ui

# Progress callback to update Telegram UI
async def progress(current, total, app, chat_id, message, start_time):
    now = time.time()
    if not hasattr(progress, "last_update"): progress.last_update = 0
    if now - progress.last_update < 5: return # Update every 5s
    
    progress.last_update = now
    elapsed = now - start_time
    percent = (current / total) * 100
    speed = current / elapsed / (1024 * 1024) if elapsed > 0 else 0
    size_mb = total / (1024 * 1024)
    
    try:
        await app.edit_message_text(
            chat_id, message.id, 
            get_download_ui(percent, speed, size_mb),
            parse_mode=enums.ParseMode.HTML
        )
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
        
        final_name = "video.mkv"

        # CASE A: Telegram Post Link (Public or Private)
        if "t.me/" in url:
            link = url.rstrip("/")
            parts = link.split("/")
            msg_id = int(parts[-1].split("?")[0])
            
            # Resolve Private (-100...) vs Public IDs
            if parts[-3] == "c":
                c_id = int(f"-100{parts[-2]}")
            else:
                c_id = parts[-2]
            
            # Prime the peer to avoid "ID not found" errors
            try: await app.get_chat(c_id)
            except: pass
            
            msg = await app.get_messages(c_id, msg_id)
            if not msg or not msg.media:
                print("❌ No media found in link!"); sys.exit(1)
            
            media = msg.video or msg.document
            final_name = media.file_name or "video.mkv"
            
            await app.download_media(
                msg, file_name="./source.mkv",
                progress=progress, progress_args=(app, chat_id, status, start_time)
            )
        
        # CASE B: Raw File ID
        elif url.startswith("tg_file:"):
            parts = url.replace("tg_file:", "").split("|")
            file_id = parts[0]
            if len(parts) > 1: final_name = parts[1]
            
            await app.download_media(
                file_id, file_name="./source.mkv",
                progress=progress, progress_args=(app, chat_id, status, start_time)
            )

        await app.edit_message_text(chat_id, status.id, "✅ <b>[ DOWNLOAD.COMPLETE ] Transferring to Encoder...</b>", parse_mode=enums.ParseMode.HTML)
        
        # Save filename for the next stage of the pipeline
        with open("tg_fname.txt", "w") as f:
            f.write(final_name)

if __name__ == "__main__":
    asyncio.run(main())