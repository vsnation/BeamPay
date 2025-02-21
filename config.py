import json
import os
from telegram.ext import ApplicationBuilder

from dotenv import load_dotenv
load_dotenv(dotenv_path='.env')

# Get environment variables or fallback to default
DATABASE_URL = os.getenv("DATABASE_URL")
BEAM_API_RPC = os.getenv("BEAM_WALLET_API_RPC")


# Load Telegram Bot Token from ENV
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_GROUP_MONITOR_ID = os.getenv("TELEGRAM_GROUP_MONITOR_ID")

CONFIRMATION_THRESHOLD = os.getenv("CONFIRMATION_THRESHOLD")
BEAMPAY_API_URL = os.getenv("BEAMPAY_API_URL")
BEAMPAY_API_KEY = os.getenv("BEAMPAY_API_KEY")

TG_APP = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build() if TELEGRAM_BOT_TOKEN else None



async def send_to_logs(text, ch="general", parse_mode=None):
    """Send logs/alerts to a Telegram chat."""
    if not TG_APP or not TELEGRAM_GROUP_MONITOR_ID:
        return  # Skip if Telegram bot is not configured
    try:
        if "<a href" in str(text):
            parse_mode = "HTML"

        await TG_APP.bot.send_message(
            TELEGRAM_GROUP_MONITOR_ID,
            text,
            parse_mode=parse_mode,
            disable_web_page_preview=True
        )
    except Exception as exc:
        print(f"Telegram Error: {exc}")

