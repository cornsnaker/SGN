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
import sys

# Enhanced logging configuration
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG for more verbose output
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("anime_signer_debug.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables with verification
def load_config():
    if not load_dotenv():
        logger.warning("No .env file found or it's empty")
    
    required_vars = ['API_ID', 'API_HASH', 'BOT_TOKEN']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        sys.exit(1)
    
    try:
        return {
            'api_id': int(os.getenv('API_ID')),
            'api_hash': os.getenv('API_HASH'),
            'bot_token': os.getenv('BOT_TOKEN'),
            'max_size': int(os.getenv('MAX_FILE_SIZE', 4 * 1024 * 1024 * 1024))  # Default 4GB
        }
    except ValueError as e:
        logger.error(f"Invalid environment variables: {e}")
        sys.exit(1)

config = load_config()

# Constants
MAX_FILE_SIZE = config['max_size']
ANILIST_API = "https://graphql.anilist.co"
TEMP_DIR = tempfile.gettempdir()

# Initialize Pyrogram Client with error handling
try:
    app = Client(
        "anime_signer_bot",
        api_id=config['api_id'],
        api_hash=config['api_hash'],
        bot_token=config['bot_token'],
        workers=4
    )
    logger.info("Pyrogram client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Pyrogram client: {e}")
    sys.exit(1)

# Utility functions with enhanced error handling
def generate_file_key(file_id: str) -> str:
    return md5(file_id.encode()).hexdigest()[:8]

def clean_filename(filename: str) -> str:
    try:
        filename = re.sub(r'[\[\]_]', ' ', filename)
        filename = re.sub(r'\s+', ' ', filename).strip()
        return filename.title()
    except Exception as e:
        logger.error(f"Error cleaning filename: {e}")
        return filename

def build_proper_filename(parsed: dict) -> str:
    try:
        anime_title = clean_filename(parsed.get('anime_title', 'Unknown'))
        episode_number = parsed.get('episode_number', '')
        file_extension = parsed.get('file_extension', 'mkv')
        
        season_info = ""
        if parsed.get('anime_season'):
            season_number = parsed['anime_season']
            if isinstance(season_number, list):
                season_number = season_number[0]
            season_info = f" S{season_number}"
        
        language = parsed.get('language', 'Jpn')
        if isinstance(language, list):
            language = language[0]
        
        return f"{anime_title}{season_info} - {episode_number} [{language}].{file_extension}"
    except Exception as e:
        logger.error(f"Error building filename: {e}")
        return "processed_anime.mkv"

# Subtitle processing functions
def extract_subtitles(input_path: str, output_path: str) -> bool:
    try:
        logger.info(f"Extracting subtitles from {input_path}")
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-map", "0:s:0",
            "-c:s", "ass",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"FFmpeg failed: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg timed out after 5 minutes")
        return False
    except Exception as e:
        logger.error(f"Error extracting subtitles: {e}")
        return False

def create_sign_subtitles(sub_path: str, output_path: str) -> bool:
    try:
        logger.info(f"Creating sign subtitles from {sub_path}")
        
        style_keywords = ["sign", "signs", "overlay", "text", "caption"]
        effect_keywords = ["\\an", "\\pos", "\\move", "\\fad"]
        actor_keywords = ["sign", "signs"]

        with open(sub_path, 'r', encoding='utf-8-sig') as f:  # Handle BOM if present
            content = f.read()

        lines = content.split('\n')
        filtered_lines = []
        in_events = False

        for line in lines:
            try:
                line = line.strip()
                if line.startswith("[Events]"):
                    in_events = True
                    filtered_lines.append(line)
                    continue
                if line.startswith("["):
                    in_events = False
                    filtered_lines.append(line)
                    continue

                if in_events and line.startswith("Dialogue:"):
                    parts = line.split(',', 9)
                    if len(parts) < 10:
                        continue

                    layer, start, end, style, name, margin_l, margin_r, margin_v, effect, text = parts
                    style = style.strip().lower()
                    name = name.strip().lower()
                    effect = effect.strip()
                    text = text.strip()

                    # Check all filter conditions
                    if (any(kw.lower() in style for kw in style_keywords) or
                        any(kw.lower() in name for kw in actor_keywords) or
                        any(kw in effect for kw in effect_keywords) or
                        any(kw in text for kw in effect_keywords)):
                        filtered_lines.append(line)

            except Exception as e:
                logger.warning(f"Error processing line: {line} - {e}")
                continue

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(filtered_lines))
        return True

    except Exception as e:
        logger.error(f"Error creating sign subtitles: {e}")
        return False

