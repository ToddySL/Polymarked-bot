import requests
import sqlite3
from datetime import datetime

DB = "trades.db"

# Lag database
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


def get_top_traders():
    url = "https://data-api.polymarket.com/v1/leaderboard"

    traders = {}

    for period in ["DAY", "WEEK", "MONTH"]:
        params = {
            "category": "OVERALL",
            "timePeriod": period,
            "orderBy": "PNL",
            "limit": 10
        }

        response = requests.get(url, params=params)

        if response.status_code != 200:
            print("Feil ved leaderboard:", response.status_code)
            continue

        for trader in response.json():
            wallet = trader.get("proxyWallet")

            if wallet:
                traders[wallet] = trader

    return list(traders.values())


def get_trades(wallet):
    url = "https://data-api.polymarket.com/trades"

    params = {
        "user": wallet,
        "limit": 50
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        return []

    return response.json()


print("🤖 Polymarket tracker starter...")
print()

traders = get_top_traders()

print(f"Fant {len(traders)} unike topptradere.")
print()

new_trades = 0

for trader in traders:

    name = trader.get("userName") or "Ukjent"
    wallet = trader.get("proxyWallet")

    print(f"🔎 Sjekker {name}...")

    trades = get_trades(wallet)

    for trade in trades:

        timestamp = trade.get("timestamp", 0)

        trade_id = (
            f"{wallet}-"
            f"{timestamp}-"
            f"{trade.get('conditionId')}-"
            f"{trade.get('side')}-"
            f"{trade.get('price')}-"
            f"{trade.get('size')}"
        )

        cursor.execute(
            "SELECT trade_id FROM trades WHERE trade_id = ?",
            (trade_id,)
        )

        if cursor.fetchone():
            continue

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
            name,
            wallet,
            trade.get("side"),
            trade.get("outcome"),
            trade.get("price"),
            trade.get("size"),
            trade.get("title"),
            timestamp,
            datetime.now().isoformat()
        ))

        new_trades += 1

        print()
        print("🔥 NY TRADE!")
        print("Trader:", name)
        print("Handling:", trade.get("side"))
        print("Outcome:", trade.get("outcome"))
        print("Pris:", trade.get("price"))
        print("Størrelse:", trade.get("size"))
        print("Marked:", trade.get("title"))
        print()

conn.commit()
conn.close()

print("=" * 50)
print(f"✅ Ferdig! {new_trades} nye trades lagret.")
