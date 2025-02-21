import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext
import aiohttp
from db import db
from config import TG_APP as app
from config import BEAMPAY_API_KEY, BEAMPAY_API_URL

# Logging setup
logging.basicConfig(level=logging.INFO)


async def get_or_create_user(user_id: int):
    """Retrieve user from DB or create a new one."""
    user = await db.users.find_one({"_id": user_id})
    if not user:
        response = await call_api("/create_wallet", method="POST", data={"note": str(user_id)})
        if "address" in response:
            user = {"_id": user_id, "address": response["address"]}
            await db.users.insert_one(user)
        else:
            return None
    return user

async def call_api(endpoint: str, method="GET", data=None):
    """Helper function to call BeamPay API."""
    url = f"{BEAMPAY_API_URL}{endpoint}"
    headers = {"X-API-Key": f"{BEAMPAY_API_KEY}"}  # Add API key to headers
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.request(method, url, json=data) as response:
            return await response.json()

async def start(update: Update, context: CallbackContext):
    """Start command - registers a user if not exists."""
    user_id = update.message.chat_id
    user = await get_or_create_user(user_id)
    if not user:
        await update.message.reply_text("🚨 Error creating your account. Please try again later.")
        return
    await update.message.reply_text("✅ Welcome to BeamPay! Use /deposit, /balance, or /withdraw.")

async def deposit(update: Update, context: CallbackContext):
    """Retrieve deposit address for the user."""
    user_id = update.message.chat_id
    user = await get_or_create_user(user_id)
    if user:
        await update.message.reply_text(f"💰 Your deposit address: `{user['address']}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("🚨 Error retrieving your deposit address.")

async def balance(update: Update, context: CallbackContext):
    """Check the user's balance and format it in a human-readable way."""
    user_id = update.message.chat_id
    user = await get_or_create_user(user_id)
    if not user:
        await update.message.reply_text("🚨 Error retrieving your balance.")
        return

    # Fetch user's balance
    balance_data = await call_api(f"/balances?address={user['address']}")
    print(balance_data)
    
    # Fetch assets metadata from the BeamPay API
    assets_data = await call_api("/assets")
    assets = {str(a["asset_id"]): a for a in assets_data}  # Convert asset_id to string for matching
    
    # Format the balance output
    balance_text = "📊 *Your Balances:*\n"

    for asset_id, raw_value in balance_data.get("available", {}).items():
        if asset_id == "0":  # Default BEAM asset
            name = "BEAM"
            decimals = 8
        else:
            asset = assets.get(asset_id, {})
            name = asset.get("metadata_pairs", {}).get("N", f"Asset {asset_id}")  # Default to Asset ID if no name
            decimals = asset.get("decimals", 8)  # Default to 8 decimals

        formatted_available = int(raw_value) / (10 ** decimals)  # Convert to human-readable format
        formatted_locked = int(balance_data.get("locked", {}).get(asset_id, "0")) / (10 ** decimals)

        balance_text += f"🔹 *{name}* ({asset_id}):\n"
        balance_text += f"   ✅ Available: `{formatted_available:.{decimals}f} {name}`\n"
        balance_text += f"   🔒 Locked: `{formatted_locked:.{decimals}f} {name}`\n"

    await update.message.reply_text(balance_text, parse_mode="Markdown")

async def withdraw(update: Update, context: CallbackContext):
    """Process a withdrawal request."""
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("🚨 Usage: /withdraw <asset_id> <target_address> <amount>")
        return

    user_id = update.message.chat_id
    user = await get_or_create_user(user_id)
    if not user:
        await update.message.reply_text("🚨 Error processing your withdrawal.")
        return

    asset_id, target_address, amount = args[0], args[1], args[2]
    amount_in_groths = int(float(amount) * 1e8)  # Convert amount to groths
    response = await call_api("/withdraw", method="POST", data={
            "from_address": user["address"],
            "to_address": target_address,
            "asset_id": asset_id,
            "amount": amount_in_groths
        })
    print(response)
    
    if "txId" in response:
        await update.message.reply_text(f"✅ Withdrawal successful! TxID: `{response['txId']}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("🚨 Withdrawal failed.")

# Register bot commands
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("deposit", deposit))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CommandHandler("withdraw", withdraw))

if __name__ == "__main__":
    logging.info("🚀 Telegram bot is running...")
    app.run_polling()

