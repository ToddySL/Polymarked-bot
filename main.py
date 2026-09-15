import requests
from datetime import datetime

leaderboard_url = "https://data-api.polymarket.com/v1/leaderboard"

params = {
    "category": "OVERALL",
    "timePeriod": "ALL",
    "orderBy": "PNL",
    "limit": 5
}

response = requests.get(leaderboard_url, params=params)

print("Leaderboard status:", response.status_code)

if response.status_code != 200:
    print(response.text)
    exit()

traders = response.json()

for trader in traders:

    name = trader.get("userName") or "Ukjent"
    wallet = trader.get("proxyWallet")

    print("\n" + "=" * 70)
    print(name)
    print("=" * 70)

    trade_url = "https://data-api.polymarket.com/trades"

    trade_params = {
        "user": wallet,
        "limit": 20
    }

    trade_response = requests.get(trade_url, params=trade_params)

    if trade_response.status_code != 200:
        print("Kunne ikke hente trades.")
        continue

    trades = trade_response.json()

    for trade in trades:

        timestamp = trade.get("timestamp")

        if timestamp:
            time = datetime.fromtimestamp(timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            time = "Ukjent"

        print(
            f"\n{time}"
            f"\n  {trade.get('side')} "
            f"{trade.get('outcome')}"
            f"\n  Marked: {trade.get('title')}"
            f"\n  Pris: {trade.get('price')}"
            f"\n  Størrelse: {trade.get('size')}"
            f"\n  Market: {trade.get('conditionId')}"
        )
