import asyncio
import os
from pyrogram import Client

async def main():
    # Load environment variables from GitHub Secrets
    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    bot_token = os.getenv("BOT_TOKEN")
    chat_id = int(os.getenv("CHAT_ID"))
    file_path = os.getenv("FILE_NAME")

    async with Client("uploader", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as app:
        print(f"Uploading {file_path} to {chat_id}...")
        await app.send_document(
            chat_id=chat_id,
            document=file_path,
            caption=f"✅ **Encoding Complete!**\n\n📄 `{file_path}`",
            progress=None # You can add a progress callback here if you want logs
        )

if __name__ == "__main__":
    asyncio.run(main())
  
