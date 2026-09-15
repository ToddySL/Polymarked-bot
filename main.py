import requests
import sqlite3
from datetime import datetime


DB = "trades.db"

LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
TRADES_URL = "https://data-api.polymarket.com/trades"


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

conn = sqlite3.connect(DB)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    trader TEXT,
    wallet TEXT,
    side TEXT,
    outcome TEXT,
    price REAL,
    size REAL,
    title TEXT,
    timestamp INTEGER,
    first_seen TEXT
)
""")

conn.commit()


# --------------------------------------------------
# TOPP TRADERE
# --------------------------------------------------

def get_top_traders():

    traders = {}

    for period in ["DAY", "WEEK", "MONTH", "ALL"]:

        params = {
            "category": "OVERALL",
            "timePeriod": period,
            "orderBy": "PNL",
            "limit": 50
        }

        response = requests.get(
            LEADERBOARD_URL,
            params=params
        )

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

            traders[wallet][period.lower()] = (
                trader.get("pnl", 0) or 0
            )

    return traders


# --------------------------------------------------
# TRADER SCORE
# --------------------------------------------------

def calculate_score(trader):

    day = trader["day"]
    week = trader["week"]
    month = trader["month"]
    all_time = trader["all"]

    score = (
        day * 0.40 +
        week * 0.30 +
        month * 0.20 +
        all_time * 0.10
    )

    return score


# --------------------------------------------------
# HENT TRADES
# --------------------------------------------------

def get_trades(wallet):

    params = {
        "user": wallet,
        "limit": 50
    }

    response = requests.get(
        TRADES_URL,
        params=params
    )

    if response.status_code != 200:
        return []

    return response.json()


# --------------------------------------------------
# SJEKK OM TRADE ALLEREDE ER SETT
# --------------------------------------------------

def trade_exists(trade_id):

    cursor.execute(
        "SELECT trade_id FROM trades WHERE trade_id = ?",
        (trade_id,)
    )

    return cursor.fetchone() is not None


# --------------------------------------------------
# LAG TRADE-ID
# --------------------------------------------------

def create_trade_id(wallet, trade):

    return (
        f"{wallet}-"
        f"{trade.get('timestamp')}-"
        f"{trade.get('conditionId')}-"
        f"{trade.get('side')}-"
        f"{trade.get('price')}-"
        f"{trade.get('size')}"
    )


# --------------------------------------------------
# START
# --------------------------------------------------

print("🤖 SMART SIGNAL-BOT")
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


# --------------------------------------------------
# NYE TRADES
# --------------------------------------------------

print()
print("=" * 60)
print("🔎 SJEKKER NYE TRADES")
print("=" * 60)

new_trades = []

for trader in scored_traders[:20]:

    if trader["score"] <= 0:
        continue

    trades = get_trades(trader["wallet"])

    for trade in trades:

        trade_id = create_trade_id(
            trader["wallet"],
            trade
        )

        # Allerede sett?
        if trade_exists(trade_id):
            continue

        # Lagre i database
        cursor.execute("""
        INSERT INTO trades (
            trade_id,
            trader,
            wallet,
            side,
            outcome,
            price,
            size,
            title,
            timestamp,
            first_seen
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (

            trade_id,
            trader["name"],
            trader["wallet"],
            trade.get("side"),
            trade.get("outcome"),
            trade.get("price"),
            trade.get("size"),
            trade.get("title"),
            trade.get("timestamp"),
            datetime.now().isoformat()

        ))

        new_trades.append({
            "trader": trader["name"],
            "score": trader["score"],
            "side": trade.get("side"),
            "outcome": trade.get("outcome"),
            "price": trade.get("price"),
            "size": trade.get("size"),
            "title": trade.get("title"),
            "timestamp": trade.get("timestamp")
        })


conn.commit()


# --------------------------------------------------
# SIGNALER
# --------------------------------------------------

print()
print("=" * 60)
print("🚨 NYE BUY-SIGNALER")
print("=" * 60)

signals = []

for trade in new_trades:

    if trade["side"] != "BUY":
        continue

    price = trade["price"] or 0
    size = trade["size"] or 0

    value = price * size

    # Minimum størrelse
    if value < 1000:
        continue

    signals.append({
        **trade,
        "value": value
    })


signals.sort(
    key=lambda x: x["value"],
    reverse=True
)


if not signals:

    print()
    print("Ingen nye sterke BUY-signaler.")


else:

    for signal in signals[:20]:

        timestamp = signal["timestamp"]

        if timestamp:

            time = datetime.fromtimestamp(
                timestamp
            ).strftime("%Y-%m-%d %H:%M")

        else:

            time = "Ukjent"


        print()
        print("🔥 NYTT SIGNAL")
        print("-" * 50)

        print("Trader:", signal["trader"])
        print(
            "Score:",
            f"${signal['score']:,.0f}"
        )

        print("Handling:", signal["side"])
        print("Outcome:", signal["outcome"])
        print("Pris:", signal["price"])

        print(
            "Posisjonsverdi:",
            f"${signal['value']:,.2f}"
        )

        print("Marked:", signal["title"])
        print("Tid:", time)


print()
print("=" * 60)

print(
    f"📥 {len(new_trades)} nye trades registrert."
)

print(
    f"🚨 {len(signals)} nye BUY-signaler."
)

print("=" * 60)


conn.close()
