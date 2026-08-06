"""Shared current-price resolution.

Live price keys have short TTLs (30-120s) so they vanish whenever the
market is quiet — US stocks are null ~18h a day. Workers therefore also
write a "last:"-prefixed copy with a 7-day TTL, and resolve_price falls
back: live key -> last-known key -> latest price_history close.
"""
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models.price_history import PriceHistory
from services.price_backfill import COIN_ID_TO_SYMBOL

# TTL for the "last:" copy of every live price key
LAST_PRICE_TTL = 7 * 24 * 3600


def _crypto_live_key(symbol: str) -> str:
    """Map a CoinGecko-style coin id (e.g. "ethereum") to the Binance
    Redis key written by binance_ws.py (e.g. "price:crypto:ethusdt")."""
    sym = symbol.lower()
    binance_sym = COIN_ID_TO_SYMBOL.get(sym)
    if binance_sym:
        return f"price:crypto:{binance_sym}"
    return f"price:crypto:{sym}usdt"


REDIS_PRICE_KEYS = {
    "stock": lambda s: f"price:stock:{s.lower().replace('.', '_')}",
    "crypto": _crypto_live_key,
    "mutualfund": lambda s: f"nav:{s}",
}


def live_price_key(symbol: str, asset_type: str) -> str | None:
    key_fn = REDIS_PRICE_KEYS.get(asset_type)
    return key_fn(symbol) if key_fn else None


def last_price_key(live_key: str) -> str:
    return f"last:{live_key}"


async def resolve_price(
    symbol: str,
    asset_type: str,
    redis,
    db: AsyncSession | None = None,
) -> tuple[float | None, bool]:
    """Return (price, stale). stale=True when the price did not come from
    the live key (i.e. it is the last known value, not a live tick)."""
    key = live_price_key(symbol, asset_type)
    if not key:
        return None, False

    val = await redis.get(key)
    if val:
        return float(val), False

    val = await redis.get(last_price_key(key))
    if val:
        return float(val), True

    if db is not None:
        result = await db.execute(
            select(PriceHistory.close_price)
            .where(PriceHistory.symbol == symbol)
            .order_by(desc(PriceHistory.price_date))
            .limit(1)
        )
        close = result.scalars().first()
        if close is not None:
            return float(close), True

    return None, False
