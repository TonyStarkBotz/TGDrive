import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import config
from utils.logger import Logger
from pathlib import Path

logger = Logger(__name__)

START_CMD = """🚀 **Welcome To TG Drive's Bot Mode**

You can use this bot to upload files to your TG Drive website directly instead of doing it from website.

🗄 **Commands:**
/set_folder - Set folder for file uploads
/current_folder - Check current folder

📤 **How To Upload Files:** Send a file to this bot and it will be uploaded to your TG Drive website. You can also set a folder for file uploads using /set_folder command.

Read more about [TG Drive's Bot Mode](https://github.com/TechShreyash/TGDrive#tg-drives-bot-mode)
"""

SET_FOLDER_PATH_CACHE = {}  # Cache to store folder path for each folder id
DRIVE_DATA = None
BOT_MODE = None
ACTIVE_CONVERSATIONS = {}  # Track active conversations

session_cache_path = Path(f"./cache")
session_cache_path.parent.mkdir(parents=True, exist_ok=True)

main_bot = Client(
    name="main_bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.MAIN_BOT_TOKEN,
    sleep_threshold=config.SLEEP_THRESHOLD,
    workdir=session_cache_path,
)

async def wait_for_response(client: Client, chat_id: int, question: str, timeout: int = 60) -> Message:
    """
    Wait for user response with timeout
    """
    # Send the question
    msg = await client.send_message(chat_id, question)
    
    # Create a future to wait for the response
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    
    # Store the future in active conversations
    ACTIVE_CONVERSATIONS[chat_id] = future
    
    try:
        # Wait for the response or timeout
        response = await asyncio.wait_for(future, timeout=timeout)
        return response
    except asyncio.TimeoutError:
        await msg.edit_text("⌛ Timeout! Please try the command again.")
        return None
    finally:
        # Clean up
        if chat_id in ACTIVE_CONVERSATIONS:
            del ACTIVE_CONVERSATIONS[chat_id]

@main_bot.on_message(filters.private & filters.text & ~filters.command)
async def handle_response(client: Client, message: Message):
    """
    Handle responses for active conversations
    """
    chat_id = message.chat.id
    if chat_id in ACTIVE_CONVERSATIONS:
        future = ACTIVE_CONVERSATIONS[chat_id]
        if not future.done():
            future.set_result(message)
            return True
    return False

@main_bot.on_message(
    filters.command(["start", "help"])
    & filters.private
    & filters.user(config.TELEGRAM_ADMIN_IDS),
)
async def start_handler(client: Client, message: Message):
    await message.reply_text(START_CMD)

