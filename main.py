import requests
import sqlite3
import json
from datetime import datetime

DB = "trades.db"

LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
TRADES_URL = "https://data-api.polymarket.com/trades"
GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets"
CLOB_MIDPOINT_URL = "https://clob.polymarket.com/midpoint"
CLOB_PRICE_URL = "https://clob.polymarket.com/price"

PAPER_START_BALANCE = 400.0
PAPER_TRADE_SIZE = 40.0


# =========================================================
# DATABASE
# =========================================================

conn = sqlite3.connect(DB, timeout=30)
cursor = conn.cursor()

cursor.execute("PRAGMA busy_timeout = 30000")
cursor.execute("PRAGMA journal_mode = WAL")

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
    first_seen TEXT,
    condition_id TEXT
)
""")

try:
    cursor.execute("ALTER TABLE trades ADD COLUMN condition_id TEXT")
except sqlite3.OperationalError:
    pass

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
    status TEXT,
    condition_id TEXT
)
""")

try:
    cursor.execute(
        "ALTER TABLE paper_positions ADD COLUMN condition_id TEXT"
    )
except sqlite3.OperationalError:
    pass

conn.commit()


# =========================================================
# PAPER BUY
# =========================================================

def paper_buy(
    trader,
    outcome,
    price,
    title,
    timestamp,
    condition_id
):

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

    cursor.execute("""
        UPDATE paper_account
        SET balance = ?
        WHERE id = 1
    """, (new_balance,))

    cursor.execute("""
        INSERT INTO paper_positions (
            trader,
            outcome,
            price,
            shares,
            invested,
            title,
            timestamp,
            status,
            condition_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trader,
        outcome,
        price,
        shares,
        PAPER_TRADE_SIZE,
        title,
        timestamp,
        "OPEN",
        condition_id
    ))

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


# =========================================================
# REPAIR OLD POSITIONS
# =========================================================

def repair_position_from_trades(
    position_id,
    title,
    outcome
):

    print()
    print("🔧 Mangler condition_id.")
    print("🔎 Søker i registrerte trades...")

    # Samme marked + samme outcome
    cursor.execute("""
        SELECT condition_id, title, outcome
        FROM trades
        WHERE condition_id IS NOT NULL
          AND condition_id != ''
          AND title = ?
          AND LOWER(outcome) = LOWER(?)
        ORDER BY timestamp DESC
        LIMIT 1
    """, (title, outcome))

    match = cursor.fetchone()

    if match:

        condition_id = match[0]

        print("✅ Fant match i trades-databasen!")
        print("Condition ID:", condition_id)

        cursor.execute("""
            UPDATE paper_positions
            SET condition_id = ?
            WHERE id = ?
        """, (condition_id, position_id))

        conn.commit()

        return condition_id

    # Samme marked, uansett outcome
    cursor.execute("""
        SELECT condition_id, title, outcome
        FROM trades
        WHERE condition_id IS NOT NULL
          AND condition_id != ''
          AND title = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (title,))

    match = cursor.fetchone()

    if match:

        condition_id = match[0]

        print("✅ Fant markedet i trades-databasen!")
        print("Matchende outcome:", match[2])
        print("Condition ID:", condition_id)

        cursor.execute("""
            UPDATE paper_positions
            SET condition_id = ?
            WHERE id = ?
        """, (condition_id, position_id))

        conn.commit()

        return condition_id

    print(
        "⚠️ Fant ingen registrert trade med dette markedet."
    )

    return None


# =========================================================
# GET MARKET
# =========================================================

def get_market_by_condition(condition_id):

    if not condition_id:
        return None

    try:

        response = requests.get(
            GAMMA_MARKET_URL,
            params={
                "condition_ids": condition_id,
                "limit": 1
            },
            timeout=10
        )

        if response.status_code != 200:
            return None

        markets = response.json()

        if not markets:
            return None

        return markets[0]

    except Exception as e:

        print(
            "⚠️ Feil ved henting av marked:",
            e
        )

        return None


# =========================================================
# JSON ARRAY
# =========================================================

def parse_json_array(value):

    if isinstance(value, list):
        return value

    if isinstance(value, str):

        try:
            return json.loads(value)
        except Exception:
            return None

    return None


# =========================================================
# TOKEN ID
# =========================================================

def get_token_id(
    condition_id,
    outcome
):

    market = get_market_by_condition(
        condition_id
    )

    if not market:
        return None

    try:

        outcomes = parse_json_array(
            market.get("outcomes")
        )

        token_ids = parse_json_array(
            market.get("clobTokenIds")
        )

        if not outcomes or not token_ids:
            return None

        for i, market_outcome in enumerate(
            outcomes
        ):

            if (
                str(market_outcome)
                .strip()
                .lower()
                ==
                str(outcome)
                .strip()
                .lower()
            ):

                if i < len(token_ids):
                    return token_ids[i]

    except Exception as e:

        print(
            "⚠️ Kunne ikke finne token ID:",
            e
        )

    return None


# =========================================================
# SETTLEMENT PRICE
# =========================================================

