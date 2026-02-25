# Trading Assistant Dashboard (Streamlit)

## Quick start
1) Install deps:
   pip install -r requirements.txt

2) Run:
   streamlit run app.py

## Telegram alerts (high-confidence only)
Create a Telegram bot with @BotFather and get:
- BOT_TOKEN
- CHAT_ID (your user or group chat id)

Set environment variables:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

Then restart Streamlit.

## Notes
- This Step 1 build uses mock data (no live feed yet).
- Step 2 will wire in your real decision engine outputs + real charts.

## cTrader live data source
This app can use a cTrader-backed live data assistant endpoint as its only OHLC source.

Set these Streamlit secrets/environment vars:
- `CTRADER_LIVE_DATA_URL` (required): endpoint that returns candles
- `CTRADER_ACCESS_TOKEN` (optional bearer token)
- `CTRADER_API_KEY` (optional custom gateway header)
- `CTRADER_TOKEN_URL` + `CTRADER_CLIENT_ID` + `CTRADER_CLIENT_SECRET` (optional client-credentials flow)

Expected payload formats are documented in `data/live_data.py` inside `_fetch_ctrader_ohlc`.
