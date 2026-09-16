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
PAPER_TRADE_SIZE_PERCENT = 0.10

MIN_TRADE_VALUE_USD = 1000.0


# ============================================================
# DATABASE
# ============================================================

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
# HELPERS
# ============================================================

def parse_json_array(value):
    if isinstance(value, list):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None

    return None


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
            print(
                "⚠️ Gamma-feil:",
                response.status_code
            )
            return None

        data = response.json()

        if isinstance(data, list):
            if data:
                return data[0]

        elif isinstance(data, dict):

            markets = data.get("markets")

            if markets:
                return markets[0]

    except Exception as e:
        print("⚠️ Feil ved henting av marked:", e)

    return None


def get_token_id(condition_id, outcome):

    market = get_market_by_condition(condition_id)

    if not market:
        return None

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
                return str(token_ids[i])

    return None


# ============================================================
# SETTLEMENT / CURRENT PRICE
# ============================================================

def get_settlement_price(condition_id, outcome):

    market = get_market_by_condition(condition_id)

    if not market:
        return None

    outcomes = parse_json_array(
        market.get("outcomes")
    )

    prices = parse_json_array(
        market.get("outcomePrices")
    )

    if not outcomes or not prices:
        return None

    closed = market.get("closed")

    # Marketet må være lukket for at vi skal bruke
    # outcomePrices som endelig settlement.
    if not closed:
        return None

    for i, market_outcome in enumerate(outcomes):

        if (
            str(market_outcome).strip().lower()
            == str(outcome).strip().lower()
        ):

            if i < len(prices):

                try:
                    price = float(prices[i])

                    # Bare gyldige prediction-market-priser
                    if 0.0 <= price <= 1.0:
                        return price

                except Exception:
                    return None

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

    # --------------------------------------------------------
    # FØRST: SJEKK OM MARKEDET ER AVGJORT
    # --------------------------------------------------------

    settlement_price = get_settlement_price(
        condition_id,
        outcome
    )

    if settlement_price is not None:
        return settlement_price

    # --------------------------------------------------------
    # ELLERS: LIVE CLOB-PRIS
    # --------------------------------------------------------

    token_id = get_token_id(
        condition_id,
        outcome
    )

    if not token_id:
        return None

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

                price = float(midpoint)

                if 0 <= price <= 1:
                    return price

    except Exception as e:
        print("⚠️ Midpoint-feil:", e)

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

                price = float(price)

                if 0 <= price <= 1:
                    return price

    except Exception as e:
        print("⚠️ Pris-feil:", e)

    return None


# ============================================================
# REPAIR CONDITION ID
# ============================================================

def repair_position_from_trades(
    position_id,
    title,
    outcome
):

    cursor.execute("""
        SELECT condition_id
        FROM trades
        WHERE condition_id IS NOT NULL
          AND condition_id != ''
          AND title = ?
          AND LOWER(outcome) = LOWER(?)
        ORDER BY timestamp DESC
        LIMIT 1
    """, (
        title,
        outcome
    ))

    match = cursor.fetchone()

    if match:

        condition_id = match[0]

        cursor.execute("""
            UPDATE paper_positions
            SET condition_id = ?
            WHERE id = ?
        """, (
            condition_id,
            position_id
        ))

        conn.commit()

        return condition_id

    return None


# ============================================================
# PAPER CAPITAL
# ============================================================

def get_cash_balance():

    cursor.execute("""
        SELECT balance
        FROM paper_account
        WHERE id = 1
    """)

    row = cursor.fetchone()

    if not row:
        return 0.0

    return float(row[0])


def get_total_paper_capital():

    cash = get_cash_balance()

    cursor.execute("""
        SELECT COALESCE(SUM(invested), 0)
        FROM paper_positions
        WHERE status = 'OPEN'
    """)

    invested = cursor.fetchone()[0] or 0.0

    return cash + invested


def get_bet_size():

    total_capital = get_total_paper_capital()

    return total_capital * PAPER_TRADE_SIZE_PERCENT


# ============================================================
# PAPER BUY
# ============================================================

