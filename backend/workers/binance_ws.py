import asyncio
import json
import websockets
from websockets.exceptions import InvalidStatus

COINS = ["btcusdt", "ethusdt", "solusdt", "bnbusdt", "adausdt", "dogeusdt"]

async def binance_price_worker():
    from core.redis import get_redis
    redis_client = await get_redis()
    streams = "/".join(f"{c}@ticker" for c in COINS)
    uri = f"wss://stream.binance.com:9443/stream?streams={streams}"
    print("🔄 Connecting to Binance WebSocket...")

    backoff_time = 1  # Start with 1 second backoff
    max_backoff = 300  # Max 5 minutes

    while True:
        try:
            async for ws in websockets.connect(uri):
                try:
                    print("✅ Binance WebSocket connected")
                    backoff_time = 1  # Reset backoff on successful connection

                    async for msg in ws:
                        data = json.loads(msg)["data"]
                        symbol = data["s"].lower()
                        price  = data["c"]
                        await redis_client.setex(f"price:crypto:{symbol}", 30, price)
                        # last-known copy so prices survive quiet markets / worker gaps
                        await redis_client.setex(f"last:price:crypto:{symbol}", 604800, price)
                        await redis_client.publish(
                            "prices",
                            json.dumps({"symbol": symbol, "price": price, "type": "crypto"})
                        )
                except Exception as e:
                    print(f"⚠️ Binance WS error: {e}, reconnecting...")
                    await asyncio.sleep(1)
        except InvalidStatus as e:
            # Handle rate limiting (429) and other HTTP errors
            if "429" in str(e) or "Too Many Requests" in str(e):
                print(f"🚫 Rate limited by Binance. Backing off for {backoff_time}s...")
                await asyncio.sleep(backoff_time)
                backoff_time = min(backoff_time * 2, max_backoff)
            else:
                print(f"❌ Binance connection error: {e}. Retrying in {backoff_time}s...")
                await asyncio.sleep(backoff_time)
                backoff_time = min(backoff_time * 2, max_backoff)
        except Exception as e:
            print(f"❌ Unexpected error: {e}. Retrying in {backoff_time}s...")
            await asyncio.sleep(backoff_time)
            backoff_time = min(backoff_time * 2, max_backoff)
