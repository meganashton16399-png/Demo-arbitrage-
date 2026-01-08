import telebot
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask

# --- CONFIGURATION ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)
market = ccxt.binance() # Public data source

# --- UNIVERSAL USER STORAGE ---
# Multi-user support: stores individual balances and trade states
users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 10000.0, "initial_stake": 10.0, "current_stake": 10.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USDT"
        }
    return users[chat_id]

# --- TECHNICAL ENGINE ---

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
        # Fetch 1m candles
        ohlcv = market.fetch_ohlcv(symbol, timeframe='1m', limit=20)
        c = ohlcv[-2] # Last closed candle: [time, O, H, L, C, V]
        o, h, l, cl, v = c[1], c[2], c[3], c[4], c[5]
        
        # Strategy Calculations
        body = abs(cl - o)
        total_range = h - l
        u_wick = h - max(o, cl)
        l_wick = min(o, cl) - l
        rsi = get_rsi([x[4] for x in ohlcv])
        
        # Determine Candle Type
        if body <= 0.4 * total_range: c_type = "REJECTION"
        elif body >= 0.6 * total_range: c_type = "MOMENTUM"
        else: c_type = "NEUTRAL"

        # Formulate Expert Prompt
        prompt = (
            f"Asset: {symbol}. O:{o}, H:{h}, L:{l}, C:{cl}. "
            f"Type: {c_type} (Body:{body}, UpWick:{u_wick}, LowWick:{l_wick}). "
            f"Indicators: RSI is {round(rsi,1)}, Vol is {v}. "
            f"Strategy: Rejection w/ long wick means reversal. Momentum means pullback. "
            f"Response: 'BUY' or 'SELL' only. One word."
        )

        # Query Groq AI (Llama 3.3 for 2026 performance)
        response = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
        )
        decision = response.choices[0].message.content.strip().upper()
        return ("BUY" if "BUY" in decision else "SELL"), cl
    except Exception as e:
        print(f"AI Error: {e}")
        return None, None

# --- TRADING LOOP ---

def trade_engine(chat_id):
    u = get_user(chat_id)
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                side, price = get_ai_signal(u["symbol"], chat_id)
                if not side: continue
                
                # Scalp Settings: 5-8 Pips (~0.01% move for BTC)
                tp_dist = 8.0 if "BTC" in u["symbol"] else 0.5
                tp = price + tp_dist if side == "BUY" else price - tp_dist
                sl = price - (tp_dist * 2) if side == "BUY" else price + (tp_dist * 2)

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **AI FIRED {side}**\nEntry: {price}\nStake: ${u['current_stake']}")

            else:
                # Live Tracking
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                
                win = (t['side'] == "BUY" and curr >= t['tp']) or (t['side'] == "SELL" and curr <= t['tp'])
                loss = (t['side'] == "BUY" and curr <= t['sl']) or (t['side'] == "SELL" and curr >= t['sl'])

                if win:
                    profit = t['stake'] * 0.15 # 15% sim profit
                    u["balance"] += (t['stake'] + profit)
                    u["current_stake"] = u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN!** +${round(profit, 2)}\nBal: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["current_stake"] *= 2.0 # Martingale
                    bot.send_message(chat_id, f"❌ **LOSS.** Next Stake: ${u['current_stake']}")
                    u["active_trade"] = None
            
            time.sleep(4)
        except: time.sleep(10)

# --- TELEGRAM INTERFACE ---

@bot.message_handler(commands=['trade'])
def select_asset(message):
    markup = telebot.types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("BTC/USDT", "ETH/USDT") # ETH/USDT used for XAU mapping in public API
    msg = bot.send_message(message.chat.id, "Select Asset to Start AI Engine:", reply_markup=markup)
    bot.register_next_step_handler(msg, start_loop)

def start_loop(message):
    u = get_user(message.chat.id)
    u["symbol"] = message.text
    u["is_trading"] = True
    bot.send_message(message.chat.id, f"🚀 AI Machine Gun active for {u['symbol']}")
    threading.Thread(target=trade_engine, args=(message.chat.id,), daemon=True).start()

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    u = get_user(message.chat.id)
    u["is_trading"] = False
    bot.send_message(message.chat.id, "🛑 Emergency Stop Engaged.")

@bot.message_handler(commands=['bal', 'status', 'check'])
def check_status(message):
    u = get_user(message.chat.id)
    curr_status = "🟢 Trading" if u["is_trading"] else "🔴 Idle"
    bot.send_message(message.chat.id, f"📊 **STATUS**\nMode: {curr_status}\nBalance: ${round(u['balance'], 2)}\nStake: ${u['current_stake']}")

@bot.message_handler(commands=['help'])
def help_menu(message):
    bot.reply_to(message, "/trade - Start AI\n/stop - Stop AI\n/bal - Wallet info\n/check - Diagnostics")

# --- RENDER FLASK & ANTI-CONFLICT ---
@app.route('/')
def home(): return "AI Virtual Engine 2.0 Online", 200

if __name__ == "__main__":
    # Start web server for Render health checks
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    
    # Solve 409 Conflict: Force kill old sessions
    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
    except: pass
    
    print("🚀 Bot Polling...")
    bot.polling(non_stop=True)
