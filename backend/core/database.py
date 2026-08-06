import asyncio

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    # Neon suspends idle computes; pre-ping + recycle replace connections
    # that died while the database was paused instead of erroring.
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=5,
    connect_args={"ssl": "require"}
)
AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def with_db_retry(fn, retries: int = 2, delay: float = 2.0):
    """Run an async DB operation, retrying on connection failures.

    The first connection after a Neon cold start can fail or time out
    while the compute wakes up (a few seconds).
    """
    for attempt in range(retries + 1):
        try:
            return await fn()
        except (OperationalError, InterfaceError, OSError, asyncio.TimeoutError) as e:
            if attempt == retries:
                raise
            print(f"⚠️ DB not ready ({e!r}), retry {attempt + 1}/{retries}...")
            await asyncio.sleep(delay * (attempt + 1))
