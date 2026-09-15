import requests

url = "https://data-api.polymarket.com/trades"

response = requests.get(url)

print("Status:", response.status_code)

if response.status_code == 200:
    trades = response.json()

    print(f"Fant {len(trades)} trades.\n")

    for trade in trades[:20]:
        print(
            "Trader:",
            trade.get("proxyWallet"),
            "| Side:",
            trade.get("side"),
            "| Pris:",
            trade.get("price"),
            "| Størrelse:",
            trade.get("size")
        )
else:
    print("Noe gikk galt:")
    print(response.text)