def get_settlement_price(
    condition_id,
    outcome
):

    market = get_market_by_condition(
        condition_id
    )

    if not market:
        return None

    closed = market.get("closed")

    if not closed:
        return None

    try:

        outcomes = parse_json_array(
            market.get("outcomes")
        )

        prices = parse_json_array(
            market.get("outcomePrices")
        )

        if not outcomes or not prices:
            return None

        for i, market_outcome in enumerate(
            outcomes
        ):

            if (
                str(market_outcome)
                .strip()
                .lower()
                ==
                str(outcome)
                .strip()
                .lower()
            ):

                if i < len(prices):

                    price = float(prices[i])

                    print(
                        "🏁 Bruker sluttpris fra markedet:",
                        price
                    )

                    return price

    except Exception as e:

        print(
            "⚠️ Kunne ikke hente sluttpris:",
            e
        )

    return None


# =========================================================
# CURRENT PRICE
# =========================================================

def get_current_price(
    condition_id,
    outcome,
    position_id=None,
    title=None
):

    if not condition_id:

        if position_id and title:

            condition_id = (
                repair_position_from_trades(
                    position_id,
                    title,
                    outcome
                )
            )

        if not condition_id:
            return None

    # Prøv sluttpris først
    settlement_price = (
        get_settlement_price(
            condition_id,
            outcome
        )
    )

    if settlement_price is not None:
        return settlement_price

    # Finn token
    token_id = get_token_id(
        condition_id,
        outcome
    )

    if not token_id:
        return None

    # Midpoint
    try:

        response = requests.get(
            CLOB_MIDPOINT_URL,
            params={
                "token_id": token_id
            },
            timeout=10
        )

        if response.status_code == 200:

            data = response.json()

            midpoint = data.get("mid")

            if midpoint is not None:

                return float(midpoint)

    except Exception as e:

        print(
            "⚠️ Midpoint-feil:",
            e
        )

    # BUY price fallback
    try:

        response = requests.get(
            CLOB_PRICE_URL,
            params={
                "token_id": token_id,
                "side": "BUY"
            },
            timeout=10
        )

        if response.status_code == 200:

            data = response.json()

            price = data.get("price")

            if price is not None:

                return float(price)

    except Exception as e:

        print(
            "⚠️ Pris-feil:",
            e
        )

    return None


