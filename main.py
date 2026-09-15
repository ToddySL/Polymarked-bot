import requests

url = "https://data-api.polymarket.com/v1/leaderboard"

params = {
    "category": "OVERALL",
    "timePeriod": "ALL",
    "orderBy": "PNL",
    "limit": 20
}

response = requests.get(url, params=params)

print("Status:", response.status_code)

if response.status_code == 200:
    traders = response.json()

    print(f"\nFant {len(traders)} topptradere:\n")

    for trader in traders:
        print(
            f"#{trader.get('rank')} "
            f"{trader.get('userName') or 'Ukjent'} | "
            f"PNL: ${trader.get('pnl'):,.2f} | "
            f"Volum: ${trader.get('vol'):,.2f} | "
            f"Wallet: {trader.get('proxyWallet')}"
        )

else:
    print("Noe gikk galt:")
    print(response.text)
