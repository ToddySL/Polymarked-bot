import requests
import sqlite3
import json
import time
from datetime import datetime

DB = "trades.db"

LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
TRADES_URL = "https://data-api.polymarket.com/trades"
GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets"
CLOB_MARKET_URL = "https://clob.polymarket.com/markets"          # + /{condition_id}
CLOB_MIDPOINT_URL = "https://clob.polymarket.com/midpoint"
CLOB_PRICE_URL = "https://clob.polymarket.com/price"

PAPER_START_BALANCE = 400.0
PAPER_TRADE_SIZE_PERCENT = 0.10

MIN_TRADE_VALUE_USD = 1000.0

# Hvor lenge vi stoler paa en cachet "fant ikke marked"-status før vi
# prøver på nytt. Gamma-indeksen henger noen ganger etter CLOB for
# ferske/nisje-markeder, så vi vil prøve igjen etter en stund i stedet
# for å gi opp for alltid.
MARKET_NOT_FOUND_RETRY_SECONDS = 15 * 60

# Kort pause mellom eksterne kall for å være grei mot rate-limits.
REQUEST_SLEEP = 0.12

# Sett til True for å se den gamle, veldig verbose debug-loggingen
# for hvert eneste API-kall. Default False = mye ryddigere output.
VERBOSE_MARKET_DEBUG = False


# ============================================================
# HTTP SESSION (gjenbruk av tilkobling => raskere + færre feil)
# ============================================================

session = requests.Session()
session.headers.update({"User-Agent": "smart-signal-bot/2.0"})


def http_get(url, params=None, timeout=10):
    """Wrapper med én automatisk retry og pause mellom kall."""
    for attempt in range(2):
        try:
            response = session.get(url, params=params, timeout=timeout)
            time.sleep(REQUEST_SLEEP)
            return response
        except requests.RequestException as e:
            if attempt == 0:
                time.sleep(0.5)
                continue
            print(f"⚠️ Nettverksfeil mot {url}: {e!r}")
            return None
    return None


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

# NYTT: cache for market-oppslag, slik at vi ikke spør Gamma/CLOB
# på nytt for hvert eneste rune når vi allerede vet svaret (eller
# allerede vet at det er "ikke funnet ennå").
cursor.execute("""
CREATE TABLE IF NOT EXISTS market_cache (
    condition_id TEXT PRIMARY KEY,
    source TEXT,              -- 'gamma', 'clob' eller 'not_found'
    outcomes_json TEXT,       -- JSON-liste med outcome-navn
    token_ids_json TEXT,      -- JSON-liste med token_id, samme rekkefølge
    closed INTEGER,           -- 0/1, kun kjent når source != 'not_found'
    checked_at TEXT
)
""")

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


def _cache_get(condition_id):
    cursor.execute("""
        SELECT source, outcomes_json, token_ids_json, closed, checked_at
        FROM market_cache
        WHERE condition_id = ?
    """, (condition_id,))
    row = cursor.fetchone()
    if not row:
        return None

    source, outcomes_json, token_ids_json, closed, checked_at = row

    # "not_found" cache har en TTL, slik at vi prøver på nytt
    # etter en stund i stedet for å gi opp permanent.
    if source == "not_found":
        try:
            checked_dt = datetime.fromisoformat(checked_at)
            age = (datetime.now() - checked_dt).total_seconds()
        except Exception:
            age = MARKET_NOT_FOUND_RETRY_SECONDS + 1

        if age > MARKET_NOT_FOUND_RETRY_SECONDS:
            return None  # cache er "utløpt", prøv på nytt

    return {
        "source": source,
        "outcomes": parse_json_array(outcomes_json) or [],
        "token_ids": parse_json_array(token_ids_json) or [],
        "closed": bool(closed) if closed is not None else None,
    }


def _cache_set(condition_id, source, outcomes=None, token_ids=None, closed=None):
    cursor.execute("""
        INSERT INTO market_cache (condition_id, source, outcomes_json, token_ids_json, closed, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(condition_id) DO UPDATE SET
            source = excluded.source,
            outcomes_json = excluded.outcomes_json,
            token_ids_json = excluded.token_ids_json,
            closed = excluded.closed,
            checked_at = excluded.checked_at
    """, (
        condition_id,
        source,
        json.dumps(outcomes) if outcomes is not None else None,
        json.dumps(token_ids) if token_ids is not None else None,
        int(bool(closed)) if closed is not None else None,
        datetime.now().isoformat(),
    ))
    conn.commit()


