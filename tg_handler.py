import asyncio
import os
import random
import sys
from pyrogram import Client

async def download_telegram_media():
    api_id = int(os.environ.get("TG_API_ID", 0))
    api_hash = os.environ.get("TG_API_HASH", "")
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    url = os.environ.get("VIDEO_URL", "")
    
    session_dir = "tg_session_dir"
    session_path = os.path.join(session_dir, "tg_dl_session")
    
    # FIX: Ensure the directory exists before Pyrogram tries to create the database
    os.makedirs(session_dir, exist_ok=True)
    
    if not os.path.exists(f"{session_path}.session"):
        delay = random.uniform(1, 15)
        print(f"⏳ Anti-Spam: Staggering NEW login by {delay:.1f} seconds...")
        await asyncio.sleep(delay)

    async with Client(session_path, api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        
        # CASE A: Direct File ID
        if url.startswith("tg_file:"):
            print("📱 Telegram Direct File ID detected.")
            # Extract ID and Name: tg_file:ID|Name
            parts = url.replace("tg_file:", "").split("|")
            file_id = parts[0]
            
            # If a name was provided in the tg_file string, save it
            if len(parts) > 1:
                with open("tg_fname.txt", "w") as f:
                    f.write(parts[1])
            
            await app.download_media(file_id, file_name="./source.mkv")
        
        # CASE B: Telegram Message Link
        elif "t.me/" in url:
            print("📱 Telegram Post Link detected.")
            link = url.rstrip("/")
            parts = link.split("/")
            msg_id = int(parts[-1].split("?")[0])
            
            if len(parts) > 2 and parts[-3] == "c":
                chat_id = int(f"-100{parts[-2]}")
            else:
                chat_id = parts[-2]

            print(f"⬇️ Fetching message {msg_id} from {chat_id}...")
            msg = await app.get_messages(chat_id, msg_id)

            if not msg or msg.empty or not msg.media:
                print("❌ Error: No media found in this link!")
                sys.exit(1)

            print("⬇️ Downloading file from Telegram...")
            await app.download_media(msg, file_name="./source.mkv")

            fn = "Telegram_Video.mkv"
            if getattr(msg, "video", None) and getattr(msg.video, "file_name", None):
                fn = msg.video.file_name
            elif getattr(msg, "document", None) and getattr(msg.document, "file_name", None):
                fn = msg.document.file_name
            
            with open("tg_fname.txt", "w") as f:
                f.write(fn)

if __name__ == "__main__":
    asyncio.run(download_telegram_media())