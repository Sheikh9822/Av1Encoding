import os
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# Configure these environment variables wherever you host this controller
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# GitHub details needed to trigger the Action
GITHUB_TOKEN = os.getenv("GH_PAT") # A GitHub Personal Access Token
GITHUB_REPO = os.getenv("GH_REPO") # e.g., "Username/Av1Encoding"
WORKFLOW_FILE = "encode.yml"       # The name of your workflow file

app = Client("satellite_controller", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("encode") & filters.private)
async def trigger_encode(client: Client, message: Message):
    # Split the message into arguments
    args = message.text.split()[1:]
    
    if not args:
        await message.reply_text(
            "⚠️ **MISSING UPLINK DATA**\n\n"
            "**Usage:** `/encode <URL> [preset=6] [crf=42] [res=1080] [name=CustomName]`\n"
            "**Example:** `/encode https://youtu.be/... crf=36 res=720`"
        )
        return

    video_url = args[0]
    
    # Default parameters
    payload_inputs = {
        "video_url": video_url,
        "custom_name": "",
        "res_choice": "",
        "custom_crf": "42",
        "custom_preset": "6",
        "audio_mode": "opus",
        "audio_bitrate": "128k",
        "run_ssim": "true"
    }

    # Parse optional arguments like crf=30 or res=720
    for arg in args[1:]:
        if "=" in arg:
            key, val = arg.split("=", 1)
            key = key.lower()
            if key == "name": payload_inputs["custom_name"] = val
            elif key == "res": payload_inputs["res_choice"] = val
            elif key == "crf": payload_inputs["custom_crf"] = val
            elif key == "preset": payload_inputs["custom_preset"] = val
            elif key == "audio": payload_inputs["audio_mode"] = val
            elif key == "bitrate": payload_inputs["audio_bitrate"] = val
            elif key == "ssim": payload_inputs["run_ssim"] = val.lower()

    # Hit the GitHub API to trigger the workflow
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    data = {
        "ref": "main", # Or whichever branch your workflow is on
        "inputs": payload_inputs
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 204:
            await message.reply_text(
                "🚀 **[ SATELLITE LINK ESTABLISHED ]**\n\n"
                f"Uplink signal sent to `GitHub Actions`.\n"
                f"Encoding sequence will begin shortly."
            )
        else:
            await message.reply_text(f"❌ **UPLINK FAILED:** Server responded with HTTP {response.status_code}\n`{response.text}`")
    except Exception as e:
        await message.reply_text(f"❌ **SYSTEM ERROR:**\n`{str(e)}`")

if __name__ == "__main__":
    print("🛰️ Satellite Controller Booting...")
    app.run()
