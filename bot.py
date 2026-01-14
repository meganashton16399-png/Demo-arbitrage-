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
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
BINANCE_KEY = os.environ.get("BINANCE_KEY")
BINANCE_SECRET = os.environ.get("BINANCE_SECRET")

# Initialize Bots
bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)

# Trading Settings
SCORE_THRESHOLD = 50
SCAN_SLEEP_SECONDS = 30
ACTIVE_TRADE_POLL = 2
STAKE_MULTIPLIER = 2.2
MAX_STAKE = 5000
TP_PIPS = 10
SL_PIPS = 5

def cooldown_seconds(loss_streak: int) -> int:
    if loss_streak < 2: return 0
    return (loss_streak - 1) * 5 * 60

# =========================
# BINANCE SETUP (FIXED)
# =========================
market = ccxt.binanceusdm({
    "enableRateLimit": True,
    "apiKey": BINANCE_KEY,
    "secret": BINANCE_SECRET,
    "options": {
        "defaultType": "future",
    }
})

# ENABLE TESTNET CORRECTLY
market.set_sandbox_mode(True) 

# Load markets once at startup to cache precision data
try:
    market.load_markets()
    print("✅ Binance Testnet Markets Loaded")
except Exception as e:
    print(f"❌ Error loading markets: {e}")

