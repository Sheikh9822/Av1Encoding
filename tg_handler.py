import asyncio
import os
import sys
import time
import subprocess
from pyrogram import Client, enums
from ui import get_download_ui, get_download_fail_ui

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
    api_id = int(os.environ.get("TG_API_ID", 0))
    api_hash = os.environ.get("TG_API_HASH", "")
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = int(os.environ.get("TG_CHAT_ID", "0"))
    url = os.environ.get("VIDEO_URL", "")
    custom_name = os.environ.get("CUSTOM", "")
    
    session_path = os.path.join("tg_session_dir", "tg_dl_session")
    
    async with Client(session_path, api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        status = await app.send_message(chat_id, "📡 <b>[ SYSTEM.INIT ] Establishing Downlink...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            start_time = time.time()
            final_name = "video.mkv"

            # --- CASE 1: TELEGRAM MEDIA ---
            if "t.me/" in url or url.startswith("tg_file:"):
                if "t.me/" in url:
                    link = url.rstrip("/")
                    parts = link.split("/")
                    msg_id = int(parts[-1].split("?")[0])
                    c_id = int(f"-100{parts[-2]}") if parts[-3] == "c" else parts[-2]
                    try: await app.get_chat(c_id)
                    except: pass
                    msg = await app.get_messages(c_id, msg_id)
                    media = msg.video or msg.document
                else:
                    file_id = url.replace("tg_file:", "").split("|")[0]
                    media = await app.get_messages(chat_id, file_id) # Simplified lookup

                if not media: raise Exception("No media found at the provided Telegram link.")
                final_name = media.file_name or "video.mkv"
                
                await app.download_media(
                    media, file_name="./source.mkv",
                    progress=progress, progress_args=(app, chat_id, status, start_time)
                )

            # --- CASE 2: GENERIC LINKS (yt-dlp) ---
            else:
                await app.edit_message_text(chat_id, status.id, "🌐 <b>[ EXTERNAL.FETCH ] Running yt-dlp Core...</b>", parse_mode=enums.ParseMode.HTML)
                
                # Get filename first
                if not custom_name:
                    cmd_name = ["yt-dlp", "--print", "filename", "-o", "%(title)s.mkv", url]
                    final_name = subprocess.check_output(cmd_name).decode().strip()
                else:
                    final_name = f"{custom_name}.mkv"

                # Download using yt-dlp
                cmd_dl = [
                    "yt-dlp", "--downloader", "aria2c", 
                    "--downloader-args", "aria2c:-x 16 -s 16", 
                    "--merge-output-format", "mkv", "-o", "source.mkv", url
                ]
                
                process = subprocess.Popen(cmd_dl, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                process.wait()
                
                if process.returncode != 0:
                    raise Exception("yt-dlp failed to fetch the external resource.")

            # SUCCESS: Save name and notify
            with open("tg_fname.txt", "w") as f: f.write(final_name)
            await app.edit_message_text(chat_id, status.id, "✅ <b>[ DOWNLOAD.COMPLETE ] Transferring to Encoder...</b>", parse_mode=enums.ParseMode.HTML)

        except Exception as e:
            # REPORT FAILURE TO TELEGRAM
            fail_text = get_download_fail_ui(str(e))
            await app.edit_message_text(chat_id, status.id, fail_text, parse_mode=enums.ParseMode.HTML)
            sys.exit(1) # Kill the workflow

if __name__ == "__main__":
    asyncio.run(main())