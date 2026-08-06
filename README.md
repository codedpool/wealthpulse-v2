# WealthPulse

WealthPulse is an AI-powered portfolio cockpit for Indian retail investors. Track stocks, mutual funds, and crypto in one place, see real risk/return analytics, and get conversational guidance from AI Dost and AI Report.

**Stack:** Next.js (Vercel) · FastAPI (Render) · PostgreSQL (Neon) · Redis (Upstash) · Auth0

---

## What you can do

* **Track everything in one dashboard**

  * Add stocks, mutual funds, and crypto with buy price, quantity, and buy date.
  * See aggregated positions (average buy price, total quantity) instead of scattered lots.
  * View per-holding and overall P&L, XIRR, and timeline charts.

* **See real market behaviour**

  * Live prices via:

    * Binance WebSocket (crypto → Redis).
    * Finnhub WebSocket (US stocks → Redis).
    * yfinance polling (Indian stocks → Redis).
  * MF NAVs parsed directly from the AMFI NAV text file and stored in `price_history`.

* **Understand risk, not just returns**

  * Volatility, Sharpe ratio, and max drawdown per asset using daily price history.
  * 1-year Monte Carlo simulation for each holding, cached in Redis for fast access.
  * Daily portfolio snapshots (total value, cost, and per-asset-type breakdown) so you can see how the portfolio evolved over time.

* **Talk to your portfolio**

  * **AI Dost**: friendly assistant that explains your portfolio in simple language and suggests next steps.
  * **AI Report**: professional-style report with allocation, risk assessment, and performance by asset.
  * Both use Groq (Llama-3.3-70B) with automatic Gemini fallback.

---

## Architecture & data flow

```
                 Binance WS ──┐
                 Finnhub  WS ─┤  workers (asyncio tasks     ┌────────────┐
                 yfinance ────┤  inside the FastAPI process) │   Redis    │
                 AMFI NAV ────┘            │                 │  (Upstash) │
                                           ▼                 └─────┬──────┘
   Next.js  ──►  FastAPI  ──►  live keys (30–120s TTL)             │
  (Vercel)      (Render)       last-known keys (7d TTL)            │ pub/sub
                     │         NAV + API caches                    ▼
                     ▼                                      SSE /api/stream/prices
              ┌─────────────┐
              │  PostgreSQL │   holdings · price_history · portfolio_snapshots
              │   (Neon)    │
              └─────────────┘
```

* **Live prices** land in Redis under short-TTL keys (`price:stock:*`, `price:crypto:*`, `nav:*`) and are also published on a `prices` pub/sub channel, which the SSE endpoint streams to the browser.
* **Price resolution** (`services/prices.py`) falls back in order: live key → 7-day `last:*` key → latest `price_history` close. Responses carry a `stale` flag so the UI can label non-live prices instead of showing nothing outside market hours.
* **History** matters because volatility, Sharpe, drawdown, and Monte Carlo need a *series*, not a spot price. Backfill jobs fetch ~1 year of daily closes into `price_history` whenever a symbol appears in holdings.
* **Snapshots** run nightly (23:55 IST): real current values per user (via the same price-resolution chain), upserted per `(user, date)` with a JSONB per-asset-type breakdown.

## Built for free tiers

The whole stack runs on free plans, which normally means things stop: Render spins down after ~15 idle minutes, Neon suspends idle computes, and Upstash deletes databases after 14 days without commands. WealthPulse is built to survive all of that:

