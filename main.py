import requests

url = "https://data-api.polymarket.com/v1/leaderboard"

periods = ["DAY", "WEEK", "MONTH", "ALL"]

for period in periods:

    params = {
        "category": "OVERALL",
        "timePeriod": period,
        "orderBy": "PNL",
        "limit": 10
    }

    response = requests.get(url, params=params)

    print("\n" + "=" * 70)
    print(f"TOPP 10 - {period}")
    print("=" * 70)

    if response.status_code != 200:
        print("Feil:", response.text)
        continue

    traders = response.json()

    for trader in traders:
        print(
            f"{trader.get('rank')}. "
            f"{trader.get('userName') or 'Ukjent'}"
            f" | PNL: ${trader.get('pnl'):,.2f}"
            f" | Vol: ${trader.get('vol'):,.2f}"
        )
