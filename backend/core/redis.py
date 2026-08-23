import asyncio
import time

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from core.config import settings

# Errors that mean "Redis is unreachable", not "your command is wrong"
_CONN_ERRORS = (RedisError, OSError, asyncio.TimeoutError)

RECONNECT_INTERVAL = 60  # seconds between attempts to reach Redis while down


class _MemoryTTLCache:
    """Dict-based stand-in used while Redis is unreachable (e.g. the Upstash
    free database expired). Per-process only, but keeps the API serving
    instead of returning 500s."""

    def __init__(self):
        self._store = {}  # key -> (value, expires_at)

    def get(self, key):
        item = self._store.get(key)
        if not item:
            return None
        value, expires_at = item
        if time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    def setex(self, key, ttl, value):
        if len(self._store) > 5000:
            now = time.monotonic()
            for k in [k for k, (_, exp) in self._store.items() if exp <= now]:
                del self._store[k]
        self._store[key] = (str(value), time.monotonic() + int(ttl))


class _SafePubSub:
    """Pub/sub that degrades to yielding nothing while Redis is down and
    resubscribes automatically once it is back."""

    def __init__(self, safe: "SafeRedis"):
        self._safe = safe
        self._real = None
        self._channels: tuple = ()

    async def subscribe(self, *channels):
        self._channels = channels
        if not self._safe._should_try():
            return
        try:
            if self._real is None:
                self._real = self._safe._client.pubsub()
            await self._real.subscribe(*channels)
            self._safe._mark_up()
        except _CONN_ERRORS as e:
            self._safe._mark_down(e)
            self._real = None

    async def unsubscribe(self, *channels):
        if self._real is None:
            return
        try:
            await self._real.unsubscribe(*channels)
        except _CONN_ERRORS:
            self._real = None

    async def get_message(self, ignore_subscribe_messages=False, timeout=None):
        if self._real is None and self._channels and self._safe._should_try():
            await self.subscribe(*self._channels)
        if self._real is not None:
            try:
                return await self._real.get_message(
                    ignore_subscribe_messages=ignore_subscribe_messages,
                    timeout=timeout,
                )
            except _CONN_ERRORS as e:
                self._safe._mark_down(e)
                self._real = None
        if timeout:
            await asyncio.sleep(timeout)
        return None


class SafeRedis:
    """Wraps the async Redis client with an in-memory fallback so a dead or
    expired Redis degrades to per-process caching instead of breaking every
    endpoint. Retries the real Redis every RECONNECT_INTERVAL seconds."""

    def __init__(self, url: str):
        self._client = aioredis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        self._fallback = _MemoryTTLCache()
        self._down = False
        self._last_attempt = 0.0

    def _should_try(self) -> bool:
        return (not self._down) or (
            time.monotonic() - self._last_attempt >= RECONNECT_INTERVAL
        )

    def _mark_down(self, e):
        if not self._down:
            print(f"[redis] unavailable ({e!r}) - using in-memory fallback cache")
        self._down = True
        self._last_attempt = time.monotonic()

    def _mark_up(self):
        if self._down:
            print("[redis] connection restored")
        self._down = False

    async def ping(self):
        try:
            res = await self._client.ping()
            self._mark_up()
            return res
        except _CONN_ERRORS as e:
            self._mark_down(e)
            raise

    async def get(self, key):
        if self._should_try():
            try:
                val = await self._client.get(key)
                self._mark_up()
                if val is not None:
                    return val
            except _CONN_ERRORS as e:
                self._mark_down(e)
        return self._fallback.get(key)

    async def setex(self, key, ttl, value):
        self._fallback.setex(key, ttl, value)
        if self._should_try():
            try:
                await self._client.setex(key, ttl, value)
                self._mark_up()
                return True
            except _CONN_ERRORS as e:
                self._mark_down(e)
        return True

    async def publish(self, channel, message):
        if self._should_try():
            try:
                res = await self._client.publish(channel, message)
                self._mark_up()
                return res
            except _CONN_ERRORS as e:
                self._mark_down(e)
        return 0

    def pubsub(self):
        return _SafePubSub(self)


redis_client: SafeRedis = None

async def init_redis():
    global redis_client
    redis_client = SafeRedis(settings.REDIS_URL)
    try:
        await redis_client.ping()
        print("✅ Redis connected")
    except Exception as e:
        print(f"⚠️ Redis connection failed: {e}")
        # don't crash — SafeRedis serves from the in-memory fallback

async def get_redis():
    global redis_client
    if redis_client is None:
        redis_client = SafeRedis(settings.REDIS_URL)
    return redis_client
