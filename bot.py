import os
import time
import threading
import math
import telebot
from telebot import types
import ccxt
import pandas as pd
from groq import Groq
from flask import Flask

# =========================
# CONFIG
# =========================
TELE_TOKEN = os.environ["BOT_TOKEN"]
GROQ_KEY = os.environ["GROQ_API_KEY"]
BINANCE_KEY = os.environ["BINANCE_KEY"]
BINANCE_SECRET = os.environ["BINANCE_SECRET"]

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)

# Trading rules
SCORE_THRESHOLD = 50
SCAN_SLEEP_SECONDS = 30          # when no trade taken / low score
ACTIVE_TRADE_POLL = 2           # check TP/SL faster for quick scalps

STAKE_MULTIPLIER = 2.2          # 50 -> 110
MAX_STAKE = 5000                # safety cap (edit anytime)

TP_PIPS = 10                    # profit target in "ticks"
SL_PIPS = 5                     # stop in "ticks"

def cooldown_seconds(loss_streak: int) -> int:
    # 2 losses -> 5m, 3 -> 10m, 4 -> 15m...
    if loss_streak < 2:
        return 0
    return (loss_streak - 1) * 5 * 60

# =========================
# BINANCE USD-M FUTURES TESTNET (CCXT)
# =========================
# Binance docs: testnet REST base url = https://demo-fapi.binance.com  [oai_citation:2‡Binance Developer Center](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info?utm_source=chatgpt.com)
market = ccxt.binanceusdm({
    "enableRateLimit": True,
    "apiKey": BINANCE_KEY,
    "secret": BINANCE_SECRET,
    "options": {
        "defaultType": "future",
    }
})

# Force testnet endpoints
market.urls["api"] = {
    "public": "https://demo-fapi.binance.com",
    "private": "https://demo-fapi.binance.com",
}

# =========================
# USER STATE (IN MEMORY)
# =========================
users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "wins": 0,
            "losses": 0,
            "loss_streak": 0,

            "initial_stake": 50.0,
            "current_stake": 50.0,

            "active_trade": None,
            "is_trading": False,

            "symbol": "BTC/USDT",
            "next_trade_time": 0.0,
        }
    return users[chat_id]

# =========================
# INDICATORS
# =========================
def get_market_data(symbol):
    try:
        tf5 = market.fetch_ohlcv(symbol, timeframe="5m", limit=80)
        tf1 = market.fetch_ohlcv(symbol, timeframe="1m", limit=50)
        ticker = market.fetch_ticker(symbol)

        df5 = pd.DataFrame(tf5, columns=["t", "o", "h", "l", "c", "v"])
        df1 = pd.DataFrame(tf1, columns=["t", "o", "h", "l", "c", "v"])

        e20 = df5["c"].ewm(span=20).mean().iloc[-1]
        e50 = df5["c"].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if e20 > e50 else "SELL"

        delta = df1["c"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = (gain / loss).iloc[-1]
        rsi = 100 - (100 / (1 + rs)) if rs and not math.isnan(rs) else 50.0

        return bias, float(rsi), float(ticker["last"])
    except Exception as e:
        print(f"[Market Error] {e}")
        return None

# =========================
# GROQ SCORING (STRICT SCORE)
# =========================
def groq_score(symbol: str, bias: str, rsi: float) -> int:
    prompt = (
        f"ASSET: {symbol}\n"
        f"BIAS (EMA20>EMA50 ?): {bias}\n"
        f"RSI(1m): {rsi:.2f}\n\n"
        f"Return ONLY:\n"
        f"SCORE: <0-100>\n"
        f"Give 50+ only if trade is high probability for a quick scalp."
    )
    try:
        res = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0,
            max_tokens=20,
        ).choices[0].message.content.strip().upper()

        digits = "".join([c for c in res if c.isdigit()])
        score = int(digits) if digits else 0
        return max(0, min(100, score))
    except Exception as e:
        print(f"[Groq Error] {e}")
        return 0

# =========================
# FUTURES HELPERS (AMOUNT, TICK SIZE, ORDERS)
# =========================
def floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step

def get_symbol_filters(symbol: str):
    market.load_markets()
    m = market.market(symbol)
    info = m.get("info", {})
    filters = info.get("filters", []) if isinstance(info, dict) else []

    tick = None
    step = None
    min_qty = None

    for f in filters:
        if f.get("filterType") == "PRICE_FILTER":
            try: tick = float(f.get("tickSize"))
            except: pass
        if f.get("filterType") == "LOT_SIZE":
            try:
                step = float(f.get("stepSize"))
                min_qty = float(f.get("minQty"))
            except:
                pass

    # Fallbacks if filters missing
    if tick is None:
        tick = 0.1
    if step is None:
        # try ccxt precision
        prec = m.get("precision", {}).get("amount", None)
        if prec is not None:
            step = 10 ** (-prec)
        else:
            step = 0.001
    if min_qty is None:
        min_qty = m.get("limits", {}).get("amount", {}).get("min", 0.0) or 0.0

    return float(tick), float(step), float(min_qty)