* **Keep-alive pinger** — a GitHub Actions cron (`.github/workflows/keepalive.yml`) curls `GET /health` every 10 minutes. That request keeps Render awake, runs a real SQL query (Neon activity), and performs a real Redis write (resets Upstash's inactivity clock). Setup: add a repository variable `RENDER_APP_URL` pointing at your backend.
* **DB cold-start resilience** — the SQLAlchemy engine uses `pool_pre_ping` + `pool_recycle`, and startup jobs retry, so the first queries after a Neon wake-up succeed instead of erroring.
* **Redis loss is non-fatal** — all Redis access goes through a `SafeRedis` wrapper (`core/redis.py`). If Redis is unreachable, the API serves from an in-process TTL cache, pub/sub degrades silently, and the wrapper retries the real Redis every 60s and self-heals.
* **Single-writer workers** — all pollers/crons run inside the API process; set `WORKERS_ENABLED=false` on extra instances to avoid duplicate polling.

---

## API overview (backend)

Base URL (local): `http://localhost:8000`

### Health

* `GET /health`
  Liveness + dependency status: `{"status":"ok","db":"ok|down","redis":"ok|down"}`. Always HTTP 200; used by the keep-alive pinger.

### Portfolio

* `GET /api/portfolio`
  List all holdings for the current user.

* `POST /api/portfolio`
  Add a holding (symbol, name, assettype, buyprice, quantity, buydate). Triggers a background history backfill for the symbol.

* `DELETE /api/portfolio/holding/{id}`
  Remove a specific lot.

* `GET /api/portfolio/history/{symbol}`
  Full buy history (all lots) for a symbol for the current user.

### Analytics

* `GET /api/analytics/portfolio`
  Aggregated portfolio analytics:

  * Summary (invested, current value, total P&L, P&L %).
  * Holdings (one per symbol) with P&L, XIRR, risk metrics, Monte Carlo.

* `GET /api/analytics/history`
  Portfolio value over time from daily snapshots.

### Market data

* `GET /api/market/mutualfunds?q=…` – MF search.
* `GET /api/market/mutualfunds/{schemecode}` – MF NAV history.
* `GET /api/market/stocks/india?symbol=…` – Indian stock price.
* `GET /api/market/stocks/us?symbol=…` – US stock price.
* `GET /api/market/crypto?symbol=…` – Crypto price.
* `GET /api/market/price/{asset_type}/{symbol}` – unified lookup (`stock` | `crypto` | `mutualfund`).

Price responses include `"stale": true|false` — `false` means a live tick, `true` means last-known or historical close.

### Streaming

* `GET /api/stream/prices`
  Server-Sent Events stream of live prices from Redis pub/sub.

### AI

* `GET /api/ai/dost`
  Friendly AI overview of your portfolio.

* `GET /api/ai/report`
  Structured AI report (`{ text, format: "markdown" }`).

All protected endpoints (including the maintenance endpoints `POST /api/portfolio/test-nav-refresh`, `POST /api/portfolio/backfill-nav/{code}`, `POST /api/analytics/test-snapshot`) expect an Auth0 access token in the `Authorization: Bearer` header.

---

## Environment configuration

### Backend `.env`

```bash
# ── Copy .env.example to .env and fill in the real values ────────────

# PostgreSQL — async URL for the app (asyncpg driver).
# IMPORTANT: no ?sslmode=... or channel_binding=... here — asyncpg rejects
# libpq-style params (TLS is enforced in code). Strip them from the URL
# your provider gives you.
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST/DATABASE

# Sync URL for Alembic migrations (psycopg2) — this one DOES take sslmode.
DATABASE_URL_SYNC=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require

# Redis (Upstash URLs start with rediss:// — TLS)
REDIS_URL=redis://localhost:6379

# Run price workers / AMFI cron / backfills in this process.
# Set false on extra instances so shared services aren't polled twice.
WORKERS_ENABLED=true

# Auth0 (from Auth0 Dashboard → Applications → your app)
AUTH0_DOMAIN=your-tenant.us.auth0.com
AUTH0_AUDIENCE=https://wealthpulse/api   # must match the `aud` claim in the access token

# External APIs
GROQ_API_KEY=your_groq_key
GEMINI_API_KEY=your_gemini_key
FINNHUB_API_KEY=your_finnhub_key

# CORS — set this to your real Vercel deployment URL
FRONTEND_URL=https://your-frontend.vercel.app
```

### Frontend `.env.local`

```bash
# Auth0 Configuration for Next.js
AUTH0_SECRET=your_secret_key_here
AUTH0_BASE_URL=http://localhost:3000
AUTH0_ISSUER_BASE_URL=https://your-auth0-domain.us.auth0.com
AUTH0_CLIENT_ID=your_client_id_here
AUTH0_CLIENT_SECRET=your_client_secret_here

# API Keys (for frontend AI routes, if used)
GROQ_API_KEY=your_groq_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here

# Backend URL
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## Getting started

### 1. Clone the repo

```bash
git clone https://github.com/codedpool/wealthpulse-v2.git
cd wealthpulse-v2
```

### 2. Backend (FastAPI)

```bash
cd backend

# Create virtualenv
python -m venv venv
# Activate (Windows)
source venv/Scripts/activate
# or on Unix/macOS: source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure env
cp .env.example .env
# edit .env with your DB, Redis, Auth0, and API keys

# Apply migrations
alembic upgrade head

# Run backend
uvicorn main:app --reload
```

Backend will run on `http://localhost:8000`. Redis being unavailable locally is fine — the app falls back to in-memory caching.

### 3. Frontend (Next.js)

```bash
cd frontend

# Install deps
npm install

# Create .env.local and fill the values above

# Run dev server
npm run dev
```

Frontend will run on `http://localhost:3000` and proxy API calls to the backend (via `NEXT_PUBLIC_API_URL` and Next.js rewrites).

### 4. Tests

```bash
cd backend
python -m pytest -q
```

---

## Deployment

| Piece | Where | Notes |
|---|---|---|
| Frontend | Vercel | set `NEXT_PUBLIC_API_URL` to the Render URL |
| Backend | Render (free) | env vars from “Backend `.env`” above; auto-deploys from `main` |
| Postgres | Neon (free) | async URL **without** `sslmode`, sync URL **with** it |
| Redis | Upstash (free) | `rediss://` URL |
| Keep-alive | GitHub Actions | add repo variable `RENDER_APP_URL`; verify a green `keepalive` run, then check `GET /health` returns `db: ok, redis: ok` |