def paper_buy(
    trader,
    outcome,
    price,
    title,
    timestamp,
    condition_id
):

    if not price or price <= 0:

        print(
            "⚠️ Ugyldig pris. "
            "Paper-kjøp hoppes over."
        )

        return False

    # --------------------------------------------------------
    # TOTAL PAPER-KAPITAL
    # --------------------------------------------------------

    total_capital = get_total_paper_capital()

    max_per_event = (
        total_capital
        * PAPER_TRADE_SIZE_PERCENT
    )

    # --------------------------------------------------------
    # SJEKK ALLEREDE INVESTERT I SAMME HENDELSE
    # --------------------------------------------------------

    if condition_id:

        cursor.execute("""
            SELECT COALESCE(SUM(invested), 0)
            FROM paper_positions
            WHERE status = 'OPEN'
              AND condition_id = ?
              AND LOWER(outcome) = LOWER(?)
        """, (
            condition_id,
            outcome
        ))

    else:

        cursor.execute("""
            SELECT COALESCE(SUM(invested), 0)
            FROM paper_positions
            WHERE status = 'OPEN'
              AND LOWER(outcome) = LOWER(?)
              AND title = ?
        """, (
            outcome,
            title
        ))

    existing_investment = (
        cursor.fetchone()[0] or 0.0
    )

    remaining_limit = (
        max_per_event
        - existing_investment
    )

    if remaining_limit <= 0:

        print()
        print("🛑 10 %-GRENSE NÅDD")
        print("-" * 50)
        print("Marked:", title)
        print("Outcome:", outcome)
        print(
            "Allerede investert:",
            f"{existing_investment:.2f} kr"
        )
        print(
            "Paper-kapital:",
            f"{total_capital:.2f} kr"
        )
        print(
            "Maks på denne hendelsen:",
            f"{max_per_event:.2f} kr"
        )
        print("Nytt kjøp hoppes over.")

        return False

    # Det normale kjøpet er 10 % av kapitalen.
    # Hvis noe allerede er investert på hendelsen,
    # brukes bare det som er igjen av grensen.
    trade_amount = min(
        max_per_event,
        remaining_limit
    )

    # Ikke sats mindre enn 0.01 kr
    if trade_amount < 0.01:
        return False

    # --------------------------------------------------------
    # SALDO
    # --------------------------------------------------------

    balance = get_cash_balance()

    if balance < trade_amount:

        print()
        print("⚠️ Ikke nok paper-penger.")
        print(
            "Saldo:",
            f"{balance:.2f} kr"
        )
        print(
            "Nødvendig:",
            f"{trade_amount:.2f} kr"
        )

        return False

    # --------------------------------------------------------
    # KJØP
    # --------------------------------------------------------

    shares = trade_amount / price

    new_balance = (
        balance - trade_amount
    )

    cursor.execute("""
        UPDATE paper_account
        SET balance = ?
        WHERE id = 1
    """, (
        new_balance,
    ))

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
    print("Marked:", title)
    print(
        "Pris:",
        price
    )
    print(
        "Investert:",
        f"{trade_amount:.2f} kr"
    )
    print(
        "Paper-kapital:",
        f"{total_capital:.2f} kr"
    )
    print(
        "10 %-grense:",
        f"{max_per_event:.2f} kr"
    )
    print(
        "Investert i hendelsen:",
        f"{existing_investment + trade_amount:.2f} kr"
    )
    print(
        "Ny saldo:",
        f"{new_balance:.2f} kr"
    )

    return True


# ============================================================
# CLOSE POSITION
# ============================================================

