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


### Optional: run a local cTrader bridge (`ctrader_client.py`)
If you want to connect directly with cTrader Open API SDK and still keep the dashboard on HTTP candles:

1. Create `ctrader_client.py` in the **repo root** (same folder level as `app.py`).
   - In this repo it already exists at: `trading-assistant-dashboard/ctrader_client.py`.

2. Implement real SDK calls inside:
   - `CTraderAdapter.connect()`
   - `CTraderAdapter.fetch_candles()`
   
   Your snippet:
   - `from ctrader_open_api import Client, EndPoints, Auth`
   belongs inside that file/methods (not in `app.py`).

3. Start the bridge as a **separate running process** (keep terminal open):
   ```bash
   pip install fastapi uvicorn ctrader-open-api
   python ctrader_client.py
