import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask

# --- CONFIG (Check your Render Env Variables!) ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
MY_CHAT_ID = os.environ.get("CHAT_ID")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)
market = ccxt.binance() 

# --- UNIVERSAL STORAGE ---
users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 10000.0, "initial_stake": 10.0, "current_stake": 10.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USDT"
        }
    return users[chat_id]

# --- AI & DATA ENGINE ---
def get_ai_signal(symbol, chat_id):
    try:
        print(f"DEBUG: Fetching data for {symbol}")
        # Fetching 20 candles for RSI calculation
        ohlcv = market.fetch_ohlcv(symbol, timeframe='1m', limit=20)
        c = ohlcv[-2] # Last closed candle
        o, h, l, cl, v = c[1], c[2], c[3], c[4], c[5]
        
        # Strategy Logic
        body = abs(cl - o)
        total_range = h - l
        u_wick = h - max(o, cl)
        l_wick = min(o, cl) - l
        
        # Candle Classification
        if body <= 0.4 * total_range: c_type = "REJECTION"
        elif body >= 0.6 * total_range: c_type = "MOMENTUM"
        else: c_type = "NEUTRAL"

        prompt = (f"Market: {symbol}. Last Candle O:{o} H:{h} L:{l} C:{cl}. "
                  f"Body: {body}, Range: {total_range}, UpWick: {u_wick}, LowWick: {l_wick}. "
                  f"Type: {c_type}. Decisions: If Rejection Sell if HighWick else Buy. "
                  f"Momentum Bullish means pullback so Sell. Answer BUY or SELL only.")

        # Using Latest 2026 Model
        response = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile", 
        )
        decision = response.choices[0].message.content.strip().upper()
        print(f"DEBUG: AI Decision for {chat_id} is {decision}")
        return decision, cl
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ AI ERROR: {str(e)}")
        return None, None

# --- TRADING LOOP ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                side, price = get_ai_signal(u["symbol"], chat_id)
                if not side or "BUY" not in side and "SELL" not in side:
                    time.sleep(10)
                    continue
                
                # Setup Virtual Trade
                tp_dist = 8.0 if "BTC" in u["symbol"] else 0.5
                tp = price + tp_dist if "BUY" in side else price - tp_dist
                sl = price - (tp_dist * 2) if "BUY" in side else price + (tp_dist * 2)

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **ORDER FIRED: {side}**\nEntry: {price}\nStake: ${u['current_stake']}")

            else:
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    profit = t['stake'] * 0.15 
                    u["balance"] += (t['stake'] + profit)
                    u["current_stake"] = u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN!** +${round(profit, 2)}\nBal: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["current_stake"] *= 2.0 
                    bot.send_message(chat_id, f"❌ **LOSS.** Next Lot: ${u['current_stake']}")
                    u["active_trade"] = None
            
            time.sleep(5)
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(10)

# --- COMMANDS ---
@bot.message_handler(commands=['trade'])
def start_cmd(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("BTC/USDT", "ETH/USDT")
    msg = bot.send_message(message.chat.id, "Select Asset:", reply_markup=markup)
    bot.register_next_step_handler(msg, process_trade)

def process_trade(message):
    u = get_user(message.chat.id)
    u["symbol"] = message.text
    u["is_trading"] = True
    bot.send_message(message.chat.id, f"🚀 Engine Active for {u['symbol']}")
    threading.Thread(target=trade_engine, args=(message.chat.id,), daemon=True).start()

@bot.message_handler(commands=['check', 'status'])
def diag(message):
    u = get_user(message.chat.id)
    try:
        price = market.fetch_ticker(u['symbol'])['last']
        bot.send_message(message.chat.id, f"🔍 **SYSTEM CHECK**\nPrice: ${price}\nBal: ${round(u['balance'], 2)}\nAI Status: ✅ Ready")
    except:
        bot.send_message(message.chat.id, "❌ Error connecting to Market.")

@bot.message_handler(commands=['stop'])
def stop(message):
    u = get_user(message.chat.id)
    u["is_trading"] = False
    bot.send_message(message.chat.id, "🛑 Trading Stopped.")

# --- RENDER BOILERPLATE ---
@app.route('/')
def home(): return "AI Engine Online", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    
    # Cleaning old 409 Conflicts
    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
    except: pass
    
    print("🚀 Polling Started...")
    bot.polling(non_stop=True)
