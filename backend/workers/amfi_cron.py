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
                nav = float(parts[4].strip())
                date_str = parts[5].strip()
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
    async for db in get_db():
        try:
            result = await db.execute(select(Holding))
            holdings = result.scalars().all()

            user_totals = {}
            for h in holdings:
                uid = h.user_id
                invested = float(h.buy_price) * float(h.quantity)
                current = invested  # fallback — Redis lookup optional here
                user_totals.setdefault(uid, {"invested": 0, "value": 0})
                user_totals[uid]["invested"] += invested
                user_totals[uid]["value"] += current

            for uid, totals in user_totals.items():
                stmt = pg_insert(PortfolioSnapshot).values(
                    user_id=uid,
                    snapshot_date=date.today(),
                    total_value=totals["value"],
                    total_cost=totals["invested"],
                ).on_conflict_do_nothing()
                await db.execute(stmt)

            await db.commit()
            print("✅ Daily snapshot saved")
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
