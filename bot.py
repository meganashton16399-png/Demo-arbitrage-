import telebot
from telebot import types
import websocket
import json
import threading
import time
import pandas as pd
import pandas_ta as ta

# --- 1. CONFIGURATION ---
APP_ID = 119348 #
API_TOKEN = "6D17WOjBDvq51Dz" #
TELE_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
MY_CHAT_ID = "YOUR_CHAT_ID" # Jaha logs chahiye

bot = telebot.TeleBot(TELE_TOKEN)

# Trading Variables
is_trading = False
SELECTED_SYMBOL = ""
current_lot = 0.01
multiplier = 3
ticks_history = []
last_trade_status = "WIN" # Initial status

ASSETS = {
    "Gold (XAUUSD)": "frxXAUUSD",
    "Bitcoin (BTCUSD)": "cryBTCUSD"
}

# --- 2. INDICATOR LOGIC ---
def get_market_bias():
    global ticks_history
    if len(ticks_history) < 30:
        return None
    
    df = pd.DataFrame(ticks_history, columns=['close'])
    # Indicators: EMA 9, EMA 21, RSI 14
    ema9 = ta.ema(df['close'], length=9).iloc[-1]
    ema21 = ta.ema(df['close'], length=21).iloc[-1]
    rsi = ta.rsi(df['close'], length=14).iloc[-1]
    current_price = df['close'].iloc[-1]
    prev_price = df['close'].iloc[-2]

    # 3 Confirmations Bias
    if ema9 > ema21 and rsi > 50 and current_price > prev_price:
        return "buy"
    elif ema9 < ema21 and rsi < 50 and current_price < prev_price:
        return "sell"
    return None

# --- 3. DERIV API HANDLERS ---
def on_open(ws):
    # Authenticate immediately
    auth_data = {"authorize": API_TOKEN}
    ws.send(json.dumps(auth_data))

def on_message(ws, message):
    global ticks_history, is_trading, SELECTED_SYMBOL, last_trade_status
    data = json.loads(message)

    # Handle Ticks
    if 'tick' in data:
        price = data['tick']['quote']
        ticks_history.append(price)
        if len(ticks_history) > 100: ticks_history.pop(0)

    # Handle Trade Results for Martingale
    if 'proposal_open_contract' in data:
        contract = data['proposal_open_contract']
        if contract['is_sold']:
            profit = float(contract['profit'])
            last_trade_status = "WIN" if profit > 0 else "LOSS"

def place_order(ws, direction, amount):
    # Trade with SL 12 and TP 8
    buy_msg = {
        "buy": 1,
        "subscribe": 1,
        "price": 100, # Max price
        "parameters": {
            "amount": amount,
            "basis": "stake",
            "contract_type": "CALL" if direction == "buy" else "PUT",
            "currency": "USD",
            "symbol": SELECTED_SYMBOL,
            "duration": 1,
            "duration_unit": "m" # 1-min duration for testing
        }
    }
    ws.send(json.dumps(buy_msg))

# --- 4. TELEGRAM INTERFACE ---
@bot.message_handler(commands=['trade'])
def start_cmd(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Gold (XAUUSD)", "Bitcoin (BTCUSD)")
    bot.send_message(message.chat.id, "Bhai, asset select karo:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ASSETS.keys())
def asset_selected(message):
    global is_trading, SELECTED_SYMBOL, current_lot
    SELECTED_SYMBOL = ASSETS[message.text]
    is_trading = True
    current_lot = 0.01
    bot.send_message(message.chat.id, f"🚀 Bot Active: {message.text}\nMartingale: 3x | Loop: 1s", reply_markup=types.ReplyKeyboardRemove())
    # Start loop in background
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['stop'])
def stop_cmd(message):
    global is_trading
    is_trading = False
    bot.reply_to(message, "🛑 Bot Stopped.")

@bot.message_handler(commands=['status'])
def status_cmd(message):
    bot.reply_to(message, f"📊 Symbol: {SELECTED_SYMBOL}\nLot: {current_lot}\nLast: {last_trade_status}")

# --- 5. MAIN TRADING LOOP (1 SECOND) ---
def trading_loop():
    global is_trading, current_lot, last_trade_status
    
    # Establish WebSocket Connection
    ws = websocket.WebSocketApp(f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}", on_open=on_open, on_message=on_message)
    wst = threading.Thread(target=ws.run_forever)
    wst.daemon = True
    wst.start()
    
    time.sleep(5) # Wait for connection
    
    # Subscribe to Ticks
    ws.send(json.dumps({"ticks": SELECTED_SYMBOL}))

    while is_trading:
        bias = get_market_bias() # Check 3-Bias
        
        # Martingale Check
        if last_trade_status == "LOSS":
            current_lot *= multiplier
            bot.send_message(MY_CHAT_ID, f"⚠️ Martingale Active: Lot {current_lot}")
        else:
            current_lot = 0.01

        if bias:
            place_order(ws, bias, current_lot)
            bot.send_message(MY_CHAT_ID, f"⚡ Trade: {bias.upper()} | Lot: {current_lot}")
        
        time.sleep(1) # Har 1 second loop

if __name__ == "__main__":
    bot.polling()
