import json
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from datetime import date
from core.database import get_db
from core.security import get_current_user
from core.redis import get_redis
from models.holding import Holding
from models.price_history import PriceHistory
from models.snapshot import PortfolioSnapshot
from services.analytics import calc_pnl, xirr, calc_risk_metrics, monte_carlo, numpy_to_python
from services.prices import resolve_price

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


async def get_current_price(symbol: str, asset_type: str, redis, db=None) -> float | None:
    price, _stale = await resolve_price(symbol, asset_type, redis, db)
    return price


async def get_price_series(symbol: str, db: AsyncSession) -> list[float]:
    result = await db.execute(
        select(PriceHistory.close_price)
        .where(PriceHistory.symbol == symbol)
        .order_by(desc(PriceHistory.price_date))
        .limit(365)
    )
    rows = result.scalars().all()
    return [float(r) for r in reversed(rows)]


@router.get("/portfolio")
async def portfolio_analytics(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns aggregated portfolio analytics.

    Holdings with the same (symbol, assettype) are aggregated:
    - Quantities summed
    - Buy prices weighted-averaged
    - XIRR computed from ALL lots for that symbol
    - Risk metrics and Monte Carlo computed per aggregated symbol
    """
    from collections import defaultdict

    redis = await get_redis()

    result = await db.execute(
        select(Holding).where(Holding.user_id == user["sub"])
    )
    holdings = result.scalars().all()

    # Group by (symbol, assettype)
    grouped = defaultdict(list)
    for h in holdings:
        key = (h.symbol, h.asset_type)
        grouped[key].append(h)

    total_invested = 0
    total_current = 0
    holdings_data = []

    for (symbol, assettype), lots in grouped.items():
        # Aggregate metrics across all lots
        total_qty = sum(float(h.quantity) for h in lots)
        total_lot_invested = sum(float(h.buy_price) * float(h.quantity) for h in lots)
        avg_buy_price = (total_lot_invested / total_qty) if total_qty > 0 else 0

        # Get current price (single lookup per symbol)
        current_price = await get_current_price(symbol, assettype, redis, db)
        if current_price is None or current_price <= 0:
            current_price = avg_buy_price

        # Calculate aggregated P&L
        pnl = calc_pnl(avg_buy_price, current_price, total_qty)

        # Build XIRR cashflows from ALL lots
        cashflows = []
        for lot in lots:
            lot_buydate = lot.buy_date if isinstance(lot.buy_date, date) else date.fromisoformat(str(lot.buy_date))
            lot_invested = float(lot.buy_price) * float(lot.quantity)
            cashflows.append((lot_buydate, -lot_invested))

        # Add final cashflow: current value at today
        cashflows.append((date.today(), pnl["current_value"]))
        holding_xirr = xirr(cashflows)

        # Fetch price series (same logic as before)
        price_series = await get_price_series(symbol, db)
        if len(price_series) < 2:
            price_series = [avg_buy_price, current_price]

        # Risk metrics (computed fresh)
        risk = calc_risk_metrics(price_series)

        # Monte Carlo (cached per symbol+assettype)
        mc_cache_key = f"mc:{symbol}:{assettype}"
        cached_mc = await redis.get(mc_cache_key)
        if cached_mc:
            mc = json.loads(cached_mc)
        else:
            mc = monte_carlo(pnl["current_value"], price_series)
            await redis.setex(mc_cache_key, 21600, json.dumps(mc))  # 6h cache

        total_invested += pnl["invested"]
        total_current += pnl["current_value"]

        # Build aggregated holding record (use first lot's ID as representative)
        holdings_data.append({
            "id": str(lots[0].id),
            "symbol": symbol,
            "name": lots[0].name,
            "asset_type": assettype,
            "quantity": round(total_qty, 8),  # Round to avoid float precision issues
            "buy_price": round(avg_buy_price, 4),
            "current_price": current_price,
            **pnl,
            "xirr": holding_xirr,
            "monte_carlo": mc,
            "risk": risk,
        })

    total_pnl = total_current - total_invested
    total_pnl_pct = round((total_pnl / total_invested) * 100, 2) if total_invested else 0

    return numpy_to_python({
        "summary": {
            "total_invested": round(total_invested, 2),
            "total_current_value": round(total_current, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": total_pnl_pct,
        },
        "holdings": holdings_data,
    })


@router.get("/history")
async def portfolio_history(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.user_id == user["sub"])
        .order_by(PortfolioSnapshot.snapshot_date)
        .limit(365)
    )
    snapshots = result.scalars().all()
    return [
        {
            "date": str(s.snapshot_date),
            "total_value": float(s.total_value),
            "total_invested": float(s.total_cost),
            "pnl": float(s.total_value - s.total_cost),
        }
        for s in snapshots
    ]


@router.post("/test-snapshot")
async def test_snapshot():
    from workers.amfi_cron import take_daily_snapshot
    await take_daily_snapshot()
    return {"message": "Snapshot triggered"}
