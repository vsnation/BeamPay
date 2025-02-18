from __future__ import print_function
import asyncio
import datetime
import traceback
from lib.beam import BEAMWalletAPI
from db import db
from config import BEAM_API_RPC

# Configuration
beam_api = BEAMWalletAPI(BEAM_API_RPC)

# Constants
CONFIRMATION_THRESHOLD = 5

async def process_transactions():
    """Processes and updates transactions in the database."""
    limit = 100
    skip = 0
    while True:
        # Fetch transactions from the API
        transactions = sorted(beam_api.tx_list(skip=skip, count=limit), key=lambda x: x['create_time'])  # Get all transactions
        if not transactions:
            return
        skip += len(transactions)

        for tx in transactions:
            try:

                tx_id = tx["txId"]
                asset_id = str(tx["asset_id"])  # Convert asset_id to string for MongoDB keys
                value = int(tx["value"])  # Use integers for calculations
                fee = int(tx["fee"])  # Use integers for fee
                status = tx["status"]
                confirmations = tx.get("confirmations", 0)

                # Fetch existing transaction from the database
                existing_tx = await db.txs.find_one({"_id": tx_id})

                # Skip if the transaction has already been successfully processed
                if existing_tx and existing_tx.get("success", False):
                    continue

                if existing_tx:
                    update_fields = {}

                    # If the status has changed
                    if status != existing_tx["status"]:
                        update_fields["status"] = status
                        update_fields["status_string"] = tx["status_string"]

                    if status in [2, 3]:
                        await handle_failed_transaction(tx)

                    # Update confirmations
                    if existing_tx.get("confirmations", 0) != confirmations:
                        update_fields["confirmations"] = confirmations

                    # Apply updates if any
                    if update_fields:
                        await db.txs.update_one(
                            {"_id": tx_id},
                            {"$set": update_fields}
                        )

                else:
                    # Insert new transaction
                    tx_data = {
                        "_id": tx_id,
                        "status": status,
                        "status_string": tx["status_string"],
                        "income": tx["income"],
                        "type": tx["tx_type"],
                        "type_string": tx["tx_type_string"],
                        "asset_id": asset_id,
                        "value": str(value),
                        "fee": str(fee),
                        "sender": tx["sender"],
                        "receiver": tx["receiver"],
                        "sender_identity": tx.get("sender_identity", ""),
                        "receiver_identity": tx.get("receiver_identity", ""),
                        "comment": tx.get("comment", ""),
                        "create_time": tx["create_time"],
                        "confirmations": confirmations,
                        "kernel": tx.get("kernel", ""),
                        "failure_reason": tx.get("failure_reason", ""),
                        "rates": tx.get("rates", []),
                        "success": False,  # Initial state. If fully checked.
                    }
                    await db.txs.insert_one(tx_data)

                    # Handle balance updates based on the transaction status
                    if status == 3:
                        # Update locked balance
                        await handle_locked_balance(tx)
                        # Refresh available balance only if confirmations are sufficient
                        if confirmations >= CONFIRMATION_THRESHOLD:
                            await handle_finalized_transaction(tx)
                            # Mark transaction as successfully processed
                            await db.txs.update_one(
                                {"_id": tx_id},
                                {"$set": {"success": True}}
                            )

            except Exception as exc:
                traceback.print_exc()

async def handle_locked_balance(tx):
    """Lock funds for a pending or in-progress transaction."""
    receiver = tx["receiver"]
    sender = tx["sender"]
    asset_id = str(tx["asset_id"])  # Asset ID as string
    value = int(tx["value"])  # Convert value to integer
    fee = int(tx.get("fee", 0))  # Convert value to integer

    if receiver:
        # Lock funds in the receiver's address
        await update_balance(receiver, asset_id, locked_delta=value)
        print(f"{receiver} Received {value} aid: {asset_id}")
    if sender:
        # If you're a service you should lock the funds of the sender by decreasing "available" balance to "locked" balance.
        # await update_balance(sender, asset_id, available_delta=((value + fee) * -1), locked_delta=value)
        print(f"{sender} Sent {value} aid: {asset_id}")


async def handle_failed_transaction(tx):
    """Remove locked funds for canceled or failed transactions."""
    sender = tx["sender"]
    asset_id = str(tx["asset_id"])  # Asset ID as string
    value = int(tx["value"])  # Convert value to integer
    fee = int(tx.get("fee", 0))  # Convert value to integer

    if sender:
        # Unlock funds for the sender
        await update_balance(sender, asset_id, locked_delta=value * -1, available_delta=(value + fee))
        print(f"Sent {value} aid: {asset_id}")


async def handle_finalized_transaction(tx):
    """Move locked funds to available for finalized transactions."""
    receiver = tx["receiver"]
    sender = tx["sender"]
    asset_id = str(tx["asset_id"])  # Asset ID as string
    value = int(tx["value"])  # Convert value to integer

    if receiver:
        # Move funds to the receiver's available balance
        print(f"{receiver} Received {value} aid: {asset_id} | Confirmed")
        await update_balance(receiver, asset_id, available_delta=value, locked_delta=value * -1)

    if sender:
        # Deduct funds from the sender's available balance
        print(f"{sender} Sent {value} aid: {asset_id} | Confirmed")
        await update_balance(sender, asset_id, locked_delta=value * -1)

