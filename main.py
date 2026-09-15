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


cursor.execute("""
CREATE TABLE IF NOT EXISTS paper_account (
    id INTEGER PRIMARY KEY,
    balance REAL NOT NULL
)
""")


cursor.execute("""
INSERT OR IGNORE INTO paper_account (id, balance)
VALUES (1, ?)
""", (PAPER_START_BALANCE,))


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
# PAPER BUY
# --------------------------------------------------

def paper_buy(trader, outcome, price, title, timestamp):

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

    shares = PAPER_TRADE_SIZE / price

    new_balance = balance - PAPER_TRADE_SIZE

    cursor.execute(
        """
        UPDATE paper_account
        SET balance = ?
        WHERE id = 1
        """,
        (new_balance,)
    )

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
# HENT AKTUELL MARKEDSPRIS
# --------------------------------------------------

def get_current_price(title, outcome):

    try:

        response = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={
                "search": title,
                "limit": 10
            },
            timeout=10
        )

        if response.status_code != 200:
            return None

        markets = response.json()

        for market in markets:

            market_title = market.get("question")

            if market_title != title:
                continue

            tokens = market.get("tokens", [])

            for token in tokens:

                if token.get("outcome") == outcome:

                    price = token.get("price")

                    if price is not None:
                        return float(price)

    except Exception as e:

        print(
            "⚠️ Kunne ikke hente markedspris:",
            e
        )

    return None


# --------------------------------------------------
# PAPER-KONTO STATUS
# --------------------------------------------------

def show_paper_account():

    cursor.execute(
        "SELECT balance FROM paper_account WHERE id = 1"
    )

    paper_balance = cursor.fetchone()[0]

    cursor.execute("""
        SELECT
            id,
            trader,
            outcome,
            price,
            shares,
            invested,
            title
        FROM paper_positions
        WHERE status = 'OPEN'
    """)

    positions = cursor.fetchall()

    total_invested = 0
    total_value = 0

    print()
    print("💰 PAPER-KONTO")
    print("=" * 60)

    print(
        "Saldo:",
        f"{paper_balance:.2f} kr"
    )

    print(
        "Åpne posisjoner:",
        len(positions)
    )

    print("-" * 60)

    for position in positions:

        (
            position_id,
            trader,
            outcome,
            entry_price,
            shares,
            invested,
            title
        ) = position

        current_price = get_current_price(
            title,
            outcome
        )

        total_invested += invested

        if current_price is not None:

            current_value = shares * current_price

            profit = current_value - invested

            profit_percent = (
                profit / invested
            ) * 100

            total_value += current_value

            print()
            print("📊 POSISJON")
            print("Marked:", title)
            print("Outcome:", outcome)
            print("Trader:", trader)
            print("Kjøpspris:", f"{entry_price:.4f}")
            print(
                "Nåværende pris:",
                f"{current_price:.4f}"
            )
            print(
                "Investert:",
                f"{invested:.2f} kr"
            )
            print(
                "Verdi:",
                f"{current_value:.2f} kr"
            )

            if profit >= 0:
                print(
                    "Resultat:",
                    f"+{profit:.2f} kr "
                    f"(+{profit_percent:.2f}%)"
                )
            else:
                print(
                    "Resultat:",
                    f"{profit:.2f} kr "
                    f"({profit_percent:.2f}%)"
                )

        else:

            print()
            print("📊 POSISJON")
            print("Marked:", title)
            print("Outcome:", outcome)
            print(
                "⚠️ Nåværende pris ikke tilgjengelig."
            )

    print()
    print("-" * 60)

    print(
        "Totalt investert:",
        f"{total_invested:.2f} kr"
    )

    if total_value > 0:

        total_profit = (
            total_value - total_invested
        )

        total_profit_percent = (
            total_profit / total_invested
        ) * 100

        print(
            "Markedsverdi:",
            f"{total_value:.2f} kr"
        )

        if total_profit >= 0:

            print(
                "Totalt resultat:",
                f"+{total_profit:.2f} kr "
                f"(+{total_profit_percent:.2f}%)"
            )

        else:

            print(
                "Totalt resultat:",
                f"{total_profit:.2f} kr "
                f"({total_profit_percent:.2f}%)"
            )

    print("=" * 60)


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
# TRADE ALLEREDE SETT?
# --------------------------------------------------

def trade_exists(trade_id):

    cursor.execute(
        "SELECT trade_id FROM trades WHERE trade_id = ?",
        (trade_id,)
    )

    return cursor.fetchone() is not None


# --------------------------------------------------
# TRADE-ID
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
# PAPER STATUS
# --------------------------------------------------

show_paper_account()


# --------------------------------------------------
# FERDIG
# --------------------------------------------------

conn.close()
