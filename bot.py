import telebot
from telebot import types
import websocket
import json
import threading
import time
import pandas as pd
import pandas_ta as ta

# --- 1. CONFIGURATION ---
APP_ID = 119348
API_TOKEN = "6D17WOjBDvq51Dz"
TELE_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
MY_CHAT_ID = "YOUR_CHAT_ID" 

bot = telebot.TeleBot(TELE_TOKEN)

# Global Variables
is_trading = False
SELECTED_SYMBOL = ""
current_lot = 0.01
multiplier = 3
ticks_history = []
last_trade_status = "WIN" 

ASSETS = {
    "Gold (XAUUSD)": "frxXAUUSD",
    "Bitcoin (BTCUSD)": "cryBTCUSD"
}

# --- 2. INDICATOR LOGIC (3-Bias Confirmation) ---
def get_market_bias():
    global ticks_history
    if len(ticks_history) < 30:
        return None
    
    df = pd.DataFrame(ticks_history, columns=['close'])
    # Technical Indicators
    ema9 = ta.ema(df['close'], length=9).iloc[-1]
    ema21 = ta.ema(df['close'], length=21).iloc[-1]
    rsi = ta.rsi(df['close'], length=14).iloc[-1]
    current_price = df['close'].iloc[-1]
    prev_price = df['close'].iloc[-2]

    # Bias Logic: EMA Cross + RSI + Price Direction
    if ema9 > ema21 and rsi > 50 and current_price > prev_price:
        return "buy"
    elif ema9 < ema21 and rsi < 50 and current_price < prev_price:
        return "sell"
    return None

# --- 3. DERIV API WEBSOCKET HANDLERS ---
def on_open(ws):
    auth_data = {"authorize": API_TOKEN}
    ws.send(json.dumps(auth_data))

def on_message(ws, message):
    global ticks_history, is_trading, last_trade_status
    data = json.loads(message)

    if 'tick' in data:
        price = data['tick']['quote']
        ticks_history.append(price)
        if len(ticks_history) > 100: ticks_history.pop(0)

    # Check Trade Result for Martingale
    if 'proposal_open_contract' in data:
        contract = data['proposal_open_contract']
        if contract['is_sold']:
            profit = float(contract['profit'])
            last_trade_status = "WIN" if profit > 0 else "LOSS"
            msg = "✅ PROFIT" if profit > 0 else f"❌ LOSS (Next Lot: {current_lot * multiplier})"
            bot.send_message(MY_CHAT_ID, f"Trade Result: {msg}")

def place_order(ws, direction, amount):
    # Call/Put based on Bias
    trade_msg = {
        "buy": 1,
        "parameters": {
            "amount": amount,
            "basis": "stake",
            "contract_type": "CALL" if direction == "buy" else "PUT",
            "currency": "USD",
            "symbol": SELECTED_SYMBOL,
            "duration": 1,
            "duration_unit": "m" 
        }
    }
    ws.send(json.dumps(trade_msg))

# --- 4. TELEGRAM COMMANDS ---
@bot.message_handler(commands=['trade'])
def start_cmd(message):
    # Asset selection buttons
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Gold (XAUUSD)", "Bitcoin (BTCUSD)")
    bot.send_message(message.chat.id, "Bhai, kaunsa asset trade karna hai? (Weekends pe Bitcoin lo)", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ASSETS.keys())
def asset_selected(message):
    global is_trading, SELECTED_SYMBOL, current_lot
    if is_trading:
        bot.send_message(message.chat.id, "⚠️ Bot pehle se chal raha hai! Pehle /stop karo.")
        return
        
    SELECTED_SYMBOL = ASSETS[message.text]
    is_trading = True
    current_lot = 0.01
    bot.send_message(message.chat.id, f"🚀 Bot Started on {message.text}!\n1s Scan Active | Martingale x3", reply_markup=types.ReplyKeyboardRemove())
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['stop'])
def stop_cmd(message):
    global is_trading
    is_trading = False
    bot.reply_to(message, "🛑 Bot stopped. New trades disabled.")

@bot.message_handler(commands=['status'])
def status_cmd(message):
    bot.reply_to(message, f"📊 Status:\nAsset: {SELECTED_SYMBOL}\nCurrent Lot: {current_lot}\nLast Trade: {last_trade_status}")

# --- 5. MAIN HFT LOOP ---
def trading_loop():
    global is_trading, current_lot, last_trade_status
    
    ws = websocket.WebSocketApp(f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}", on_open=on_open, on_message=on_message)
    wst = threading.Thread(target=ws.run_forever)
    wst.daemon = True
    wst.start()
    
    time.sleep(3) # Wait for auth
    ws.send(json.dumps({"ticks": SELECTED_SYMBOL, "subscribe": 1}))

    while is_trading:
        bias = get_market_bias() # 3-Confirmation check
        
        # Martingale Logic
        if last_trade_status == "LOSS":
            current_lot = round(current_lot * multiplier, 2)
        else:
            current_lot = 0.01

        if bias:
            place_order(ws, bias, current_lot)
            bot.send_message(MY_CHAT_ID, f"⚡ Trade Placed: {bias.upper()}\nLot: {current_lot}")
            # Wait for trade to clear before next logic
            time.sleep(61) 
        
        time.sleep(1) # Har 1 sec scan

if __name__ == "__main__":
    print("Bot is polling...")
    bot.polling()
