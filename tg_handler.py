import asyncio
import os
import sys
import time
import traceback
from pyrogram import Client, enums
from ui import get_download_ui

# Progress callback to update Telegram UI
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
        await app.edit_message_text(
            chat_id, message.id, 
            get_download_ui(percent, speed, size_mb),
            parse_mode=enums.ParseMode.HTML
        )
    except: pass

async def main():
    # .strip() is crucial to prevent 'to_bytes' errors caused by hidden newlines
    api_id = int(os.environ.get("TG_API_ID", "0").strip())
    api_hash = os.environ.get("TG_API_HASH", "").strip()
    bot_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = int(os.environ.get("TG_CHAT_ID", "0").strip())
    url = os.environ.get("VIDEO_URL", "").strip()
    
    session_dir = "tg_session_dir"
    os.makedirs(session_dir, exist_ok=True)
    session_path = os.path.join(session_dir, "tg_dl_session")

    try:
        async with Client(session_path, api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
            status = await app.send_message(chat_id, "📡 <b>[ SYSTEM.INIT ] Establishing Downlink...</b>", parse_mode=enums.ParseMode.HTML)
            start_time = time.time()
            final_name = "video.mkv"

            # CASE A: Telegram Post Link
            if "t.me/" in url:
                link = url.rstrip("/")
                parts = link.split("/")
                msg_id = int(parts[-1].split("?")[0])
                
                if parts[-3] == "c":
                    c_id = int(f"-100{parts[-2]}")
                else:
                    c_id = parts[-2]
                
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
            elif "tg_file:" in url:
                raw_data = url.replace("tg_file:", "")
                if "|" in raw_data:
                    file_id, final_name = raw_data.split("|", 1)
                else:
                    file_id = raw_data

                # We pass file_id as a named argument to ensure Pyrogram handles it correctly
                await app.download_media(
                    message=file_id.strip(), 
                    file_name="./source.mkv",
                    progress=progress, progress_args=(app, chat_id, status, start_time)
                )

            await app.edit_message_text(chat_id, status.id, "✅ <b>[ DOWNLOAD.COMPLETE ] Transferring to Encoder...</b>", parse_mode=enums.ParseMode.HTML)
            with open("tg_fname.txt", "w") as f:
                f.write(final_name)

    except Exception as e:
        # This will print the exact line and cause of the failure
        error_msg = f"<code>┌─── ❌ [ DOWNLOAD.MISSION.FAILED ] ───┐\n│\n│ ❌ ERROR: {str(e)}\n│ 🛠️ STATUS: Downlink Terminated.\n│\n└────────────────────────────────────┘</code>"
        print(error_msg)
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())