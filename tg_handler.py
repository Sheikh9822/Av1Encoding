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
    
    session_path = "tg_session_dir/tg_dl_session"
    
    # Anti-Spam: Stagger new logins to avoid Telegram bans
    if not os.path.exists(f"{session_path}.session"):
        delay = random.uniform(1, 15)
        print(f"⏳ Anti-Spam: Staggering NEW login by {delay:.1f} seconds...")
        await asyncio.sleep(delay)

    async with Client(session_path, api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        
        # CASE A: Direct File ID (Format: tg_file:FILE_ID|Optional_Name)
        if url.startswith("tg_file:"):
            print("📱 Telegram Direct File ID detected.")
            file_id = url.replace("tg_file:", "").split("|")[0]
            await app.download_media(file_id, file_name="./source.mkv")
        
        # CASE B: Telegram Message Link (Format: https://t.me/...)
        elif "t.me/" in url:
            print("📱 Telegram Post Link detected.")
            link = url.rstrip("/")
            parts = link.split("/")
            msg_id = int(parts[-1].split("?")[0])
            
            # Handle Private Channel IDs vs Public Usernames
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

            # Save the original filename so the workflow can use it
            fn = "Telegram_Video.mkv"
            if getattr(msg, "video", None) and getattr(msg.video, "file_name", None):
                fn = msg.video.file_name
            elif getattr(msg, "document", None) and getattr(msg.document, "file_name", None):
                fn = msg.document.file_name
            
            with open("tg_fname.txt", "w") as f:
                f.write(fn)

if __name__ == "__main__":
    asyncio.run(download_telegram_media())