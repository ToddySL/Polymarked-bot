import requests

url = "https://data-api.polymarket.com/trades"

response = requests.get(url)

print("Status:", response.status_code)

if response.status_code == 200:
    trades = response.json()

    print(f"Fant {len(trades)} trades.")

    for trade in trades[:10]:
        print(trade)
else:
    print("Noe gikk galt:")
    print(response.text)
