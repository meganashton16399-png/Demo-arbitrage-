import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask
import pandas as pd

# --- 1. SETUP ---
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
            "balance": 10000.0, "pnl": 0.0, "wins": 0, "losses": 0,
            "initial_stake": 50.0, "current_stake": 50.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USD"
        }
    return users[chat_id]

# --- 2. THE MECHANICAL ENGINE ---

def get_indicators(symbol):
    try:
        # Fetch 5m for Bias, 1m for Entry
        tf5 = market.fetch_ohlcv(symbol, timeframe='5m', limit=60)
        tf1 = market.fetch_ohlcv(symbol, timeframe='1m', limit=30)
        ticker = market.fetch_ticker(symbol)
        
        df5 = pd.DataFrame(tf5, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(tf1, columns=['t','o','h','l','c','v'])
        
        # 5m Bias (EMA 20 vs 50)
        ema20_5m = df5['c'].ewm(span=20).mean().iloc[-1]
        ema50_5m = df5['c'].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if ema20_5m > ema50_5m else "SELL"
        
        # 1m Entry Data
        ema9_1m = df1['c'].ewm(span=9).mean().iloc[-1]
        ema21_1m = df1['c'].ewm(span=21).mean().iloc[-1]
        
        # RSI 14
        delta = df1['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs.iloc[-1]))
        
        # Spread Calculation
        spread = ticker['ask'] - ticker['bid']
        spread_pips = (spread / ticker['last']) * 10000 # Rough pip conversion
        
        curr_price = ticker['last']
        pos = "above" if curr_price > ema9_1m else "below" if curr_price < ema21_1m else "inside"
        
        return bias, ema20_5m, ema50_5m, pos, rsi, spread_pips, curr_price, df1.iloc[-2]
    except: return None

# --- 3. THE GROQ BIAS ENGINE ---

def get_ai_sniper_v8(symbol, chat_id):
    data = get_indicators(symbol)
    if not data: return None
    bias, e20, e50, pos, rsi, spread, price, last_c = data
    
    # 1-Line Spread Guard
    if spread > 1.0: return "WAIT", price, f"Spread High ({round(spread,2)})"

    prompt = (
        f"FORCE BIAS: {bias} (EMA20:{round(e20,1)} vs EMA50:{round(e50,1)}). "
        f"1m POSITION: {pos} EMAs. RSI: {round(rsi,1)}. SPREAD: {round(spread,2)}. "
        f"CANDLE: O:{last_c['o']} C:{last_c['c']}. "
        f"TASK: Verify {bias} Setup. Rules: Pullback to EMAs + RSI 40-60 + Candle Confirmation. "
        f"DECIDE: {bias} or WAIT. Format: [SIDE] | [REASON]"
    )

    try:
        response = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
        )
        res = response.choices[0].message.content.strip().upper()
        if "WAIT" in res: return "WAIT", price, "Condition mismatch"
        
        side = "BUY" if "BUY" in res else "SELL"
        reason = res.split("|")[-1] if "|" in res else "Bias Confirmed"
        return side, price, reason
    except: return None

# --- 4. EXECUTION ---

def trade_engine(chat_id):
    u = get_user(chat_id)
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                side, price, reason = get_ai_sniper_v8(u["symbol"], chat_id)
                if not side or side == "WAIT":
                    time.sleep(5)
                    continue
                
                # Scalp: 5-8 Pips
                tp_dist = 6.0 if "BTC" in u["symbol"] else 0.4
                tp = price + tp_dist if side == "BUY" else price - tp_dist
                sl = price - (tp_dist * 2) if side == "BUY" else price + (tp_dist * 2)

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **V8 ENTRY: {side}**\nPrice: {price}\nBias Reason: {reason}")

            else:
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    profit = t['stake'] * 0.15
                    u["balance"] += (t['stake'] + profit)
                    u["pnl"] += profit
                    u["wins"] += 1
                    u["current_stake"] = u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN!** +${round(profit, 2)}\nBalance: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["pnl"] -= t['stake']
                    u["losses"] += 1
                    u["current_stake"] *= 2.0
                    bot.send_message(chat_id, f"❌ **LOSS.** Next Stake: ${u['current_stake']}")
                    u["active_trade"] = None
            
            time.sleep(3)
        except: time.sleep(5)

# --- 5. COMMANDS ---

@bot.message_handler(commands=['status'])
def status(message):
    u = get_user(message.chat.id)
    rate = (u['wins'] / (u['wins'] + u['losses']) * 100) if (u['wins'] + u['losses']) > 0 else 0
    bot.send_message(message.chat.id, f"📊 **V8 MECHANICAL STATUS**\nWin Rate: {round(rate,1)}%\nWins: {u['wins']} | Losses: {u['losses']}\nNext Stake: ${u['current_stake']}")

@bot.message_handler(commands=['trade'])
def start_v8(message):
    m = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    m.add("BTC/USD", "ETH/USD")
    msg = bot.send_message(message.chat.id, "Select Asset:", reply_markup=m)
    bot.register_next_step_handler(msg, lambda m: bot.register_next_step_handler(bot.send_message(m.chat.id, "Base Stake:"), lambda s: launch(m, s)))

def launch(m, s):
    u = get_user(m.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
    bot.send_message(m.chat.id, "🌪️ **MECHANICAL V8 ENGINE LIVE**")
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(commands=['reset'])
def reset_u(message):
    users[message.chat.id] = {"balance": 10000.0, "pnl": 0.0, "wins": 0, "losses": 0, "initial_stake": 50.0, "current_stake": 50.0, "active_trade": None, "is_trading": False, "symbol": "BTC/USD"}
    bot.reply_to(message, "🔄 Reset to $10k.")

@bot.message_handler(commands=['stop'])
def stop(m): get_user(m.chat.id)["is_trading"] = False; bot.send_message(m.chat.id, "🛑 Stopped.")

@app.route('/')
def h(): return "V8 Sniper Online", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    bot.remove_webhook()
    bot.delete_webhook(drop_pending_updates=True)
    bot.polling(non_stop=True)
