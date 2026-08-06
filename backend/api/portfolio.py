from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, text
from typing import List
from core.database import get_db
from core.security import get_current_user
from models.holding import Holding
from schemas.portfolio import HoldingCreate, HoldingResponse
from workers.amfi_cron import parse_and_store_navs
from services.price_backfill import ensure_stock_history, ensure_mf_history, ensure_crypto_history

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


@router.get("", response_model=List[HoldingResponse])
async def get_portfolio(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Holding).where(Holding.user_id == user["sub"])
    )
    return result.scalars().all()


@router.get("/history/{symbol}", response_model=List[HoldingResponse])
async def get_symbol_history(
    symbol: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all buy lots for a given symbol for the current user.
    Used by the portfolio card click to show buy history modal.
    """
    result = await db.execute(
        select(Holding)
        .where(
            Holding.user_id == user["sub"],
            Holding.symbol.ilike(symbol),
        )
        .order_by(Holding.buy_date, Holding.created_at)
    )
    rows = result.scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No history found for {symbol}")
    return rows


@router.post("", response_model=HoldingResponse, status_code=201)
async def add_holding(
    item: HoldingCreate,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    holding = Holding(**item.model_dump(), user_id=user["sub"])
    db.add(holding)
    await db.commit()
    await db.refresh(holding)

    # Trigger lazy backfill in background — non-blocking
    asset_type = item.asset_type or item.item_type  # handle legacy field
    if asset_type == "stock":
        background_tasks.add_task(ensure_stock_history, item.symbol, db)
    elif asset_type == "mutualfund":
        background_tasks.add_task(ensure_mf_history, item.symbol, db)
        # Refresh NAVs so Redis gets nav:{schemecode} for this and other MF holdings
        background_tasks.add_task(parse_and_store_navs)
    elif asset_type == "crypto":
        background_tasks.add_task(ensure_crypto_history, item.symbol, db)

    return holding


@router.delete("/holding/{holding_id}")
async def remove_holding(
    holding_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify the holding belongs to the current user
    result = await db.execute(
        select(Holding).where(Holding.id == holding_id, Holding.user_id == user["sub"])
    )
    holding = result.scalars().one_or_none()

    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")

    # Delete the holding
    result = await db.execute(
        delete(Holding).where(Holding.id == holding_id, Holding.user_id == user["sub"])
    )
    await db.commit()
    return {"message": "Removed"}


@router.post("/test-nav-refresh")
async def test_nav_refresh(user: dict = Depends(get_current_user)):
    await parse_and_store_navs()
    return {"message": "NAV refresh triggered"}


@router.post("/backfill-nav/{scheme_code}")
async def backfill_nav(
    scheme_code: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import httpx
    from datetime import datetime

    async with httpx.AsyncClient() as client:
        r = await client.get(f"https://api.mfapi.in/mf/{scheme_code}", timeout=15)

    nav_history = r.json().get("data", [])
    rows = []
    for entry in nav_history:
        try:
            date_val = datetime.strptime(entry["date"], "%d-%m-%Y").date()
            nav = float(entry["nav"])
            rows.append({"s": scheme_code, "d": date_val, "p": nav})
        except Exception:
            continue

    if rows:
        await db.execute(
            text("""
                INSERT INTO price_history (symbol, asset_type, price_date, close_price)
                VALUES (:s, 'mutualfund', :d, :p)
                ON CONFLICT (symbol, "price_date") DO NOTHING
            """),
            rows,
        )
        await db.commit()

    return {"scheme_code": scheme_code, "inserted": len(rows)}
