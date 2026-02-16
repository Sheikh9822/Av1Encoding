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
        
        # Write the chunk to its specific location in the file
        file_obj.seek(offset)
        file_obj.write(chunk_data)
    except Exception as e:
        print(f"❌ Chunk {chunk_index} failed: {e}")

async def fast_download(app, file_id, file_size, output_path):
    """Coordinates parallel chunked downloading for maximum speed."""
    print(f"🚀 Initializing High-Speed Download: {file_size / (1024*1024):.2f} MB")
    print(f"📡 Engaging {CHUNK_COUNT} parallel streams...")
    
    # Pre-allocate full file size on disk
    with open(output_path, "wb") as f:
        f.truncate(file_size)
    
    # Open in 'r+b' to allow simultaneous writes at different offsets
    with open(output_path, "r+b") as f:
        tasks = []
        for i in range(CHUNK_COUNT):
            tasks.append(download_chunk(app, file_id, file_size, i, CHUNK_COUNT, f))
        await asyncio.gather(*tasks)

async def main():
    # 1. Configuration & Env Setup
    api_id = int(os.environ.get("TG_API_ID", 0))
    api_hash = os.environ.get("TG_API_HASH", "")
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    url = os.environ.get("VIDEO_URL", "")
    
    if not url:
        print("❌ No VIDEO_URL provided!")
        sys.exit(1)

    session_dir = "tg_session_dir"
    master_session = os.path.join(session_dir, "tg_dl_session")
    
    # Generate a unique session name for THIS run to allow simultaneous jobs
    job_uuid = uuid.uuid4().hex[:8]
    temp_session = os.path.join(session_dir, f"session_{job_uuid}")
    
    if not os.path.exists(session_dir):
        os.makedirs(session_dir)

    # 2. Session Handshake (Copy from cache if available)
    if os.path.exists(f"{master_session}.session"):
        shutil.copy(f"{master_session}.session", f"{temp_session}.session")
        print("🔑 Using existing session from cache.")
    else:
        print("⏳ New login required. Staggering to avoid Telegram flood protection...")
        await asyncio.sleep(random.uniform(2, 10))

    # 3. Start Client and Download
    async with Client(temp_session, api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        file_id = None
        file_size = 0
        final_name = "video.mkv"

        # CASE A: Raw File ID (tg_file:ID|Name)
        if url.startswith("tg_file:"):
            print("📱 Mode: Raw File ID")
            parts = url.replace("tg_file:", "").split("|")
            file_id = parts[0]
            if len(parts) > 1: final_name = parts[1]
            
            # Note: Parallel download requires a message context to get size.
            # For raw IDs, we use the standard Pyrogram downloader.
            await app.download_media(file_id, file_name="./source.mkv")
        
        # CASE B: Telegram Post Link (https://t.me/...)
        elif "t.me/" in url:
            print("📱 Mode: Telegram Link")
            link = url.rstrip("/")
            url_parts = link.split("/")
            msg_id = int(url_parts[-1].split("?")[0])
            
            # Resolve Chat ID (Private channel vs Public username)
            if len(url_parts) > 2 and url_parts[-3] == "c":
                chat_id = int(f"-100{url_parts[-2]}")
            else:
                chat_id = url_parts[-2]

            msg = await app.get_messages(chat_id, msg_id)
            if not msg or not msg.media:
                print("❌ Error: No media found in link!")
                sys.exit(1)
            
            media = msg.video or msg.document
            file_id = media.file_id
            file_size = media.file_size
            final_name = media.file_name or "video.mkv"
            
            # Use High-Speed Parallel Download
            await fast_download(app, file_id, file_size, "./source.mkv")

        # Save the detected filename for the GitHub workflow
        with open("tg_fname.txt", "w") as f:
            f.write(final_name)
        
        print(f"✅ Download Complete: {final_name}")

    # 4. Final Cleanup
    # We update the master session for the next job's cache
    if not os.path.exists(f"{master_session}.session"):
        shutil.copy(f"{temp_session}.session", f"{master_session}.session")

    # Delete temp session files to keep runner clean
    for ext in [".session", ".session-journal"]:
        f_path = temp_session + ext
        if os.path.exists(f_path):
            os.remove(f_path)

if __name__ == "__main__":
    asyncio.run(main())