# =========================================================
# PAPER ACCOUNT
# =========================================================

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
            title,
            condition_id
        FROM paper_positions
        WHERE status = 'OPEN'
    """)

    positions = cursor.fetchall()

    total_invested = 0
    total_value = 0

    priced_invested = 0
    priced_value = 0

    missing_prices = 0

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
            title,
            condition_id
        ) = position

        current_price = get_current_price(
            condition_id,
            outcome,
            position_id,
            title
        )

        total_invested += invested

        print()
        print("📊 POSISJON")
        print("Marked:", title)
        print("Outcome:", outcome)
        print("Trader:", trader)

        print(
            "Kjøpspris:",
            f"{entry_price:.4f}"
        )

        if current_price is not None:

            current_value = (
                shares * current_price
            )

            profit = (
                current_value - invested
            )

            profit_percent = (
                profit / invested
            ) * 100

            total_value += current_value

            # Foreløpig beregning
            priced_invested += invested
            priced_value += current_value

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

            missing_prices += 1

            print(
                "⚠️ Nåværende pris ikke tilgjengelig."
            )

    print()
    print("-" * 60)

    print(
        "Totalt investert:",
        f"{total_invested:.2f} kr"
    )

    # -----------------------------------------------------
    # VANLIG TOTALT RESULTAT
    # -----------------------------------------------------

    if missing_prices == 0:

        print(
            "Markedsverdi:",
            f"{total_value:.2f} kr"
        )

        total_profit = (
            total_value - total_invested
        )

        total_profit_percent = (
            total_profit / total_invested
        ) * 100

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

    else:

        # Behold advarslene
        print(
            f"⚠️ {missing_prices} "
            "posisjon(er) mangler pris."
        )

        print(
            "⚠️ Totalt resultat vises ikke "
            "før alle priser er tilgjengelige."
        )

    # -----------------------------------------------------
    # FORELØPIG RESULTAT
    # -----------------------------------------------------

    if priced_invested > 0:

        preliminary_profit = (
            priced_value - priced_invested
        )

        preliminary_percent = (
            preliminary_profit / priced_invested
        ) * 100

        print()

        if preliminary_profit >= 0:

            print(
                "📈 Foreløpig resultat "
                "(kun prisede posisjoner):",
                f"+{preliminary_profit:.2f} kr "
                f"(+{preliminary_percent:.2f}%)"
            )

        else:

            print(
                "📉 Foreløpig resultat "
                "(kun prisede posisjoner):",
                f"{preliminary_profit:.2f} kr "
                f"({preliminary_percent:.2f}%)"
            )

        print(
            "Prisede posisjoner:",
            f"{priced_invested:.2f} kr "
            f"av {total_invested:.2f} kr"
        )

    print("=" * 60)


# =========================================================
# LEADERBOARD
# =========================================================

def get_top_traders():

    traders = {}

    for period in [
        "DAY",
        "WEEK",
        "MONTH",
        "ALL"
    ]:

        params = {
            "category": "OVERALL",
            "timePeriod": period,
            "orderBy": "PNL",
            "limit": 50
        }

        try:

            response = requests.get(
                LEADERBOARD_URL,
                params=params,
                timeout=10
            )

        except Exception as e:

            print(
                "⚠️ Leaderboard-feil:",
                e
            )

            continue

        if response.status_code != 200:

            print(
                "Feil ved",
                period
            )

            continue

        for trader in response.json():

            wallet = trader.get(
                "proxyWallet"
            )

            if not wallet:
                continue

            if wallet not in traders:

                traders[wallet] = {
                    "name":
                        trader.get(
                            "userName"
                        )
                        or "Ukjent",

                    "day": 0,
                    "week": 0,
                    "month": 0,
                    "all": 0
                }

            traders[wallet][
                period.lower()
            ] = (
                trader.get("pnl", 0)
                or 0
            )

    return traders


# =========================================================
# SCORE
# =========================================================

def calculate_score(trader):

    day = trader["day"]
    week = trader["week"]
    month = trader["month"]
    all_time = trader["all"]

    return (
        day * 0.40
        + week * 0.30
        + month * 0.20
        + all_time * 0.10
    )


# =========================================================
# TRADES
# =========================================================

def get_trades(wallet):

    params = {
        "user": wallet,
        "limit": 50
    }

    try:

        response = requests.get(
            TRADES_URL,
            params=params,
            timeout=10
        )

    except Exception:

        return []

    if response.status_code != 200:
        return []

    return response.json()


def trade_exists(trade_id):

    cursor.execute(
        """
        SELECT trade_id
        FROM trades
        WHERE trade_id = ?
        """,
        (trade_id,)
    )

    return cursor.fetchone() is not None


def create_trade_id(wallet, trade):

    return (
        f"{wallet}-"
        f"{trade.get('timestamp')}-"
        f"{trade.get('conditionId')}-"
        f"{trade.get('side')}-"
        f"{trade.get('price')}-"
        f"{trade.get('size')}"
    )


# =========================================================
# MAIN
# =========================================================

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

for i, trader in enumerate(
    scored_traders[:20],
    1
):

    print(
        f"{i}. {trader['name']} | "
        f"Score: "
        f"${trader['score']:,.0f}"
    )


# =========================================================
# NEW TRADES
# =========================================================

print()
print("=" * 60)
print("🔎 SJEKKER NYE TRADES")
print("=" * 60)

new_trades = []

for trader in scored_traders[:20]:

    if trader["score"] <= 0:
        continue

    trades = get_trades(
        trader["wallet"]
    )

    for trade in trades:

        trade_id = create_trade_id(
            trader["wallet"],
            trade
        )

        if trade_exists(trade_id):
            continue

        condition_id = trade.get(
            "conditionId"
        )

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
                first_seen,
                condition_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            datetime.now().isoformat(),
            condition_id
        ))

        new_trades.append({
            "trader":
                trader["name"],

            "score":
                trader["score"],

            "side":
                trade.get("side"),

            "outcome":
                trade.get("outcome"),

            "price":
                trade.get("price"),

            "size":
                trade.get("size"),

            "title":
                trade.get("title"),

            "timestamp":
                trade.get("timestamp"),

            "condition_id":
                condition_id
        })

conn.commit()


# =========================================================
# SIGNALS
# =========================================================

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

if not signals:

    print()
    print(
        "Ingen nye sterke BUY-signaler."
    )

else:

    for signal in signals[:20]:

        timestamp = signal["timestamp"]

        if timestamp:

            time = datetime.fromtimestamp(
                timestamp
            ).strftime(
                "%Y-%m-%d %H:%M"
            )

        else:

            time = "Ukjent"

        print()
        print("🔥 NYTT SIGNAL")
        print("-" * 50)

        print(
            "Trader:",
            signal["trader"]
        )

        print(
            "Score:",
            f"${signal['score']:,.0f}"
        )

        print(
            "Handling:",
            signal["side"]
        )

        print(
            "Outcome:",
            signal["outcome"]
        )

        print(
            "Pris:",
            signal["price"]
        )

        print(
            "Posisjonsverdi:",
            f"${signal['value']:,.2f}"
        )

        print(
            "Marked:",
            signal["title"]
        )

        print(
            "Tid:",
            time
        )

        paper_buy(
            trader=signal["trader"],
            outcome=signal["outcome"],
            price=signal["price"],
            title=signal["title"],
            timestamp=signal["timestamp"],
            condition_id=signal["condition_id"]
        )


# =========================================================
# SUMMARY
# =========================================================

print()
print("=" * 60)

print(
    f"📥 {len(new_trades)} "
    "nye trades registrert."
)

print(
    f"🚨 {len(signals)} "
    "nye BUY-signaler."
)

print("=" * 60)

show_paper_account()

conn.commit()
conn.close()
