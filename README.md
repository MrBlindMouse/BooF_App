# BooF App

Crypto trading bot platform for the [VALR](https://valr.com) exchange. Users can create and configure automated trading bots linked to their VALR API keys, view market trends and bot performance, and manage credits via one-off purchases or PayPal subscriptions.

## Features

- **User accounts**: Signup, login (with Cloudflare Turnstile), email verification, password reset, profile and config.
- **Bot management**: Create bots with VALR API key/secret, configure currency (ZAR/USDC/USDT), margin, refined weight, dynamic margin, downturn protection; view stats, reports, and clear/delete bots.
- **Trading logic**: VALR integration for balances, market/limit orders, staking; rebalancing and position sizing; optional downturn protection (pause and liquidate when market trend is low).
- **Credits**: Credit system (4-week blocks); purchase via PayPal (one-off or subscription); reminders and low-credit emails.
- **Market view**: Technical view (trend/RSI) per quote currency.
- **Optional**: Daily market tweets (Twitter/X via Groq + Tweepy).

---

![A new verified account, no bots loaded yet](docs/Screenshot_New%20Empty.png)

---

## Prerequisites

- **Python** 3.10+ (or version compatible with dependencies).
- **VALR account** and API keys (View + Trade) for the bot loop; separate keys for the ticker stream (see below).
- **Postmark** server token for transactional email (verification, password reset, notifications).
- **PayPal** app (sandbox or live): client ID/secret, products/plans in `product_list.json`, webhook URL for `/hook`.
- **Cloudflare Turnstile** site + secret keys for login/signup/reset forms.
- **Optional**: Twitter/X app (API keys + bearer token) and **Groq** API key for daily market tweets.

---

## Setup

1. **Clone and virtual environment**

   ```bash
   git clone <repo-url>
   cd BooF_App
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Environment variables**

   Copy `.env.sample` to `.env` and set values. Required keys:

   | Key | Used by | Description |
   |-----|---------|-------------|
   | `APP_SECRET` | app, valr | Flask secret key and app signing. |
   | `VERIFY_SALT` | app, valr | Salt for verification/reset tokens. |
   | `TURNSTILE_KEY` | app | Cloudflare Turnstile site key (frontend). |
   | `TURNSTILE_SECRET` | app | Cloudflare Turnstile secret (backend verify). |
   | `POSTMARK_KEY` | app, valr | Postmark server API token. |
   | `PAYPAL_ID` | app, paypal, valr | PayPal client ID. |
   | `PAYPAL_SECRET` | app, paypal, valr | PayPal client secret. |
   | `PAYPAL_MODE` | app, paypal | `LIVE` or `SANDBOX` (sandbox). |
   | `FORBIDDEN` | valr | Python list string of forbidden ticker substrings, e.g. `'["USD","EUR","PERP"]'`. |
   | `STAKE` | valr | Python list of stakeable symbols, e.g. `'["AVAX","SOL","TRX"]'`. |

   Optional / per-component:

   | Key | Used by | Description |
   |-----|---------|-------------|
   | `VALR_KEY` | tickers | VALR API key for WebSocket (ticker stream). |
   | `VALR_SECRET` | tickers | VALR API secret for WebSocket. |
   | `GROQ_API_KEY` | twitter | Groq API key for tweet generation. |
   | `X_KEY`, `X_SECRET`, `X_TOKEN`, `X_TOKEN_SECRET`, `X_BEARER_TOKEN` | twitter | Twitter/X API credentials. |

   Ensure `.env` is not committed (it is in `.gitignore`).

3. **Database and data directory**

   The app uses SQLite at `data/database.db`. The `data/` directory is created on first run when the DB is set up (e.g. via `wsgi.py`). Alternatively create it manually: `mkdir -p data`.

4. **PayPal and ticker data**

   - **PayPal**: Ensure `product_list.json` exists (products, plans, and optionally webhook id). Running `wsgi.py` once calls `setupPaypal('https://www.boof-bots.com/hook')` to create/update products and the webhook; adjust URL for your environment.
   - **Tickers**: The VALR bot loop reads `prices.json` (and optionally `history.json`). These are produced by the **ticker stream** (`python tickers.py`). For the bot loop to have up-to-date prices, run the ticker stream first or ensure these files exist from a previous run.

---

## Running

Three processes are involved:

1. **Ticker stream** (writes `prices.json`, `history.json`)

   ```bash
   python tickers.py
   ```

   Requires `VALR_KEY` and `VALR_SECRET` in `.env`. Run this first so the bot loop has price data (or run it alongside the others).

2. **Web application**

   ```bash
   python wsgi.py
   ```

   This runs `setupDB()`, `setupPaypal(...)`, then starts the Flask app (default port 5005). For production, use gunicorn (or similar) pointing at the Flask app in `app.py`.

3. **Bot loop** (VALR trading, credits, reminders)

   ```bash
   python valr.py
   ```

   Loads config from `.env` and pickle state, runs the scheduled bot loop (e.g. every 30s) and admin loop (e.g. hourly). Depends on `prices.json` (and `history.json` for indicators); start the ticker stream first for a full setup.

**Suggested order**: Start ticker stream and web app; then start the bot loop once prices are being written.

---

## Project layout

| Path | Description |
|------|-------------|
| `app.py` | Flask app: routes for auth, profile, bots, market, PayPal checkout/subscription, webhook, error handler. |
| `db.py` | SQLite schema and ORM-like classes (User, Bot, ActiveAccount, Transaction, Credit, Message, Token, Subscription); `setupDB()` creates/updates tables. |
| `valr.py` | VALR config (pickle state), API auth, trading, balances, staking, downturn protection; scheduled bot and admin loops; remote logging. |
| `tickers.py` | WebSocket client for VALR; maintains live tickers, writes `prices.json` and hourly `history.json`. |
| `postmark.py` | Postmark email sending. |
| `paypal.py` | PayPal OAuth, orders, subscriptions, webhooks, products/plans. |
| `twitter.py` | Groq + Tweepy for generating and posting daily market tweets. |
| `wsgi.py` | Entrypoint: setup DB and PayPal, then run Flask app. |
| `templates/` | Jinja2 HTML templates (login, signup, home, bot config, market, report, etc.). |
| `static/` | CSS, fonts, robots.txt, sitemap.xml. |
| `data/` | Created at runtime; holds `database.db`. |
| `config_state.pkl` | Pickle file used by valr for config state (not in git). |
| `prices.json`, `history.json`, `history_metadata.json` | Written by tickers; used by valr (not in git). |

---

## Known issues and bugs

Fixes below will make the app more reliable and secure. References are file:line or area.

| Location | Issue |
|----------|--------|
| `app.py:1117` | Route is `@app.route('/deletebot/id')` (literal `"id"`). Should be `'/deletebot/<id>'` so the delete-bot link works. |
| `app.py:1134` | `entry.delet()` typo; should be `entry.delete()`. |
| `app.py:1247–1266` | Webhook handlers use `sub.status` / `sub.update()` but the variable is `subscription`; causes NameError. |
| `app.py:1249`, `1266`, `1283` | `db.Message(0, userID, 'INFO', ...)` — Message expects a list: `[0, user_id, type, message]`. |
| `app.py:1299` | `paypal.captureSubsription(data)` called with only `data`; missing `userID`. Method name typo: Subsription. |
| `app.py:763` | `session["error"] = f"Error:'{e}"` — missing closing quote. |
| `app.py:84–88` | `userLogin` compares `user.password == password` — passwords stored and compared in plaintext (critical security issue). |
| `app.py:458–459` | Reset flow emails new password in plaintext; should force change on next login instead. |
| `db.py:323` | `Bot.delete()` uses `if account:`; should be `if accounts:` (iterating `accounts`). |
| `valr.py:520` | `bot.equity += trade["value"]` — undefined `trade`; should be `result["value"]`. |
| `valr.py:2009` | `reminders.remove[reminder]` — should be `reminders.remove(reminder)`. |
| `paypal.py:570` | `header["PAYPAL-CERT-UR"]` — PayPal header is `PAYPAL-CERT-URL`. |
| `paypal.py:273`, `301`, `322` | URLs use double slash `f"{baseURL}//v1/..."`; should be single `/`. |

---

## Improvements to reach professional standards

The following changes would bring the app up to professional standards (architecture, safety, maintainability, operations).

### Architecture

- **Layered structure**: Move business logic out of `app.py` into service modules (e.g. `services/auth.py`, `services/bot.py`, `services/credits.py`). Keep routes thin (routing, request/response, session only).
- **Configuration**: Single config module loading env vars with defaults and validation at startup; avoid ad-hoc `dotenv_values` and pickle state where possible.
- **Dependency injection**: Pass config, DB, and external clients (VALR, Postmark, PayPal) into services instead of global imports for testability and clarity.
- **Separation of concerns**: Extract token encode/decode, Turnstile verification, and email body building into dedicated modules. Move PayPal webhook handling from `app.py` to e.g. `webhooks/paypal.py`.
- **State**: Prefer DB or Redis for bot/run state instead of pickle files to avoid filesystem coupling and multi-process issues.

### Error handling and logging

- **Structured logging**: Replace `print()` and `printLog()` with Python `logging` (one config, levels, optional JSON in production). Remove `print()` of sensitive payloads in order/capture/hook routes.
- **Centralized errors**: In `@app.errorhandler`, log full tracebacks and request context (user id, path) before returning user-facing messages; never expose internal exceptions in production.
- **External calls**: Wrap VALR, Postmark, PayPal, and Turnstile in try/except; log and return clear messages; avoid silent failures (e.g. postmark currently only prints on non-200).
- **Remote logging**: Make remote log posting (bmd-studios.com) optional and failure-safe so the app works when the logging server is down.

### Security

- **Passwords**: Store only hashed passwords (e.g. bcrypt or argon2) and compare with a safe check; remove plaintext storage and comparison. Reset flow: use a temporary token and force password change on first login instead of emailing a new password.
- **Secrets and env**: Keep `.env` out of version control; use `SESSION_COOKIE_SECURE=True` and HTTPS in production; document in README.
- **Input/output**: Validate and sanitize webhook and form input; avoid logging or displaying raw bodies that may contain PII or secrets.
- **CSRF**: Ensure Flask-WTF CSRF is enabled on all state-changing routes and API endpoints.

### Self-documentation and maintainability

- **AGENTS.md**: Add a high-level architecture doc (main flows, where to find things, conventions).
- **Docstrings and types**: Add module and public-function docstrings and type hints in `db.py`, `valr.py`, `paypal.py`, and new service modules.
- **Constants**: Replace magic numbers (e.g. lockout time, credit period) and string literals (e.g. token types) with named constants or enums in one place.
- **Copy**: Fix typo “Verivication” in email subject in `app.py`.

### Testing

- **Test suite**: Add `tests/` with pytest (or unittest): unit tests for token encode/decode; DB layer tests (setupDB, CRUD with test DB path); service-layer tests with mocked VALR/PayPal/Postmark.
- **CI**: Minimal CI (e.g. GitHub Actions) to run tests and lint (e.g. ruff, mypy with gradual typing).

### Operations and deployment

- **Requirements**: Pin versions in `requirements.txt` (e.g. `flask>=2.3,<3`) for reproducible builds.
- **Health**: Add a `/health` endpoint (DB and optionally external connectivity) for load balancers and monitoring.
- **Migrations**: Replace in-code “add/drop column” schema evolution with explicit migrations (SQL or a small runner) and document backup before migrations.

### Other

- **Duplicate logic**: Extract Turnstile verification into one function; use a decorator or `before_request` for “session.modified” and auth checks on protected routes.
- **PayPal webhook**: Fix `subscription` vs `sub`, `Message` constructor (list), `captureSubsription` name and signature, and `PAYPAL-CERT-URL` header; add idempotency for payment/subscription events where applicable.
- **DB**: Fix `Bot.delete()` `if account:` → `if accounts:`; document SQLite version requirement for `DROP COLUMN` if using schema evolution.
