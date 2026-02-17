import asyncio
import os
import sys
import time
import traceback
from pyrogram import Client, enums
from ui import get_download_ui

# Progress callback to update Telegram UI
async def progress(current, total, app, chat_id, message, start_time):
    # Fix: Prevent ZeroDivisionError if total is 0 or None
    if not total:
        return
        
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
    except Exception:
        pass

async def main():
    try:
        api_id = os.environ.get("TG_API_ID")
        api_hash = os.environ.get("TG_API_HASH")
        bot_token = os.environ.get("TG_BOT_TOKEN")
        chat_id_raw = os.environ.get("TG_CHAT_ID", "0")
        url = os.environ.get("VIDEO_URL", "")
        
        # Validation
        if not api_id or not api_hash or not bot_token:
            print("❌ ERROR: Missing Telegram Credentials (API_ID/HASH/BOT_TOKEN)")
            sys.exit(1)
            
        api_id = int(api_id)
        chat_id = int(chat_id_raw)
        
        session_dir = "tg_session_dir"
        os.makedirs(session_dir, exist_ok=True)
        session_path = os.path.join(session_dir, "tg_dl_session")

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
                
                try: 
                    await app.get_chat(c_id)
                except Exception as e:
                    print(f"⚠️ Warning: Could not resolve chat {c_id}: {e}")
                
                msg = await app.get_messages(c_id, msg_id)
                if not msg or not msg.media:
                    print("❌ ERROR: No media found in link!")
                    await app.edit_message_text(chat_id, status.id, "❌ <b>ERROR: Media not found or inaccessible.</b>")
                    sys.exit(1)
                
                media = msg.video or msg.document
                final_name = media.file_name or "video.mkv"
                
                await app.download_media(
                    msg, file_name="./source.mkv",
                    progress=progress, progress_args=(app, chat_id, status, start_time)
                )
            
            # CASE B: Raw File ID
            elif url.startswith("tg_file:"):
                # Format: tg_file:FILE_ID|FILENAME.mkv
                content = url.replace("tg_file:", "")
                if "|" in content:
                    file_id, final_name = content.split("|", 1)
                else:
                    file_id, final_name = content, "video.mkv"
                
                print(f"📥 Attempting download by File ID: {file_id}")
                try:
                    await app.download_media(
                        file_id, file_name="./source.mkv",
                        progress=progress, progress_args=(app, chat_id, status, start_time)
                    )
                except Exception as e:
                    print(f"❌ ERROR: Bot cannot download this File ID directly: {e}")
                    print("💡 Tip: The bot must be a member of the chat where this file originated.")
                    await app.edit_message_text(chat_id, status.id, f"❌ <b>DOWNLOAD FAILED:</b>\n<code>{str(e)}</code>")
                    sys.exit(1)

            await app.edit_message_text(chat_id, status.id, "✅ <b>[ DOWNLOAD.COMPLETE ] Transferring to Encoder...</b>", parse_mode=enums.ParseMode.HTML)
            
            with open("tg_fname.txt", "w") as f:
                f.write(final_name)

    except Exception as e:
        print(f"CRITICAL ERROR in tg_handler:\n{traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())