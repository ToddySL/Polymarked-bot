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

# Maks hvor mye som kan være investert i én bestemt
# trader + marked + outcome.
MAX_POSITION_PER_EVENT = 40.0

MIN_TRADE_VALUE_USD = 1000.0


conn = sqlite3.connect(DB, timeout=30)
cursor = conn.cursor()

cursor.execute("PRAGMA busy_timeout = 30000")
cursor.execute("PRAGMA journal_mode = WAL")


# ============================================================
# DATABASE
# ============================================================

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
    condition_id TEXT,
    sell_price REAL,
    sell_value REAL,
    profit REAL,
    closed_timestamp INTEGER
)
""")

for column, definition in [
    ("condition_id", "TEXT"),
    ("sell_price", "REAL"),
    ("sell_value", "REAL"),
    ("profit", "REAL"),
    ("closed_timestamp", "INTEGER"),
]:
    try:
        cursor.execute(
            f"ALTER TABLE paper_positions ADD COLUMN {column} {definition}"
        )
    except sqlite3.OperationalError:
        pass

conn.commit()


# ============================================================
# PAPER BUY
# ============================================================

def paper_buy(trader, outcome, price, title, timestamp, condition_id):

    if not price or price <= 0:
        print("⚠️ Ugyldig pris. Paper-kjøp hoppes over.")
        return False

    # --------------------------------------------------------
    # SJEKK MAKS POSISJON PER HENDELSE
    # --------------------------------------------------------

    if condition_id:
        cursor.execute("""
            SELECT COALESCE(SUM(invested), 0)
            FROM paper_positions
            WHERE status = 'OPEN'
              AND trader = ?
              AND condition_id = ?
              AND LOWER(outcome) = LOWER(?)
        """, (trader, condition_id, outcome))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(invested), 0)
            FROM paper_positions
            WHERE status = 'OPEN'
              AND trader = ?
              AND LOWER(outcome) = LOWER(?)
              AND title = ?
        """, (trader, outcome, title))

    existing_investment = cursor.fetchone()[0] or 0.0

    remaining_limit = MAX_POSITION_PER_EVENT - existing_investment

    if remaining_limit <= 0:
        print()
        print("🛑 MAKS GRENSE NÅDD")
        print("-" * 50)
        print("Trader:", trader)
        print("Outcome:", outcome)
        print("Marked:", title)
        print("Allerede investert:", f"{existing_investment:.2f} kr")
        print(
            "Maks per hendelse:",
            f"{MAX_POSITION_PER_EVENT:.2f} kr"
        )
        print("Nytt paper-kjøp hoppes over.")
        return False

    # Vi skal aldri investere mer enn det som er igjen
    # av grensen.
    trade_amount = min(PAPER_TRADE_SIZE, remaining_limit)

    # Sikkerhet: ikke opprett et merkelig lite kjøp
    if trade_amount <= 0:
        return False

    # --------------------------------------------------------
    # SJEKK PAPER-SALDO
    # --------------------------------------------------------

    cursor.execute(
        "SELECT balance FROM paper_account WHERE id = 1"
    )

    row = cursor.fetchone()

    if not row:
        print("❌ Fant ikke paper-konto.")
        return False

    balance = row[0]

    if balance < trade_amount:
        print(
            f"⚠️ Ikke nok paper-penger. "
            f"Saldo: {balance:.2f} kr"
        )
        return False

    # --------------------------------------------------------
    # KJØP
    # --------------------------------------------------------

    shares = trade_amount / price
    new_balance = balance - trade_amount

    cursor.execute(
        "UPDATE paper_account SET balance = ? WHERE id = 1",
        (new_balance,)
    )

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
            condition_id,
            sell_price,
            sell_value,
            profit,
            closed_timestamp
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?,
            NULL, NULL, NULL, NULL
        )
    """, (
        trader,
        outcome,
        price,
        shares,
        trade_amount,
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
    print("Investert:", f"{trade_amount:.2f} kr")
    print("Shares:", f"{shares:.2f}")
    print("Investert i denne hendelsen totalt:",
          f"{existing_investment + trade_amount:.2f} kr")
    print("Maks per hendelse:",
          f"{MAX_POSITION_PER_EVENT:.2f} kr")
    print("Ny saldo:", f"{new_balance:.2f} kr")

    return True


# ============================================================
# REPAIR CONDITION ID
# ============================================================

def repair_position_from_trades(position_id, title, outcome):

    print()
    print("🔧 Mangler condition_id.")
    print("🔎 Søker i registrerte trades...")

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

    print("⚠️ Fant ingen registrert trade med dette markedet.")

    return None


# ============================================================
# MARKET DATA
# ============================================================

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
        print("⚠️ Feil ved henting av marked:", e)
        return None


def parse_json_array(value):

    if isinstance(value, list):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None

    return None


def get_token_id(condition_id, outcome):

    market = get_market_by_condition(condition_id)

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

        for i, market_outcome in enumerate(outcomes):

            if (
                str(market_outcome).strip().lower()
                == str(outcome).strip().lower()
            ):

                if i < len(token_ids):
                    return token_ids[i]

    except Exception as e:
        print("⚠️ Kunne ikke finne token ID:", e)

    return None


def get_settlement_price(condition_id, outcome):

    # Rettet: denne funksjonen skal bare sende
    # condition_id til get_market_by_condition().
    market = get_market_by_condition(condition_id)

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

        for i, market_outcome in enumerate(outcomes):

            if (
                str(market_outcome).strip().lower()
                == str(outcome).strip().lower()
            ):

                if i < len(prices):

                    price = float(prices[i])

                    print(
                        "🏁 Bruker sluttpris fra markedet:",
                        price
                    )

                    return price

    except Exception as e:
        print("⚠️ Kunne ikke hente sluttpris:", e)

    return None


def get_current_price(
    condition_id,
    outcome,
    position_id=None,
    title=None
):

    if not condition_id:

        if position_id and title:

            condition_id = repair_position_from_trades(
                position_id,
                title,
                outcome
            )

        if not condition_id:
            return None

    settlement_price = get_settlement_price(
        condition_id,
        outcome
    )

    if settlement_price is not None:
        return settlement_price

    token_id = get_token_id(
        condition_id,
        outcome
    )

    if not token_id:
        return None

    # --------------------------------------------------------
    # MIDPOINT
    # --------------------------------------------------------

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
        print("⚠️ Midpoint-feil:", e)

    # --------------------------------------------------------
    # BUY PRICE
    # --------------------------------------------------------

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
        print("⚠️ Pris-feil:", e)

    return None


# ============================================================
# PAPER SELL
# ============================================================

def paper_sell(
    position_id,
    sell_price,
    reason="Trader solgte"
):

    cursor.execute("""
        SELECT
            trader,
            outcome,
            price,
            shares,
            invested,
            title
        FROM paper_positions
        WHERE id = ?
          AND status = 'OPEN'
    """, (position_id,))

    position = cursor.fetchone()

    if not position:
        return False

    (
        trader,
        outcome,
        entry_price,
        shares,
        invested,
        title
    ) = position

    if not sell_price or sell_price <= 0:

        print(
            "⚠️ Kunne ikke selge paper-posisjon "
            "fordi salgsprisen mangler."
        )

        return False

    sell_value = shares * sell_price
    profit = sell_value - invested

    cursor.execute(
        "SELECT balance FROM paper_account WHERE id = 1"
    )

    row = cursor.fetchone()

    if not row:
        print("❌ Fant ikke paper-konto.")
        return False

    old_balance = row[0]
    new_balance = old_balance + sell_value

    now = int(datetime.now().timestamp())

    cursor.execute(
        "UPDATE paper_account SET balance = ? WHERE id = 1",
        (new_balance,)
    )

    cursor.execute("""
        UPDATE paper_positions
        SET
            status = 'CLOSED',
            sell_price = ?,
            sell_value = ?,
            profit = ?,
            closed_timestamp = ?
        WHERE id = ?
    """, (
        sell_price,
        sell_value,
        profit,
        now,
        position_id
    ))

    conn.commit()

    print()
    print("🔴 PAPER-SELL")
    print("-" * 50)
    print("Trader:", trader)
    print("Outcome:", outcome)
    print("Marked:", title)
    print("Kjøpspris:", f"{entry_price:.4f}")
    print("Salgspris:", f"{sell_price:.4f}")
    print("Investert:", f"{invested:.2f} kr")
    print("Solgt for:", f"{sell_value:.2f} kr")

    if profit >= 0:
        print("Resultat:", f"+{profit:.2f} kr")
    else:
        print("Resultat:", f"{profit:.2f} kr")

    print("Årsak:", reason)
    print("Ny saldo:", f"{new_balance:.2f} kr")

    return True


# ============================================================
# SELL SIGNAL
# ============================================================

def process_sell_signal(signal):

    trader = signal["trader"]
    outcome = signal["outcome"]
    condition_id = signal["condition_id"]
    title = signal["title"]

    print()
    print("🔴 NYTT SELL-SIGNAL")
    print("-" * 50)
    print("Trader:", trader)
    print("Outcome:", outcome)
    print("Marked:", title)
    print("Traderens pris:", signal["price"])
    print(
        "Traderens posisjonsverdi:",
        f"${signal['value']:,.2f}"
    )

    if condition_id:

        cursor.execute("""
            SELECT
                id,
                condition_id,
                outcome,
                title
            FROM paper_positions
            WHERE status = 'OPEN'
              AND trader = ?
              AND condition_id = ?
              AND LOWER(outcome) = LOWER(?)
            ORDER BY id ASC
        """, (
            trader,
            condition_id,
            outcome
        ))

        positions = cursor.fetchall()

    else:

        cursor.execute("""
            SELECT
                id,
                condition_id,
                outcome,
                title
            FROM paper_positions
            WHERE status = 'OPEN'
              AND trader = ?
              AND LOWER(outcome) = LOWER(?)
              AND title = ?
            ORDER BY id ASC
        """, (
            trader,
            outcome,
            title
        ))

        positions = cursor.fetchall()

    if not positions:

        print(
            "ℹ️ Ingen åpen paper-posisjon "
            "som matcher dette salget."
        )

        return 0

    closed_count = 0

    for position in positions:

        position_id = position[0]
        position_condition_id = position[1]
        position_outcome = position[2]
        position_title = position[3]

        current_price = get_current_price(
            position_condition_id,
            position_outcome,
            position_id,
            position_title
        )

        if current_price is None:

            current_price = signal["price"]

            print(
                "⚠️ Markedspris ikke tilgjengelig."
            )

            print(
                "Bruker traderens observerte salgspris "
                "som paper-salgspris:",
                current_price
            )

        if paper_sell(
            position_id,
            current_price,
            reason="Følger traderens SELL-signal"
        ):
            closed_count += 1

    return closed_count


# ============================================================
# PAPER ACCOUNT
# ============================================================

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

            current_value = shares * current_price
            profit = current_value - invested

            profit_percent = (
                profit / invested
            ) * 100

            total_value += current_value

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

    if missing_prices == 0:

        print(
            "Markedsverdi:",
            f"{total_value:.2f} kr"
        )

        total_profit = (
            total_value - total_invested
        )

        if total_invested > 0:

            total_profit_percent = (
                total_profit / total_invested
            ) * 100

        else:

            total_profit_percent = 0

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

        print(
            f"⚠️ {missing_prices} "
            "posisjon(er) mangler pris."
        )

        print(
            "⚠️ Totalt resultat vises ikke "
            "før alle priser er tilgjengelige."
        )

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

    cursor.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(profit), 0)
        FROM paper_positions
        WHERE status = 'CLOSED'
    """)

    closed_count, closed_profit = cursor.fetchone()

    print()
    print(
        "🔴 Lukkede paper-posisjoner:",
        closed_count
    )

    if closed_count > 0:

        if closed_profit >= 0:

            print(
                "💵 Realisert paper-resultat:",
                f"+{closed_profit:.2f} kr"
            )

        else:

            print(
                "💵 Realisert paper-resultat:",
                f"{closed_profit:.2f} kr"
            )

    print("=" * 60)


