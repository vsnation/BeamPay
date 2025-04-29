from fastapi import FastAPI, HTTPException, Depends, Body, Query
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

@app.get("/wallet_status", dependencies=[Depends(get_api_key)])
async def wallet_status():
    wallet_status = beam_api.wallet_status()
    print(wallet_status)
    return {"status": True, "result": wallet_status}

@app.get("/validate_address", dependencies=[Depends(get_api_key)])
async def validate_address(address: str):
    """Retrieve a list of addresses linked to a specific note."""
    address = beam_api.validate_address(address)
    print(address)
    return {"status": True, "result": address['is_valid']}

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
    asset_id: int = Body(...),
    amount: int = Body(...),
    comment: str = Body(...),
    fee: int = Body(None)
):
    """Validates and locks funds for withdrawal, actual transaction will be processed later."""
    # 1. Validate Address
    address_info = beam_api.validate_address(to_address)
    print("IS VALID ADDRESS:", address_info)

    if not address_info.get('is_valid'):
        raise HTTPException(status_code=404, detail="Incorrect Withdrawal Address.")

    if to_address == from_address:
        raise HTTPException(status_code=404, detail="Sender can't send assets to itself")

    # 2. Determine correct FEE
    address_type = address_info.get('type', 'regular')
    print(f"Address Type: {address_type}")

    # Define fees
    FEE_REGULAR = 100000      # 0.001 BEAM in Groths
    FEE_OFFLINE = 1100000     # 0.011 BEAM in Groths

    if address_type in ['regular', 'regular_new']:
        tx_fee = FEE_REGULAR
    else:  # offline, public_offline, max_privacy
        tx_fee = FEE_OFFLINE

    # 3. Override 'fee' parameter safely
    fee = tx_fee

    # Fetch sender's balance from DB
    from_address_data = await db.addresses.find_one({"_id": from_address})
    receiver_address_data = await db.addresses.find_one({"_id": to_address})  # Check if receiver is in system
    if not from_address_data:
        raise HTTPException(status_code=404, detail="Sender address not found")

    # Extract available balances
    available_balance = int(from_address_data["balance"]["available"].get(str(asset_id), "0"))
    available_beam = int(from_address_data["balance"]["available"].get("0", "0"))  # BEAM (0) balance
    locked_beam = int(from_address_data["balance"]["locked"].get("0", "0"))  # Locked BEAM balance
    locked_asset = int(from_address_data["balance"]["locked"].get(str(asset_id), "0"))  # Locked asset balance

    # Validate BEAM balance if it's the main asset (Gas Fees)
    if asset_id == 0:
        total_required = amount + fee  # BEAM + transaction fee
        if available_beam < total_required:
            return {"status": False, "msg": "Insufficient BEAM balance (including transaction fee)"}

        # Lock funds
        new_available_beam = available_beam - total_required
        new_locked_beam = locked_beam + total_required

        await db.addresses.update_one(
            {"_id": from_address},
            {"$set": {
                f"balance.available.0": str(new_available_beam),
                f"balance.locked.0": str(new_locked_beam)
            }}
        )
    else:  # If sending an Asset
        if available_balance < amount:
            return {"status": False, "msg": "Insufficient asset balance"}

        if available_beam < fee:
            return {"status": False, "msg": "Insufficient BEAM balance for transaction fee"}

        # Lock asset amount & BEAM fee
        new_available_asset = available_balance - amount
        new_locked_asset = locked_asset + amount

        new_available_beam = available_beam - fee
        new_locked_beam = locked_beam + fee

        await db.addresses.update_one(
            {"_id": from_address},
            {"$set": {
                f"balance.available.{asset_id}": str(new_available_asset),
                f"balance.locked.{asset_id}": str(new_locked_asset),
                f"balance.available.0": str(new_available_beam),
                f"balance.locked.0": str(new_locked_beam)
            }}
        )

    # Handle Internal Transfers (Lock Receiver’s Balance)
    if receiver_address_data:
        new_locked_receiver = int(receiver_address_data["balance"]["locked"].get(str(asset_id), "0")) + amount
        await db.addresses.update_one(
            {"_id": to_address},
            {"$set": {f"balance.locked.{asset_id}": str(new_locked_receiver)}}
        )

    # Store withdrawal in `pending_withdrawals`
    withdrawal_request = {
        "status": "pending",
        "asset_id": asset_id,
        "value": str(amount),
        "fee": str(fee),
        "sender": from_address,
        "receiver": to_address,
        "comment": comment,
        "create_time": datetime.datetime.utcnow().timestamp(),
    }

    await db.pending_withdrawals.insert_one(withdrawal_request)

    # 🔔 Notify Admins
    await send_to_logs(f"*[1/3]*💸 *Withdrawal Queued*\n💰 `{amount}` `{asset_id}`\n🔗 `{to_address}`", parse_mode="Markdown")

    return {"status": True, "result": True, "msg": "Withdrawal request recorded"}


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
async def get_transactions(
    address: str = Body(None),
    status: int = Body(None),
    count: int = Body(10, ge=1, le=100),  # Limits max results per query
    skip: int = Body(0, ge=0)
):
    """Retrieve transactions, optionally filtered by address or status, sorted by newest first."""
    print(address, status, count, skip)

    query = {}
    if address:
        query["$or"] = [{"sender": address}, {"receiver": address}]
    if status is not None:
        query["status"] = status

    # Count matching transactions
    txs_count = await db.txs.count_documents(query)

    # Retrieve transactions sorted by `create_time` DESCENDING (newest first)
    transactions = await db.txs.find(query).sort("create_time", -1).skip(skip).limit(count).to_list(None)
    return {"txs": transactions, "count": txs_count}

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
