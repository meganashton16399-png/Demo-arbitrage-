import telebot
from telebot import types
import websocket
import json
import threading
import time
import os
from flask import Flask
from threading import Thread
import pandas as pd
import pandas_ta as ta

# --- 1. CONFIGURATION ---
APP_ID = 119348
API_TOKEN = "6D17WOjBDvq51Dz"
TELE_TOKEN = "8472550297:AAGylw6wRt-k6Y-ASzDKZCE-ExnI77yJSlU"
MY_CHAT_ID = "8559974035"

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# Global Variables
is_trading = False
SELECTED_SYMBOL = ""
current_lot = 0.50 
multiplier = 2.1
ticks_history = []
ws_connected = False 

ASSETS = {
    "Volatility 100 (1s) Index": "1HZ100V", 
    "Bitcoin (BTCUSD)": "cryBTCUSD",
    "Gold (XAUUSD)": "frxXAUUSD"
}

# --- 2. UPTIME SERVER ---
@app.route('/')
def home():
    return "Bot is Alive! Fixed Ping Issue."

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# --- 3. TRADING LOGIC ---
def get_bias():
    global ticks_history
    if len(ticks_history) < 20: return None 
    
    df = pd.DataFrame(ticks_history, columns=['close'])
    ema9 = ta.ema(df['close'], length=9).iloc[-1]
    ema21 = ta.ema(df['close'], length=21).iloc[-1]
    rsi = ta.rsi(df['close'], length=14).iloc[-1]
    current = df['close'].iloc[-1]
    prev = df['close'].iloc[-2]

    buy_vote = 0
    sell_vote = 0
    
    if ema9 > ema21: buy_vote += 1
    else: sell_vote += 1
    if rsi > 50: buy_vote += 1
    else: sell_vote += 1
    if current > prev: buy_vote += 1
    else: sell_vote += 1

    if buy_vote >= 2: return "buy"
    if sell_vote >= 2: return "sell"
    return "buy"

# --- 4. DERIV HANDLERS ---
def on_open(ws):
    global ws_connected
    ws_connected = True
    auth_data = {"authorize": API_TOKEN}
    ws.send(json.dumps(auth_data))

def on_message(ws, message):
    global ticks_history, current_lot
    try:
        data = json.loads(message)

        if 'error' in data:
            bot.send_message(MY_CHAT_ID, f"❌ API Error: {data['error']['message']}")
            return

        if 'tick' in data:
            price = data['tick']['quote']
            ticks_history.append(price)
            if len(ticks_history) > 100: ticks_history.pop(0)

        if 'proposal_open_contract' in data:
            contract = data['proposal_open_contract']
            if contract['is_sold']:
                profit = float(contract['profit'])
                if profit > 0:
                    current_lot = 0.50
                else:
                    current_lot = round(current_lot * multiplier, 2)
                    bot.send_message(MY_CHAT_ID, f"💔 LOSS! Next: {current_lot}")

    except Exception as e:
        print(f"Error: {e}")

def place_order(ws, direction, amount):
    try:
        trade_msg = {
            "buy": 1,
            "price": amount,
            "parameters": {
                "amount": amount,
                "basis": "stake",
                "contract_type": "CALL" if direction == "buy" else "PUT",
                "currency": "USD",
                "symbol": SELECTED_SYMBOL,
                "duration": 5,
                "duration_unit": "t"
            }
        }
        ws.send(json.dumps(trade_msg))
    except Exception as e:
        bot.send_message(MY_CHAT_ID, f"⚠️ Trade Fail: {e}")

# --- 5. COMMANDS ---
@bot.message_handler(commands=['trade'])
def ask_asset(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Volatility 100 (1s) Index", "Bitcoin (BTCUSD)")
    bot.send_message(message.chat.id, "Select Asset:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ASSETS.keys())
def start_bot(message):
    global is_trading, SELECTED_SYMBOL, current_lot, ticks_history
    if is_trading:
        bot.send_message(message.chat.id, "Bot already running!")
        return

    SELECTED_SYMBOL = ASSETS[message.text]
    is_trading = True
    ticks_history = [] 
    current_lot = 0.50
    
    bot.send_message(message.chat.id, f"🚀 Launching {SELECTED_SYMBOL}...", reply_markup=types.ReplyKeyboardRemove())
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.reply_to(message, "🛑 Stopped.")

# --- 6. MAIN LOOP (FIXED) ---
def trading_loop():
    global is_trading, ws_connected
    
    # ✅ FIX: Removed ping_interval from here
    ws = websocket.WebSocketApp(f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}", 
                                on_open=on_open, on_message=on_message)
    
    # ✅ FIX: Moved ping_interval to run_forever using kwargs
    wst = threading.Thread(target=ws.run_forever, kwargs={'ping_interval': 30, 'ping_timeout': 10})
    wst.daemon = True
    wst.start()
    
    time.sleep(3)
    ws.send(json.dumps({"ticks": SELECTED_SYMBOL, "subscribe": 1}))
    ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))
    
    # Initial status
    bot.send_message(MY_CHAT_ID, "📡 Gathering Data...")
    data_ready_sent = False

    while is_trading:
        try:
            if len(ticks_history) < 20:
                if len(ticks_history) > 0 and len(ticks_history) % 5 == 0:
                    bot.send_message(MY_CHAT_ID, f"⏳ Loading Data: {len(ticks_history)}/20...")
                    time.sleep(2) 
                time.sleep(1)
                continue
            
            if not data_ready_sent:
                bot.send_message(MY_CHAT_ID, "✅ Data Full! Machine Gun Mode ON 🔫")
                data_ready_sent = True

            bias = get_bias()
            if bias:
                place_order(ws, bias, current_lot)
            
            time.sleep(1) 
            
        except Exception as e:
            time.sleep(5)
    
    ws.close()

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
