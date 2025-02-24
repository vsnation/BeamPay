from fastapi import FastAPI, HTTPException, Depends, Body
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html

import asyncio
from lib.beam import BEAMWalletAPI
from db import db
from config import BEAM_API_RPC, send_to_logs
from auth import get_api_key
import datetime

import os

app = FastAPI(
    title="BeamPay API",
    description="API Documentation for BeamPay",
    version="1.0.0",
)

@app.get("/api/health")
def health_check():
    return {"status": "ok"}


# Basic auth for docs
security = HTTPBasic()

# Define the allowed username and password
DOCS_USERNAME = os.getenv("ADMIN_USERNAME", "admin")  # Set a default username
DOCS_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")  # Set a default password
print(DOCS_USERNAME, DOCS_PASSWORD)

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != DOCS_USERNAME or credentials.password != DOCS_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


@app.get("/api/docs", include_in_schema=False)
async def custom_swagger_ui(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """
    Protects the Swagger UI with basic authentication.
    """
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title="Secure Swagger UI"
    )

@app.get("/api/openapi.json", include_in_schema=False)
async def openapi(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """
    Protects the OpenAPI schema with basic authentication.
    """
    return get_openapi(
        title="Secure API",
        version="1.0.0",
        routes=app.routes
    )


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


@app.get("/assets")
async def get_assets():
    """Return all assets and their decimals from the database."""
    assets = await db.assets.find().to_list(None)
    return assets


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

    # Extract available balances
    available_balance = int(from_address_data["balance"]["available"].get(asset_id, "0"))
    available_beam = int(from_address_data["balance"]["available"].get("0", "0"))  # BEAM (0) balance
    locked_beam = int(from_address_data["balance"]["locked"].get("0", "0"))  # Locked BEAM balance
    locked_asset = int(from_address_data["balance"]["locked"].get(asset_id, "0"))  # Locked asset balance

    # If BEAM (0) is being sent, lock total_required (amount + fee)
    if asset_id == "0":
        total_required = amount + fee  # Total BEAM required for transaction

        if available_beam < total_required:
            raise HTTPException(status_code=400, detail="Insufficient BEAM balance (including transaction fee)")

        # Lock total_required in BEAM (0)
        new_available_beam = available_beam - total_required
        new_locked_beam = locked_beam + total_required

        # Update database with new locked and available balances
        await db.addresses.update_one(
            {"_id": from_address},
            {"$set": {
                f"balance.available.0": str(new_available_beam),
                f"balance.locked.0": str(new_locked_beam)
            }}
        )

    else:  # If sending an asset (not BEAM)
        if available_balance < amount:
            raise HTTPException(status_code=400, detail="Insufficient asset balance")

        if available_beam < fee:
            raise HTTPException(status_code=400, detail="Insufficient BEAM balance for transaction fee")

        # Lock the asset amount
        new_available_asset = available_balance - amount
        new_locked_asset = locked_asset + amount

        # Lock BEAM fee separately
        new_available_beam = available_beam - fee
        new_locked_beam = locked_beam + fee

        # Update database with locked balances for asset and BEAM
        await db.addresses.update_one(
            {"_id": from_address},
            {"$set": {
                f"balance.available.{asset_id}": str(new_available_asset),
                f"balance.locked.{asset_id}": str(new_locked_asset),
                f"balance.available.0": str(new_available_beam),
                f"balance.locked.0": str(new_locked_beam)
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
        # ❌ Transaction Failed: Revert locked balances
        if asset_id == "0":
            await db.addresses.update_one(
                {"_id": from_address},
                {"$set": {
                    f"balance.available.0": str(available_beam),
                    f"balance.locked.0": str(locked_beam)
                }}
            )
        else:
            await db.addresses.update_one(
                {"_id": from_address},
                {"$set": {
                    f"balance.available.{asset_id}": str(available_balance),
                    f"balance.locked.{asset_id}": str(locked_asset),
                    f"balance.available.0": str(available_beam),
                    f"balance.locked.0": str(locked_beam)
                }}
            )
        
        await db.txs.delete_one({"_id": tx_id})  # Remove failed tx from DB
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
    uvicorn.run(app, host="127.0.0.1", port=8010)