def get_market_by_condition(condition_id):
    """
    Slår opp et marked på condition_id.

    Prøver først Gamma (gir mest metadata: outcomePrices, closed osv.).
    Gamma sin indeks kan imidlertid ligge etter CLOB for ferske eller
    lav-volum markeder (typisk esport / mindre tenniskamper), så hvis
    Gamma svarer tomt faller vi tilbake på CLOB sitt market-endepunkt
    som går rett på selve ordreboken.

    Returnerer en dict med felles format:
        {
            "source": "gamma" | "clob",
            "outcomes": [...],
            "token_ids": [...],
            "closed": bool eller None (ukjent, typisk fra CLOB),
            "outcome_prices": [...] eller None (kun fra Gamma),
        }
    eller None hvis marked ikke ble funnet i noen av kildene.
    """

    if not condition_id:
        print("⚠️ Ingen condition_id.")
        return None

    cached = _cache_get(condition_id)
    if cached is not None:
        if cached["source"] == "not_found":
            return None
        return {
            "source": cached["source"],
            "outcomes": cached["outcomes"],
            "token_ids": cached["token_ids"],
            "closed": cached["closed"],
            "outcome_prices": None,  # cache lagrer ikke priser, de er ferske hver gang
        }

    if VERBOSE_MARKET_DEBUG:
        print()
        print("🔍 DEBUG: Henter marked (Gamma)")
        print("Condition ID:", condition_id)

    # --------------------------------------------------------
    # 1) GAMMA
    # --------------------------------------------------------
    gamma_market = None

    response = http_get(
        GAMMA_MARKET_URL,
        params={"condition_ids": condition_id, "limit": 1},
    )

    if response is not None:
        if VERBOSE_MARKET_DEBUG:
            print("HTTP-status:", response.status_code)

        if response.status_code == 200:
            try:
                data = response.json()
            except Exception:
                data = None

            if isinstance(data, list) and data:
                gamma_market = data[0]
            elif isinstance(data, dict) and data.get("markets"):
                gamma_market = data["markets"][0]

    if gamma_market:
        outcomes = parse_json_array(gamma_market.get("outcomes")) or []
        token_ids = parse_json_array(gamma_market.get("clobTokenIds")) or []
        outcome_prices = parse_json_array(gamma_market.get("outcomePrices"))
        closed = bool(gamma_market.get("closed"))

        _cache_set(condition_id, "gamma", outcomes, token_ids, closed)

        return {
            "source": "gamma",
            "outcomes": outcomes,
            "token_ids": token_ids,
            "closed": closed,
            "outcome_prices": outcome_prices,
        }

    # --------------------------------------------------------
    # 2) CLOB FALLBACK
    #
    # Gamma fant ingenting -> gå direkte mot CLOB, som er selve
    # ordreboken og derfor alltid oppdatert med mindre markedet
    # rett og slett ikke finnes.
    # --------------------------------------------------------
    if VERBOSE_MARKET_DEBUG:
        print("🔍 DEBUG: Gamma tom, prøver CLOB-fallback")

    clob_response = http_get(f"{CLOB_MARKET_URL}/{condition_id}")

    if clob_response is not None and clob_response.status_code == 200:
        try:
            clob_data = clob_response.json()
        except Exception:
            clob_data = None

        if isinstance(clob_data, dict) and clob_data.get("tokens"):
            tokens = clob_data.get("tokens") or []
            outcomes = [t.get("outcome") for t in tokens]
            token_ids = [t.get("token_id") for t in tokens]
            closed = bool(clob_data.get("closed"))

            _cache_set(condition_id, "clob", outcomes, token_ids, closed)

            print(f"✅ Fant marked via CLOB-fallback (Gamma manglet det): {condition_id[:12]}...")

            return {
                "source": "clob",
                "outcomes": outcomes,
                "token_ids": token_ids,
                "closed": closed,
                "outcome_prices": None,
            }

    # --------------------------------------------------------
    # Ingen av kildene fant markedet.
    # --------------------------------------------------------
    print(f"❌ Fant ingen marked for condition_id {condition_id[:12]}... (Gamma + CLOB)")
    _cache_set(condition_id, "not_found")
    return None


