from __future__ import print_function
import math
import asyncio
import datetime
import traceback
from lib.beam import BEAMWalletAPI
from db import db
from config import BEAM_API_RPC, send_to_logs, CONFIRMATION_THRESHOLD

# Configuration
beam_api = BEAMWalletAPI(BEAM_API_RPC)

# Constants

# Load assets globally at startup
ASSETS = {}

async def load_assets():
    """Load asset metadata from the database."""
    global ASSETS
    assets = await db.assets.find().to_list(None)
    ASSETS = {str(asset["_id"]): asset.get("metadata_pairs", {}).get("UN", f"Asset {asset['_id']}") for asset in assets}

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

                    if status in [2, 4]:
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


                     # Refresh available balance only if confirmations are sufficient
                    if status == 3 and confirmations >= CONFIRMATION_THRESHOLD:
                        await handle_finalized_transaction(tx)
                        # Mark transaction as successfully processed
                        await db.txs.update_one(
                            {"_id": tx_id},
                            {"$set": {"success": True}}
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
    """Lock funds in receiver’s wallet and pending in sender’s wallet."""
    receiver = tx["receiver"]
    sender = tx["sender"]
    asset_id = str(tx["asset_id"])
    value = int(tx["value"])
    fee = int(tx.get("fee", 0))

    sender_exists = await db.addresses.find_one({"_id": sender})
    receiver_exists = await db.addresses.find_one({"_id": receiver})

    if sender_exists:
        # Deduct from sender's available balance
        await update_balance(sender, asset_id, available_delta=-value, locked_delta=value)
        # Deduct BEAM fee from available balance
        await update_balance(sender, "0", available_delta=-fee, locked_delta=fee)
        print(f"Locked {value} of Asset {asset_id} & {fee} BEAM from {sender}.")

    if receiver_exists:
        # Incoming Transfer: Lock amount for pending deposit
        print(f"Locked {value} for Receiver")
        await update_balance(receiver, asset_id, locked_delta=value)

async def handle_finalized_transaction(tx):
    """Move locked funds to available after confirmation threshold is met."""
    receiver = tx["receiver"]
    sender = tx["sender"]
    asset_id = str(tx["asset_id"])
    value = int(tx["value"])
    kernel = tx.get('kernel', tx["txId"])
    fee = int(tx.get("fee", 0))

    # Get human-readable asset name
    asset_name = ASSETS.get(asset_id, f"??? {asset_id}")

    # Format value
    value_formatted = f"{value / 10**8:,.8f}"  # Assuming 8 decimal places


    sender_exists = await db.addresses.find_one({"_id": sender})
    receiver_exists = await db.addresses.find_one({"_id": receiver})

    is_notified = False
    # If sender & receiver are both in the system, notify them both
    if sender_exists and receiver_exists:
        await send_to_logs(
            f"🔄 *Internal Transfer Confirmed*\n💱 *Amount:* `{value_formatted} {asset_name}`\n🔀 *From:* `{sender}` ➡ *To:* `{receiver}`\n🆔 *Kernel:* `{kernel}`",
            parse_mode="Markdown"
        )
        is_notified = True

    if sender_exists:
        # Outgoing or Internal Transfer: Unlock funds (deduct permanently)
        print(f"Finalised. Released Locked -{value + fee} for Sender")
        # Deduct locked funds and fee from sender
        await update_balance(sender, asset_id, locked_delta=-value)
        await update_balance(sender, "0", locked_delta=-fee)  # Deduct BEAM fee
        if not is_notified:
            await send_to_logs(
                f"✅ *Withdrawal Confirmed*\n💸 *Amount:* `{value_formatted} {asset_name}`\n📤 *From:* `{sender}`\n🆔 *Kernel:* `{kernel}`",
                parse_mode="Markdown"
            )

    if receiver_exists:
        # Incoming Transfer: Move funds to available
        print(f"Finalised. Released Locked -{value} for Receiver")
        await update_balance(receiver, asset_id, available_delta=value, locked_delta=-value)
        if not is_notified:
            await send_to_logs(
                f"✅ *Deposit Confirmed*\n💰 *Amount:* `{value_formatted} {asset_name}`\n📥 *To:* `{receiver}`\n🆔 *Kernel:* `{kernel}`",
                parse_mode="Markdown"
            )

async def handle_failed_transaction(tx):
    """Restore locked funds if transaction fails."""
    sender = tx["sender"]
    asset_id = str(tx["asset_id"])
    value = int(tx["value"])
    fee = int(tx.get("fee", 0))

    sender_exists = await db.addresses.find_one({"_id": sender})

    if sender_exists:
        # Refund locked funds and BEAM fee back to sender
        await update_balance(sender, asset_id, available_delta=value, locked_delta=-value)
        await update_balance(sender, "0", available_delta=fee, locked_delta=-fee)  # Refund BEAM fee


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
            if existing_address and 'identity' not in existing_address:
                print(f"Updated existed address {address_id}")
                await db.addresses.update_one(
                        {"_id": address_id},
                        {"$set": {
                            "own_id": addr.get("own_id", ""),
                            "type": addr.get("type", ""),
                            "identity": addr.get("identity", ""),
                            "create_time": addr.get("create_time", ""),
                            "category": addr.get("category", ""),
                            "comment": addr.get("comment", ""),
                            "wallet_id": addr.get("wallet_id", ""),
                        }}
                )
                return

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
        print(wallet_status)
        if "totals" not in wallet_status:
            return

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



async def sync_assets():
    """
    Synchronize all registered assets on the Beam blockchain with MongoDB.
    """
    try:
        print("Syncing assets from the Beam blockchain...")
        beam_asset = { "_id" : "0", "asset_id" : 0, "asset_meta" : "", "confirmations" : 0, "height" : 0, "issue_height" : 0, "owner_id" : "", "emission" : "", "locked_amount" : "", "max_emission" : "", "metadata_signature" : "", "is_owned" : False, "schema_version" : "", "nft_metadata" : "", "nft_rules" : "", "updated_at" : 0, "metadata" : "STD:SCH_VER=1;N=Beam Token;SN=Beam;UN=BEAM;NTHUN=GROTH", "metadata_pairs" : { "N" : "Beam Token", "NTHUN" : "GROTH", "SCH_VER" : "1", "SN" : "Beam", "UN" : "BEAM" }, "decimals" : 8 }
        _is_beam_exist = await db.assets.find_one({"_id": "0"})
        if not _is_beam_exist:
            await db.assets.insert_one(beam_asset)


        # Fetch asset list from Beam blockchain
        assets = beam_api.assets_list(refresh=True)
        if not assets:
            print("No assets found on the Beam blockchain.")
            return

        for asset in assets:
            asset_id = str(asset["asset_id"])  # Convert asset_id to string for MongoDB keys

            # Fetch existing asset data from the database
            existing_asset = await db.assets.find_one({"_id": asset_id})

            decimals = 8
            if asset.get("metadata_pairs", {}) and "NTH_RATIO" in asset["metadata_pairs"]:
                try:
                    decimals = int(math.log10(int(asset["metadata_pairs"]['NTH_RATIO'])))
                except Exception as exc:
                    decimals = 8

            # Prepare asset data
            asset_data = {
                "_id": asset_id,
                "asset_id": asset["asset_id"],
                "asset_meta": asset.get("asset_meta", ""),
                "confirmations": asset.get("confirmations", 0),
                "height": asset.get("height", 0),
                "issue_height": asset.get("issue_height", 0),
                "owner_id": asset.get("owner_id", ""),
                "emission": asset.get("emission", ""),
                "locked_amount": asset.get("locked_amount", ""),
                "max_emission": asset.get("max_emission", ""),
                "decimals": decimals,
                "metadata": asset.get("metadata", ""),
                "metadata_pairs": asset.get("metadata_pairs", {}),
                "metadata_signature": asset.get("metadata_signature", ""),
                "is_owned": asset.get("is_owned", False),
                "schema_version": asset.get("schema_version", ""),
                "nft_metadata": asset.get("nft_metadata", ""),
                "nft_rules": asset.get("nft_rules", ""),
                "updated_at": asset.get("updated_at", 0),
            }

            # If asset already exists, update if changes are detected
            if existing_asset:
                del asset_data["_id"]  # MongoDB does not allow updating _id
                await db.assets.update_one({"_id": asset_id}, {"$set": asset_data})
                #print(f"Updated asset {asset_id}")
            else:
                # Insert new asset
                await db.assets.insert_one(asset_data)
                print(f"Inserted new asset {asset_id}")

        print("Asset synchronization completed successfully.")

    except Exception as e:
        print(f"Error syncing assets: {e}")
        traceback.print_exc()

async def process_payments():
    """
    Main function to update rates, addresses, and process transactions.
    """
    await sync_assets()
    await load_assets()
    await verify_balances()
    await sync_addresses()
    await process_transactions()

async def main():
    await process_payments()

if __name__ == "__main__":
    asyncio.run(main())
