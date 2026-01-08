import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask

# --- 1. CONFIGURATION ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)

# Switch to Kraken (Reliable on Render/US IPs)
market = ccxt.kraken({'enableRateLimit': True}) 

# --- 2. UNIVERSAL STORAGE ---
users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 10000.0, "initial_stake": 10.0, "current_stake": 10.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USD"
        }
    return users[chat_id]

# --- 3. INDICATOR ENGINE ---

def get_rsi(prices, period=14):
    if len(prices) < period: return 50
    gains = [max(prices[i] - prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1] - prices[i], 0) for i in range(1, len(prices))]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def get_ai_signal(symbol, chat_id):
    try:
        # Fetching data from Kraken instead of Binance
        ohlcv = market.fetch_ohlcv(symbol, timeframe='1m', limit=20)
        c = ohlcv[-2] # Last closed candle
        o, h, l, cl, v = c[1], c[2], c[3], c[4], c[5]
        
        body = abs(cl - o)
        total_range = h - l
        u_wick = h - max(o, cl)
        l_wick = min(o, cl) - l
        rsi = get_rsi([x[4] for x in ohlcv])
        
        if body <= 0.4 * total_range: c_type = "REJECTION"
        elif body >= 0.6 * total_range: c_type = "MOMENTUM"
        else: c_type = "NEUTRAL"

        prompt = (
            f"Asset: {symbol}. O:{o}, H:{h}, L:{l}, C:{cl}. "
            f"Type: {c_type}, RSI: {round(rsi,1)}, Vol: {v}. "
            f"Rules: Rejection+HighWick=Sell, Momentum Bullish=Sell (Pullback). "
            f"Respond with ONLY 'BUY' or 'SELL'."
        )

        response = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
        )
        return response.choices[0].message.content.strip().upper(), cl
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ DATA ERROR: {str(e)}")
        return None, None

# --- 4. TRADING ENGINE ---

def trade_engine(chat_id):
    u = get_user(chat_id)
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                side, price = get_ai_signal(u["symbol"], chat_id)
                if not side or ("BUY" not in side and "SELL" not in side):
                    time.sleep(10)
                    continue
                
                # Scalp Logic (5-8 Pips)
                tp_dist = 8.0 if "BTC" in u["symbol"] else 0.5
                tp = price + tp_dist if "BUY" in side else price - tp_dist
                sl = price - (tp_dist * 2) if "BUY" in side else price + (tp_dist * 2)

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **AI FIRED {side}**\nEntry: {price}\nStake: ${u['current_stake']}")

            else:
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    profit = t['stake'] * 0.15
                    u["balance"] += (t['stake'] + profit)
                    u["current_stake"] = u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN!** +${round(profit, 2)}\nNew Bal: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["current_stake"] *= 2.0
                    bot.send_message(chat_id, f"❌ **LOSS.** Martingale x2: ${u['current_stake']}")
                    u["active_trade"] = None
            
            time.sleep(5)
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(10)

# --- 5. COMMANDS ---

@bot.message_handler(commands=['trade'])
def select_asset(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("BTC/USD", "ETH/USD") # Kraken symbols use USD
    msg = bot.send_message(message.chat.id, "Select Asset:", reply_markup=markup)
    bot.register_next_step_handler(msg, start_trading)

def start_trading(message):
    u = get_user(message.chat.id)
    u["symbol"] = message.text
    u["is_trading"] = True
    bot.send_message(message.chat.id, f"🚀 AI Engine active for {u['symbol']} (via Kraken)")
    threading.Thread(target=trade_engine, args=(message.chat.id,), daemon=True).start()

@bot.message_handler(commands=['check', 'bal', 'status'])
def show_status(message):
    u = get_user(message.chat.id)
    mode = "🟢 ON" if u["is_trading"] else "🔴 OFF"
    bot.send_message(message.chat.id, f"📊 **STATUS**\nMode: {mode}\nBal: ${round(u['balance'], 2)}\nNext Stake: ${u['current_stake']}")

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    u = get_user(message.chat.id)
    u["is_trading"] = False
    bot.send_message(message.chat.id, "🛑 Trading Stopped.")

# --- 6. RENDER DEPLOY ---
@app.route('/')
def home(): return "AI Engine (Kraken Fix) Online", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    
    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
    except: pass
    
    bot.polling(non_stop=True)
