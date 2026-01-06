import telebot
from telebot import types
import ccxt
import pandas as pd
import time
import os
from flask import Flask
import threading

# --- 1. CONFIGURATION ---
API_KEY = os.environ.get("BYBIT_API")
API_SECRET = os.environ.get("BYBIT_SC")
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

# --- GLOBAL SETTINGS ---
is_trading = False
SELECTED_SYMBOL = "" 
INITIAL_STAKE = 10.0  # $10 se shuru
CURRENT_STAKE = 10.0
LEVERAGE = 10         

# 🔥 HIGH FREQUENCY SETTINGS (Fast Profit/Loss)
TP_PERCENT = 0.0015  # 0.15% Profit Target (Jaldi hit hoga)
SL_PERCENT = 0.0025  # 0.25% Stop Loss
MARTINGALE_FACTOR = 2.0 # Loss hote hi paisa double

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# Bybit Connection
try:
    exchange = ccxt.bybit({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'future', 'adjustForTimeDifference': True}
    })
    exchange.set_sandbox_mode(True) # ✅ Testnet
except Exception as e:
    bot.send_message(MY_CHAT_ID, f"API Error: {e}")

# --- 2. SERVER ---
@app.route('/')
def home(): return "High Frequency Bot Running"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web_server)
    t.start()

# --- 3. TRADING LOGIC ---
def get_last_candle_color(symbol):
    try:
        # Sirf last candle chahiye direction ke liye
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=2)
        if not ohlcv: return None
        
        # Last completed candle
        open_price = ohlcv[-2][1]
        close_price = ohlcv[-2][4]
        
        # Agar Green hai to BUY, Red hai to SELL
        if close_price > open_price:
            return "buy", close_price
        else:
            return "sell", close_price
    except:
        return None, None

def trade_loop():
    global is_trading, CURRENT_STAKE
    bot.send_message(MY_CHAT_ID, f"🔫 Machine Gun Mode ON: {SELECTED_SYMBOL}")
    
    # Leverage Set
    try: exchange.set_leverage(LEVERAGE, SELECTED_SYMBOL)
    except: pass

    last_trade_status = "WIN" # Maan ke chalte hain pehla trade normal hoga

    while is_trading:
        try:
            # 1. Check agar koi Position pehle se open hai
            positions = exchange.fetch_positions([SELECTED_SYMBOL])
            active_pos = [p for p in positions if float(p['contracts']) > 0]

            if len(active_pos) > 0:
                # Agar trade chal rahi hai, to bas wait karo (spam mat karo)
                # Hum yahan check kar sakte hain PnL real time mein
                # Lekin Bybit khud TP/SL handle karega
                time.sleep(5) 
                continue
            
            # --- AGAR KOI TRADE NAHI HAI -> TO TURANT LAGAO ---
            
            # 2. Decide Direction (Trend follow: Last candle color)
            direction, current_price = get_last_candle_color(SELECTED_SYMBOL)
            
            if direction:
                # 3. TP/SL Calculation
                if direction == "buy":
                    tp = current_price * (1 + TP_PERCENT)
                    sl = current_price * (1 - SL_PERCENT)
                else:
                    tp = current_price * (1 - TP_PERCENT)
                    sl = current_price * (1 + SL_PERCENT)

                # 4. Check Balance (Safety)
                bal = exchange.fetch_balance()['USDT']['free']
                if CURRENT_STAKE > bal:
                    CURRENT_STAKE = INITIAL_STAKE # Reset agar balance kam pad gaya
                    bot.send_message(MY_CHAT_ID, "⚠️ Martingale Reset (Low Balance)")

                # 5. Place Order
                amount = CURRENT_STAKE / current_price
                params = {'takeProfit': tp, 'stopLoss': sl}
                
                try:
                    order = exchange.create_order(SELECTED_SYMBOL, 'market', direction, amount, params)
                    
                    bot.send_message(MY_CHAT_ID, 
                        f"🚀 **INSTANT ENTRY**\n"
                        f"Side: {direction.upper()}\n"
                        f"Stake: ${round(CURRENT_STAKE, 2)}\n"
                        f"Price: {current_price}\n"
                        f"🎯 TP: {round(tp, 2)} | 🛑 SL: {round(sl, 2)}")
                    
                    # Ab wait karo trade khatam hone ka check karne ke liye
                    # Hum loop mein 'check result' logic lagayenge
                    check_result_loop(SELECTED_SYMBOL)
                    
                except Exception as e:
                    bot.send_message(MY_CHAT_ID, f"⚠️ Order Failed: {e}")
                    time.sleep(5)

            time.sleep(2) # 2 Second gap bus

        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

def check_result_loop(symbol):
    global CURRENT_STAKE
    # Ye function tab tak chalega jab tak position close na ho jaye
    # Aur jaise hi close hogi, ye Martingale calculate karega
    
    bot.send_message(MY_CHAT_ID, "⏳ Waiting for Result...")
    
    while True:
        try:
            positions = exchange.fetch_positions([symbol])
            active = [p for p in positions if float(p['contracts']) > 0]
            
            if len(active) == 0:
                # Trade Gayab! Matlab TP ya SL hit hua.
                # Since Bybit API history slow hoti hai, hum balance check nahi kar rahe abhi.
                # Hum assume karenge: 
                # Agar hume result API se nahi mil raha instant, toh hum agla trade
                # Same stake se lagayenge agar Profit pata na chale, ya user manually bataye.
                
                # AUTOMATION FIX: 
                # Bybit Testnet pe PnL fetch karna tricky hai bina Websocket ke.
                # Logic: Agar balance badha -> Win. Ghata -> Loss.
                # Lekin 'Wait' hatana hai, toh hum abhi Martingale ko 
                # "Alternate" rakhte hain ya simply Har Loss pe double nahi kar payenge bina confirmation ke.
                
                # Simple High Frequency Mode:
                # Hamesha Trade maaro. Martingale ke liye hume PnL history chahiye.
                # Abhi ke liye hum FIXED STAKE rakhte hain taaki speed bani rahe.
                
                bot.send_message(MY_CHAT_ID, "🏁 Trade Closed! Looking for next...")
                break
                
            time.sleep(2)
        except:
            break

# --- 4. COMMANDS ---
ASSETS = {
    "Bitcoin (BTC)": "BTC/USDT:USDT",
    "Gold (XAU)": "XAU/USDT:USDT"
}

@bot.message_handler(commands=['trade'])
def start_trade(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTC)", "Gold (XAU)")
    msg = bot.send_message(message.chat.id, "Select Asset (Machine Gun Mode 🔫):", reply_markup=markup)
    bot.register_next_step_handler(msg, set_asset)

def set_asset(message):
    global SELECTED_SYMBOL
    if message.text not in ASSETS:
        bot.send_message(message.chat.id, "Invalid.")
        return
    SELECTED_SYMBOL = ASSETS[message.text]
    
    global is_trading
    is_trading = True
    threading.Thread(target=trade_loop).start()

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.send_message(message.chat.id, "🛑 Stopped.")

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
