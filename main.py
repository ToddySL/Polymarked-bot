import requests
import sqlite3
from datetime import datetime


# --------------------------------------------------
# INNSTILLINGER
# --------------------------------------------------

DB = "trades.db"

LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
TRADES_URL = "https://data-api.polymarket.com/trades"

PAPER_START_BALANCE = 400.0
PAPER_TRADE_SIZE = 40.0


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

conn = sqlite3.connect(DB)
cursor = conn.cursor()


# Vanlige trades
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


# Paper-konto
cursor.execute("""
CREATE TABLE IF NOT EXISTS paper_account (
    id INTEGER PRIMARY KEY,
    balance REAL NOT NULL
)
""")


# Opprett konto med 400 kr første gang
cursor.execute("""
INSERT OR IGNORE INTO paper_account (id, balance)
VALUES (1, ?)
""", (PAPER_START_BALANCE,))


# Paper-posisjoner
cursor.execute("""
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trader TEXT,
    outcome TEXT,
    price REAL,
    shares REAL,
    invested REAL,
    title TEXT,
    timestamp INTEGER,
    status TEXT
)
""")


conn.commit()


# --------------------------------------------------
# PAPER TRADING
# --------------------------------------------------

def paper_buy(trader, outcome, price, title, timestamp):

    # Sikkerhet mot ugyldig pris
    if not price or price <= 0:
        print("⚠️ Ugyldig pris. Paper-kjøp hoppes over.")
        return False

    cursor.execute(
        "SELECT balance FROM paper_account WHERE id = 1"
    )

    row = cursor.fetchone()

    if not row:
        print("❌ Fant ikke paper-konto.")
        return False

    balance = row[0]

    if balance < PAPER_TRADE_SIZE:

        print(
            f"⚠️ Ikke nok paper-penger. "
            f"Saldo: {balance:.2f} kr"
        )

        return False

    # Hvor mange shares vi får for 40 kr
    shares = PAPER_TRADE_SIZE / price

    # Trekk 40 kr fra saldo
    new_balance = balance - PAPER_TRADE_SIZE

    cursor.execute(
        """
        UPDATE paper_account
        SET balance = ?
        WHERE id = 1
        """,
        (new_balance,)
    )

    # Lagre posisjonen
    cursor.execute(
        """
        INSERT INTO paper_positions (
            trader,
            outcome,
            price,
            shares,
            invested,
            title,
            timestamp,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trader,
            outcome,
            price,
            shares,
            PAPER_TRADE_SIZE,
            title,
            timestamp,
            "OPEN"
        )
    )

    conn.commit()

    print()
    print("💰 PAPER-KJØP")
    print("-" * 50)
    print("Trader:", trader)
    print("Outcome:", outcome)
    print("Pris:", price)
    print("Investert:", f"{PAPER_TRADE_SIZE:.2f} kr")
    print("Shares:", f"{shares:.2f}")
    print("Ny saldo:", f"{new_balance:.2f} kr")

    return True


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

        if trade_exists(trade_id):
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


# --------------------------------------------------
# BEHANDLE SIGNALER
# --------------------------------------------------

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


        # Paper-kjøp
        paper_buy(
            trader=signal["trader"],
            outcome=signal["outcome"],
            price=signal["price"],
            title=signal["title"],
            timestamp=signal["timestamp"]
        )


# --------------------------------------------------
# RESULTAT
# --------------------------------------------------

print()
print("=" * 60)

print(
    f"📥 {len(new_trades)} nye trades registrert."
)

print(
    f"🚨 {len(signals)} nye BUY-signaler."
)

print("=" * 60)


# --------------------------------------------------
# PAPER-KONTO
# --------------------------------------------------

cursor.execute(
    "SELECT balance FROM paper_account WHERE id = 1"
)

paper_balance = cursor.fetchone()[0]


cursor.execute("""
    SELECT SUM(invested)
    FROM paper_positions
    WHERE status = 'OPEN'
""")

paper_invested = cursor.fetchone()[0] or 0


cursor.execute("""
    SELECT COUNT(*)
    FROM paper_positions
    WHERE status = 'OPEN'
""")

paper_positions = cursor.fetchone()[0]


print()
print("💰 PAPER-KONTO")
print("-" * 50)

print(
    "Saldo:",
    f"{paper_balance:.2f} kr"
)

print(
    "Investert:",
    f"{paper_invested:.2f} kr"
)

print(
    "Åpne posisjoner:",
    paper_positions
)

print("=" * 60)


# --------------------------------------------------
# FERDIG
# --------------------------------------------------

conn.close()