@main_bot.on_message(
    filters.command("set_folder")
    & filters.private
    & filters.user(config.TELEGRAM_ADMIN_IDS),
)
async def set_folder_handler(client: Client, message: Message):
    global SET_FOLDER_PATH_CACHE, DRIVE_DATA

    # Ask for folder name
    response = await wait_for_response(
        client,
        message.chat.id,
        "📁 Send the folder name where you want to upload files\n\n/cancel to cancel"
    )

    if response is None:
        return  # Timeout occurred
    
    if response.text.lower() == "/cancel":
        await response.reply_text("❌ Cancelled")
        return

    folder_name = response.text.strip()
    search_result = DRIVE_DATA.search_file_folder(folder_name)

    # Get folders from search result
    folders = {}
    for item in search_result.values():
        if item.type == "folder":
            folders[item.id] = item

    if len(folders) == 0:
        await response.reply_text(f"❌ No folder found with name '{folder_name}'")
        return

    # Prepare folder selection buttons
    buttons = []
    folder_cache = {}
    folder_cache_id = len(SET_FOLDER_PATH_CACHE) + 1

    for folder in search_result.values():
        path = folder.path.strip("/")
        folder_path = "/" + ("/" + path + "/" + folder.id).strip("/")
        folder_cache[folder.id] = (folder_path, folder.name)
        buttons.append(
            [
                InlineKeyboardButton(
                    folder.name,
                    callback_data=f"set_folder_{folder_cache_id}_{folder.id}",
                )
            ]
        )
    SET_FOLDER_PATH_CACHE[folder_cache_id] = folder_cache

    await response.reply_text(
        "📂 Select the folder where you want to upload files:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

@main_bot.on_callback_query(
    filters.user(config.TELEGRAM_ADMIN_IDS) & filters.regex(r"set_folder_")
)
async def set_folder_callback(client: Client, callback_query: Message):
    global SET_FOLDER_PATH_CACHE, BOT_MODE

    folder_cache_id, folder_id = callback_query.data.split("_")[2:]

    folder_path_cache = SET_FOLDER_PATH_CACHE.get(int(folder_cache_id))
    if folder_path_cache is None:
        await callback_query.answer("⌛ Request expired, please send /set_folder again")
        await callback_query.message.delete()
        return

    folder_path, name = folder_path_cache.get(folder_id)
    del SET_FOLDER_PATH_CACHE[int(folder_cache_id)]
    BOT_MODE.set_folder(folder_path, name)

    await callback_query.answer(f"✅ Folder set to: {name}")
    await callback_query.message.edit_text(
        f"📁 **Folder Set Successfully**\n\n"
        f"📍 Path: `{folder_path}`\n"
        f"📛 Name: {name}\n\n"
        "Now you can send files to me and they will be uploaded to this folder."
    )

@main_bot.on_message(
    filters.command("current_folder")
    & filters.private
    & filters.user(config.TELEGRAM_ADMIN_IDS),
)
async def current_folder_handler(client: Client, message: Message):
    global BOT_MODE

    if not BOT_MODE.current_folder_name:
        await message.reply_text("ℹ️ No folder is currently set. Use /set_folder to set one.")
    else:
        await message.reply_text(
            f"📂 **Current Folder**\n\n"
            f"📍 Path: `{BOT_MODE.current_folder}`\n"
            f"📛 Name: {BOT_MODE.current_folder_name}"
        )

@main_bot.on_message(
    filters.private
    & filters.user(config.TELEGRAM_ADMIN_IDS)
    & (
        filters.document
        | filters.video
        | filters.audio
        | filters.photo
        | filters.sticker
    )
)
async def file_handler(client: Client, message: Message):
    global BOT_MODE, DRIVE_DATA

    if not BOT_MODE.current_folder:
        await message.reply_text(
            "❌ No upload folder set!\n"
            "Please set a folder first using /set_folder command."
        )
        return

    try:
        # Copy file to storage channel
        copied_message = await message.copy(config.STORAGE_CHANNEL)
        file = (
            copied_message.document
            or copied_message.video
            or copied_message.audio
            or copied_message.photo
            or copied_message.sticker
        )

        # Get file name based on type
        if message.document:
            file_name = message.document.file_name
        elif message.video:
            file_name = message.video.file_name or f"video_{message.id}.mp4"
        elif message.audio:
            file_name = message.audio.file_name or f"audio_{message.id}.mp3"
        elif message.photo:
            file_name = f"photo_{message.id}.jpg"
        elif message.sticker:
            file_name = f"sticker_{message.id}.webp"

        # Add to drive data
        DRIVE_DATA.new_file(
            BOT_MODE.current_folder,
            file_name,
            copied_message.id,
            file.file_size if hasattr(file, 'file_size') else 0,
        )

        await message.reply_text(
            f"✅ **File Uploaded Successfully**\n\n"
            f"📄 Name: `{file_name}`\n"
            f"📁 Folder: {BOT_MODE.current_folder_name}\n"
            f"📦 Size: {get_size_format(file.file_size) if hasattr(file, 'file_size') else 'N/A'}"
        )
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        await message.reply_text(
            "❌ Failed to upload file!\n"
            "Please try again or check logs for details."
        )

def get_size_format(size_in_bytes):
    """Convert file size to human-readable format"""
    if not size_in_bytes:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} TB"

async def start_bot_mode(d, b):
    global DRIVE_DATA, BOT_MODE
    DRIVE_DATA = d
    BOT_MODE = b

    logger.info("Starting Main Bot")
    await main_bot.start()

    await main_bot.send_message(
        config.STORAGE_CHANNEL, 
        "🔔 Main Bot Started -> TG Drive's Bot Mode Enabled"
    )
    logger.info("Main Bot Started")
    logger.info("TG Drive's Bot Mode Enabled")