async def process_anime_file(file_path: str, original_name: str) -> Tuple[Optional[str], str]:
    try:
        logger.info(f"Starting processing for {original_name}")
        
        parsed = anitopy.parse(original_name)
        proper_name = build_proper_filename(parsed)
        
        temp_sub = os.path.join(TEMP_DIR, f"temp_{md5(original_name.encode()).hexdigest()[:8]}.ass")
        temp_sign_sub = os.path.join(TEMP_DIR, f"sign_{md5(original_name.encode()).hexdigest()[:8]}.ass")
        output_path = os.path.join(TEMP_DIR, proper_name)

        if not extract_subtitles(file_path, temp_sub):
            logger.error("Subtitle extraction failed")
            return None, proper_name

        if not create_sign_subtitles(temp_sub, temp_sign_sub):
            logger.error("Sign subtitle creation failed")
            return None, proper_name

        cmd = [
            "mkvmerge", "-o", output_path,
            "--language", "0:eng", "--track-name", "0:SignSub", 
            "--default-track", "0:yes", temp_sign_sub,
            file_path
        ]
        
        logger.info(f"Running mkvmerge command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        if result.returncode != 0:
            logger.error(f"mkvmerge failed: {result.stderr}")
            return None, proper_name

        # Clean up temp files
        for temp_file in [temp_sub, temp_sign_sub]:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception as e:
                logger.warning(f"Couldn't delete temp file {temp_file}: {e}")

        return output_path, proper_name

    except Exception as e:
        logger.error(f"Error in process_anime_file: {e}")
        return None, original_name

# Telegram handlers with enhanced error handling
@app.on_message(filters.document | filters.video)
async def handle_file(client: Client, message: Message):
    try:
        if message.document and message.document.file_size > MAX_FILE_SIZE:
            await message.reply(f"⚠️ File is too large (max {MAX_FILE_SIZE//1024//1024}MB). Please send a smaller file.")
            return
        
        file_name = message.document.file_name if message.document else message.video.file_name
        file_id = message.document.file_id if message.document else message.video.file_id
        
        logger.info(f"Received file: {file_name} (ID: {file_id[:6]}...)")
        
        parsed = anitopy.parse(file_name)
        anime_title = clean_filename(parsed.get('anime_title', 'Unknown'))
        cache_key = generate_file_key(file_id)
        
        download_path = os.path.join(TEMP_DIR, file_name)
        try:
            msg = await message.reply("⏳ Downloading file...")
            start_time = time.time()
            
            await message.download(file_name=download_path)
            dl_time = time.time() - start_time
            logger.info(f"Downloaded {file_name} in {dl_time:.2f} seconds")
            
            await msg.edit_text("🔍 Processing file...")
            output_path, proper_name = await process_anime_file(download_path, file_name)
            
            if not output_path:
                await msg.edit_text("❌ Failed to process file. Please check logs.")
                return
            
            proc_time = time.time() - start_time - dl_time
            logger.info(f"Processed {file_name} in {proc_time:.2f} seconds")
            
            thumbnail_url = await get_anilist_thumbnail(anime_title)
            
            response_text = (
                f"🎬 **Anime:** {anime_title}\n"
                f"📁 **Original:** `{file_name}`\n"
                f"✨ **Processed:** `{proper_name}`\n"
                f"⏱️ **Time:** Download: {dl_time:.1f}s | Process: {proc_time:.1f}s"
            )
            
            app.file_cache = getattr(app, 'file_cache', {})
            app.file_cache[cache_key] = (output_path, proper_name)
            
            if thumbnail_url:
                await message.reply_photo(
                    photo=thumbnail_url,
                    caption=response_text,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📥 Download", callback_data=f"dl_{cache_key}")
                    ]])
                )
            else:
                await message.reply(
                    text=response_text,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📥 Download", callback_data=f"dl_{cache_key}")
                    ]])
                )
                
        except Exception as e:
            logger.error(f"Error in handle_file: {e}")
            await message.reply("❌ An error occurred during processing. Please try again.")
        finally:
            try:
                if os.path.exists(download_path):
                    os.remove(download_path)
                if 'msg' in locals():
                    await msg.delete()
            except Exception as e:
                logger.warning(f"Cleanup error: {e}")
                
    except Exception as e:
        logger.error(f"Error in handle_file outer: {e}")
        await message.reply("❌ A critical error occurred. Please check logs.")

@app.on_callback_query(filters.regex(r"^dl_"))
async def handle_download(client: Client, callback_query):
    try:
        cache_key = callback_query.data.split("_")[1]
        cached_data = getattr(app, 'file_cache', {}).get(cache_key)
        
        if not cached_data or not os.path.exists(cached_data[0]):
            await callback_query.answer("❌ File expired. Please resend the file.", show_alert=True)
            return
        
        file_path, proper_name = cached_data
        await callback_query.answer("Preparing download...")
        
        try:
            start_time = time.time()
            await callback_query.message.reply_document(
                document=file_path,
                file_name=proper_name,
                caption="Here's your processed file with sign subtitles!"
            )
            dl_time = time.time() - start_time
            logger.info(f"Sent file {proper_name} in {dl_time:.2f} seconds")
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            await callback_query.message.reply("❌ Failed to send file. Please try again.")
        finally:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                if cache_key in getattr(app, 'file_cache', {}):
                    del app.file_cache[cache_key]
            except Exception as e:
                logger.warning(f"Cleanup error: {e}")
                
    except Exception as e:
        logger.error(f"Error in handle_download: {e}")
        await callback_query.answer("❌ An error occurred.", show_alert=True)

async def get_anilist_thumbnail(anime_title: str) -> Optional[str]:
    try:
        query = """
        query ($search: String) {
            Media (search: $search, type: ANIME) {
                coverImage {
                    large
                }
            }
        }
        """
        
        variables = {'search': anime_title}
        
        response = requests.post(ANILIST_API, json={'query': query, 'variables': variables}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get('data', {}).get('Media', {}).get('coverImage', {}).get('large')
    except Exception as e:
        logger.warning(f"Error fetching AniList thumbnail: {e}")
    return None

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    try:
        welcome_text = (
            "👋 **Welcome to Anime Sign Subtitle Bot!**\n\n"
            "Send me an anime file and I'll:\n"
            "1. Extract existing subtitles\n"
            "2. Create sign language subtitles\n"
            "3. Embed them as the first subtitle track\n"
            "4. Properly rename the file\n\n"
            f"📁 Supported formats: .mkv, .mp4 (max {MAX_FILE_SIZE//1024//1024}MB)\n"
            "⚙️ Requirements: FFmpeg and MKVToolNix must be installed"
        )
        await message.reply(welcome_text)
    except Exception as e:
        logger.error(f"Error in start_command: {e}")

if __name__ == "__main__":
    logger.info("Starting bot with enhanced debugging...")
    try:
        app.run()
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        sys.exit(1)
