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
    # Index for transactions by ID and status
    db.txs.create_index([("_id", 1)], unique=True)  # Ensure transaction ID is unique
    db.txs.create_index([("status", 1)])  # Index for transaction status
    db.txs.create_index([("confirmations", 1)])  # Index for confirmations
    db.txs.create_index([("asset_id", 1)])  # Index for asset_id

    # Index for addresses
    db.addresses.create_index([("_id", 1)], unique=True)  # Ensure address ID is unique
    db.addresses.create_index([("own_id", 1)])  # Index for own_id
    db.addresses.create_index([("wallet_id", 1)])  # Index for wallet_id

    print("Indexes created successfully")
    return True

#update_indexes()
