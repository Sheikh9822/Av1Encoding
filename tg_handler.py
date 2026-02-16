import asyncio
import os
import random
import sys
from pyrogram import Client

async def download_telegram_media():
    # 1. Fetch Environment Variables
    api_id = int(os.environ.get("TG_API_ID", 0))
    api_hash = os.environ.get("TG_API_HASH", "")
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    url = os.environ.get("VIDEO_URL", "")
    
    # 2. Setup Session Path and ensure FOLDER exists
    session_dir = "tg_session_dir"
    session_path = os.path.join(session_dir, "tg_dl_session")
    
    # CRITICAL FIX: Create the directory if it doesn't exist
    if not os.path.exists(session_dir):
        os.makedirs(session_dir)
    
    # 3. Anti-Spam (Only for new logins)
    if not os.path.exists(f"{session_path}.session"):
        delay = random.uniform(1, 15)
        print(f"⏳ Anti-Spam: Staggering NEW login by {delay:.1f} seconds...")
        await asyncio.sleep(delay)

    # 4. Start Pyrogram Client
    async with Client(session_path, api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        
        # CASE A: Direct File ID (tg_file:ID|Name)
        if url.startswith("tg_file:"):
            print("📱 Telegram Direct File ID detected.")
            # We split the URL to get the File ID and the Filename
            parts = url.replace("tg_file:", "").split("|")
            file_id = parts[0]
            
            # Save the filename for the YAML environment
            if len(parts) > 1:
                with open("tg_fname.txt", "w") as f:
                    f.write(parts[1])
            
            await app.download_media(file_id, file_name="./source.mkv")
        
        # CASE B: Telegram Link (https://t.me/...)
        elif "t.me/" in url:
            print("📱 Telegram Post Link detected.")
            link = url.rstrip("/")
            parts = link.split("/")
            msg_id = int(parts[-1].split("?")[0])
            
            if len(parts) > 2 and parts[-3] == "c":
                chat_id = int(f"-100{parts[-2]}")
            else:
                chat_id = parts[-2]

            msg = await app.get_messages(chat_id, msg_id)
            if not msg or msg.empty or not msg.media:
                print("❌ Error: No media found!")
                sys.exit(1)

            await app.download_media(msg, file_name="./source.mkv")

            # Extract filename from the message
            fn = "Telegram_Video.mkv"
            if getattr(msg, "video", None) and getattr(msg.video, "file_name", None):
                fn = msg.video.file_name
            elif getattr(msg, "document", None) and getattr(msg.document, "file_name", None):
                fn = msg.document.file_name
            
            with open("tg_fname.txt", "w") as f:
                f.write(fn)

if __name__ == "__main__":
    asyncio.run(download_telegram_media())