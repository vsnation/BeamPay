import os
import traceback

from auth import get_api_key
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from lib.beam import BEAMWalletAPI
from config import BEAM_API_RPC, send_to_logs, VERIFIED_CA
import asyncio
from db import db
from datetime import datetime

beam_api = BEAMWalletAPI(BEAM_API_RPC)

app = FastAPI(
    openapi_url=None,
    docs_url=None,
    redoc_url=None
)

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


templates = Jinja2Templates(directory="templates")

# Custom filter for formatting timestamps
def datetimeformat(value, fmt="%Y-%m-%d %H:%M:%S"):
    return datetime.utcfromtimestamp(value).strftime(fmt)

# Register the filter in Jinja
templates.env.filters["datetimeformat"] = datetimeformat

@app.get("/admin/dashboard")#, dependencies=[Depends(get_api_key)])
async def dashboard(request: Request, credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Admin dashboard showing transaction summaries."""
    total_deposits = await db.txs.count_documents({"status": 3})
    pending_withdrawals = await db.txs.count_documents({"success": {"$ne": True}, "status": {"$ne": 4}})
    total_users = await db.addresses.count_documents({})
    assets = await db.assets.find().to_list(None)

    addresses = await db.addresses.find().limit(10).to_list(None)
    transactions = await db.txs.find().sort("create_time", -1).limit(10).to_list(None)

    # Fetch the balance comparison data
    _balance_comparison = await balance_comparison()
    print(_balance_comparison)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "transactions": transactions,
        "assets": assets,
        "addresses": addresses,
        "total_deposits": total_deposits,
        "pending_withdrawals": pending_withdrawals,
        "total_users": total_users,
        "balance_comparison": _balance_comparison,
        "whitelisted_assets": VERIFIED_CA 
    })


async def get_db_balances():
    """Fetch total available balance for each asset from the database using aggregation."""
    pipeline = [
        {"$project": {"balance.available": 1}},  # Extract only available balances
        {"$unwind": {"path": "$balance.available", "preserveNullAndEmptyArrays": True}},  # Flatten assets
        {"$group": {"_id": "$balance.available", "total_balance": {"$sum": {"$toInt": "$balance.available"}}}}
    ]

    aggregated = await db.addresses.aggregate(pipeline).to_list(None)
    return {str(a["_id"]): a["total_balance"] for a in aggregated}

async def balance_comparison():
    """
    Compare Beam Wallet balance with the Database balance.
    """
    try:
        print("Comparing wallet balances...")

        # Fetch wallet balance from BEAM Wallet API
        wallet_status = beam_api.wallet_status()
        if not wallet_status or "totals" not in wallet_status:
            raise HTTPException(status_code=500, detail="Failed to fetch wallet status")

        # Extract balances from API response
        wallet_balances = {}
        for asset in wallet_status["totals"]:
            asset_id = str(asset["asset_id"])  # Convert to string for consistency
            available = int(asset["available_str"])
            locked = int(asset["locked_str"]) + int(asset['receiving_regular_str']) + int(asset['sending_regular_str'])
            wallet_balances[asset_id] = {"available": available, "locked": locked}

        # Fetch database balances
        db_balances = {}
        async for address in db.addresses.find({}, {"balance": 1}):
            for asset_id, amount in address["balance"]["available"].items():
                db_balances.setdefault(asset_id, {"available": 0, "locked": 0})
                db_balances[asset_id]["available"] += int(amount)

            for asset_id, amount in address["balance"]["locked"].items():
                db_balances.setdefault(asset_id, {"available": 0, "locked": 0})
                db_balances[asset_id]["locked"] += int(amount)

        # Compare wallet and database balances
        comparison = []
        all_assets = set(wallet_balances.keys()).union(db_balances.keys())

        for asset_id in all_assets:
            api_available = wallet_balances.get(asset_id, {}).get("available", 0)
            db_available = db_balances.get(asset_id, {}).get("available", 0)
            api_locked = wallet_balances.get(asset_id, {}).get("locked", 0)
            db_locked = db_balances.get(asset_id, {}).get("locked", 0)

            comparison.append({
                "asset_id": asset_id,
                "api_available": api_available,
                "db_available": db_available,
                "api_locked": api_locked,
                "db_locked": db_locked,
            })

        # Log discrepancies
        discrepancies = [c for c in comparison if c["api_available"] != c["db_available"] or c["api_locked"] != c["db_locked"]]

        if discrepancies:
            print("⚠️ Balance Discrepancies Found:")
            for d in discrepancies:
                print(f"Asset {d['asset_id']}:")
                print(f"  API Available: {d['api_available']} | DB Available: {d['db_available']}")
                print(f"  API Locked: {d['api_locked']} | DB Locked: {d['db_locked']}")
        else:
            print("✅ All balances match between the API and the database.")

        return comparison

    except Exception as e:
        print(f"Error comparing balances: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error verifying balances")


# --- RUNNING ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8009)