def get_token_id(condition_id, outcome):

    market = get_market_by_condition(condition_id)

    if not market:
        return None

    outcomes = market["outcomes"]
    token_ids = market["token_ids"]

    if not outcomes or not token_ids:
        return None

    for i, market_outcome in enumerate(outcomes):
        if str(market_outcome).strip().lower() == str(outcome).strip().lower():
            if i < len(token_ids):
                return str(token_ids[i])

    return None


# ============================================================
# SETTLEMENT / CURRENT PRICE
# ============================================================

def get_settlement_price(condition_id, outcome):

    market = get_market_by_condition(condition_id)

    if not market or not market.get("closed"):
        return None

    # --------------------------------------------------------
    # Foretrukket kilde: Gamma sine outcomePrices, siden de er
    # den offisielle, endelige oppgjørsprisen.
    # --------------------------------------------------------
    outcome_prices = market.get("outcome_prices")

    if outcome_prices:
        outcomes = market["outcomes"]
        for i, market_outcome in enumerate(outcomes):
            if str(market_outcome).strip().lower() == str(outcome).strip().lower():
                if i < len(outcome_prices):
                    try:
                        price = float(outcome_prices[i])
                        if 0.0 <= price <= 1.0:
                            return price
                    except Exception:
                        pass

    # --------------------------------------------------------
    # Fallback (typisk når marked kom fra CLOB, ikke Gamma):
    # markedet er "closed", men vi har ikke outcomePrices.
    # Spør CLOB om prisen på selve outcome-tokenet direkte --
    # et avgjort marked konvergerer mot 0.0 eller 1.0.
    # Vi krever at prisen er tydelig ekstrem for å stole på den,
    # slik at vi ikke feilaktig avgjør et marked som bare er
    # illikvid men fortsatt egentlig åpent.
    # --------------------------------------------------------
    token_id = get_token_id(condition_id, outcome)

    if not token_id:
        return None

    response = http_get(CLOB_PRICE_URL, params={"token_id": token_id, "side": "BUY"})

    if response is not None and response.status_code == 200:
        try:
            price = float(response.json().get("price"))
        except Exception:
            return None

        if price >= 0.98 or price <= 0.02:
            return round(price)  # rund av til rent 0.0 eller 1.0

    return None


def get_current_price(condition_id, outcome, position_id=None, title=None):

    if not condition_id:
        if position_id and title:
            condition_id = repair_position_from_trades(position_id, title, outcome)
        if not condition_id:
            return None

    # --------------------------------------------------------
    # FØRST: SJEKK OM MARKEDET ER AVGJORT
    # --------------------------------------------------------
    settlement_price = get_settlement_price(condition_id, outcome)
    if settlement_price is not None:
        return settlement_price

    # --------------------------------------------------------
    # ELLERS: LIVE CLOB-PRIS
    # --------------------------------------------------------
    token_id = get_token_id(condition_id, outcome)
    if not token_id:
        return None

    response = http_get(CLOB_MIDPOINT_URL, params={"token_id": token_id})
    if response is not None and response.status_code == 200:
        try:
            midpoint = response.json().get("mid")
            if midpoint is not None:
                price = float(midpoint)
                if 0 <= price <= 1:
                    return price
        except Exception as e:
            print("⚠️ Midpoint-feil:", e)

    response = http_get(CLOB_PRICE_URL, params={"token_id": token_id, "side": "BUY"})
    if response is not None and response.status_code == 200:
        try:
            price = response.json().get("price")
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

