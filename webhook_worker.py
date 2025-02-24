import requests
import asyncio
from db import db
from config import BEAMPAY_WEBHOOK_URLS

CONFIRMATIONS_REQUIRED = 80
MAX_RETRIES = 5  # Retry up to 5 times


async def notify_telegram(event_type, data):
    """Send transaction updates to Telegram monitoring."""
    messages = {
        "deposit_pending": f"⏳ *Deposit Pending*\n💰 Amount: {data['amount']} (Asset: {data['asset']})\n🔗 Tx: `{data['txId']}`",
        "deposit_confirmed": f"✅ *Deposit Confirmed*\n💰 Amount: {data['amount']} (Asset: {data['asset']})\n🔗 Tx: `{data['txId']}`",
        "withdraw_pending": f"📤 *Withdrawal Pending*\n💸 Amount: {data['amount']} (Asset: {data['asset']})\n🔗 Tx: `{data['txId']}`",
        "withdraw_confirmed": f"✅ *Withdrawal Confirmed*\n💸 Amount: {data['amount']} (Asset: {data['asset']})\n🔗 Tx: `{data['txId']}`",
        "failed": f"❌ *Transaction Failed*\n🔗 Tx: `{data['txId']}`\n📢 Reason: {data['reason']}",
        "cancelled": f"⚠️ *Transaction Cancelled*\n🔗 Tx: `{data['txId']}`"
    }
    if event_type in messages:
        await send_to_logs(messages[event_type], parse_mode="Markdown")


async def dispatch_webhook(event_type, data):
    """Send webhook notifications to all registered URLs."""
    if not BEAMPAY_WEBHOOK_URLS:
        print("❌ No webhook URLs configured in .env")
        await send_to_logs("❌ No webhook URLs configured in .env")
        return

    for webhook in BEAMPAY_WEBHOOK_URLS:
        attempt = 0
        while attempt < MAX_RETRIES:
            try:
                print(webhook, event_type, data)
                response = requests.post(webhook, json={"event": event_type, **data}, timeout=5)
                print(response.content)
                if response.status_code == 200:
                    print(f"✅ Webhook sent to {webhook}")
                    break  # Stop retrying if successful
            except Exception as e:
                print(f"❌ Failed to send webhook ({attempt+1}/{MAX_RETRIES}): {e}")

            attempt += 1
            await asyncio.sleep(10 * (2 ** attempt))  # Exponential Backoff (10s, 20s, 40s, etc.)

        if attempt == MAX_RETRIES:
            # Store failed webhook for later retry
            await db.failed_webhooks.insert_one({
                "url": webhook,
                "event_type": event_type,
                "data": data,
                "last_attempt": datetime.datetime.utcnow(),
                "attempts": attempt
            })
            await send_to_logs(f"❌ Webhook Failed: {webhook}\n🔄 Event: {event_type}")

    # Send Telegram alerts
    #await notify_telegram(event_type, data)


async def monitor_transactions():
    """Monitor transactions and trigger appropriate webhooks."""
    while True:
        transactions = await db.txs.find({"success": True}).to_list(None)
        for tx in transactions:
            tx_id = tx["_id"]
            asset_id = tx["asset_id"]
            value = tx["value"]
            status = tx["status"]
            confirmations = tx["confirmations"]
            webhook_sent = tx.get("webhook_sent", {})

            # Identify and dispatch missing webhooks
            if status in [0,1] and tx.get("income") and not webhook_sent.get("deposit_pending"):
                await dispatch_webhook("deposit_pending", {"txId": tx_id, "amount": value, "asset_id": asset_id, "address": tx['receiver']})
                webhook_sent["deposit_pending"] = True

            elif status == 3 and tx.get("income") and confirmations >= CONFIRMATIONS_REQUIRED and not webhook_sent.get("deposit_confirmed"):
                await dispatch_webhook("deposit_confirmed", {"txId": tx_id, "amount": value, "asset_id": asset_id, "address": tx['receiver']})
                webhook_sent["deposit_confirmed"] = True

            elif status in [0,1] and not tx.get("income") and not webhook_sent.get("withdraw_pending"):
                await dispatch_webhook("withdraw_pending", {"txId": tx_id, "amount": value, "asset_id": asset_id, "address": tx['sender']})
                webhook_sent["withdraw_pending"] = True

            elif status == 3 and not tx.get("income") and not webhook_sent.get("withdraw_confirmed"):
                await dispatch_webhook("withdraw_confirmed", {"txId": tx_id, "amount": value, "asset_id": asset_id, "address": tx['sender']})
                webhook_sent["withdraw_confirmed"] = True

            elif status == 4 and not webhook_sent.get("failed"):
                await dispatch_webhook("failed", {"txId": tx_id, "amount": value, "asset_id": asset_id, "reason": tx.get("failure_reason", "Unknown Error"), "address": tx['sender']})
                webhook_sent["failed"] = True

            elif status == 2 and not webhook_sent.get("cancelled"):
                await dispatch_webhook("cancelled", {"txId": tx_id, "amount": value, "asset_id": asset_id, "address": tx['sender']})
                webhook_sent["cancelled"] = True

            # Update transaction webhook history
            await db.txs.update_one({"_id": tx_id}, {"$set": {"webhook_sent": webhook_sent}})

        # Retry failed webhooks
        failed_webhooks = await db.failed_webhooks.find().to_list(None)
        for webhook in failed_webhooks:
            await dispatch_webhook(webhook["event_type"], webhook["data"])
            await db.failed_webhooks.delete_one({"_id": webhook["_id"]})  # Remove if successful

        await asyncio.sleep(10)  # Check every 10 seconds

if __name__ == "__main__":
    print("Launching Webhook Worker")
    asyncio.run(monitor_transactions())
