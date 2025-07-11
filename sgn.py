#!/usr/bin/env python3
import os
import re
import anitopy
import subprocess
import logging
from typing import Optional, Tuple
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
import requests
from hashlib import md5
import tempfile
import time

# Clean logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("anime_signer.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_FILE_SIZE = 4 * 1024 * 1024 * 1024  # 4GB

# Initialize Pyrogram Client
app = Client(
    "anime_signer_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

def extract_subtitles(input_path: str, output_path: str) -> bool:
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-map", "0:s:0", "-c:s", "ass", output_path
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr.decode()[:200]}...")
        return False

def create_sign_subtitles(sub_path: str, output_path: str) -> bool:
    try:
        with open(sub_path, 'r', encoding='utf-8') as f:
            content = f.read()

        style_keywords = ["sign", "signs", "overlay", "text", "caption"]
        effect_keywords = ["\\an", "\\pos", "\\move", "\\fad"]
        actor_keywords = ["sign", "signs"]

        filtered_lines = []
        for line in content.split('\n'):
            if line.startswith("Dialogue:"):
                parts = line.split(',', 9)
                if len(parts) >= 10:
                    style, name, effect, text = parts[3].lower(), parts[4].lower(), parts[8], parts[9]
                    if (any(kw in style for kw in style_keywords) or
                        any(kw in name for kw in actor_keywords) or
                        any(kw in effect or kw in text for kw in effect_keywords)):
                        filtered_lines.append(line)
            else:
                filtered_lines.append(line)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(filtered_lines))
        return True
    except Exception as e:
        logger.error(f"Subtitle processing error: {e}")
        return False

async def process_file(file_path: str, original_name: str) -> Tuple[Optional[str], str]:
    try:
        parsed = anitopy.parse(original_name)
        anime_title = re.sub(r'[\[\]_]', ' ', parsed.get('anime_title', 'Unknown')).strip()
        episode = parsed.get('episode_number', '')
        lang = parsed.get('language', ['Jpn'])[0]
        new_name = f"{anime_title} - {episode} [{lang}].mkv"

        temp_sub = os.path.join(tempfile.gettempdir(), f"temp_{md5(original_name.encode()).hexdigest()[:8]}.ass")
        temp_sign = os.path.join(tempfile.gettempdir(), f"sign_{md5(original_name.encode()).hexdigest()[:8]}.ass")
        output_path = os.path.join(tempfile.gettempdir(), new_name)

        if not extract_subtitles(file_path, temp_sub):
            return None, new_name

        if not create_sign_subtitles(temp_sub, temp_sign):
            return None, new_name

        subprocess.run([
            "mkvmerge", "-o", output_path,
            "--language", "0:eng", "--track-name", "0:SignSub",
            "--default-track", "0:yes", temp_sign,
            file_path
        ], check=True)

        for f in [temp_sub, temp_sign]:
            try: os.remove(f)
            except: pass

        return output_path, new_name
    except Exception as e:
        logger.error(f"Processing error: {e}")
        return None, original_name

@app.on_message(filters.document | filters.video)
async def handle_file(client: Client, message: Message):
    try:
        if message.document and message.document.file_size > MAX_FILE_SIZE:
            await message.reply("⚠️ File is too large (max 4GB)")
            return

        file_name = message.document.file_name if message.document else message.video.file_name
        msg = await message.reply("⬇️ Downloading file...")
        
        dl_path = os.path.join(tempfile.gettempdir(), file_name)
        await message.download(dl_path)
        
        await msg.edit("🔄 Processing file...")
        output_path, new_name = await process_file(dl_path, file_name)
        
        if not output_path:
            await msg.edit("❌ Processing failed")
            return

        await msg.edit("📤 Uploading result...")
        await message.reply_document(
            output_path,
            file_name=new_name,
            caption=f"Processed: {new_name}"
        )
        await msg.delete()
        
        os.remove(dl_path)
        os.remove(output_path)
    except Exception as e:
        logger.error(f"Handler error: {e}")
        await message.reply("❌ An error occurred")

@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    await message.reply(
        "Send me an anime file and I'll:\n"
        "1. Extract sign subtitles\n"
        "2. Embed them as first track\n"
        "3. Rename properly\n\n"
        "Max size: 4GB | Formats: MKV/MP4"
    )

if __name__ == "__main__":
    logger.info("Starting bot...")
    app.run()
