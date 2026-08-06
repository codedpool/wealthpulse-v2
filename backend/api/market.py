import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import get_db
from core.redis import get_redis
from services.prices import resolve_price

router = APIRouter(prefix="/api/market", tags=["Market"])


# ── MUTUAL FUNDS ───────────────────────────────────────────────────────────

@router.get("/mutualfunds")
async def search_mutualfunds(q: str = Query(..., min_length=1)):
    redis = await get_redis()
    cache_key = f"mf:search:{q.lower()}"
    cached = await redis.get(cache_key)
    if cached:
        import json
        return json.loads(cached)

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.mfapi.in/mf/search?q={q}",
            timeout=10
        )
    results = r.json()[:10]
    import json
    await redis.setex(cache_key, 3600, json.dumps(results))
    return results


@router.get("/mutualfunds/{scheme_code}")
async def get_mutualfund_nav(scheme_code: str):
    redis = await get_redis()
    cache_key = f"mf:nav:{scheme_code}"
    cached = await redis.get(cache_key)
    if cached:
        import json
        return json.loads(cached)

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.mfapi.in/mf/{scheme_code}",
            timeout=10
        )
    data = r.json()
    import json
    await redis.setex(cache_key, 3600, json.dumps(data))
    return data


# ── INDIAN STOCKS ──────────────────────────────────────────────────────────

@router.get("/stocks/india")
async def get_india_stock_price(symbol: str = Query(...), db: AsyncSession = Depends(get_db)):
    redis = await get_redis()
    price, stale = await resolve_price(symbol, "stock", redis, db)
    return {
        "symbol": symbol,
        "price": price,
        "source": "yfinance",
        "cached": price is not None,
        "stale": stale,
    }


# ── US STOCKS ──────────────────────────────────────────────────────────────

@router.get("/stocks/us")
async def get_us_stock_price(symbol: str = Query(...), db: AsyncSession = Depends(get_db)):
    redis = await get_redis()
    price, stale = await resolve_price(symbol, "stock", redis, db)
    return {
        "symbol": symbol,
        "price": price,
        "source": "finnhub",
        "cached": price is not None,
        "stale": stale,
    }


# ── CRYPTO ─────────────────────────────────────────────────────────────────

@router.get("/crypto")
async def get_crypto_price(symbol: str = Query(...), db: AsyncSession = Depends(get_db)):
    redis = await get_redis()
    price, stale = await resolve_price(symbol, "crypto", redis, db)
    return {
        "symbol": symbol.upper(),
        "price": price,
        "source": "binance",
        "cached": price is not None,
        "stale": stale,
    }


# ── UNIFIED PRICE LOOKUP ───────────────────────────────────────────────────

@router.get("/price/{asset_type}/{symbol}")
async def get_price(asset_type: str, symbol: str, db: AsyncSession = Depends(get_db)):
    redis = await get_redis()
    if asset_type not in ("stock", "crypto", "mutualfund"):
        return {"error": "Invalid asset_type"}
    price, stale = await resolve_price(symbol, asset_type, redis, db)
    return {
        "symbol": symbol,
        "asset_type": asset_type,
        "price": price,
        "stale": stale,
    }
