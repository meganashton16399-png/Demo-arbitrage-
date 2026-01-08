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
        
        df5 = pd.DataFrame(tf5, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(tf1, columns=['t','o','h','l','c','v'])
        
        # Bias Logic
        e20 = df5['c'].ewm(span=20).mean().iloc[-1]
        e50 = df5['c'].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if e20 > e50 else "SELL"
        
        # Entry RSI (14)
        delta = df1['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain / loss).iloc[-1]))
        
        return bias, rsi, ticker['last'], df1.iloc[-2].to_dict()
    except Exception as e:
        print(f"Data Error: {e}")
        return None

# --- 3. ZERO-HESITATION AI ---
def get_ai_v12(symbol, chat_id):
    data = get_market_data(symbol, chat_id)
    if not data: return None, None, "Data Error", 0
    bias, rsi, price, last_c = data

    prompt = (
        f"ASSET: {symbol}. BIAS: {bias}. RSI: {round(rsi,1)}. PRICE: {price}. "
        f"RULES: Scalp 10 pips. If RSI matches {bias} trend, ENTER. "
        f"DECIDE: [SIDE] | [SCORE 0-100] | [REASON 5 WORDS]"
    )

    try:
        res = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
        ).choices[0].message.content.strip().upper()
        
        parts = res.split("|")
        side = "BUY" if "BUY" in parts[0] else "SELL"
        score = int(''.join(filter(str.isdigit, parts[1]))) if len(parts) > 1 else 0
        reason = parts[2].strip() if len(parts) > 2 else "Trend Follow"
        
        if score < 40: return "SKIP", price, "Low Confidence", score
        return side, price, reason, score
    except:
        return None, None, "AI Busy", 0

# --- 4. 100% RECOVERY ENGINE ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, f"🚀 **V12 DEBT-KILLER ONLINE**\nAsset: {u['symbol']}")
    
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                side, price, reason, score = get_ai_v12(u["symbol"], chat_id)
                if not side or side == "SKIP":
                    time.sleep(5); continue

                # Pips Logic (BTC: 10, ETH: 0.8, XAU: 1.0)
                if "BTC" in u["symbol"]: tp_dist = 10.0
                elif "ETH" in u["symbol"]: tp_dist = 0.8
                elif "XAU" in u["symbol"]: tp_dist = 1.0  # 1.0 Point = 10 Gold Pips
                else: tp_dist = 0.5

                tp = price + tp_dist if side == "BUY" else price - tp_dist
                sl = price - tp_dist if side == "BUY" else price + tp_dist

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **FIRED {side} ({score}%)**\nEntry: {price} -> Target: {round(tp, 2)}\nReason: {reason}")

            else:
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    # 100% RECOVERY MATH: Profit covers everything lost
                    # Profit = Stake (1:1 RR)
                    profit = t['stake'] 
                    u["balance"] += (t['stake'] + profit)
                    u["wins"] += 1
                    u["total_lost"] = 0
                    u["current_stake"] = u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN!** 100% Recovery Done.\nBalance: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["total_lost"] += t['stake']
                    u["losses"] += 1
                    # Formula for 100% Recovery + Net Profit
                    u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.1 
                    bot.send_message(chat_id, f"❌ **LOSS.** Debt: ${round(u['total_lost'], 2)}\nNext Stake: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(3)
        except Exception as e:
            time.sleep(10)

# --- 5. COMMAND HANDLERS ---
@bot.message_handler(commands=['trade'])
def trade_init(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("XAU/USD", "BTC/USD", "ETH/USD")
    msg = bot.send_message(m.chat.id, "Select Asset:", reply_markup=kb)
    bot.register_next_step_handler(msg, lambda msg: bot.register_next_step_handler(bot.send_message(m.chat.id, "Base Stake:"), lambda s: start_v12(msg, s)))

def start_v12(m, s):
    u = get_user(m.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(commands=['status'])
def report(m):
    u = get_user(m.chat.id)
    bot.send_message(m.chat.id, f"📊 **V12 RECOVERY REPORT**\nBalance: ${round(u['balance'],2)}\nWins/Losses: {u['wins']}W - {u['losses']}L\nTotal Debt: ${round(u['total_lost'],2)}")

@bot.message_handler(commands=['reset', 'stop', 'check'])
def utils(m):
    if 'reset' in m.text:
        users[m.chat.id] = {"balance": 10000.0, "total_lost": 0.0, "wins": 0, "losses": 0, "initial_stake": 50.0, "current_stake": 50.0, "active_trade": None, "is_trading": False}
        bot.reply_to(m, "🔄 Wallet Reset to $10k.")
    elif 'stop' in m.text:
        get_user(m.chat.id)["is_trading"] = False; bot.reply_to(m, "🛑 Stopping...")
    elif 'check' in m.text:
        p = market.fetch_ticker('XAU/USD')['last']
        bot.reply_to(m, f"✅ Kraken & AI Linked. Gold Price: ${p}")

@app.route('/')
def home(): return "V12 Absolute Recovery Online", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    bot.remove_webhook()
    bot.delete_webhook(drop_pending_updates=True)
    bot.polling(non_stop=True)