async def update_balance(address, asset_id, available_delta=0, locked_delta=0):
    """Update balance for a specific address and asset."""
    address_data = await db.addresses.find_one({"_id": address})
    if not address_data:
        # print(f"Address not found: {address}")
        return

    # Read current balances
    current_available = int(address_data["balance"]["available"].get(asset_id, "0"))
    current_locked = int(address_data["balance"]["locked"].get(asset_id, "0"))

    # Update balances
    updated_available = current_available + available_delta
    updated_locked = current_locked + locked_delta

    # Write updates back to the database
    await db.addresses.update_one(
        {"_id": address},
        {
            "$set": {
                f"balance.available.{asset_id}": str(updated_available),
                f"balance.locked.{asset_id}": str(updated_locked),
            }
        }
    )
    # print(f"New transaction to {address}. Updated Locked: {updated_available} | Available: {updated_locked}")


async def sync_addresses():
    """
    Synchronize wallet addresses with the database by fetching the addr_list and adding missing ones.
    """
    try:
        """
            Synchronize wallet addresses from the BEAM Wallet API to the database.
            """
        print("Synchronizing addresses...")
        addresses = beam_api.addr_list()  # Fetch all addresses

        for addr in addresses:
            address_id = addr["address"]
            expired = addr["expired"]

            # Check if the address exists in the database
            existing_address = await db.addresses.find_one({"_id": address_id})

            if not existing_address:
                # Add new address to the database
                address_data = {
                    "_id": address_id,
                    "own_id": addr.get("own_id", ""),
                    "type": addr.get("type", ""),
                    "balance": {
                        "available": {},
                        "locked": {},
                    },
                    "identity": addr.get("identity", ""),
                    "create_time": addr.get("create_time", ""),
                    "category": addr.get("category", ""),
                    "comment": addr.get("comment", ""),
                    "wallet_id": addr.get("wallet_id", ""),
                }
                await db.addresses.insert_one(address_data)
                print(f"New Address {address_id} found and added.")

            # Check if the address is expired and extend its expiration
            if expired:
                print(f"Address {address_id} is expired. Extending expiration to 'never'.")
                beam_api.edit_address(address=address_id, expiration="never")

        print("Address synchronization completed.")

    except Exception as e:
        print(f"Error synchronizing addresses: {e}")
        traceback.print_exc()


async def verify_balances():
    """
    Verify wallet balances by comparing the BEAM Wallet API balance with stored balances in the database.
    """
    try:
        print("Verifying wallet balances...")

        # Fetch wallet balance from BEAM API
        wallet_status = beam_api.wallet_status()
        if not wallet_status:
            print("Error: Failed to fetch wallet status from API.")
            return

        # Extract balances from API response
        api_balances = {}
        for asset in wallet_status["totals"]:
            asset_id = str(asset["asset_id"])  # Convert to string for consistency
            available = int(asset["available"])
            locked = int(asset["locked"])
            api_balances[asset_id] = {"available": available, "locked": locked}

        # Fetch balances from the database
        db_balances = {}
        async for address in db.addresses.find({}, {"balance": 1}):
            for asset_id, amount in address["balance"]["available"].items():
                db_balances.setdefault(asset_id, {"available": 0, "locked": 0})
                db_balances[asset_id]["available"] += int(amount)

            for asset_id, amount in address["balance"]["locked"].items():
                db_balances.setdefault(asset_id, {"available": 0, "locked": 0})
                db_balances[asset_id]["locked"] += int(amount)

        # Compare balances
        discrepancies = []
        for asset_id in set(api_balances.keys()).union(set(db_balances.keys())):
            api_available = api_balances.get(asset_id, {}).get("available", 0)
            db_available = db_balances.get(asset_id, {}).get("available", 0)
            api_locked = api_balances.get(asset_id, {}).get("locked", 0)
            db_locked = db_balances.get(asset_id, {}).get("locked", 0)

            if api_available != db_available or api_locked != db_locked:
                discrepancies.append({
                    "asset_id": asset_id,
                    "api_available": api_available,
                    "db_available": db_available,
                    "api_locked": api_locked,
                    "db_locked": db_locked,
                })

        # Report results
        if discrepancies:
            print("⚠️ Balance Discrepancies Found:")
            for d in discrepancies:
                print(f"Asset {d['asset_id']}:")
                print(f"  API Available: {d['api_available']} | DB Available: {d['db_available']}")
                print(f"  API Locked: {d['api_locked']} | DB Locked: {d['db_locked']}")
        else:
            print("✅ All balances match between the API and the database.")

    except Exception as e:
        print(f"Error verifying balances: {e}")
        traceback.print_exc()


async def process_payments():
    """
    Main function to update rates, addresses, and process transactions.
    """
    await verify_balances()
    await sync_addresses()
    await process_transactions()

async def main():
    await process_payments()

if __name__ == "__main__":
    asyncio.run(main())
