import asyncio
import json
import os
import websockets
from dotenv import load_dotenv
from websockets.exceptions import InvalidStatus

load_dotenv()

SYMBOLS = ["AAPL", "GOOGL", "MSFT", "TSLA", "AMZN", "NVDA"]
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY")

async def finnhub_price_worker():
    from core.redis import get_redis
    redis_client = await get_redis()
    uri = f"wss://ws.finnhub.io?token={FINNHUB_KEY}"
    print("🔄 Connecting to Finnhub WebSocket...")

    backoff_time = 1  # Start with 1 second backoff
    max_backoff = 300  # Max 5 minutes

    while True:
        try:
            async for ws in websockets.connect(uri):
                try:
                    print("✅ Finnhub WebSocket connected")
                    backoff_time = 1  # Reset backoff on successful connection

                    for symbol in SYMBOLS:
                        await ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))

                    async for msg in ws:
                        data = json.loads(msg)
                        if data.get("type") != "trade":
                            continue
                        for trade in data.get("data", []):
                            symbol = trade["s"].lower().replace(".", "_")
                            price  = str(trade["p"])
                            await redis_client.setex(f"price:stock:{symbol}", 30, price)
                            # last-known copy — US market is closed ~18h/day
                            await redis_client.setex(f"last:price:stock:{symbol}", 604800, price)
                            await redis_client.publish(
                                "prices",
                                json.dumps({"symbol": symbol, "price": price, "type": "stock"})
                            )
                except Exception as e:
                    print(f"⚠️ Finnhub WS error: {e}, reconnecting...")
                    await asyncio.sleep(1)
        except InvalidStatus as e:
            # Handle rate limiting (429) and other HTTP errors
            if "429" in str(e) or "Too Many Requests" in str(e):
                print(f"🚫 Rate limited by Finnhub. Backing off for {backoff_time}s...")
                await asyncio.sleep(backoff_time)
                backoff_time = min(backoff_time * 2, max_backoff)
            else:
                print(f"❌ Finnhub connection error: {e}. Retrying in {backoff_time}s...")
                await asyncio.sleep(backoff_time)
                backoff_time = min(backoff_time * 2, max_backoff)
        except Exception as e:
            print(f"❌ Unexpected error: {e}. Retrying in {backoff_time}s...")
            await asyncio.sleep(backoff_time)
            backoff_time = min(backoff_time * 2, max_backoff)
