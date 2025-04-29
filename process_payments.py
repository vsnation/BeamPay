from __future__ import print_function
import math
import asyncio
import requests
import json
import datetime
import traceback
from lib.beam import BEAMWalletAPI
from db import db
from config import BEAM_API_RPC, send_to_logs, CONFIRMATION_THRESHOLD
from config import VERIFIED_CA, SPAM_CA, DEX_CONTRACT_ID
import aiohttp


# Configuration
beam_api = BEAMWalletAPI(BEAM_API_RPC)

wallet_status = beam_api.wallet_status()
print(beam_api.block_details(wallet_status['current_height']))
#print(beam_api.get_utxo(count=100, sort_field="status", sort_direction="asc", filter={"asset_id": int(0)}))

# Update BEAM Price
COINGECKO_API_URL = "https://api.coingecko.com/api/v3/simple/price?ids=beam&vs_currencies=usd"

async def fetch_beam_price():
    """Fetch BEAM price from CoinGecko and store in MongoDB."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(COINGECKO_API_URL) as response:
                data = await response.json()

        if "beam" not in data or "usd" not in data["beam"]:
            print("⚠️ Failed to fetch BEAM price from CoinGecko.")
            return

        beam_price = data["beam"]["usd"]

        await db.price.update_one(
            {"_id": "beam_usd"},
            {"$set": {"price": beam_price, "last_updated": datetime.datetime.utcnow()}},
            upsert=True
        )

        print(f"✅ Updated BEAM price: ${beam_price}")

    except Exception as e:
        print(f"❌ Error fetching BEAM price: {e}")

# Load assets globally at startup
ASSETS = {}

async def load_assets():
    """Load asset metadata from the database."""
    global ASSETS
    assets = await db.assets.find().to_list(None)
    ASSETS = {str(asset["_id"]): asset.get("meta", {}).get("UN", f"Asset {asset['_id']}") for asset in assets}

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

                elif status in [1, 3, 5]:
                    # Insert new transaction
                    tx_data = {
                        "_id": tx_id,
                        "status": status,
                        "status_string": tx["status_string"],
                        "income": tx.get("income", None),
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
                        "create_time": int(tx["create_time"]),
                        "confirmations": confirmations,
                        "kernel": tx.get("kernel", ""),
                        "failure_reason": tx.get("failure_reason", ""),
                        "rates": tx.get("rates", []),
                        "success": False,  # Initial state. If fully checked.
                        "webhook_sent": {}
                    }
                    await db.txs.insert_one(tx_data)

                    # Get human-readable asset name
                    try:
                        if tx.get('income', None):
                            asset_name = ASSETS.get(asset_id, f"??? {asset_id}")

                            # Format value
                            value_formatted = f"{int(value) / 10**8:,.8f}"  # Assuming 8 decimal places
                            #await send_to_logs(
                            #    f"⏳ *Deposit Pending*\n💰 *Amount*: `{value_formatted} {asset_name}`\n📥 *To*: `{tx['receiver']}`\n🔗 *Tx*: `{tx_id}`",
                            #    parse_mode="Markdown"
                            #)
                    except Exception as exc:
                        print(exc)

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
    tx_id = tx['txId']

    # Get human-readable asset name
    asset_name = ASSETS.get(asset_id, f"??? {asset_id}")

    # Format value
    value_formatted = f"{value / 10**8:,.8f}"  # Assuming 8 decimal places


    sender_exists = await db.addresses.find_one({"_id": sender})
    receiver_exists = await db.addresses.find_one({"_id": receiver})

    # Check if TX exists in pending_withdrawals
    pending_tx = await db.pending_withdrawals.find_one({"txId": tx_id})

    if pending_tx:
        # Mark withdrawal as "pending" (allow send it again)
        await db.pending_withdrawals.update_one(
            {"txId": tx_id},
            {"$set": {"status": "sent_confirmed"}}
        )

    is_notified = False
    # If sender & receiver are both in the system, notify them both
    if sender_exists and receiver_exists:
        await send_to_logs(
            f"*[3/3]* 🔄 *Internal Transfer Confirmed*\n💱 *Amount:* `{value_formatted} {asset_name}`\n🔀 *From:* `{sender}` ➡ *To:* `{receiver}`\n🆔 *Kernel:* `{kernel}`",
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
                f"*[3/3]*✅ *Withdrawal Confirmed*\n💸 *Amount:* `{value_formatted} {asset_name}`\n📤 *From:* `{sender}`\n🆔 *Kernel:* `{kernel}`",
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
    """Mark withdrawal as failed & allow reprocessing without modifying balances."""
    sender = tx["sender"]
    receiver = tx["receiver"]
    asset_id = str(tx["asset_id"])
    value = int(tx["value"])
    fee = int(tx.get("fee", 0))
    tx_id = tx["txId"]

    # Check if TX exists in pending_withdrawals
    pending_tx = await db.pending_withdrawals.find_one({"txId": tx_id})
    if pending_tx:
        # Mark withdrawal as "pending" (allow send it again)
        await db.pending_withdrawals.update_one(
            {"txId": tx_id},
            {"$set": {"status": "failed"}}
        )
        await db.txs.update_one(
            {"_id": tx_id},
            {"$set": {"success": True}}
        )
        await send_to_logs(
            f"❌ *Withdrawal Failed*\n"
            f"🔗 *From:* `{sender}` ➡ *To:* `{receiver}`\n"
            f"💰 *Amount:* `{value/ 10**8:,.8f} {ASSETS.get(str(asset_id), '???')}`\n"
            f"🆔 *Pending TX:* `{tx_id}`",
            parse_mode="Markdown"
        )
        sender_exists = await db.addresses.find_one({"_id": sender})
        if sender_exists:
            # Refund locked funds and BEAM fee back to sender
            await update_balance(sender, asset_id, available_delta=value, locked_delta=-value)
            await update_balance(sender, "0", available_delta=fee, locked_delta=-fee)  # Refund BEAM fee
        return

    receiver_exists = await db.addresses.find_one({"_id": receiver})
    if receiver_exists:
        await update_balance(receiver, asset_id, locked_delta=-value)
        await send_to_logs(
            f"❌ *DEPOSIT Failed*\n"
            f"🔗 *From:* `{sender}` ➡ *To:* `{receiver}`\n"
            f"💰 *Amount:* `{value / 10**8:,.8f} {ASSETS.get(str(asset_id), '???')}`\n"
            f"🆔 *Pending TX:* `{tx_id}`",
            parse_mode="Markdown"
        )
        await db.txs.update_one(
            {"_id": tx_id},
            {"$set": {"success": True}}
        )


    


#async def handle_failed_transaction(tx):
#    """Restore locked funds if transaction fails."""
#    sender = tx["sender"]
#    asset_id = str(tx["asset_id"])
#    value = int(tx["value"])
#    fee = int(tx.get("fee", 0))
#
#    sender_exists = await db.addresses.find_one({"_id": sender})
#
#    if sender_exists:
#        # Refund locked funds and BEAM fee back to sender
#        await update_balance(sender, asset_id, available_delta=value, locked_delta=-value)
#        await update_balance(sender, "0", available_delta=fee, locked_delta=-fee)  # Refund BEAM fee


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
            available = int(asset["available_str"])
            locked = int(asset["locked_str"])
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
    """Synchronize all registered assets on the Beam blockchain and DEX with MongoDB."""
    try:
        print("🔄 Syncing assets...")

        # Ensure BEAM (asset_id = 0) exists in the database
        beam_asset = {
            "_id": "0", "asset_id": 0, "decimals": 8,
            "metadata": "STD:SCH_VER=1;N=Beam Token;SN=BEAM;UN=BEAM;NTHUN=GROTH;OPT_FAVICON_URL=https://beam.mw/svg/logo.svg",
            "meta": {
                "N": "Beam Token", "NTHUN": "GROTH", "SCH_VER": "1", "SN": "BEAM", "UN": "BEAM",
                "OPT_FAVICON_URL": "https://beam.mw/svg/logo.svg"
            },
            "confirmations": 0, "height": 0, "issue_height": 0, "owner_id": "", "is_verified": True
        }
        _is_beam_exist = await db.assets.find_one({"_id": "0"})
        if not _is_beam_exist:
            await db.assets.insert_one(beam_asset)

        # 1️⃣ Fetch assets from Beam blockchain
        assets = beam_api.assets_list(refresh=True)
        if not assets:
            print("⚠️ No assets found on the Beam blockchain.")
        else:
            await process_assets(assets)

        # 2️⃣ Fetch assets from Beam DEX (if enabled)
        if DEX_CONTRACT_ID:
            print("🔄 Fetching assets from DEX contract...")
            dex_assets = beam_api.invoke_contract(contract_file="./dapps/dex_app.wasm", args="role=manager,action=view_all_assets")
            if dex_assets and "output" in dex_assets:
                try:
                    assets_data = json.loads(dex_assets["output"]).get("res", [])
                    await process_assets(assets_data, is_dex=True)
                except Exception as e:
                    print(f"⚠️ Error parsing DEX asset data: {e}")

        # 3️⃣ Fetch liquidity pools & update asset rates (if DEX enabled)
        if DEX_CONTRACT_ID:
            await sync_liquidity_pools()

        # 4️⃣ Fetch & Overwrite CA Metadata from External JSON File
        ca_updates = requests.get("https://raw.githubusercontent.com/vsnation/BeamPay/master/ca_assets_updates.json").json()
        for asset in ca_updates:
            asset_id = str(asset["asset_id"])
            update_data = {}
            if "logo_url" in asset and asset["logo_url"]:
                update_data["meta.OPT_FAVICON_URL"] = asset["logo_url"]
            if "about" in asset and asset["about"]:
                update_data["meta.ABOUT"] = asset["about"]

            if update_data:
                await db.assets.update_one({"_id": asset_id}, {"$set": update_data})
                print(f"✅ Updated asset {asset_id}: {update_data}")

        print("✅ Asset synchronization completed.")

    except Exception as e:
        print(f"⚠️ Error syncing assets: {e}")
        traceback.print_exc()


async def process_assets(assets, is_dex=False):
    """Processes and updates asset data in the database."""
    for asset in assets:
        asset_id = str(asset["asset_id"] if not is_dex else asset["aid"])
        metadata = asset.get("metadata", "")

        # Parse metadata (convert metadata string to dict)
        meta = {}
        try:
            for pair in metadata.split(";"):
                key, value = pair.split("=")
                meta[key] = value
        except Exception:
            pass  # Skip if metadata is invalid

        # Determine decimals (using NTH_RATIO)
        decimals = 8
        if "NTH_RATIO" in meta:
            try:
                decimals = int(math.log10(int(meta["NTH_RATIO"])))
            except Exception:
                pass  # Keep default

        # Check if asset is verified
        is_verified = int(asset_id) in VERIFIED_CA if VERIFIED_CA else False
        is_spam = int(asset_id) in SPAM_CA if SPAM_CA else False

        # Prepare asset data
        asset_data = {
            "_id": asset_id, "asset_id": int(asset_id), "decimals": decimals,
            "metadata": metadata, "meta": meta,
            "confirmations": asset.get("confirmations", 0), "height": asset.get("height", 0),
            "issue_height": asset.get("issue_height", 0), "owner_id": asset.get("owner_id", ""),
            "is_verified": is_verified, "is_spam": is_spam
        }

        # Update or insert asset into the database
        existing_asset = await db.assets.find_one({"_id": asset_id})
        if existing_asset:
            del asset_data["_id"]
            await db.assets.update_one({"_id": asset_id}, {"$set": asset_data})
        else:
            await db.assets.insert_one(asset_data)
            print(f"✅ Inserted new asset {asset_id}")

    print(f"✅ Processed {len(assets)} assets ({'DEX' if is_dex else 'Blockchain'})")

async def sync_liquidity_pools():
    """
    Synchronize liquidity pool data from the Beam DEX contract and update db.assets with only rates.
    """
    if not DEX_CONTRACT_ID:
        print("⚠️ No DEX_CONTRACT_ID found. Skipping liquidity sync.")
        return

    try:
        print("🔄 Fetching liquidity pools from DEX...")

        # Call the DEX contract
        pools_response = beam_api.invoke_contract(
            contract_file="./dapps/dex_app.wasm",
            args=f"role=manager,action=pools_view,cid={DEX_CONTRACT_ID}"
        )

        if not pools_response or "output" not in pools_response:
            print("⚠️ No response from DEX pools.")
            return
        
        pools_data = json.loads(pools_response["output"]).get("res", [])

        if not pools_data:
            print("⚠️ No pools found in the contract response.")
            return

        # Fetch BEAM price for USD conversion
        beam_price_data = await db.price.find_one({"_id": "beam_usd"})
        beam_price = float(beam_price_data["price"]) if beam_price_data else 0

        # Dictionary to store updates
        asset_updates = {}

        for pool in pools_data:
            aid1, aid2 = str(pool["aid1"]), str(pool["aid2"])

            # Get rates
            rate1_2 = float(pool.get("k1_2", 0))
            rate2_1 = float(pool.get("k2_1", 0))

            # BEAM Price Calculation
            rate_beam_1, rate_beam_2 = None, None
            if aid1 == "0":
                rate_beam_2 = rate1_2
            elif aid2 == "0":
                rate_beam_1 = rate2_1

            # USD Pricing
            rate1_usd, rate2_usd = None, None
            if beam_price > 0:
                rate1_usd = rate_beam_1 * beam_price if rate_beam_1 else None
                rate2_usd = rate_beam_2 * beam_price if rate_beam_2 else None

            # Store price conversions in each asset
            asset_updates[aid1] = {
                f"rate_{aid1}_{aid2}": str(rate1_2),
                "rate_beam": str(rate_beam_1) if rate_beam_1 else None,
                "rate_usd": str(rate1_usd) if rate1_usd else None
            }

            asset_updates[aid2] = {
                f"rate_{aid2}_{aid1}": str(rate2_1),
                "rate_beam": str(rate_beam_2) if rate_beam_2 else None,
                "rate_usd": str(rate2_usd) if rate2_usd else None
            }

        # Batch update asset prices
        asset_update_queries = [
            db.assets.update_one({"_id": aid}, {"$set": data}, upsert=True)
            for aid, data in asset_updates.items()
        ]

        # Execute all updates in parallel
        await asyncio.gather(*asset_update_queries)

        print("✅ Liquidity pools synchronized successfully.")

    except Exception as e:
        print(f"❌ Error syncing liquidity pools: {e}")
        traceback.print_exc()


async def process_withdrawal_queue():
    """Process pending withdrawals securely (avoid duplicate TXs & ensure UTXOs exist)."""

    print("Processing Pending withdrawals.")
    async for tx in db.pending_withdrawals.find({"status": "pending"}):
        try:
            sender = tx["sender"]
            asset_id = tx["asset_id"]
            amount = int(tx["value"])
            fee = int(tx["fee"])
            receiver = tx["receiver"]
            comment = tx.get('comment', "")

            # TUDO Double check SENDER's address balance and math.
            sender_data = await db.addresses.find_one({"_id": sender})

            # Extract Balances
            available_balance = int(sender_data["balance"]["available"].get(str(asset_id), "0"))
            locked_balance = int(sender_data["balance"]["locked"].get(str(asset_id), "0"))
            available_beam = int(sender_data["balance"]["available"].get("0", "0"))  # BEAM (for gas fees)
            locked_beam = int(sender_data["balance"]["locked"].get("0", "0"))

            # Validate Locked Balance Matches Pending Withdrawals
            pending_withdrawals = await db.pending_withdrawals.find({"sender": sender, "status": {"$ne": "sent_confirmed"}}).to_list(None)
            pending_total = 0
            pending_beam_fees = 0  # Separate BEAM fee total
            total_pending_beam = 0
            
            for t in pending_withdrawals:
                if int(t["asset_id"]) == 0:  # If BEAM Withdrawal
                    total_pending_beam += int(t["value"]) + int(t["fee"])  # Value + Fee
                elif t["asset_id"] == asset_id:  # If CA Withdrawal
                    pending_total += int(t["value"])  # Only Asset Value
                    pending_beam_fees += int(t["fee"])  # Count BEAM fee separately
                else:
                    pending_beam_fees += int(t["fee"])  # Count BEAM fee separately
                    continue

            total_pending_beam = total_pending_beam + pending_beam_fees
            pending_total  = total_pending_beam if asset_id == 0 else pending_total
            
            if locked_beam != total_pending_beam or locked_balance != pending_total:
                await db.pending_withdrawals.update_one(
                    {"_id": tx["_id"]},
                    {"$set": {"status": "admin_check"}}
                )
                await send_to_logs(
                    f"🚨 *Balance Mismatch Detected!*\n"
                    f"📤 *Sender:* `{sender}`\n"
                    f"🔒 *Locked Balance:* `{locked_balance / 10**8:,.8f} {ASSETS.get(str(asset_id), 'Unknown Asset')}`\n"
                    f"⏳ *Pending Withdrawals:* `{pending_total / 10**8:,.8f} {ASSETS.get(str(asset_id), 'Unknown Asset')}`\n"
                    f"🔒 *Locked BEAM:* `{locked_beam / 10**8:,.8f} {ASSETS.get(str(0), 'Unknown Asset')}`\n"
                    f"⏳ *Pending Withdrawals BEAM:* `{total_pending_beam / 10**8:,.8f} {ASSETS.get(str(0), 'Unknown Asset')}`\n"
                    f"🆔 *TxID:* `{tx['_id']}`",
                    parse_mode="Markdown"
                )
                continue  # Skip processing to prevent errors


            # 🔹 Fetch UTXOs & Check Balance Again
            utxos = beam_api.get_utxo(count=100, sort_field="status", sort_direction="asc", filter={"asset_id": int(asset_id)})
            available_utxo_amount = sum(utxo["amount"] for utxo in utxos if utxo["status"] == 1)  # Only 'available' UTXOs

            print("AVAILABLE UTXOs", available_utxo_amount)
            print(f"REQUIRED AMOUNT {amount}\t\t Asset ID: {asset_id}")
            print(f"FEE {fee}\t\t Asset ID: 0")

            # 🔹 If UTXOs are insufficient, delay the TX
            if available_utxo_amount < (amount + fee) :
                print(f"🚧 Insufficient UTXOs for {tx['_id']}. Retrying later.")
                continue  # Try in next cycle

            # 🔹 Mark TX as "processing" before sending (prevents race conditions)
            await db.pending_withdrawals.update_one(
                {"_id": tx["_id"]},
                {"$set": {"status": "processing"}}
            )
            print(f"🚀 Sending {amount/1e8:.8f} of Asset {asset_id} from {sender[:6]}... to {receiver[:6]}... | Comment: '{comment}'")
            print(f"AVAILABLE UTXOs: {available_utxo_amount/1e8:.8f} aid: {asset_id} | REQUIRED: {(amount + fee)/1e8:.8f} {asset_id} | Asset ID: {asset_id} | Fee: {fee/1e8:.8f} {asset_id}")

            # 🔹 Send Withdrawal via BeamPay API
            response = beam_api.tx_send(
                value=amount,
                fee=fee,
                sender=sender,
                receiver=receiver,
                asset_id=asset_id,
                comment=comment,
            )


            # 🔹 If TX fails, revert status
            if not response or "error" in response:
                await db.pending_withdrawals.update_one(
                    {"_id": tx["_id"]},
                    {"$set": {"status": "pending"}}  # Revert back to pending
                )
                await send_to_logs(
                    f"❌ *Withdrawal Failed (Pending TX)*\n"
                    f"🔗 *Sender:* `{sender}`\n"
                    f"💰 *Amount:* `{amount / 10**8:,.8f} {ASSETS.get(str(asset_id), 'Unknown Asset')}`\n"
                    f"🆔 *Pending TX:* `{pending_tx['_id']}`",
                    parse_mode="Markdown"
                )
                continue  # Retry in next loop

            tx_id = response["txId"]

            # 🔹 Mark TX as "sent"
            await db.pending_withdrawals.update_one(
                {"_id": tx["_id"]},
                {"$set": {"txId": tx_id, "status": "sent"}}
            )

            # 🔹 Store TX in `db.txs`
            await db.txs.insert_one({
                "_id": tx_id,
                "status": 0,  # Pending
                "status_string": "pending",
                "income": False,
                "comment": comment,
                "type": "withdrawal",
                "asset_id": asset_id,
                "value": str(amount),
                "fee": str(fee),
                "sender": sender,
                "receiver": receiver,
                "create_time": datetime.datetime.utcnow().timestamp(),
                "confirmations": 0,
                "success": False,
                "webhook_sent": {}
            })

            await send_to_logs(
                f"*[2/3]* ✅ *Withdrawal Successful*\n"
                f"💸 *Amount:* `{amount / 10**8:,.8f} {ASSETS.get(str(asset_id), '???')}`\n"
                f"📤 *From:* `{sender}` ➡ *To:* `{receiver}`\n"
                f"🆔 *TxID:* `{tx_id}`",
                parse_mode="Markdown"
            )
        except Exception as exc:
            traceback.print_exc()
            await send_to_logs(traceback.format_exc())



    await asyncio.sleep(90)  # Retry every 5 seconds


async def process_updates():
    """Main function to update rates, addresses."""
    print("Processing 120 Sec. Updates")
    await load_assets()
    while True:
        try:
            await fetch_beam_price()
            await sync_assets()
            await load_assets()
            await verify_balances()
            await sync_addresses()
            await asyncio.sleep(120)
        except Exception as exc:
            traceback.print_exc()
            await send_to_logs(traceback.format_exc())



async def process_payments():
    """
    Function to process transactions.
    """
    while True:
        print("Processing Onchain Transactions. 30 Sec.")
        await process_transactions()  # Function that processes txs
        await process_withdrawal_queue() # Function that processes mempool
        await asyncio.sleep(5)  # Avoid hammering the system

async def main():
    """Runs both daemons simultaneously."""
    """Run all tasks concurrently."""
    tasks = [
        asyncio.create_task(process_updates()),
        asyncio.create_task(process_payments()),
    ]
    await asyncio.gather(*tasks)  # Run all tasks concurrently


if __name__ == "__main__":
    asyncio.run(main())
