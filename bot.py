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

# --- CONFIGURATION ---
APP_ID = 119348
API_TOKEN = "6D17WOjBDvq51Dz"  # Aapka Deriv Token
TELE_TOKEN = "8472550297:AAGSXMkqSZKg2ALDbV2BKdgQJ_rDBUHNAuA" # Apna Token (Colon : wala)
MY_CHAT_ID = "8559974035"    # Apni Chat ID

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__) # Web Server

# Global Variables
is_trading = False
SELECTED_SYMBOL = ""
current_lot = 0.50 
multiplier = 2.1
ticks_history = []

ASSETS = {
    "Volatility 100 (1s) Index": "R_100", 
    "Bitcoin (BTCUSD)": "cryBTCUSD",
    "Gold (XAUUSD)": "frxXAUUSD"
}

# --- 1. WEB SERVER FOR UPTIME ROBOT ---
@app.route('/')
def home():
    return "Bot is Alive! 🚀 Machine Gun Mode Ready."

def run_web_server():
    # Render assigns a PORT via environment variable
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# --- 2. LOGIC (2/3 Confirm) ---
def get_bias():
    global ticks_history
    if len(ticks_history) < 20: return None 
    
    df = pd.DataFrame(ticks_history, columns=['close'])
    ema9 = ta.ema(df['close'], length=9).iloc[-1]
    ema21 = ta.ema(df['close'], length=21).iloc[-1]
    rsi = ta.rsi(df['close'], length=14).iloc[-1]
    current = df['close'].iloc[-1]
    prev = df['close'].iloc[-2]

    # Voting Logic
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

# --- 3. DERIV HANDLERS ---
def on_open(ws):
    print("Connected to Deriv!")
    auth_data = {"authorize": API_TOKEN}
    ws.send(json.dumps(auth_data))

def on_message(ws, message):
    global ticks_history, current_lot
    data = json.loads(message)

    if 'error' in data:
        error_msg = data['error']['message']
        print(f"Error: {error_msg}")
        bot.send_message(MY_CHAT_ID, f"⚠️ Trade Error: {error_msg}")
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
                bot.send_message(MY_CHAT_ID, f"❌ LOSS! Next: {current_lot}")

def place_order(ws, direction, amount):
    trade_msg = {
        "buy": 1,
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

# --- 4. TELEGRAM COMMANDS ---
@bot.message_handler(commands=['trade'])
def ask_asset(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Volatility 100 (1s) Index", "Bitcoin (BTCUSD)")
    bot.send_message(message.chat.id, "Select Asset:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ASSETS.keys())
def start_bot(message):
    global is_trading, SELECTED_SYMBOL, current_lot
    SELECTED_SYMBOL = ASSETS[message.text]
    is_trading = True
    current_lot = 0.50
    bot.send_message(message.chat.id, f"🚀 Started on {SELECTED_SYMBOL}", reply_markup=types.ReplyKeyboardRemove())
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.reply_to(message, "🛑 Stopped.")

# --- 5. MAIN LOOP ---
def trading_loop():
    global is_trading
    ws = websocket.WebSocketApp(f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}", on_open=on_open, on_message=on_message)
    wst = threading.Thread(target=ws.run_forever)
    wst.daemon = True
    wst.start()
    
    time.sleep(5)
    ws.send(json.dumps({"ticks": SELECTED_SYMBOL, "subscribe": 1}))
    ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))

    while is_trading:
        bias = get_bias()
        if bias:
            place_order(ws, bias, current_lot)
            print(f"Ordered: {bias}")
        time.sleep(1) 

if __name__ == "__main__":
    keep_alive() # Starts Flask Server
    bot.polling()
