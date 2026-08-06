# WealthPulse API Entry Point
import asyncio
import os
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from core.limiter import limiter
from core.redis import init_redis
from core.database import get_db, with_db_retry
from core.config import settings
from api.portfolio import router as portfolio_router
from api.stream import router as stream_router
from api.analytics import router as analytics_router
from api.market import router as market_router
from api.ai import router as ai_router
from workers.amfi_cron import scheduler, parse_and_store_navs
from workers.binance_ws import binance_price_worker
from workers.finnhub_ws import finnhub_price_worker
from workers.india_stocks import india_stocks_worker
from services.backfill import backfill_stock_history
from services.crypto_backfill import backfill_crypto_history
from api.stock_compat import router as stock_router
from api.mf_compat import router as mf_router
from api.crypto_compat import router as crypto_router

app = FastAPI(title="WealthPulse API v2")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ──────────────────────────────────────────────────────────────────────
# CORS origins list with explicit Vercel URL and FRONTEND_URL from .env.
# CORSMiddleware must be added BEFORE other middleware and BEFORE startup event.
origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "https://wealthpulse-nu.vercel.app",
    os.getenv("FRONTEND_URL", ""),
]
# Remove empty strings in case FRONTEND_URL is not set
origins = [o for o in origins if o]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    # Redis — non-fatal if down
    try:
        await init_redis()
        print("Redis connected.")
    except Exception as e:
        print(f"WARNING: Redis unavailable on startup: {e}")
        print("App will continue without caching. Live prices will be unavailable.")

    if not settings.WORKERS_ENABLED:
        print("WORKERS_ENABLED=false — skipping price workers, AMFI cron and backfills")
        print("WealthPulse v2 started (API only)")
        return

    # Binance worker
    try:
        asyncio.create_task(binance_price_worker())
    except Exception as e:
        print(f"WARNING: Binance worker failed to start: {e}")

    # Finnhub worker
    try:
        asyncio.create_task(finnhub_price_worker())
    except Exception as e:
        print(f"WARNING: Finnhub worker failed to start: {e}")

    # India stocks worker
    try:
        asyncio.create_task(india_stocks_worker())
    except Exception as e:
        print(f"WARNING: India stocks worker failed to start: {e}")

    # AMFI scheduler and MF NAV refresh
    try:
        scheduler.start()
        asyncio.create_task(parse_and_store_navs())
    except Exception as e:
        print(f"WARNING: AMFI scheduler failed to start: {e}")

    # Stock history backfill
    try:
        async def run_backfill():
            async def _do():
                async for db in get_db():
                    await backfill_stock_history(db)
                    break
            await with_db_retry(_do)

        asyncio.create_task(run_backfill())
    except Exception as e:
        print(f"WARNING: Stock history backfill failed to start: {e}")

    # Crypto history backfill
    try:
        async def run_crypto_backfill():
            async def _do():
                async for db in get_db():
                    await backfill_crypto_history(db)
                    break
            await with_db_retry(_do)

        asyncio.create_task(run_crypto_backfill())
    except Exception as e:
        print(f"WARNING: Crypto history backfill failed to start: {e}")

    print("WealthPulse v2 started")


app.include_router(portfolio_router)
app.include_router(stream_router)
app.include_router(analytics_router)
app.include_router(market_router)
app.include_router(ai_router)
app.include_router(stock_router)
app.include_router(mf_router)
app.include_router(crypto_router)


@app.get("/")
async def root():
    return {"status": "WealthPulse v2 running"}


@app.get("/health")
async def health():
    """Keep-alive + status endpoint, hit by an external pinger.

    Runs a real SQL query (counts as activity for Neon) and a real Redis
    write (counts as activity for Upstash, resetting its 14-day inactivity
    clock). Always returns 200 so the pinger doesn't alert on partial
    outages; the body says which dependency is down.
    """
    from core.redis import get_redis

    db_status = "ok"
    redis_status = "ok"

    try:
        async for db in get_db():
            await db.execute(text("SELECT 1"))
            break
    except Exception as e:
        db_status = "down"
        print(f"[health] database check failed: {e!r}")

    try:
        redis = await get_redis()
        await redis.ping()  # raises when Redis is unreachable
        await redis.setex(
            "keepalive:ping", 86400, datetime.now(timezone.utc).isoformat()
        )
    except Exception as e:
        redis_status = "down"
        print(f"[health] redis check failed: {e!r}")

    return {"status": "ok", "db": db_status, "redis": redis_status}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )
