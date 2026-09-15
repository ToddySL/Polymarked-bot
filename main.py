import requests
from datetime import datetime


LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
TRADES_URL = "https://data-api.polymarket.com/trades"


def get_top_traders():
    traders = {}

    for period in ["DAY", "WEEK", "MONTH", "ALL"]:

        params = {
            "category": "OVERALL",
            "timePeriod": period,
            "orderBy": "PNL",
            "limit": 50
        }

        response = requests.get(LEADERBOARD_URL, params=params)

        if response.status_code != 200:
            print("Feil ved", period)
            continue

        for trader in response.json():

            wallet = trader.get("proxyWallet")

            if not wallet:
                continue

            if wallet not in traders:
                traders[wallet] = {
                    "name": trader.get("userName") or "Ukjent",
                    "day": 0,
                    "week": 0,
                    "month": 0,
                    "all": 0
                }

            traders[wallet][period.lower()] = trader.get("pnl", 0) or 0

    return traders


def calculate_score(trader):

    day = trader["day"]
    week = trader["week"]
    month = trader["month"]
    all_time = trader["all"]

    # Ny og mer balansert score
    score = (
        day * 0.40 +
        week * 0.30 +
        month * 0.20 +
        all_time * 0.10
    )

    return score


def get_trades(wallet):

    params = {
        "user": wallet,
        "limit": 20
    }

    response = requests.get(TRADES_URL, params=params)

    if response.status_code != 200:
        return []

    return response.json()


print("🤖 SIGNAL-BOT STARTER")
print("=" * 60)

traders = get_top_traders()

scored_traders = []

for wallet, trader in traders.items():

    score = calculate_score(trader)

    scored_traders.append({
        "wallet": wallet,
        "name": trader["name"],
        "score": score
    })


# Sorter etter score
scored_traders.sort(
    key=lambda x: x["score"],
    reverse=True
)


print()
print("🏆 TOPP TRADERE")
print("=" * 60)

for i, trader in enumerate(scored_traders[:20], 1):

    print(
        f"{i}. {trader['name']} "
        f"| Score: ${trader['score']:,.0f}"
    )


print()
print("=" * 60)
print("🔎 LETER ETTER BUY-SIGNALER")
print("=" * 60)


signals = []

# Se på topp 20
for trader in scored_traders[:20]:

    # Minimum score
    if trader["score"] <= 0:
        continue

    trades = get_trades(trader["wallet"])

    for trade in trades:

        if trade.get("side") != "BUY":
            continue

        price = trade.get("price", 0) or 0
        size = trade.get("size", 0) or 0

        # Unngå ekstremt små handler
        position_value = price * size

        if position_value < 1000:
            continue

        signals.append({
            "trader": trader["name"],
            "score": trader["score"],
            "outcome": trade.get("outcome"),
            "price": price,
            "size": size,
            "value": position_value,
            "title": trade.get("title"),
            "timestamp": trade.get("timestamp")
        })


# Største signaler først
signals.sort(
    key=lambda x: x["value"],
    reverse=True
)


print()

if not signals:

    print("Ingen sterke signaler funnet.")

else:

    for signal in signals[:20]:

        timestamp = signal["timestamp"]

        if timestamp:
            time = datetime.fromtimestamp(timestamp).strftime(
                "%Y-%m-%d %H:%M"
            )
        else:
            time = "Ukjent"

        print()
        print("🔥 SIGNAL")
        print("-" * 50)
        print("Trader:", signal["trader"])
        print("Score:", f"${signal['score']:,.0f}")
        print("Handling: BUY")
        print("Outcome:", signal["outcome"])
        print("Pris:", signal["price"])
        print("Størrelse:", f"${signal['value']:,.2f}")
        print("Marked:", signal["title"])
        print("Tid:", time)


print()
print("=" * 60)
print(f"Fant {len(signals)} mulige signaler.")
print("=" * 60)
