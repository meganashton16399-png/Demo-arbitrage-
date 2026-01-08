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
            "active_trade": None, "is_trading": False, "symbol": "PAXG/USD"
        }
    return users[chat_id]

# --- 2. DATA ENGINE (SYMBOL FIX) ---
def get_market_data(symbol, chat_id):
    try:
        # Kraken ke symbols load karna zaroori hai
        market.load_markets()
        
        tf5 = market.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        tf1 = market.fetch_ohlcv(symbol, timeframe='1m', limit=30)
        ticker = market.fetch_ticker(symbol)
        
        df5 = pd.DataFrame(tf5, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(tf1, columns=['t','o','h','l','c','v'])
        
        e20 = df5['c'].ewm(span=20).mean().iloc[-1]
        e50 = df5['c'].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if e20 > e50 else "SELL"
        
        delta = df1['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain / loss).iloc[-1]))
        
        return bias, rsi, ticker['last'], df1.iloc[-2].to_dict()
    except Exception as e:
        print(f"Market Error: {e}")
        return None

# --- 3. THE RECOVERY ENGINE ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, f"🚀 **V12.2 PAX-GOLD SNIPER START**\nMonitoring: {u['symbol']}")
    
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                # AI Logic call
                data = get_market_data(u["symbol"], chat_id)
                if not data:
                    time.sleep(10); continue
                
                bias, rsi, price, last_c = data
                
                # AI Decision (Direct Prompt)
                res = groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": f"ASSET: {u['symbol']}. BIAS: {bias}. RSI: {rsi}. Rate 0-100. [SIDE] | [SCORE]"}],
                    model="llama-3.3-70b-versatile",
                ).choices[0].message.content.strip().upper()
                
                side = "BUY" if "BUY" in res else "SELL"
                score = int(''.join(filter(str.isdigit, res))) if any(i.isdigit() for i in res) else 0

                if score < 40:
                    time.sleep(10); continue

                # Pip Math (PAXG/USD treats 1.0 as Gold Point)
                tp_dist = 1.0 if "PAXG" in u["symbol"] else 10.0 if "BTC" in u["symbol"] else 0.8
                tp = price + tp_dist if side == "BUY" else price - tp_dist
                sl = price - tp_dist if side == "BUY" else price + tp_dist

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **ORDER FIRED {side}**\nTarget: {round(tp, 2)}")

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
                    u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.15
                    bot.send_message(chat_id, f"❌ **LOSS.** Next Stake: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(5)
        except Exception as e:
            time.sleep(10)

# --- 4. COMMANDS ---
@bot.message_handler(commands=['trade'])
def trade_init(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    # Changed to PAXG/USD for Gold Stability
    kb.add("PAXG/USD", "BTC/USD", "ETH/USD")
    msg = bot.send_message(m.chat.id, "Select Asset (Gold = PAXG):", reply_markup=kb)
    bot.register_next_step_handler(msg, lambda msg: bot.register_next_step_handler(bot.send_message(m.chat.id, "Stake:"), lambda s: launch(msg, s)))

def launch(m, s):
    u = get_user(m.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(commands=['status', 'reset', 'stop', 'check', 'help'])
def utils(m):
    if 'check' in m.text:
        try:
            p = market.fetch_ticker('PAXG/USD')['last']
            bot.reply_to(m, f"✅ PAXG (Gold) Connected. Price: ${p}")
        except Exception as e: bot.reply_to(m, f"❌ Symbol Error: {e}")
    elif 'reset' in m.text:
        users[m.chat.id] = {"balance": 10000.0, "total_lost": 0.0, "wins": 0, "losses": 0, "initial_stake": 50.0, "current_stake": 50.0, "active_trade": None, "is_trading": False}
        bot.reply_to(m, "🔄 Reset Done.")
    elif 'stop' in m.text:
        get_user(m.chat.id)["is_trading"] = False; bot.reply_to(m, "🛑 Stopped.")
    else: bot.reply_to(m, "/trade, /status, /check, /reset, /stop")

@app.route('/')
def home(): return "PAX-Gold Sniper Active", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    bot.remove_webhook()
    bot.delete_webhook(drop_pending_updates=True)
    bot.polling(non_stop=True)
