import asyncio
import json
import math
import time
import yfinance as yf
from sqlalchemy import select
from core.database import AsyncSessionLocal
from models.holding import Holding

# Fallback symbols if database is empty or unavailable
FALLBACK_SYMBOLS = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS",
    "ICICIBANK.NS", "WIPRO.NS", "SBIN.NS", "BAJFINANCE.NS"
]

# No need to hit Postgres on every 60s poll — the symbol list changes only
# when holdings change, so it is cached and refreshed rarely to keep load
# off the free-tier database.
SYMBOL_CACHE_TTL = 600  # seconds
_symbol_cache: list[str] = []
_symbol_cache_at: float = 0.0


async def get_nse_symbols_from_holdings() -> list[str]:
    """
    Fetch distinct NSE stock symbols from the holdings table.
    Returns a de-duplicated, sorted list of symbols ending with '.NS'.
    If the database is empty or unreachable, returns an empty list.
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Holding.symbol).where(Holding.asset_type == "stock")
            )
            rows = result.scalars().all() or []
            # Keep only NSE symbols and de-duplicate
            symbols = sorted({s for s in rows if s and s.endswith(".NS")})
            return symbols
    except Exception as e:
        print(f"⚠️ Error fetching NSE symbols from holdings: {e}")
        return []


async def get_cached_nse_symbols() -> list[str]:
    """Return NSE symbols, hitting the database at most every SYMBOL_CACHE_TTL."""
    global _symbol_cache, _symbol_cache_at
    now = time.monotonic()
    if _symbol_cache and now - _symbol_cache_at < SYMBOL_CACHE_TTL:
        return _symbol_cache
    symbols = await get_nse_symbols_from_holdings()
    if symbols:
        _symbol_cache = symbols
        _symbol_cache_at = now
    return symbols

async def india_stocks_worker():
    from core.redis import get_redis
    redis_client = await get_redis()
    print("🔄 Starting India stocks polling worker (60s interval)...")

    while True:
        try:
            # Fetch symbols from holdings (cached, see SYMBOL_CACHE_TTL)
            symbols = await get_cached_nse_symbols()
            if not symbols:
                symbols = FALLBACK_SYMBOLS
                print("ℹ️ Using fallback symbols (no NSE holdings in database)")

            # Avoid hammering yfinance with an empty list
            if not symbols:
                print("ℹ️ No NSE holdings found, skipping this cycle.")
                await asyncio.sleep(60)
                continue

            tickers = yf.download(
                tickers=" ".join(symbols),
                period="1d",
                interval="1m",
                progress=False,
                auto_adjust=True
            )

            if "Close" not in tickers or tickers["Close"].empty:
                print("⚠️ No Close data returned from yfinance for", symbols)
                await asyncio.sleep(60)
                continue

            # the final 1m bar is often unfilled per ticker, so carry the
            # last real print forward instead of reading a NaN row
            closes = tickers["Close"].ffill().iloc[-1]

            for symbol in symbols:
                if symbol not in closes.index:
                    continue
                close = float(closes[symbol])
                if math.isnan(close):
                    continue
                price = str(round(close, 2))
                redis_key = f"price:stock:{symbol.lower().replace('.', '_')}"
                await redis_client.setex(redis_key, 120, price)
                await redis_client.setex(f"last:{redis_key}", 604800, price)
                await redis_client.publish(
                    "prices",
                    json.dumps({"symbol": symbol.lower(), "price": price, "type": "india_stock"})
                )

            sample = symbols[0] if symbols else "N/A"
            print(f"✅ India stocks updated — sample {sample}: {closes.get(sample, 'N/A')}")

        except Exception as e:
            print(f"⚠️ India stocks worker error: {e}")

        await asyncio.sleep(60)
