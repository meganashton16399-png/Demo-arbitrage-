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

# --- 1. CREDENTIALS ---
APP_ID = 119348
API_TOKEN = "97TGFzZ36ZBulqy" # Hardcoded Token (Correct)

# Telegram Env se
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

# Fallback Check
if not TELE_TOKEN:
    TELE_TOKEN = "8472550297:AAE05TUxFHedmwh8g0hrx4EnNjFaCo_LJ8E"
    MY_CHAT_ID = "8559974035"

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# Global Variables
is_trading = False
SELECTED_SYMBOL = ""
current_stake = 1.0  
martingale_factor = 2.1 # Loss recovery
ticks_history = []
ws_connected = False 
is_position_open = False
authorized = False 

# ✅ Safe Assets
ASSETS = {
    "Volatility 100 (1s) Index": "1HZ100V", # Fast & 24/7
    "Bitcoin (BTCUSD)": "cryBTCUSD"         # 24/7
}

# --- 2. SERVER ---
@app.route('/')
def home():
    return "Bot is Live! Price Action Mode."

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# --- 3. TRADING LOGIC (Trend Follow) ---
def get_bias():
    global ticks_history
    if len(ticks_history) < 20: return None 
    
    df = pd.DataFrame(ticks_history, columns=['close'])
    ema9 = ta.ema(df['close'], length=9).iloc[-1]
    ema21 = ta.ema(df['close'], length=21).iloc[-1]
    rsi = ta.rsi(df['close'], length=14).iloc[-1]
    current = df['close'].iloc[-1]
    prev = df['close'].iloc[-2]

    # Strong Trend Confirmation
    buy_score = 0
    sell_score = 0
    
    if ema9 > ema21: buy_score += 1
    else: sell_score += 1
    
    if rsi > 50: buy_score += 1
    else: sell_score += 1
    
    if current > prev: buy_score += 1
    else: sell_score += 1

    if buy_score == 3: return "buy"   # Sirf strong signal par trade
    if sell_score == 3: return "sell"
    return None

# --- 4. DERIV HANDLERS ---
def on_open(ws):
    global ws_connected
    ws_connected = True
    print("🔌 Connecting...")
    auth_data = {"authorize": API_TOKEN}
    ws.send(json.dumps(auth_data))

def on_message(ws, message):
    global ticks_history, current_stake, is_position_open, authorized
    try:
        data = json.loads(message)

        if 'error' in data:
            err_msg = data['error']['message']
            bot.send_message(MY_CHAT_ID, f"⚠️ Error: {err_msg}")
            is_position_open = False 
            return

        if 'authorize' in data:
            authorized = True
            bot.send_message(MY_CHAT_ID, "✅ Ready! Strategy: 3-Min Price Action")
            ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))

        if 'tick' in data:
            price = data['tick']['quote']
            ticks_history.append(price)
            if len(ticks_history) > 100: ticks_history.pop(0)

        # ✅ Proposal Aaya -> Buy Karo
        if 'proposal' in data:
            proposal_id = data['proposal']['id']
            ws.send(json.dumps({"buy": proposal_id, "price": 1000}))

        # ✅ Buy Confirm
        if 'buy' in data:
            buy_id = data['buy']['contract_id']
            bot.send_message(MY_CHAT_ID, f"🔫 Trade Placed (3 Min Duration)")

        # ✅ Result Monitor
        if 'proposal_open_contract' in data:
            contract = data['proposal_open_contract']
            
            if contract['is_sold']:
                is_position_open = False 
                profit = float(contract['profit'])
                
                if profit > 0:
                    status = "🟢 WIN"
                    current_stake = 1.0 
                else:
                    status = "🔴 LOSS"
                    current_stake = round(current_stake * martingale_factor, 2)
                
                msg = (f"{status}\nProfit: ${profit}\nNext: ${current_stake}")
                bot.send_message(MY_CHAT_ID, msg)

    except Exception as e:
        print(f"Error: {e}")

def send_proposal(ws, direction, amount):
    global is_position_open, authorized
    
    if not authorized: return

    try:
        # ✅ FIX: Using Standard CALL/PUT (Never Fails)
        contract = "CALL" if direction == "buy" else "PUT"
        
        proposal_msg = {
            "proposal": 1,
            "amount": amount,
            "basis": "stake",
            "contract_type": contract, 
            "currency": "USD",
            "symbol": SELECTED_SYMBOL,
            "duration": 3,      # ✅ 3 Minute Duration
            "duration_unit": "m" 
        }
        
        ws.send(json.dumps(proposal_msg))
        is_position_open = True 
        bot.send_message(MY_CHAT_ID, f"⏳ Trend Found: {direction.upper()} | Stake: ${amount}")
        
    except Exception as e:
        bot.send_message(MY_CHAT_ID, f"⚠️ Local Error: {e}")
        is_position_open = False

# --- 5. COMMANDS ---
@bot.message_handler(commands=['trade'])
def ask_asset(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Volatility 100 (1s) Index", "Bitcoin (BTCUSD)")
    bot.send_message(message.chat.id, "Select Asset:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ASSETS.keys())
def start_bot(message):
    global is_trading, SELECTED_SYMBOL, current_stake, ticks_history, is_position_open, authorized
    if is_trading:
        bot.send_message(message.chat.id, "Already Running.")
        return

    SELECTED_SYMBOL = ASSETS[message.text]
    is_trading = True
    ticks_history = [] 
    current_stake = 1.0 
    is_position_open = False
    authorized = False 
    
    bot.send_message(message.chat.id, f"🚀 Bot Started: {SELECTED_SYMBOL}", reply_markup=types.ReplyKeyboardRemove())
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.reply_to(message, "🛑 Stopped.")

# --- 6. MAIN LOOP ---
def trading_loop():
    global is_trading, ws_connected, is_position_open
    
    ws = websocket.WebSocketApp(f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}", 
                                on_open=on_open, on_message=on_message)
    
    wst = threading.Thread(target=ws.run_forever, kwargs={'ping_interval': 30, 'ping_timeout': 10})
    wst.daemon = True
    wst.start()
    
    time.sleep(3)
    ws.send(json.dumps({"ticks": SELECTED_SYMBOL, "subscribe": 1}))

    while is_trading:
        try:
            if is_position_open or len(ticks_history) < 20:
                time.sleep(1)
                continue
            
            bias = get_bias()
            if bias and authorized: 
                send_proposal(ws, bias, current_stake)
                time.sleep(180) # 3 Minute wait kyu ki trade chal rahi hogi
            
            time.sleep(1) 
            
        except Exception as e:
            time.sleep(5)
    
    ws.close()

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