# =========================
# USER STATE
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
        # Fetch fewer candles to be faster, but enough for calculation
        tf5 = market.fetch_ohlcv(symbol, timeframe="5m", limit=50)
        tf1 = market.fetch_ohlcv(symbol, timeframe="1m", limit=50)
        ticker = market.fetch_ticker(symbol)

        if not tf5 or not tf1:
            return None

        df5 = pd.DataFrame(tf5, columns=["t", "o", "h", "l", "c", "v"])
        df1 = pd.DataFrame(tf1, columns=["t", "o", "h", "l", "c", "v"])

        e20 = df5["c"].ewm(span=20).mean().iloc[-1]
        e50 = df5["c"].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if e20 > e50 else "SELL"

        delta = df1["c"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = (gain / loss).iloc[-1]
        
        # Handle division by zero or NaN
        if pd.isna(rs):
            rsi = 50.0
        else:
            rsi = 100 - (100 / (1 + rs))

        return bias, float(rsi), float(ticker["last"])
    except Exception as e:
        print(f"[Market Error] {e}")
        return None

# =========================
# AI SCORING
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
# ORDER HELPERS (FIXED)
# =========================
def place_entry(symbol: str, side: str, stake_usdt: float, chat_id: int):
    try:
        price = float(market.fetch_ticker(symbol)["last"])
        
        # Calculate amount based on stake
        raw_amount = stake_usdt / price
        
        # SAFE PRECISION CONVERSION
        # amount_to_precision converts the float to a string formatted exactly for Binance
        qty_str = market.amount_to_precision(symbol, raw_amount)
        qty = float(qty_str) # Convert back to float for logic, though ccxt accepts strings

        # side: BUY opens long, SELL opens short
        order = market.create_order(symbol, "market", side.lower(), qty)

        order_id = order.get("id")
        filled = float(order.get("filled") or 0.0)
        avg = float(order.get("average") or 0.0)

        # Handle cases where filled/avg aren't immediately available
        if filled == 0.0: filled = qty
        if avg == 0.0: avg = price

        bot.send_message(chat_id, f"🟢 ENTRY {side} (Futures)\nQty: {filled}\nAvg: {avg}")
        return {"order_id": order_id, "amount": filled, "avg_price": avg}
    
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Order Failed: {e}")
        raise e # Re-raise to trigger the exception handler in the loop

def place_exit(symbol: str, entry_side: str, amount: float, chat_id: int):
    try:
        exit_side = "SELL" if entry_side == "BUY" else "BUY"
        # ReduceOnly is safer for closing
        params = {"reduceOnly": True}
        order = market.create_order(symbol, "market", exit_side.lower(), amount, params=params)
        bot.send_message(chat_id, f"🔴 EXIT {exit_side} (closed)")
        return order
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Exit Failed: {e}")
        return None

def compute_tp_sl(symbol: str, entry_side: str, entry_price: float):
    # Fetch tick size safely
    try:
        tick = market.market(symbol)['precision']['price']
    except:
        tick = 0.01

    tp_move = TP_PIPS * tick * 10 # Adjust multiplier as needed for "Pips" vs "Ticks"
    sl_move = SL_PIPS * tick * 10

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
    
    # Try setting leverage
    try:
        market.set_leverage(1, u["symbol"])
    except:
        pass

    while u["is_trading"]:
        try:
            now = time.time()

            # Cooldown check
            if now < u["next_trade_time"]:
                remaining = int(u["next_trade_time"] - now)
                # Sleep in small chunks to allow interrupting
                time.sleep(min(30, remaining)) 
                continue

            # --- SCANNING PHASE ---
            if u["active_trade"] is None:
                data = get_market_data(u["symbol"])
                if not data:
                    time.sleep(SCAN_SLEEP_SECONDS)
                    continue

                bias, rsi, last_price = data

                score = groq_score(u["symbol"], bias, rsi)
                if score < SCORE_THRESHOLD:
                    # Optional: Comment out to reduce spam
                    # bot.send_message(chat_id, f"🔎 Score {score}. Waiting...") 
                    time.sleep(SCAN_SLEEP_SECONDS)
                    continue

                # Stake Management
                if u["current_stake"] > MAX_STAKE:
                    bot.send_message(chat_id, f"🛑 Stake capped. Reset to base {u['initial_stake']}.")
                    u["current_stake"] = u["initial_stake"]

                side = "BUY" if bias == "BUY" else "SELL"

                # Place Order
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

            # --- MANAGING PHASE ---
            else:
                t = u["active_trade"]
                ticker = market.fetch_ticker(u["symbol"])
                curr = float(ticker["last"])

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
                    if cd > 0: msg += f"\n😴 Rest: {int(cd/60)} min"
                    bot.send_message(chat_id, msg)

                time.sleep(ACTIVE_TRADE_POLL)

        except Exception as e:
            # THIS IS KEY: Send the error to Telegram so you see why it died
            bot.send_message(chat_id, f"⚠️ CRITICAL ENGINE ERROR: {e}\nRetrying in 10s...")
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
    chat_id = sym_msg.chat.id
    u = get_user(chat_id)
    
    # Check if already trading to prevent double threads
    if u["is_trading"]:
        bot.send_message(chat_id, "⚠️ Trading already active. Use /stop first.")
        return

    try:
        stake = float(stake_msg.text)
        if stake <= 0: raise ValueError()
    except:
        bot.send_message(chat_id, "❌ Invalid stake. Run /trade again.")
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

    # Start thread
    t = threading.Thread(target=trade_engine, args=(chat_id,), daemon=True)
    t.start()
    bot.send_message(chat_id, f"✅ Started FUTURES on {u['symbol']} | Base stake {stake}")

@bot.message_handler(commands=["check"])
def check(m):
    try:
        # Simple fetch to verify connection
        p = market.fetch_ticker("BTC/USDT")["last"]
        bot.reply_to(m, f"✅ Futures Testnet OK\nBTC/USDT: {p}")
    except Exception as e:
        bot.reply_to(m, f"❌ Check failed: {e}")

@bot.message_handler(commands=["stop"])
def stop(m):
    u = get_user(m.chat.id)
    u["is_trading"] = False
    bot.reply_to(m, "🛑 Stopping engine...")

@bot.message_handler(commands=["status"])
def status(m):
    u = get_user(m.chat.id)
    bot.reply_to(m, f"Trading: {u['is_trading']}\nStreak: {u['loss_streak']}\nStake: {u['current_stake']}")

# =========================
# FLASK KEEP-ALIVE
# =========================
@app.route("/")
def home():
    return "Futures Bot Active", 200

if __name__ == "__main__":
    # Start Flask in thread
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))),
        daemon=True
    ).start()

    bot.remove_webhook()
    bot.polling(non_stop=True)