# ============================================================
# LEADERBOARD
# ============================================================

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

        try:

            leaderboard = response.json()

        except Exception:

            continue

        for trader in leaderboard:

            wallet = trader.get("proxyWallet")

            if not wallet:
                continue

            if wallet not in traders:

                traders[wallet] = {
                    "name": (
                        trader.get("userName")
                        or "Ukjent"
                    ),
                    "day": 0,
                    "week": 0,
                    "month": 0,
                    "all": 0
                }

            traders[wallet][
                period.lower()
            ] = trader.get("pnl", 0) or 0

    return traders


# ============================================================
# SCORE
# ============================================================

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


# ============================================================
# TRADES
# ============================================================

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

    try:

        return response.json()

    except Exception:

        return []


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


# ============================================================
# START BOT
# ============================================================

print("🤖 SMART SIGNAL-BOT")
print("=" * 60)


# ============================================================
# FIND TOP TRADERS
# ============================================================

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
        f"Score: ${trader['score']:,.0f}"
    )


# ============================================================
# CHECK NEW TRADES
# ============================================================

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
            "trader": trader["name"],
            "wallet": trader["wallet"],
            "score": trader["score"],
            "side": trade.get("side"),
            "outcome": trade.get("outcome"),
            "price": trade.get("price"),
            "size": trade.get("size"),
            "title": trade.get("title"),
            "timestamp": trade.get("timestamp"),
            "condition_id": condition_id
        })