def close_position(
    position_id,
    settlement_price,
    reason
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
    """, (
        position_id,
    ))

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

    sell_value = (
        shares * settlement_price
    )

    profit = (
        sell_value - invested
    )

    old_balance = get_cash_balance()

    new_balance = (
        old_balance + sell_value
    )

    now = int(
        datetime.now().timestamp()
    )

    cursor.execute("""
        UPDATE paper_account
        SET balance = ?
        WHERE id = 1
    """, (
        new_balance,
    ))

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
        settlement_price,
        sell_value,
        profit,
        now,
        position_id
    ))

    conn.commit()

    print()
    print("🔒 PAPER-POSISJON AVGJORT")
    print("-" * 50)
    print("Marked:", title)
    print("Outcome:", outcome)
    print("Kjøpspris:", f"{entry_price:.4f}")
    print(
        "Sluttpris:",
        f"{settlement_price:.4f}"
    )
    print(
        "Investert:",
        f"{invested:.2f} kr"
    )
    print(
        "Sluttverdi:",
        f"{sell_value:.2f} kr"
    )

    if profit >= 0:

        print(
            "Resultat:",
            f"+{profit:.2f} kr"
        )

    else:

        print(
            "Resultat:",
            f"{profit:.2f} kr"
        )

    print("Årsak:", reason)

    return True


# ============================================================
# UPDATE CLOSED MARKETS
# ============================================================

def settle_closed_positions():

    print()
    print("=" * 60)
    print("🏁 SJEKKER AVGJORTE POSISJONER")
    print("=" * 60)

    cursor.execute("""
        SELECT
            id,
            outcome,
            condition_id,
            title
        FROM paper_positions
        WHERE status = 'OPEN'
    """)

    positions = cursor.fetchall()

    settled_count = 0

    for position in positions:

        (
            position_id,
            outcome,
            condition_id,
            title
        ) = position

        if not condition_id:

            condition_id = (
                repair_position_from_trades(
                    position_id,
                    title,
                    outcome
                )
            )

        if not condition_id:
            continue

        settlement_price = (
            get_settlement_price(
                condition_id,
                outcome
            )
        )

        if settlement_price is None:
            continue

        if settlement_price >= 0.999:

            reason = "Outcome vant"

        elif settlement_price <= 0.001:

            reason = "Outcome tapte"

        else:

            reason = "Marked avgjort"

        if close_position(
            position_id,
            settlement_price,
            reason
        ):

            settled_count += 1

    if settled_count == 0:

        print(
            "Ingen åpne posisjoner ble avgjort."
        )

    else:

        print()
        print(
            "🏁 Avg jorte posisjoner:",
            settled_count
        )

    return settled_count


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

            wallet = trader.get(
                "proxyWallet"
            )

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
            ] = (
                trader.get("pnl", 0)
                or 0
            )

    return traders


def calculate_score(trader):

    return (
        trader["day"] * 0.40
        + trader["week"] * 0.30
        + trader["month"] * 0.20
        + trader["all"] * 0.10
    )


# ============================================================
# TRADES
# ============================================================

def get_trades(wallet):

    try:

        response = requests.get(
            TRADES_URL,
            params={
                "user": wallet,
                "limit": 50
            },
            timeout=10
        )

        if response.status_code != 200:
            return []

        return response.json()

    except Exception:

        return []


def trade_exists(trade_id):

    cursor.execute("""
        SELECT trade_id
        FROM trades
        WHERE trade_id = ?
    """, (
        trade_id,
    ))

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
# START
# ============================================================

print("🤖 SMART SIGNAL-BOT")
print("=" * 60)


# Først gjør vi opp gamle posisjoner som eventuelt
# allerede er avgjort.
settle_closed_positions()


# ============================================================
# TOP TRADERS
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
# NEW TRADES
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
# SIGNALS
# ============================================================

buy_signals = []
sell_signals = []

for trade in new_trades:

    price = trade["price"] or 0
    size = trade["size"] or 0

    value = (
        price * size
    )

    if value < MIN_TRADE_VALUE_USD:
        continue

    signal = {
        **trade,
        "value": value
    }

    if trade["side"] == "BUY":

        buy_signals.append(signal)

    elif trade["side"] == "SELL":

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

if not sell_signals:

    print()
    print(
        "Ingen nye sterke SELL-signaler."
    )

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


# ============================================================
# BUY SIGNALS
# ============================================================

print()
print("=" * 60)
print("🚨 NYE BUY-SIGNALER")
print("=" * 60)

if not buy_signals:

    print()
    print(
        "Ingen nye sterke BUY-signaler."
    )

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
# FINAL ACCOUNT
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
    "📊 Bet-størrelse:",
    "10 % av paper-kapitalen"
)

print("=" * 60)


# Vis konto
print()
print("💰 PAPER-KONTO")
print("=" * 60)

cash = get_cash_balance()
capital = get_total_paper_capital()

print(
    "Kontantsaldo:",
    f"{cash:.2f} kr"
)

print(
    "Paper-kapital:",
    f"{capital:.2f} kr"
)

print(
    "Neste maksbet:",
    f"{get_bet_size():.2f} kr"
)

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

print(
    "Åpne posisjoner:",
    len(positions)
)

print("-" * 60)

total_invested = 0.0
current_value_total = 0.0
priced_positions = 0

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

    total_invested += invested

    current_price = get_current_price(
        condition_id,
        outcome,
        position_id,
        title
    )

    print()
    print("📊 POSISJON")
    print("Marked:", title)
    print("Outcome:", outcome)
    print("Trader:", trader)
    print(
        "Kjøpspris:",
        f"{entry_price:.4f}"
    )
    print(
        "Investert:",
        f"{invested:.2f} kr"
    )

    if current_price is not None:

        value = (
            shares * current_price
        )

        profit = (
            value - invested
        )

        current_value_total += value
        priced_positions += 1

        print(
            "Nåværende pris:",
            f"{current_price:.4f}"
        )

        print(
            "Nåverdi:",
            f"{value:.2f} kr"
        )

        if profit >= 0:

            print(
                "Urealisert resultat:",
                f"+{profit:.2f} kr"
            )

        else:

            print(
                "Urealisert resultat:",
                f"{profit:.2f} kr"
            )

    else:

        print(
            "⚠️ Nåværende pris ikke tilgjengelig."
        )


print()
print("-" * 60)

print(
    "Totalt investert:",
    f"{total_invested:.2f} kr"
)

if priced_positions == len(positions):

    total_equity = (
        cash + current_value_total
    )

    total_profit = (
        total_equity - PAPER_START_BALANCE
    )

    print(
        "Total paper-verdi:",
        f"{total_equity:.2f} kr"
    )

    if total_profit >= 0:

        print(
            "📈 Totalt paper-resultat:",
            f"+{total_profit:.2f} kr"
        )

    else:

        print(
            "📉 Totalt paper-resultat:",
            f"{total_profit:.2f} kr"
        )

else:

    print(
        f"⚠️ {len(positions) - priced_positions} "
        "posisjon(er) mangler pris."
    )

    print(
        "Total paper-verdi kan ikke beregnes "
        "helt nøyaktig ennå."
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
    "🔒 Lukkede posisjoner:",
    closed_count
)

if closed_count > 0:

    if closed_profit >= 0:

        print(
            "💵 Realisert resultat:",
            f"+{closed_profit:.2f} kr"
        )

    else:

        print(
            "💵 Realisert resultat:",
            f"{closed_profit:.2f} kr"
        )

print("=" * 60)


conn.commit()
conn.close()
