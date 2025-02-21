from fastapi import FastAPI, HTTPException, Depends, Body
import asyncio
from lib.beam import BEAMWalletAPI
from db import db
from config import BEAM_API_RPC, send_to_logs
from auth import get_api_key
import datetime

app = FastAPI()
beam_api = BEAMWalletAPI(BEAM_API_RPC)

# --- API ENDPOINTS ---
@app.post("/create_wallet", dependencies=[Depends(get_api_key)])
async def create_wallet(note: str = Body(None), wallet_type: str = Body("regular")):
    """Create a new Beam wallet address with an optional note."""
    address = beam_api.create_address(label=note, wallet_type=wallet_type)
    if not address:
        raise HTTPException(status_code=500, detail="Failed to create address")
    
    address_data = {
        "_id": address,
        "type": wallet_type,
        "balance": {"available": {}, "locked": {}},
        "comment": note,
    }
    await db.addresses.insert_one(address_data)
    return {"address": address, "note": note}

@app.get("/address", dependencies=[Depends(get_api_key)])
async def get_address(note: str = Body(...)):
    """Retrieve a list of addresses linked to a specific note."""
    addresses = await db.addresses.find({"comment": note}).to_list(None)
    
    if not addresses:
        raise HTTPException(status_code=404, detail="No addresses found for this note")
    
    return [{"address": a["_id"], "create_time": a["create_time"], "expired": a["expired"]} for a in addresses]

#TODO add other dependencies if this worked well
# , dependencies=[Depends(get_api_key)]

@app.get("/assets")
async def get_assets():
    """Return all assets and their decimals from the database."""
    assets = await db.assets.find().to_list(None)
    return [{"asset_id": a["_id"], "meta": a["metadata_pairs"], "decimals": a.get("decimals", 8)} for a in assets]


@app.post("/withdraw", dependencies=[Depends(get_api_key)])
async def withdraw(
    from_address: str = Body(...),
    to_address: str = Body(...),
    asset_id: str = Body(...),
    amount: int = Body(...),
    fee: int = Body(1100000)
):
    """Locks funds and processes a withdrawal request."""

    # Fetch address data from DB
    from_address_data = await db.addresses.find_one({"_id": from_address})
    receiver_address_data = await db.addresses.find_one({"_id": to_address})  # Check if receiver is in db
    if not from_address_data:
        raise HTTPException(status_code=404, detail="Address not found")

    available_balance = int(from_address_data["balance"]["available"].get(asset_id, "0"))
    locked_balance = int(from_address_data["balance"]["locked"].get(asset_id, "0"))

    total_required = amount + fee  # Ensure fee is included
    if available_balance < total_required:
        raise HTTPException(status_code=400, detail="Insufficient funds")

    # Lock the balance before processing the withdrawal
    new_available = available_balance - total_required
    new_locked = locked_balance + total_required  # Lock full amount including fee

    await db.addresses.update_one(
        {"_id": from_address},
        {"$set": {
            f"balance.available.{asset_id}": str(new_available),
            f"balance.locked.{asset_id}": str(new_locked)
        }}
    )
    # If receiver exists in the system, lock funds in their account too
    if receiver_address_data:
        new_locked_receiver = int(receiver_address_data["balance"]["locked"].get(asset_id, "0")) + amount
        await db.addresses.update_one(
            {"_id": to_address},
            {"$set": {
                f"balance.locked.{asset_id}": str(new_locked_receiver)
            }}
        )

    # Call Beam API to create the withdrawal transaction
    result = beam_api.tx_send(value=amount, fee=fee, sender=from_address, receiver=to_address, asset_id=int(asset_id))

    if not result:
        # ❌ Transaction Failed: Revert balance changes
        await db.addresses.update_one(
            {"_id": from_address},
            {"$set": {
                f"balance.available.{asset_id}": str(available_balance),  # Restore available balance
                f"balance.locked.{asset_id}": str(locked_balance)  # Restore locked balance
            }}
        )
        await send_to_logs(f"❌ *Withdrawal Failed*\n🔗 *Address:* `{from_address}`\n💰 *Amount:* `{amount}` `{asset_id}`\n⚠️ *Error: API Failure*", parse_mode="Markdown")
        raise HTTPException(status_code=500, detail="Failed to process withdrawal")

    # Store transaction in DB (if needed for future processing)
    tx_id = result["txId"]
    await db.txs.insert_one({
        "_id": tx_id,
        "status": 0,
        "status_string": "pending",
        "type": "withdrawal",
        "asset_id": asset_id,
        "value": str(amount),
        "fee": str(fee),
        "sender": from_address,
        "receiver": to_address,
        "create_time": datetime.datetime.utcnow().timestamp(),
        "confirmations": 0,
        "success": False,
    })

    return {"txId": tx_id, "status": "pending"}


@app.get("/deposits", dependencies=[Depends(get_api_key)])
async def get_deposits(address: str = Body(None), asset_ids: list[str] = Body(...)):
    """Fetch deposits for a given user or address (with optional asset filtering)."""
    query = {}
    if address:
        query["receiver"] = address
    if asset_ids:
        query["asset_id"] = {"$in": asset_ids}

    deposits = await db.txs.find(query).to_list(None)
    return [{"txId": d["_id"], "amount": d["value"], "asset_id": d["asset_id"], "status": d["status"]} for d in deposits]

@app.get("/balances", dependencies=[Depends(get_api_key)])
async def get_balances(address: str):
    """Retrieve balance for a given address."""
    address_data = await db.addresses.find_one({"_id": address})
    if not address_data:
        raise HTTPException(status_code=404, detail="Address not found")
    return address_data["balance"]

@app.get("/transactions", dependencies=[Depends(get_api_key)])
async def get_transactions(address: str = Body(None), status: int = Body(None)):
    """Retrieve transactions (optionally filtered by address or status)."""
    query = {}
    if address:
        query["$or"] = [{"sender": address}, {"receiver": address}]
    if status is not None:
        query["status"] = status

    transactions = await db.txs.find(query).to_list(None)
    return transactions

@app.post("/register_webhook", dependencies=[Depends(get_api_key)])
async def register_webhook(url: str = Body(...), event_type: str = Body(...), api_key: str = Depends(get_api_key)):
    """Register a webhook for deposits/withdrawals."""
    await db.webhooks.update_one(
        {"url": url, "event_type": event_type},
        {"$set": {"url": url, "event_type": event_type}},
        upsert=True
    )
    return {"message": "Webhook registered successfully"}

# --- RUNNING ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
