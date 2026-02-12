import os
import time
import subprocess
import sys
from pyrogram import Client, filters

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

app = Client("encoder", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def get_duration(file):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        return float(result.stdout.strip())
    except:
        return 0

@app.on_message(filters.video | filters.document)
async def encode_handler(client, message):
    status = await message.reply("📥 Downloading...")
    file_path = await message.download()
    
    duration = get_duration(file_path)
    output = f"encoded_{os.path.basename(file_path)}.mkv"

    # --- 1. SKIP LOGIC FOR TINY VIDEOS (< 10s) ---
    if duration < 10:
        await status.edit(f"⚡ Short video ({duration:.1f}s) detected. Uploading original...")
        await client.send_document(message.chat.id, file_path)
        os.remove(file_path)
        await status.delete()
        return

    # --- 2. ENCODING LOGIC ---
    await status.edit("🎬 Encoding starting... check logs for details.")
    
    # We remove '-progress pipe:1' and 'stdout=PIPE' to prevent the deadlock/hanging
    cmd = [
        "ffmpeg", "-i", file_path,
        "-c:v", "libsvtav1", "-preset", "6", "-crf", "30",
        "-c:a", "libopus", "-b:a", "64k",
        "-y", output
    ]

    try:
        # This will now print directly to your terminal/GitHub Actions log
        process = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
        
        # Simple loop to wait for completion without blocking logs
        while process.poll() is None:
            await asyncio.sleep(5)
            # You can add a simple "Still encoding..." update here if you want
            
        if process.returncode != 0:
            raise Exception(f"FFmpeg exited with code {process.returncode}")

        await status.edit("📤 Uploading...")
        await client.send_document(message.chat.id, output)
        await status.delete()

    except Exception as e:
        await message.reply(f"❌ Error during encoding: {str(e)}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output): os.remove(output)

app.run()