def repair_position_from_trades(position_id, title, outcome):

    cursor.execute("""
        SELECT condition_id
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

        cursor.execute("""
            UPDATE paper_positions
            SET condition_id = ?
            WHERE id = ?
        """, (condition_id, position_id))

        conn.commit()

        return condition_id

    return None


# ============================================================
# PAPER CAPITAL
# ============================================================

def get_cash_balance():
    cursor.execute("SELECT balance FROM paper_account WHERE id = 1")
    row = cursor.fetchone()
    return float(row[0]) if row else 0.0


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
    return get_total_paper_capital() * PAPER_TRADE_SIZE_PERCENT


# ============================================================
# PAPER BUY
# ============================================================

def paper_buy(trader, outcome, price, title, timestamp, condition_id):

    if not price or price <= 0:
        print("⚠️ Ugyldig pris. Paper-kjøp hoppes over.")
        return False

    total_capital = get_total_paper_capital()
    max_per_event = total_capital * PAPER_TRADE_SIZE_PERCENT

    if condition_id:
        cursor.execute("""
            SELECT COALESCE(SUM(invested), 0)
            FROM paper_positions
            WHERE status = 'OPEN'
              AND condition_id = ?
              AND LOWER(outcome) = LOWER(?)
        """, (condition_id, outcome))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(invested), 0)
            FROM paper_positions
            WHERE status = 'OPEN'
              AND LOWER(outcome) = LOWER(?)
              AND title = ?
        """, (outcome, title))

    existing_investment = cursor.fetchone()[0] or 0.0
    remaining_limit = max_per_event - existing_investment

    if remaining_limit <= 0:
        print()
        print("🛑 10 %-GRENSE NÅDD")
        print("-" * 50)
        print("Marked:", title)
        print("Outcome:", outcome)
        print("Allerede investert:", f"{existing_investment:.2f} kr")
        print("Paper-kapital:", f"{total_capital:.2f} kr")
        print("Maks på denne hendelsen:", f"{max_per_event:.2f} kr")
        print("Nytt kjøp hoppes over.")
        return False

    trade_amount = min(max_per_event, remaining_limit)

    if trade_amount < 0.01:
        return False

    balance = get_cash_balance()

    if balance < trade_amount:
        print()
        print("⚠️ Ikke nok paper-penger (all kapital er allerede allokert til åpne posisjoner).")
        print("Saldo:", f"{balance:.2f} kr")
        print("Nødvendig:", f"{trade_amount:.2f} kr")
        return False

    shares = trade_amount / price
    new_balance = balance - trade_amount

    cursor.execute("UPDATE paper_account SET balance = ? WHERE id = 1", (new_balance,))

    cursor.execute("""
        INSERT INTO paper_positions (
            trader, outcome, price, shares, invested, title, timestamp,
            status, condition_id, sell_price, sell_value, profit, closed_timestamp
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
    """, (
        trader, outcome, price, shares, trade_amount, title, timestamp,
        "OPEN", condition_id
    ))

    conn.commit()

    print()
    print("💰 PAPER-KJØP")
    print("-" * 50)
    print("Trader:", trader)
    print("Outcome:", outcome)
    print("Marked:", title)
    print("Pris:", price)
    print("Investert:", f"{trade_amount:.2f} kr")
    print("Paper-kapital:", f"{total_capital:.2f} kr")
    print("10 %-grense:", f"{max_per_event:.2f} kr")
    print("Investert i hendelsen:", f"{existing_investment + trade_amount:.2f} kr")
    print("Ny saldo:", f"{new_balance:.2f} kr")

    return True


# ============================================================
# CLOSE POSITION
# ============================================================

