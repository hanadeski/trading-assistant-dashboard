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
