from pydantic_settings import BaseSettings
from pydantic import model_validator


class Settings(BaseSettings):
    # ── Database (PostgreSQL via asyncpg) ─────────────────────────────────────
    DATABASE_URL: str
    DATABASE_URL_SYNC: str = ""

    # ── Cache ─────────────────────────────────────────────────────────────────
    REDIS_URL: str

    # ── Workers ───────────────────────────────────────────────────────────────
    # Set false on extra instances / local dev against shared services so the
    # price workers, AMFI cron and backfills only run in one process.
    WORKERS_ENABLED: bool = True

    # ── Auth0 ─────────────────────────────────────────────────────────────────
    AUTH0_DOMAIN: str                          # e.g. dev-qt0cqogfgwebky55.us.auth0.com
    AUTH0_AUDIENCE: str = "wealthpulse-local"  # set to AUTH0_CLIENT_ID in production

    # ── External APIs ─────────────────────────────────────────────────────────
    GROQ_API_KEY: str
    GEMINI_API_KEY: str
    FINNHUB_API_KEY: str

    # ── CORS ──────────────────────────────────────────────────────────────────
    FRONTEND_URL: str = "https://wealthpulse.vercel.app"  # override with your real Vercel URL

    @model_validator(mode="after")
    def derive_sync_url(self):
        if not self.DATABASE_URL_SYNC:
            self.DATABASE_URL_SYNC = self.DATABASE_URL.replace(
                "postgresql+asyncpg://", "postgresql://"
            )
        return self

    class Config:
        env_file = ".env"


settings = Settings()
