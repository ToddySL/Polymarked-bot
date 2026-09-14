import requests

url = "https://gamma-api.polymarket.com/markets"

response = requests.get(url)

print("Status:", response.status_code)

if response.status_code == 200:
    markets = response.json()

    print(f"Fant {len(markets)} markeder.")
    
    for market in markets[:10]:
        print(market.get("question"))
else:
    print("Noe gikk galt:")
    print(response.text)
