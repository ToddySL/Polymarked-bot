import requests


def get_leaderboard(period):
    url = "https://data-api.polymarket.com/v1/leaderboard"

    params = {
        "category": "OVERALL",
        "timePeriod": period,
        "orderBy": "PNL",
        "limit": 50
    }

    response = requests.get(url)

    if response.status_code != 200:
        print("Feil:", response.status_code)
        return []

    return response.json()


periods = ["DAY", "WEEK", "MONTH", "ALL"]

traders = {}

for period in periods:

    print(f"Henter {period}...")

    url = "https://data-api.polymarket.com/v1/leaderboard"

    params = {
        "category": "OVERALL",
        "timePeriod": period,
        "orderBy": "PNL",
        "limit": 50
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        print("Feil ved", period)
        continue

    leaderboard = response.json()

    for trader in leaderboard:

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

        pnl = trader.get("pnl", 0) or 0

        traders[wallet][period.lower()] = pnl


# Beregn score
results = []

for wallet, trader in traders.items():

    day = trader["day"]
    week = trader["week"]
    month = trader["month"]
    all_time = trader["all"]

    # Enkel første versjon av score
    score = (
        day * 0.35 +
        week * 0.30 +
        month * 0.20 +
        all_time * 0.15
    )

    results.append({
        "name": trader["name"],
        "score": score,
        "day": day,
        "week": week,
        "month": month,
        "all": all_time
    })


results.sort(key=lambda x: x["score"], reverse=True)


print()
print("=" * 70)
print("🏆 BESTE TRADERE ETTER SCORE")
print("=" * 70)

for i, trader in enumerate(results[:20], 1):

    print()
    print(f"{i}. {trader['name']}")
    print(f"   SCORE: ${trader['score']:,.2f}")
    print(f"   DAY:   ${trader['day']:,.2f}")
    print(f"   WEEK:  ${trader['week']:,.2f}")
    print(f"   MONTH: ${trader['month']:,.2f}")
    print(f"   ALL:   ${trader['all']:,.2f}")
