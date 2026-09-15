import requests
from datetime import datetime

traders = [
    "00gringo00",
    "TheReturnOfDarthMaul",
    "Kch-Temp",
    "ndb1"
]

leaderboard_url = "https://data-api.polymarket.com/v1/leaderboard"

for name in traders:

    params = {
        "userName": name,
        "category": "OVERALL",
        "timePeriod": "ALL",
        "orderBy": "PNL",
        "limit": 1
    }

    response = requests.get(leaderboard_url, params=params)

    if response.status_code != 200:
        print(f"Feil ved {name}")
        continue

    data = response.json()

    if not data:
        print(f"Fant ikke {name}")
        continue

    trader = data[0]
    wallet = trader.get("proxyWallet")

    print("\n" + "=" * 80)
    print(f"{name}")
    print("=" * 80)
    print(f"PNL: ${trader.get('pnl'):,.2f}")
    print(f"Volume: ${trader.get('vol'):,.2f}")
    print(f"Wallet: {wallet}")

    trade_url = "https://data-api.polymarket.com/trades"

    trade_params = {
        "user": wallet,
        "limit": 50
    }

    trade_response = requests.get(trade_url, params=trade_params)

    if trade_response.status_code != 200:
        print("Kunne ikke hente trades.")
        continue

    trades = trade_response.json()

    print(f"\nFant {len(trades)} trades:\n")

    for trade in trades:

        timestamp = trade.get("timestamp")

        if timestamp:
            time = datetime.fromtimestamp(timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            time = "Ukjent"

        price = trade.get("price")
        size = trade.get("size")

        try:
            value = float(price) * float(size)
        except:
            value = 0

        print(
            f"{time} | "
            f"{trade.get('side')} {trade.get('outcome')} | "
            f"Pris: {price} | "
            f"Size: {size} | "
            f"Verdi: ${value:,.2f}\n"
            f"  {trade.get('title')}"
        )
