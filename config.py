import json
import os

from dotenv import load_dotenv
load_dotenv(dotenv_path='.env')

# Get environment variables or fallback to default
DATABASE_URL = os.getenv("DATABASE_URL")
BEAM_API_RPC = os.getenv("BEAM_WALLET_API_RPC")


