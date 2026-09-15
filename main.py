import requests

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

print("\n=== TOPP 5 TRADERE ===\n")

for trader in traders:
    name = trader.get("userName") or "Ukjent"
    wallet = trader.get("proxyWallet")
    pnl = trader.get("pnl", 0)
    volume = trader.get("vol", 0)

    print(f"{name}")
    print(f"PNL: ${pnl:,.2f}")
    print(f"Volum: ${volume:,.2f}")
    print(f"Wallet: {wallet}")

    # Hent de siste 20 tradene til traderen
    trades_url = "https://data-api.polymarket.com/trades"

    trade_params = {
        "user": wallet,
        "limit": 20
    }

    trade_response = requests.get(trades_url, params=trade_params)

    if trade_response.status_code == 200:
        trades = trade_response.json()

        print(f"Antall hentede trades: {len(trades)}")

        for trade in trades[:5]:
            print(
                f"  {trade.get('side')} "
                f"{trade.get('outcome')} "
                f"@ {trade.get('price')} "
                f"size={trade.get('size')}"
            )
    else:
        print("Kunne ikke hente trades.")

    print("-" * 50)
