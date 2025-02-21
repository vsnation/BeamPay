from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from db import db
import time

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

# Simple rate limiter (5 requests per API key per second)
RATE_LIMIT = 10
rate_limits = {}

async def get_api_key(api_key: str = Security(api_key_header)):
    """Authenticate API key from request headers."""
    if not api_key:
        raise HTTPException(status_code=403, detail="API key required")
    
    valid_key = await db.api_keys.find_one({"key": api_key})
    if not valid_key:
        raise HTTPException(status_code=403, detail="Invalid API key")

    # Apply rate limiting
    now = time.time()
    if api_key in rate_limits:
        timestamps = rate_limits[api_key]
        timestamps = [t for t in timestamps if now - t < 1]  # Keep only last second
        if len(timestamps) >= RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        timestamps.append(now)
        rate_limits[api_key] = timestamps
    else:
        rate_limits[api_key] = [now]

    return api_key
