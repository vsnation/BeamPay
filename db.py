from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis
from urllib.parse import urlparse
from config import DATABASE_URL

# MongoDB connection using environment variable
client = AsyncIOMotorClient(DATABASE_URL)

# Extract the database name from the MongoDB URL
parsed_url = urlparse(DATABASE_URL)
db_name = parsed_url.path[1:]  # The database name follows the '/' in the URL

# Access the MongoDB database
db = client[db_name]


def update_indexes():
    # Create indexes for frequent queries
    db.txs.create_index([("create_time", -1)])  # Fast transaction sorting
    db.txs.create_index([("sender", 1), ("receiver", 1)])  # Speed up address filtering
    db.txs.create_index([("status", 1)])  # Faster status queries
    db.txs.create_index([("asset_id", 1)])  # Queries based on asset types

    db.addresses.create_index([("_id", 1)])  # Speed up balance lookups
    db.addresses.create_index([("balance.available.0", -1)])  # Query BEAM balances faster
    db.addresses.create_index([("balance.available", 1)])  # Faster balance queries

    db.pending_withdrawals.create_index([("sender", 1), ("status", 1)])  # Pending withdrawals by sender
    db.pending_withdrawals.create_index([("status", 1), ("create_time", 1)])  # Prioritize older withdrawals
    db.pending_withdrawals.create_index([("asset_id", 1)])  # Query withdrawals by asset ID

    db.payments.create_index([("user_id", 1)])  # Quick lookup of user payments
    db.payments.create_index([("status", 1)])  # Fast filtering of payment statuses


    # Index for addresses
    db.addresses.create_index([("_id", 1)], unique=True)  # Ensure address ID is unique

    print("Indexes created successfully")
    return True

#update_indexes()
