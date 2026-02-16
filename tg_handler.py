import asyncio
import os
import random
import sys
import math
import shutil
import uuid
from pyrogram import Client

# Optimal for GitHub Runners: 8 parallel streams
CHUNK_COUNT = 8 

async def download_chunk(app, file_id, file_size, chunk_index, total_chunks, file_obj):
    """Downloads a specific part of a file in parallel."""
    chunk_size = math.ceil(file_size / total_chunks)
    offset = chunk_index * chunk_size
    limit = min(chunk_size, file_size - offset)
    
    if limit <= 0: return

    try:
        chunk_data = b""
        async for chunk in app.get_file(file_id, offset=offset, limit=limit):
            chunk_data += chunk
        
        file_obj.seek(offset)
        file_obj.write(chunk_data)
    except Exception:
        pass

async def fast_download(app, file_id, file_size, output_path):
    """Coordinates parallel chunked downloading."""
    print(f"🚀 High-Speed Download: {file_size / (1024*1024):.2f} MB")
    
    with open(output_path, "wb") as f:
        f.truncate(file_size)
    
    with open(output_path, "r+b") as f:
        tasks = [download_chunk(app, file_id, file_size, i, CHUNK_COUNT, f) for i in range(CHUNK_COUNT)]
        await asyncio.gather(*tasks)

async def main():
    api_id = int(os.environ.get("TG_API_ID", 0))
    api_hash = os.environ.get("TG_API_HASH", "")
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    url = os.environ.get("VIDEO_URL", "")
    
    if not url:
        sys.exit(1)

    session_dir = "tg_session_dir"
    master_session = os.path.join(session_dir, "tg_dl_session")
    job_uuid = uuid.uuid4().hex[:8]
    temp_session = os.path.join(session_dir, f"session_{job_uuid}")
    
    os.makedirs(session_dir, exist_ok=True)

    if os.path.exists(f"{master_session}.session"):
        shutil.copy(f"{master_session}.session", f"{temp_session}.session")

    async with Client(temp_session, api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        file_id = None
        final_name = "video.mkv"

        if url.startswith("tg_file:"):
            parts = url.replace("tg_file:", "").split("|")
            file_id = parts[0]
            if len(parts) > 1: final_name = parts[1]
            await app.download_media(file_id, file_name="./source.mkv")
        
        elif "t.me/" in url:
            link = url.rstrip("/")
            url_parts = link.split("/")
            msg_id = int(url_parts[-1].split("?")[0])
            
            # Resolve Chat ID
            if len(url_parts) > 2 and url_parts[-3] == "c":
                # Private Channel logic
                chat_id = int(f"-100{url_parts[-2]}")
            else:
                # Public Channel logic
                chat_id = url_parts[-2]

            print(f"📡 Resolving Chat: {chat_id}...")
            
            # --- CRITICAL FIX: PRIME THE PEER ---
            try:
                # This forces Pyrogram to find the chat and save it to the session DB
                await app.get_chat(chat_id)
            except Exception as e:
                print(f"⚠️ Peer warning: {e}")
            
            # Now get the message
            try:
                msg = await app.get_messages(chat_id, msg_id)
            except Exception as e:
                print(f"❌ Failed to fetch message: {e}")
                sys.exit(1)

            if not msg or not msg.media:
                print("❌ No media found!")
                sys.exit(1)
            
            media = msg.video or msg.document
            await fast_download(app, media.file_id, media.file_size, "./source.mkv")
            final_name = media.file_name or "video.mkv"

        with open("tg_fname.txt", "w") as f:
            f.write(final_name)

    # Update Master for next run
    if not os.path.exists(f"{master_session}.session"):
        shutil.copy(f"{temp_session}.session", f"{master_session}.session")

    for ext in [".session", ".session-journal"]:
        f_path = temp_session + ext
        if os.path.exists(f_path): os.remove(f_path)

if __name__ == "__main__":
    asyncio.run(main())