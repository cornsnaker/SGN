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

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("anime_signer.log"),
        logging.StreamHandler()
    ]
)

# Constants
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_FILE_SIZE = 4 * 1024 * 1024 * 1024  # 4GB
ANILIST_API = "https://graphql.anilist.co"
TEMP_DIR = tempfile.gettempdir()

# Initialize Pyrogram Client
app = Client(
    "anime_signer_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

def generate_file_key(file_id: str) -> str:
    """Generate a short unique key for file caching."""
    return md5(file_id.encode()).hexdigest()[:8]

def clean_filename(anime_title: str) -> str:
    """Clean and format anime title."""
    anime_title = re.sub(r'[\[\]_]', ' ', anime_title)
    anime_title = re.sub(r'\s+', ' ', anime_title).strip()
    return anime_title.title()

def build_proper_filename(parsed: dict) -> str:
    """Build properly formatted filename using Anitopy data."""
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

def extract_subtitles(input_path: str, output_path: str) -> bool:
    """Extract subtitles from video file."""
    try:
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-map", "0:s:0",
            "-c:s", "ass",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logging.error(f"FFmpeg failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        logging.error(f"Error extracting subtitles: {e}")
        return False

def create_sign_subtitles(sub_path: str, output_path: str) -> bool:
    """Create sign language subtitles using specified keywords."""
    try:
        with open(sub_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # User-specified filter criteria
        style_keywords = ["sign", "signs", "overlay", "text", "caption"]
        effect_keywords = ["\\an", "\\pos", "\\move", "\\fad"]
        actor_keywords = ["sign", "signs"]

        lines = content.split('\n')
        filtered_lines = []
        in_events = False

        for line in lines:
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

                # Check style names
                if any(keyword.lower() in style for keyword in style_keywords):
                    filtered_lines.append(line)
                    continue

                # Check actor/name field
                if any(keyword.lower() in name for keyword in actor_keywords):
                    filtered_lines.append(line)
                    continue

                # Check effect tags
                if any(keyword in effect for keyword in effect_keywords):
                    filtered_lines.append(line)
                    continue

                # Check text for effect tags
                if any(keyword in text for keyword in effect_keywords):
                    filtered_lines.append(line)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(filtered_lines))
        return True
    except Exception as e:
        logging.error(f"Error creating sign subtitles: {e}")
        return False

async def process_anime_file(file_path: str, original_name: str) -> Tuple[Optional[str], str]:
    """Process an anime file to add sign subtitles."""
    try:
        parsed = anitopy.parse(original_name)
        proper_name = build_proper_filename(parsed)
        
        temp_sub = os.path.join(TEMP_DIR, f"temp_{md5(original_name.encode()).hexdigest()[:8]}.ass")
        temp_sign_sub = os.path.join(TEMP_DIR, f"sign_{md5(original_name.encode()).hexdigest()[:8]}.ass")
        output_path = os.path.join(TEMP_DIR, proper_name)

        if not extract_subtitles(file_path, temp_sub):
            return None, proper_name

        if not create_sign_subtitles(temp_sub, temp_sign_sub):
            return None, proper_name

        cmd = [
            "mkvmerge", "-o", output_path,
            "--language", "0:eng", "--track-name", "0:SignSub", 
            "--default-track", "0:yes", temp_sign_sub,
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logging.error(f"mkvmerge failed: {result.stderr}")
            return None, proper_name

        for temp_file in [temp_sub, temp_sign_sub]:
            if os.path.exists(temp_file):
                os.remove(temp_file)

        return output_path, proper_name
    except Exception as e:
        logging.error(f"Error processing file: {e}")
        return None, proper_name

# [Rest of the bot implementation remains the same...]