conn.commit()


# ============================================================
# CREATE SIGNALS
# ============================================================

buy_signals = []
sell_signals = []


for trade in new_trades:

    side = trade["side"]

    price = trade["price"] or 0
    size = trade["size"] or 0

    value = price * size

    if value < MIN_TRADE_VALUE_USD:
        continue

    signal = {
        **trade,
        "value": value
    }

    if side == "BUY":

        buy_signals.append(signal)

    elif side == "SELL":

        sell_signals.append(signal)


buy_signals.sort(
    key=lambda x: x["value"],
    reverse=True
)

sell_signals.sort(
    key=lambda x: x["value"],
    reverse=True
)


# ============================================================
# SELL SIGNALS
# ============================================================

print()
print("=" * 60)
print("🔴 NYE SELL-SIGNALER")
print("=" * 60)


closed_positions = 0


if not sell_signals:

    print()
    print("Ingen nye sterke SELL-signaler.")

else:

    for signal in sell_signals[:20]:

        timestamp = signal["timestamp"]

        if timestamp:

            try:

                time = datetime.fromtimestamp(
                    timestamp
                ).strftime(
                    "%Y-%m-%d %H:%M"
                )

            except Exception:

                time = "Ukjent"

        else:

            time = "Ukjent"


        print()
        print("🔴 SELL")
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

        closed = process_sell_signal(
            signal
        )

        closed_positions += closed


# ============================================================
# BUY SIGNALS
# ============================================================

print()
print("=" * 60)
print("🚨 NYE BUY-SIGNALER")
print("=" * 60)


if not buy_signals:

    print()
    print("Ingen nye sterke BUY-signaler.")

else:

    for signal in buy_signals[:20]:

        timestamp = signal["timestamp"]

        if timestamp:

            try:

                time = datetime.fromtimestamp(
                    timestamp
                ).strftime(
                    "%Y-%m-%d %H:%M"
                )

            except Exception:

                time = "Ukjent"

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


# ============================================================
# SUMMARY
# ============================================================

print()
print("=" * 60)

print(
    f"📥 {len(new_trades)} "
    "nye trades registrert."
)

print(
    f"🟢 {len(buy_signals)} "
    "nye BUY-signaler."
)

print(
    f"🔴 {len(sell_signals)} "
    "nye SELL-signaler."
)

print(
    f"💰 {closed_positions} "
    "paper-posisjon(er) solgt."
)

print(
    "🛑 Maks per hendelse:",
    f"{MAX_POSITION_PER_EVENT:.2f} kr"
)

print("=" * 60)


# ============================================================
# SHOW ACCOUNT
# ============================================================

show_paper_account()


conn.commit()
conn.close()
