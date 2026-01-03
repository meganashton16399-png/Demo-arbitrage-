import telebot
from telebot import types
import websocket
import json
import threading
import time
import pandas as pd
import pandas_ta as ta

# --- CONFIGURATION ---
APP_ID = 119348
API_TOKEN = "6D17WOjBDvq51Dz"  # Aapka Token
TELE_TOKEN = "8472550297:AAHcUTrMrvuxbDs3tAiFojzVp-BCM4Puc9s" # Apna Token (Colon : wala) dalo
MY_CHAT_ID = "8559974035"    # Apni Chat ID

bot = telebot.TeleBot(TELE_TOKEN)

# Global Variables
is_trading = False
SELECTED_SYMBOL = ""
current_lot = 0.35 # Start Lot
multiplier = 2.1   # Martingale Multiplier
ticks_history = []
active_trades = 0

ASSETS = {
    "Gold (XAUUSD)": "frxXAUUSD",
    "Bitcoin (BTCUSD)": "cryBTCUSD"
}

# --- 1. STRICT 2/3 BIAS LOGIC ---
def get_forced_bias():
    global ticks_history
    if len(ticks_history) < 25:
        return None # Data build hone do pehle
    
    df = pd.DataFrame(ticks_history, columns=['close'])
    
    # Indicators
    ema9 = ta.ema(df['close'], length=9).iloc[-1]
    ema21 = ta.ema(df['close'], length=21).iloc[-1]
    rsi = ta.rsi(df['close'], length=14).iloc[-1]
    current = df['close'].iloc[-1]
    prev = df['close'].iloc[-2]

    # Voting System (3 Confirmation Points)
    score_buy = 0
    score_sell = 0

    # 1. EMA Check
    if ema9 > ema21: score_buy += 1
    else: score_sell += 1

    # 2. RSI Check
    if rsi > 50: score_buy += 1
    else: score_sell += 1

    # 3. Price Action Check
    if current > prev: score_buy += 1
    else: score_sell += 1

    # Decision: Majority Wins (2 out of 3)
    if score_buy >= 2: return "buy"
    if score_sell >= 2: return "sell"
    
    return "buy" # Default fallback agar data equal ho (Rare)

# --- 2. DERIV HANDLERS ---
def on_open(ws):
    auth_data = {"authorize": API_TOKEN}
    ws.send(json.dumps(auth_data))

def on_message(ws, message):
    global ticks_history, current_lot, active_trades
    data = json.loads(message)

    # Price Update
    if 'tick' in data:
        price = data['tick']['quote']
        ticks_history.append(price)
        if len(ticks_history) > 100: ticks_history.pop(0)

    # Trade Result Update (Async Martingale)
    if 'proposal_open_contract' in data:
        contract = data['proposal_open_contract']
        if contract['is_sold']:
            profit = float(contract['profit'])
            if profit > 0:
                current_lot = 0.35 # Reset on Win
                # bot.send_message(MY_CHAT_ID, "✅ WIN") # Spam kam karne ke liye comment kiya
            else:
                current_lot = round(current_lot * multiplier, 2) # Increase on Loss
                bot.send_message(MY_CHAT_ID, f"❌ LOSS! Next Lot: {current_lot}")

def place_order(ws, direction, amount):
    # 5 Ticks Duration (Super Fast)
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

# --- 3. TELEGRAM ---
@bot.message_handler(commands=['trade'])
def ask_asset(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Gold (XAUUSD)", "Bitcoin (BTCUSD)")
    bot.send_message(message.chat.id, "Select Asset (Bitcoin for Weekend):", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ASSETS.keys())
def start_now(message):
    global is_trading, SELECTED_SYMBOL, current_lot
    if is_trading: return
    
    SELECTED_SYMBOL = ASSETS[message.text]
    is_trading = True
    current_lot = 0.35
    bot.send_message(message.chat.id, f"🚀 MACHINE GUN MODE ON: {message.text}\nSpeed: 1 Trade/Sec", reply_markup=types.ReplyKeyboardRemove())
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['stop'])
def stop_it(message):
    global is_trading
    is_trading = False
    bot.reply_to(message, "🛑 Stopping loop...")

@bot.message_handler(commands=['status'])
def stat(message):
    bot.reply_to(message, f"Current Lot: {current_lot}\nAsset: {SELECTED_SYMBOL}")

# --- 4. MAIN 1-SEC LOOP ---
def trading_loop():
    global is_trading
    
    ws = websocket.WebSocketApp(f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}", on_open=on_open, on_message=on_message)
    wst = threading.Thread(target=ws.run_forever)
    wst.daemon = True
    wst.start()
    
    time.sleep(3) # Connection wait
    ws.send(json.dumps({"ticks": SELECTED_SYMBOL, "subscribe": 1}))
    ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))

    while is_trading:
        # STRICT 1 SECOND EXECUTION
        bias = get_forced_bias()
        
        if bias:
            # Bina wait kiye trade place karo
            place_order(ws, bias, current_lot)
            print(f"Executed {bias} at {current_lot}") 
        else:
            print("Gathering Data...")
            
        time.sleep(1) # Sirf 1 second ka pause, fir repeat

if __name__ == "__main__":
    bot.polling()

