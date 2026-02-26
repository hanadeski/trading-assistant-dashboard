# Trading Assistant Dashboard (Streamlit)

## Quick start
1) Install deps:
   pip install -r requirements.txt

2) Set Streamlit secrets (see **cTrader SDK mode (no bridge URL)** below).

3) Run:
   streamlit run app.py

## Telegram alerts (high-confidence only)
Create a Telegram bot with @BotFather and get:
- BOT_TOKEN
- CHAT_ID (your user or group chat id)

Set environment variables:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

Then restart Streamlit.

## cTrader SDK mode (no bridge URL)
The dashboard now reads candles through `CTraderAdapter` directly in-process.
You do **not** need `CTRADER_LIVE_DATA_URL` for this mode.

Set these Streamlit secrets/environment vars:
- `CTRADER_CLIENT_ID` (required)
- `CTRADER_CLIENT_SECRET` (required)
- `CTRADER_ACCOUNT_ID` (required)

Optional auth vars (only if your provider requires them):
- `CTRADER_ACCESS_TOKEN`
- `CTRADER_TOKEN_URL`
- `CTRADER_API_KEY`

### Streamlit Cloud secrets layout (copy/paste)
```toml
CTRADER_CLIENT_ID = "your_client_id"
CTRADER_CLIENT_SECRET = "your_client_secret"
CTRADER_ACCOUNT_ID = "your_account_id"

# Optional:
# CTRADER_ACCESS_TOKEN = "your_access_token"
# CTRADER_TOKEN_URL = "https://.../oauth/token"
# CTRADER_API_KEY = "your_api_key"

TELEGRAM_BOT_TOKEN = "your_telegram_bot_token"
TELEGRAM_CHAT_ID = "your_telegram_chat_id"