def amount_from_stake(symbol: str, stake_usdt: float, price: float) -> float:
    tick, step, min_qty = get_symbol_filters(symbol)
    qty = stake_usdt / price

    qty = floor_to_step(qty, step)
    if qty < min_qty:
        qty = min_qty
    return float(qty)

def set_oneway_mode_and_leverage(symbol: str, leverage: int = 1):
    # Try to set 1-way mode and leverage (ignore if blocked on demo)
    try:
        market.set_position_mode(False)   # False = One-way
    except Exception:
        pass
    try:
        market.set_leverage(leverage, symbol)
    except Exception:
        pass

def place_entry(symbol: str, side: str, stake_usdt: float, chat_id: int):
    price = float(market.fetch_ticker(symbol)["last"])
    qty = amount_from_stake(symbol, stake_usdt, price)

    if qty <= 0:
        raise Exception("Qty <= 0 (check stake/minQty).")

    # side: BUY opens long, SELL opens short (futures)
    order = market.create_order(symbol, "market", side.lower(), qty)

    order_id = order.get("id")
    filled = float(order.get("filled") or 0.0)
    avg = float(order.get("average") or 0.0)

    if order_id and (avg == 0.0 or filled == 0.0):
        try:
            o2 = market.fetch_order(order_id, symbol)
            filled = float(o2.get("filled") or filled)
            avg = float(o2.get("average") or avg)
        except Exception:
            pass

    if avg == 0.0:
        avg = price
    if filled == 0.0:
        filled = qty

    bot.send_message(chat_id, f"🟢 ENTRY {side} (Futures)\nQty: {filled}\nAvg: {avg}")
    return {"order_id": order_id, "amount": filled, "avg_price": avg}

def place_exit(symbol: str, entry_side: str, amount: float, chat_id: int):
    # If entry was BUY (long), exit is SELL. If entry was SELL (short), exit is BUY.
    exit_side = "SELL" if entry_side == "BUY" else "BUY"
    order = market.create_order(symbol, "market", exit_side.lower(), amount)
    bot.send_message(chat_id, f"🔴 EXIT {exit_side} (closed)")
    return order

def compute_tp_sl(symbol: str, entry_side: str, entry_price: float):
    tick, _, _ = get_symbol_filters(symbol)
    tp_move = TP_PIPS * tick
    sl_move = SL_PIPS * tick

    if entry_side == "BUY":
        tp = entry_price + tp_move
        sl = entry_price - sl_move
    else:
        tp = entry_price - tp_move
        sl = entry_price + sl_move

    return float(tp), float(sl)

# =========================
# ENGINE
# =========================
def trade_engine(chat_id: int):
    u = get_user(chat_id)
    bot.send_message(chat_id, f"🚀 FUTURES TESTNET START\nMonitoring: {u['symbol']}")
    set_oneway_mode_and_leverage(u["symbol"], leverage=1)

    while u["is_trading"]:
        try:
            now = time.time()

            # cooldown gate
            if now < u["next_trade_time"]:
                remaining = int(u["next_trade_time"] - now)
                time.sleep(min(30, remaining))
                continue

            # scan if no active trade
            if u["active_trade"] is None:
                data = get_market_data(u["symbol"])
                if not data:
                    time.sleep(SCAN_SLEEP_SECONDS)
                    continue

                bias, rsi, last_price = data

                score = groq_score(u["symbol"], bias, rsi)
                if score < SCORE_THRESHOLD:
                    bot.send_message(chat_id, f"🔎 No trade (Score {score}<{SCORE_THRESHOLD}). Re-scan in {SCAN_SLEEP_SECONDS}s")
                    time.sleep(SCAN_SLEEP_SECONDS)
                    continue

                # stake cap
                if u["current_stake"] > MAX_STAKE:
                    bot.send_message(chat_id, f"🛑 Stake capped. Reset to base {u['initial_stake']}.")
                    u["current_stake"] = u["initial_stake"]

                # Futures: bias decides side
                side = "BUY" if bias == "BUY" else "SELL"

                entry = place_entry(u["symbol"], side, u["current_stake"], chat_id)
                entry_price = entry["avg_price"]
                amount = entry["amount"]

                tp, sl = compute_tp_sl(u["symbol"], side, entry_price)

                u["active_trade"] = {
                    "side": side,
                    "entry_price": entry_price,
                    "amount": amount,
                    "tp": tp,
                    "sl": sl,
                    "stake": u["current_stake"],
                }

                bot.send_message(chat_id, f"🎯 OPEN {side}\nEntry: {entry_price}\nTP: {tp}\nSL: {sl}\nStake: {u['current_stake']}")

            else:
                t = u["active_trade"]
                curr = float(market.fetch_ticker(u["symbol"])["last"])

                win = (t["side"] == "BUY" and curr >= t["tp"]) or (t["side"] == "SELL" and curr <= t["tp"])
                loss = (t["side"] == "BUY" and curr <= t["sl"]) or (t["side"] == "SELL" and curr >= t["sl"])

                if win:
                    place_exit(u["symbol"], t["side"], t["amount"], chat_id)

                    u["wins"] += 1
                    u["loss_streak"] = 0
                    u["current_stake"] = u["initial_stake"]
                    u["active_trade"] = None
                    u["next_trade_time"] = 0.0

                    bot.send_message(chat_id, f"✅ WIN. Stake reset to {u['initial_stake']}")

                elif loss:
                    place_exit(u["symbol"], t["side"], t["amount"], chat_id)

                    u["losses"] += 1
                    u["loss_streak"] += 1
                    u["current_stake"] = min(MAX_STAKE, u["current_stake"] * STAKE_MULTIPLIER)

                    cd = cooldown_seconds(u["loss_streak"])
                    u["next_trade_time"] = time.time() + cd if cd > 0 else 0.0

                    u["active_trade"] = None

                    msg = f"❌ LOSS. Next stake: {round(u['current_stake'], 2)}"
                    if cd > 0:
                        msg += f"\n😴 Rest: {int(cd/60)} min (streak {u['loss_streak']})"
                    bot.send_message(chat_id, msg)

                time.sleep(ACTIVE_TRADE_POLL)

        except Exception as e:
            print(f"[Engine Error] {e}")
            time.sleep(10)

