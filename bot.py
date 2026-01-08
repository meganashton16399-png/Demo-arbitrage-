import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask
import pandas as pd

# --- 1. CONFIG ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)
market = ccxt.kraken({'enableRateLimit': True})

users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 10000.0, "total_lost": 0.0, "wins": 0, "losses": 0,
            "initial_stake": 50.0, "current_stake": 50.0,
            "active_trade": None, "is_trading": False, "symbol": "XAU/USD"
        }
    return users[chat_id]

# --- 2. DATA ENGINE ---
def get_market_data(symbol, chat_id):
    try:
        tf5 = market.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        tf1 = market.fetch_ohlcv(symbol, timeframe='1m', limit=30)
        ticker = market.fetch_ticker(symbol)
        
        if not tf5 or not tf1: return None

        df5 = pd.DataFrame(tf5, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(tf1, columns=['t','o','h','l','c','v'])
        
        e20 = df5['c'].ewm(span=20).mean().iloc[-1]
        e50 = df5['c'].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if e20 > e50 else "SELL"
        
        delta = df1['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs.iloc[-1]))
        
        return bias, rsi, ticker['last'], df1.iloc[-2].to_dict()
    except Exception as e:
        print(f"Data Fetch Error: {e}")
        return None

# --- 3. AI ENGINE ---
def get_ai_v12(symbol, chat_id):
    data = get_market_data(symbol, chat_id)
    if not data: return None, None, "Data Error", 0
    bias, rsi, price, last_c = data

    prompt = (
        f"ASSET: {symbol}. BIAS: {bias}. RSI: {round(rsi,1)}. PRICE: {price}. "
        f"RULES: 10 pip scalp. If RSI matches {bias}, ENTER. "
        f"DECIDE: [SIDE/SKIP] | [SCORE] | [REASON 5 WORDS]"
    )

    try:
        res = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
        ).choices[0].message.content.strip().upper()
        
        parts = res.split("|")
        side = parts[0].strip()
        score = int(''.join(filter(str.isdigit, parts[1]))) if len(parts) > 1 else 0
        reason = parts[2].strip() if len(parts) > 2 else "Analysis"
        
        if score < 40 or "SKIP" in side: return "SKIP", price, reason, score
        return side, price, reason, score
    except: return None, None, "AI Error", 0

# --- 4. ENGINE WITH PULSE ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, f"🚀 **V12.1 DEBT-KILLER STARTED**\nMonitoring {u['symbol']}...")
    
    last_pulse = time.time()

    while u["is_trading"]:
        try:
            # Send a "Pulse" every 2 minutes so user knows it's not stuck
            if time.time() - last_pulse > 120:
                bot.send_message(chat_id, "📡 Engine scanning market... No perfect setup yet.")
                last_pulse = time.time()

            if u["active_trade"] is None:
                side, price, reason, score = get_ai_v12(u["symbol"], chat_id)
                if not side or side == "SKIP":
                    time.sleep(10); continue

                # Pip Math
                tp_dist = 1.0 if "XAU" in u["symbol"] else 10.0 if "BTC" in u["symbol"] else 0.8
                tp = price + tp_dist if side == "BUY" else price - tp_dist
                sl = price - tp_dist if side == "BUY" else price + tp_dist

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **SNIPER FIRED {side} ({score}%)**\nTarget: {round(tp, 2)}\nReason: {reason}")
                last_pulse = time.time()

            else:
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    profit = t['stake']
                    u["balance"] += (t['stake'] + profit)
                    u["wins"] += 1
                    u["total_lost"], u["current_stake"] = 0, u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN!** Balance: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["total_lost"] += t['stake']
                    u["losses"] += 1
                    # Absolute Recovery Formula
                    # $NextStake = (TotalLost + InitialStake) \times 1.1$
                    u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.1 
                    bot.send_message(chat_id, f"❌ **LOSS.** Next Stake: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(5)
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ **ENGINE ALERT:** {str(e)[:50]}... Retrying.")
            time.sleep(15)

# --- 5. UPDATED COMMANDS ---
@bot.message_handler(commands=['help', 'start'])
def help_menu(m):
    text = (
        "🤖 **V12.1 DEBT-KILLER SYSTEM**\n\n"
        "🚀 /trade - Start Engine\n"
        "🛑 /stop - Stop Engine\n"
        "📊 /status - View Performance\n"
        "🔄 /reset - Reset $10k Balance\n"
        "🛠 /check - API Health Test"
    )
    bot.reply_to(m, text)

@bot.message_handler(commands=['status'])
def status(m):
    u = get_user(m.chat.id)
    bot.send_message(m.chat.id, f"📊 **STATS**\nBal: ${round(u['balance'],2)}\nWins: {u['wins']} | Losses: {u['losses']}\nDebt: ${round(u['total_lost'],2)}")

@bot.message_handler(commands=['check'])
def check(m):
    try:
        p = market.fetch_ticker('XAU/USD')['last']
        bot.reply_to(m, f"✅ Kraken Connected. Gold: ${p}")
    except Exception as e: bot.reply_to(m, f"❌ Error: {e}")

@bot.message_handler(commands=['reset'])
def reset(m):
    users[m.chat.id] = {"balance": 10000.0, "total_lost": 0.0, "wins": 0, "losses": 0, "initial_stake": 50.0, "current_stake": 50.0, "active_trade": None, "is_trading": False}
    bot.reply_to(m, "🔄 Balance reset to $10,000.")

@bot.message_handler(commands=['stop'])
def stop(m):
    u = get_user(m.chat.id)
    u["is_trading"] = False
    bot.reply_to(m, "🛑 Engine Stopped.")

@bot.message_handler(commands=['trade'])
def trade_init(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("XAU/USD", "BTC/USD")
    msg = bot.send_message(m.chat.id, "Select Asset:", reply_markup=kb)
    bot.register_next_step_handler(msg, lambda msg: bot.register_next_step_handler(bot.send_message(m.chat.id, "Stake:"), lambda s: launch(msg, s)))

def launch(m, s):
    u = get_user(m.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@app.route('/')
def h(): return "V12.1 Pulse Active", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    bot.remove_webhook()
    bot.delete_webhook(drop_pending_updates=True)
    bot.polling(non_stop=True)
