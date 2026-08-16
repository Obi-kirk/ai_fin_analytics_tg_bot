# 🤖 FinMind — AI Financial Telegram Bot

[![CI](https://github.com/Obi-kirk/ai_fin_analytics_tg_bot/actions/workflows/ci.yml/badge.svg)](https://github.com/Obi-kirk/ai_fin_analytics_tg_bot/actions/workflows/ci.yml)

**FinMind** is a Telegram bot for financial analytics: CBR exchange rates, stocks and indexes (Finnhub), cryptocurrencies (CoinGecko), AI-powered asset analysis, portfolio tracking, price alerts, and a daily digest.

> ⚠️ **This is a demo / educational project.** It is not a financial service and does not provide investment advice. See the [Disclaimer](#-disclaimer) for details.

---

## ⚠️ Disclaimer

This bot was created **exclusively for educational and demonstration purposes**. All information, including AI-generated analysis and forecasts, is for reference only and **is not financial advice, an investment recommendation, or a call to action**.

- 🔹 **Risks.** Investing in financial instruments involves a high risk of losing capital. Past performance does not guarantee future results. The AI model may make mistakes; data may be delayed or inaccurate.
- 🔹 **Liability.** You make financial decisions on your own and bear full responsibility for all risks. The developer is not liable for any losses or lost profits resulting from the use of this bot.
- 🔹 **Data sources.** Data comes through open APIs (CBR, Finnhub, CoinGecko) and may be delayed; up-to-dateness is not guaranteed.

By continuing to use the bot, you confirm that you have read this notice and accept all risks.

---

## ✨ Features

| Section | What it does |
|---|---|
| 💱 **Currencies** | CBR exchange rates (12 currencies, `/rate` — all at once), cross-rate pairs `/rate USD EUR`, converter with crypto `/convert` |
| 📈 **Stocks & indexes** | Two markets: **🌍 World** (30 tickers, USD) and **🇷🇺 Russia** (46 tickers, RUB via MOEX); indexes (SPX, DJI, VIX → ETF proxies); news `/news` (incl. Russian stocks via Google News) |
| 🪙 **Crypto** | Prices, trends `/trending`, top `/top`, 30-day PNG price chart `/chart` (30 coins in the menu) |
| 📊 **Charts** | `/chart` for crypto, world stocks (Yahoo) and Russian stocks (MOEX) — 30-day PNG with the right currency axis |
| 🤖 **AI analysis** | `/analyze` — asset breakdown via LLM (quotes, profile, news, market cap, trend); 109 assets incl. all Russian stocks |
| 📁 **Portfolio** | Asset tracking, quantity and value, **P&L** (profit/loss from the buy price), total balance in USD + RUB, remove with confirmation |
| 🔔 **Alerts** | Absolute (“above 70,000”) and relative (“+5% from current”) |
| 📰 **Digest** | Daily summary at a personal time (or 9:00 default) + custom asset set, greeting by time of day |
| 🌐 **i18n** | Russian and English UI (`/lang` to switch, `DEFAULT_LANGUAGE` in `.env`) |
| 🛡️ **Security** | Roles (RBAC), bans, query history, prompt-injection protection (EN + RU), PII masking |

---

## 🚀 Quick start

```bash
# 1. Clone the repository
git clone https://github.com/Obi-kirk/ai_fin_analytics_tg_bot.git
cd ai_fin_analytics_tg_bot

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env from the template and fill in the secrets
cp .env.example .env

# 5. Run the bot (polling)
python -m src.main
```

---

## 🐳 Run with Docker

The project ships a `Dockerfile` and `docker-compose.yml` (bot + PostgreSQL in one command).

```bash
# 1. Create .env from the template and fill in the secrets
cp .env.example .env

# 2. Build and start bot + PostgreSQL
docker compose up -d

# 3. Follow the logs
docker compose logs -f bot

# Stop
docker compose down
```

Notes:
- `DATABASE_URL` is **overridden** by compose to point at the bundled PostgreSQL container — your local Postgres (if any) is not touched. Override `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` in `.env` if needed (defaults: `finmind`).
- Table creation is automatic on startup (`create_tables`).
- The image runs as a non-root user; only `src/` is copied in — no tests, `.env` or logs inside the image.
- Prefer `.env` for secrets; `docker-compose.yml` and `.dockerignore` keep them out of the image.

---

## ⚙️ Configuration (`.env`)

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from [@BotFather](https://t.me/BotFather) |
| `DATABASE_URL` | ✅ | `postgresql+asyncpg://user:pass@localhost:5432/dbname` |
| `FINNHUB_API_KEY` | ✅ | [finnhub.io](https://finnhub.io) key (stocks, 60 req/min) |
| `COINGECKO_API_KEY` | ❌ | CoinGecko key (demo: 100 req/min) |
| `OPENROUTER_API_KEY` | ❌* | [openrouter.ai](https://openrouter.ai) key — AI analysis |
| `ADMIN_ID` | ❌ | Telegram user_id of the admin (admin commands) |
| `OPENROUTER_MODEL` | ❌ | Model for AI analysis (default `nvidia/nemotron-3-nano-30b-a3b:free`) |
| `DEFAULT_LANGUAGE` | ❌ | Bot language: `en` (default) or `ru` |

\* Without AI-analysis and crypto keys the bot still works, but the corresponding commands are unavailable.

> **Important:** `.env` contains secrets and **must not be committed** to the repository. It is already in `.gitignore`.

---

## ⌨️ Commands

**User commands:**
`/start` `/rate` `/convert` `/stock` `/crypto` `/chart` `/trending` `/top` `/news` `/analyze` `/portfolio` `/add` `/remove` `/alert` `/alerts` `/remove_alert` `/digest` `/myrole` `/lang` `/help`

**Admin commands** (only for `ADMIN_ID`):
`/admin` `/users` `/broadcast` `/ban` `/unban` `/cachestats` `/recent` `/setrole`

Examples:
```text
/rate              — all CBR rates
/rate USD EUR      — cross-rate pair (any two CBR currencies)
/convert 100 USD RUB
/stock SPX         — S&P 500 index (via SPY ETF)
/stock SBER        — Russian stock (Sber, via MOEX, in RUB)
/crypto BTC
/chart SBER        — 30-day PNG chart (RUB axis)
/chart AAPL        — 30-day PNG chart (USD axis)
/analyze BTC       — AI analysis
/alert BTC 70000   — alert "above 70,000"
/portfolio         — portfolio menu (/add BTC)
/digest            — daily digest
```

---

## 🧠 AI analysis

`/analyze` sends the asset context to an LLM: quote, company profile, latest news, market cap, 7/30-day trend. The answer is formatted as HTML and followed by a disclaimer.

Context security:
- user input is sanitized (prompt-injection protection);
- context is truncated (max 3 news items, length limits);
- portfolio and asset quantities are **never sent** to the LLM.

---

## 🏗️ Architecture

```
src/
├── main.py               # Entry point: polling, routers, middleware, DI
├── handlers/             # /start /help /menu /rate /stock /crypto /analyze
│                         # /portfolio (portfolio+alerts) /digest /admin /lang /errors
├── services/
│   ├── financial_api.py  # CBR, MOEX, Finnhub, CoinGecko, Google News, Yahoo clients
│   ├── cache.py          # TTLCache (in-memory, with GC)
│   ├── alerts.py         # Background price-alert loop
│   ├── digest.py         # Daily digest builder and sender
│   └── llm_service.py    # LLMClient (OpenRouter), sanitization
├── i18n.py               # ru/en string dictionaries + t()
├── database/             # SQLAlchemy async (PostgreSQL), models
├── middleware/           # Throttling, user upsert, query log
├── config/               # pydantic-settings
└── utils/                # PII masking in logs
```

Key decisions:
- **PostgreSQL + SQLAlchemy 2 (async)** — models `User`, `PortfolioItem`, `Alert`, `QueryLog`, `DigestSubscription`, `DigestAsset`;
- **TTLCache** — FX rates 1 hour, stocks/crypto 10 minutes, fundamental data 30 minutes;
- **Alerts** — one CoinGecko batch request for the whole pool (saves the free limit), 30-minute interval;
- **Indexes** — Finnhub does not return `^GSPC`/`^DJI`/`^VIX`, so ETF proxies are used (SPX→SPY, DJI→DIA, VIX→VIXY);
- **Russian market** — official free MOEX ISS API (no key): quotes, candles, company names in RUB; news via Google News RSS; world stock history via Yahoo Finance (Finnhub candles are paid);
- **i18n** — every user-facing string goes through `t()` with a per-user language (`users.language`).

---

## 🧪 Tests

```bash
pytest tests/ -v            # 262 tests (no network)
black src/ tests/           # formatting
isort --profile black src/ tests/
ruff check src/ tests/      # lint
```

---

## 🤖 CI (GitHub Actions)

Every push runs the full check suite automatically: tests (`pytest`), lint (`ruff`), formatting (`black`) and import order (`isort`). The workflow lives in `.github/workflows/ci.yml`; the badge above shows the latest result.

---

## 🛡️ Security

- Secrets live only in `.env`, the file is in `.gitignore`;
- Outgoing domains are white-listed (telegram, cbr, finnhub, coingecko, openrouter);
- Prompt injections are sanitized, suspicious patterns are blocked;
- RBAC: `user` / `admin` roles, admin commands are owner-only;
- Critical actions require confirmation (deletion, broadcast);
- Logging without PII: names and contacts are masked (`[REDACTED]`).

---

## 📚 Tech stack

Python 3.14 · aiogram 3 · SQLAlchemy 2 (async) · PostgreSQL · aiohttp · pydantic-settings · matplotlib · pytest

---

*Built for learning and demonstration. Do not invest based on information from this bot.*