def close_position(position_id, settlement_price, reason):

    cursor.execute("""
        SELECT trader, outcome, price, shares, invested, title
        FROM paper_positions
        WHERE id = ? AND status = 'OPEN'
    """, (position_id,))

    position = cursor.fetchone()
    if not position:
        return False

    trader, outcome, entry_price, shares, invested, title = position

    sell_value = shares * settlement_price
    profit = sell_value - invested

    old_balance = get_cash_balance()
    new_balance = old_balance + sell_value

    now = int(datetime.now().timestamp())

    cursor.execute("UPDATE paper_account SET balance = ? WHERE id = 1", (new_balance,))

    cursor.execute("""
        UPDATE paper_positions
        SET status = 'CLOSED', sell_price = ?, sell_value = ?, profit = ?, closed_timestamp = ?
        WHERE id = ?
    """, (settlement_price, sell_value, profit, now, position_id))

    conn.commit()

    print()
    print("🔒 PAPER-POSISJON AVGJORT")
    print("-" * 50)
    print("Marked:", title)
    print("Outcome:", outcome)
    print("Kjøpspris:", f"{entry_price:.4f}")
    print("Sluttpris:", f"{settlement_price:.4f}")
    print("Investert:", f"{invested:.2f} kr")
    print("Sluttverdi:", f"{sell_value:.2f} kr")
    print("Resultat:", f"{'+' if profit >= 0 else ''}{profit:.2f} kr")
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
        SELECT id, outcome, condition_id, title
        FROM paper_positions
        WHERE status = 'OPEN'
    """)

    positions = cursor.fetchall()
    settled_count = 0

    for position_id, outcome, condition_id, title in positions:

        if not condition_id:
            condition_id = repair_position_from_trades(position_id, title, outcome)

        if not condition_id:
            continue

        settlement_price = get_settlement_price(condition_id, outcome)

        if settlement_price is None:
            continue

        if settlement_price >= 0.999:
            reason = "Outcome vant"
        elif settlement_price <= 0.001:
            reason = "Outcome tapte"
        else:
            reason = "Marked avgjort"

        if close_position(position_id, settlement_price, reason):
            settled_count += 1

    if settled_count == 0:
        print("Ingen åpne posisjoner ble avgjort.")
    else:
        print()
        print("🏁 Avgjorte posisjoner:", settled_count)

    return settled_count


# ============================================================
# LEADERBOARD
# ============================================================

def get_top_traders():

    traders = {}

    for period in ["DAY", "WEEK", "MONTH", "ALL"]:

        params = {
            "category": "OVERALL",
            "timePeriod": period,
            "orderBy": "PNL",
            "limit": 50,
        }

        response = http_get(LEADERBOARD_URL, params=params)

        if response is None or response.status_code != 200:
            print("Feil ved", period)
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
                    "name": trader.get("userName") or "Ukjent",
                    "day": 0, "week": 0, "month": 0, "all": 0,
                }

            traders[wallet][period.lower()] = trader.get("pnl", 0) or 0

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
    response = http_get(TRADES_URL, params={"user": wallet, "limit": 50})
    if response is None or response.status_code != 200:
        return []
    try:
        return response.json()
    except Exception:
        return []


def trade_exists(trade_id):
    cursor.execute("SELECT trade_id FROM trades WHERE trade_id = ?", (trade_id,))
    return cursor.fetchone() is not None


def create_trade_id(wallet, trade):
    return (
        f"{wallet}-{trade.get('timestamp')}-{trade.get('conditionId')}-"
        f"{trade.get('side')}-{trade.get('price')}-{trade.get('size')}"
    )


# ============================================================
# START
# ============================================================

print("🤖 SMART SIGNAL-BOT")
print("=" * 60)

settle_closed_positions()

# ============================================================
# TOP TRADERS
# ============================================================

traders = get_top_traders()

scored_traders = []
for wallet, trader in traders.items():
    score = calculate_score(trader)
    scored_traders.append({"wallet": wallet, "name": trader["name"], "score": score})

scored_traders.sort(key=lambda x: x["score"], reverse=True)

print()
print("🏆 TOPP TRADERE")
print("=" * 60)
for i, trader in enumerate(scored_traders[:20], 1):
    print(f"{i}. {trader['name']} | Score: ${trader['score']:,.0f}")

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

    trades = get_trades(trader["wallet"])

    for trade in trades:
        trade_id = create_trade_id(trader["wallet"], trade)

        if trade_exists(trade_id):
            continue

        condition_id = trade.get("conditionId")

        cursor.execute("""
            INSERT INTO trades (
                trade_id, trader, wallet, side, outcome, price, size,
                title, timestamp, first_seen, condition_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id, trader["name"], trader["wallet"], trade.get("side"),
            trade.get("outcome"), trade.get("price"), trade.get("size"),
            trade.get("title"), trade.get("timestamp"),
            datetime.now().isoformat(), condition_id,
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
            "condition_id": condition_id,
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
    value = price * size

    if value < MIN_TRADE_VALUE_USD:
        continue

    signal = {**trade, "value": value}

    if trade["side"] == "BUY":
        buy_signals.append(signal)
    elif trade["side"] == "SELL":
        sell_signals.append(signal)

buy_signals.sort(key=lambda x: x["value"], reverse=True)
sell_signals.sort(key=lambda x: x["value"], reverse=True)


def _print_signal(signal, emoji, label):
    timestamp = signal["timestamp"]
    if timestamp:
        try:
            time_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
        except Exception:
            time_str = "Ukjent"
    else:
        time_str = "Ukjent"

    print()
    print(f"{emoji} {label}")
    print("-" * 50)
    print("Trader:", signal["trader"])
    print("Score:", f"${signal['score']:,.0f}")
    print("Handling:", signal["side"])
    print("Outcome:", signal["outcome"])
    print("Pris:", signal["price"])
    print("Posisjonsverdi:", f"${signal['value']:,.2f}")
    print("Marked:", signal["title"])
    print("Tid:", time_str)


# ============================================================
# SELL SIGNALS
# ============================================================

print()
print("=" * 60)
print("🔴 NYE SELL-SIGNALER")
print("=" * 60)

if not sell_signals:
    print()
    print("Ingen nye sterke SELL-signaler.")
else:
    for signal in sell_signals[:20]:
        _print_signal(signal, "🔴", "SELL")

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
        _print_signal(signal, "🔥", "NYTT SIGNAL")

        paper_buy(
            trader=signal["trader"],
            outcome=signal["outcome"],
            price=signal["price"],
            title=signal["title"],
            timestamp=signal["timestamp"],
            condition_id=signal["condition_id"],
        )

# ============================================================
# FINAL ACCOUNT
# ============================================================

print()
print("=" * 60)
print(f"📥 {len(new_trades)} nye trades registrert.")
print(f"🟢 {len(buy_signals)} nye BUY-signaler.")
print(f"🔴 {len(sell_signals)} nye SELL-signaler.")
print("📊 Bet-størrelse: 10 % av paper-kapitalen")
print("=" * 60)

print()
print("💰 PAPER-KONTO")
print("=" * 60)

cash = get_cash_balance()
capital = get_total_paper_capital()

print("Kontantsaldo:", f"{cash:.2f} kr")
print("Paper-kapital:", f"{capital:.2f} kr")
print("Neste maksbet:", f"{get_bet_size():.2f} kr")

cursor.execute("""
    SELECT id, trader, outcome, price, shares, invested, title, condition_id
    FROM paper_positions
    WHERE status = 'OPEN'
""")

positions = cursor.fetchall()
print("Åpne posisjoner:", len(positions))
print("-" * 60)

total_invested = 0.0
current_value_total = 0.0
priced_positions = 0

for position_id, trader, outcome, entry_price, shares, invested, title, condition_id in positions:

    total_invested += invested

    current_price = get_current_price(condition_id, outcome, position_id, title)

    print()
    print("📊 POSISJON")
    print("Marked:", title)
    print("Outcome:", outcome)
    print("Trader:", trader)
    print("Kjøpspris:", f"{entry_price:.4f}")
    print("Investert:", f"{invested:.2f} kr")

    if current_price is not None:
        value = shares * current_price
        profit = value - invested

        current_value_total += value
        priced_positions += 1

        print("Nåværende pris:", f"{current_price:.4f}")
        print("Nåverdi:", f"{value:.2f} kr")
        print("Urealisert resultat:", f"{'+' if profit >= 0 else ''}{profit:.2f} kr")
    else:
        print("⚠️ Nåværende pris ikke tilgjengelig (marked ikke funnet i Gamma eller CLOB).")

print()
print("-" * 60)
print("Totalt investert:", f"{total_invested:.2f} kr")

if priced_positions == len(positions) and positions:
    total_equity = cash + current_value_total
    total_profit = total_equity - PAPER_START_BALANCE

    print("Total paper-verdi:", f"{total_equity:.2f} kr")
    print("📈 Totalt paper-resultat:" if total_profit >= 0 else "📉 Totalt paper-resultat:",
          f"{'+' if total_profit >= 0 else ''}{total_profit:.2f} kr")
elif positions:
    print(f"⚠️ {len(positions) - priced_positions} posisjon(er) mangler pris.")
    print("Total paper-verdi kan ikke beregnes helt nøyaktig ennå.")

cursor.execute("""
    SELECT COUNT(*), COALESCE(SUM(profit), 0)
    FROM paper_positions
    WHERE status = 'CLOSED'
""")
closed_count, closed_profit = cursor.fetchone()

print()
print("🔒 Lukkede posisjoner:", closed_count)

if closed_count > 0:
    print("💵 Realisert resultat:", f"{'+' if closed_profit >= 0 else ''}{closed_profit:.2f} kr")

print("=" * 60)

conn.commit()
conn.close()