# =========================
# COMMANDS
# =========================
@bot.message_handler(commands=["trade"])
def trade_init(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT")
    msg = bot.send_message(m.chat.id, "Select Futures Symbol:", reply_markup=kb)

    def ask_stake(sym_msg):
        msg2 = bot.send_message(m.chat.id, "Base Stake (USDT):")
        bot.register_next_step_handler(msg2, lambda s: launch(sym_msg, s))

    bot.register_next_step_handler(msg, ask_stake)

def launch(sym_msg, stake_msg):
    u = get_user(sym_msg.chat.id)
    try:
        stake = float(stake_msg.text)
        if stake <= 0:
            raise ValueError()
    except Exception:
        bot.send_message(sym_msg.chat.id, "❌ Invalid stake. Run /trade again.")
        return

    u["symbol"] = sym_msg.text.strip().upper()
    u["initial_stake"] = stake
    u["current_stake"] = stake
    u["wins"] = 0
    u["losses"] = 0
    u["loss_streak"] = 0
    u["active_trade"] = None
    u["next_trade_time"] = 0.0
    u["is_trading"] = True

    set_oneway_mode_and_leverage(u["symbol"], leverage=1)
    threading.Thread(target=trade_engine, args=(sym_msg.chat.id,), daemon=True).start()
    bot.send_message(sym_msg.chat.id, f"✅ Started FUTURES on {u['symbol']} | Base stake {stake}")

@bot.message_handler(commands=["check", "status", "reset", "stop", "help"])
def utils(m):
    u = get_user(m.chat.id)

    if "check" in m.text:
        try:
            p = market.fetch_ticker("BTC/USDT")["last"]
            bot.reply_to(m, f"✅ Futures Testnet OK\nBTC/USDT: {p}")
        except Exception as e:
            bot.reply_to(m, f"❌ Check failed: {e}")

    elif "status" in m.text:
        cd = max(0, int(u["next_trade_time"] - time.time()))
        bot.reply_to(
            m,
            f"📊 Status\nSymbol: {u['symbol']}\nTrading: {u['is_trading']}\n"
            f"Wins: {u['wins']} | Losses: {u['losses']} | Streak: {u['loss_streak']}\n"
            f"Base: {u['initial_stake']} | Current: {round(u['current_stake'], 2)}\n"
            f"Cooldown: {cd}s\nActive: {u['active_trade']}"
        )

    elif "reset" in m.text:
        users[m.chat.id] = {
            "wins": 0, "losses": 0, "loss_streak": 0,
            "initial_stake": 50.0, "current_stake": 50.0,
            "active_trade": None, "is_trading": False,
            "symbol": "BTC/USDT", "next_trade_time": 0.0
        }
        bot.reply_to(m, "🔄 Reset done.")

    elif "stop" in m.text:
        u["is_trading"] = False
        bot.reply_to(m, "🛑 Stopped.")

    else:
        bot.reply_to(m, "/trade, /status, /check, /reset, /stop")

# =========================
# FLASK
# =========================
@app.route("/")
def home():
    return "Futures Testnet Sniper Active", 200

if __name__ == "__main__":
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))),
        daemon=True
    ).start()

    bot.remove_webhook()
    bot.delete_webhook(drop_pending_updates=True)
    bot.polling(non_stop=True)