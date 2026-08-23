import asyncio
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from datetime import date
from core.database import AsyncSessionLocal, get_db
from core.redis import get_redis
from models.snapshot import PortfolioSnapshot
from models.holding import Holding

scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

async def get_active_scheme_codes() -> set:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("SELECT DISTINCT symbol FROM holdings WHERE asset_type='mutualfund'")
        )
        return {row[0] for row in result.fetchall()}

async def parse_and_store_navs():
    active = await get_active_scheme_codes()
    if not active:
        print("No active MF holdings, skipping NAV refresh")
        return

    print(f"Refreshing NAVs for {len(active)} schemes...")
    async with httpx.AsyncClient() as client:
        r = await client.get("https://portal.amfiindia.com/spages/NAVAll.txt", timeout=30)

    # Print a sample of the file to see actual format
    lines = r.text.splitlines()
    print(f"Total lines: {len(lines)}")
    print(f"Sample lines: {lines[:5]}")

    # Check if our scheme exists
    for line in lines:
        if "119551" in line:
            print(f"Found: {line}")
            break
    else:
        print("Scheme 119551 NOT found in AMFI file")

    redis = await get_redis()
    async with AsyncSessionLocal() as db:
        count = 0
        for line in lines:
            parts = line.split(";")
            if len(parts) < 6:
                continue
            code = parts[0].strip()
            if code not in active:
                continue
            try:
                # AMFI added Plan/Option columns; NAV and date are always last
                nav = float(parts[-2].strip())
                date_str = parts[-1].strip()
                await db.execute(text("""
                    INSERT INTO price_history (symbol, asset_type, price_date, close_price)
                    VALUES (:symbol, 'mutualfund', TO_DATE(:date, 'DD-Mon-YYYY'), :price)
                    ON CONFLICT (symbol, price_date) DO UPDATE SET close_price = :price
                """), {"symbol": code, "date": date_str, "price": nav})
                # Update Redis with latest NAV for this scheme (1-hour TTL)
                await redis.setex(f"nav:{code}", 3600, str(nav))
                await redis.setex(f"last:nav:{code}", 604800, str(nav))
                count += 1
            except Exception as e:
                print(f"Error for {code}: {e}")
                continue
        await db.commit()
        print(f"✅ NAVs updated for {count} schemes")

@scheduler.scheduled_job("cron", hour="10,14,18,21", minute=10)
async def scheduled_nav_refresh():
    await parse_and_store_navs()


async def take_daily_snapshot():
    from services.prices import resolve_price

    redis = await get_redis()
    async for db in get_db():
        try:
            result = await db.execute(select(Holding))
            holdings = result.scalars().all()

            # One price lookup per (symbol, asset_type), shared across users
            price_cache: dict[tuple, float | None] = {}
            user_totals = {}
            for h in holdings:
                invested = float(h.buy_price) * float(h.quantity)
                pkey = (h.symbol, h.asset_type)
                if pkey not in price_cache:
                    price, _stale = await resolve_price(h.symbol, h.asset_type, redis, db)
                    price_cache[pkey] = price
                price = price_cache[pkey]
                current = price * float(h.quantity) if price and price > 0 else invested

                totals = user_totals.setdefault(
                    h.user_id, {"invested": 0.0, "value": 0.0, "breakdown": {}}
                )
                totals["invested"] += invested
                totals["value"] += current
                bd = totals["breakdown"].setdefault(
                    h.asset_type, {"invested": 0.0, "value": 0.0}
                )
                bd["invested"] += invested
                bd["value"] += current

            for uid, totals in user_totals.items():
                breakdown = {
                    atype: {"invested": round(v["invested"], 2), "value": round(v["value"], 2)}
                    for atype, v in totals["breakdown"].items()
                }
                stmt = pg_insert(PortfolioSnapshot).values(
                    user_id=uid,
                    snapshot_date=date.today(),
                    total_value=round(totals["value"], 2),
                    total_cost=round(totals["invested"], 2),
                    breakdown=breakdown,
                )
                # re-running the same day corrects the snapshot
                stmt = stmt.on_conflict_do_update(
                    index_elements=["user_id", "snapshot_date"],
                    set_={
                        "total_value": stmt.excluded.total_value,
                        "total_cost": stmt.excluded.total_cost,
                        "breakdown": stmt.excluded.breakdown,
                    },
                )
                await db.execute(stmt)

            await db.commit()
            print(f"✅ Daily snapshot saved for {len(user_totals)} users")
        except Exception as e:
            print(f"⚠️ Snapshot error: {e}")
        break


scheduler.add_job(
    lambda: asyncio.create_task(take_daily_snapshot()),
    "cron",
    hour=23,
    minute=55,
    id="daily_snapshot"
)
