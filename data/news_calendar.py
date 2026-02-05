import requests
import datetime
import streamlit as st


def _get_key():
    try:
        return st.secrets.get("TRADINGECONOMICS_KEY")
    except Exception:
        return None


def get_high_impact_news():
    """
    Returns list of high-impact news events happening within ±30 minutes.
    Used to block Telegram signals during dangerous volatility.
    """

    key = _get_key()
    if not key:
        return []

    try:
        now = datetime.datetime.utcnow()
        today = now.strftime("%Y-%m-%d")

        url = f"https://api.tradingeconomics.com/calendar?c={key}&f=json&d1={today}&d2={today}"

        r = requests.get(url, timeout=10)
        data = r.json()

        risky_events = []

        for event in data:
            impact = event.get("Importance", 0)
            if impact < 2:  # only high impact
                continue

            date_str = event.get("Date")
            if not date_str:
                continue

            event_time = datetime.datetime.fromisoformat(date_str.replace("Z", ""))

            diff = abs((event_time - now).total_seconds())

            # within 30 minutes
            if diff <= 1800:
                risky_events.append({
                    "title": event.get("Event"),
                    "country": event.get("Country"),
                    "time": event_time
                })

        return risky_events

    except Exception:
        return []
