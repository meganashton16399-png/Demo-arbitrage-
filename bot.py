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
API_TOKEN = "97TGFzZ36ZBulqy" # Hardcoded Deriv Token

# Telegram Env se lo (taaki secure rahe), ya hardcode kar lo agar error aa raha ho
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

# Fallback agar Env set nahi hai to (Sirf testing ke liye)
if not TELE_TOKEN:
    TELE_TOKEN = "8472550297:AAE05TUxFHedmwh8g0hrx4EnNjFaCo_LJ8E"
    MY_CHAT_ID = "8559974035"

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# Global Variables
is_trading = False
SELECTED_SYMBOL = ""
current_stake = 1.0  
multiplier_val = 10 # ✅ SAFE START: x100 ki jagah x10 kar raha hu test ke liye
martingale_factor = 2.0 
ticks_history = []
ws_connected = False 
is_position_open = False
authorized = False 

ASSETS = {
    "Bitcoin (BTCUSD)": "cryBTCUSD", 
    "Gold (XAUUSD)": "frxXAUUSD"     
}

# --- 2. SERVER ---
@app.route('/')
def home():
    return "Bot is Live! Debug Mode ON."

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

    buy_score = 0
    if ema9 > ema21: buy_score += 1
    if rsi > 50: buy_score += 1
    if current > prev: buy_score += 1

    # Sirf BUY logic test kar rahe hain
    if buy_score >= 2: return "buy"
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

        # 🚨 ERROR CATCHING (Ye batayega kyu fail hua)
        if 'error' in data:
            err_msg = data['error']['message']
            err_code = data['error']['code']
            print(f"❌ API Error: {err_msg}")
            
            # Telegram par bhejo error
            bot.send_message(MY_CHAT_ID, f"⚠️ Trade Failed: {err_msg}\nCode: {err_code}")
            
            is_position_open = False # Reset taaki atak na jaye
            return

        # Login Success
        if 'authorize' in data:
            authorized = True
            bot.send_message(MY_CHAT_ID, "✅ Login Success! Ready.")
            ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))

        # Ticks
        if 'tick' in data:
            price = data['tick']['quote']
            ticks_history.append(price)
            if len(ticks_history) > 100: ticks_history.pop(0)

        # ✅ STEP 2: Proposal Aaya -> Ab Buy Karo
        if 'proposal' in data:
            proposal_id = data['proposal']['id']
            # bot.send_message(MY_CHAT_ID, f"📨 Proposal ID: {proposal_id} received. Buying...")
            ws.send(json.dumps({"buy": proposal_id, "price": 1000}))

        # ✅ STEP 3: Buy Confirm Hua
        if 'buy' in data:
            buy_id = data['buy']['contract_id']
            bot.send_message(MY_CHAT_ID, f"🔫 Trade EXECUTED! ID: {buy_id}")

        # ✅ STEP 4: Trade Result
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
        # TP/SL Calculation
        take_profit_amt = round(amount * 0.5, 2) # Profit Target kam kiya for safety
        stop_loss_amt = round(amount * 0.8, 2)
        
        proposal_msg = {
            "proposal": 1,
            "amount": amount,
            "basis": "stake",
            "contract_type": "multiplier",
            "currency": "USD",
            "symbol": SELECTED_SYMBOL,
            "multiplier": multiplier_val, # Using x10 now
            "limit_order": {
                "take_profit": take_profit_amt,
                "stop_loss": stop_loss_amt
            }
        }
        
        ws.send(json.dumps(proposal_msg))
        is_position_open = True 
        bot.send_message(MY_CHAT_ID, f"⏳ Signal: {direction.upper()} | Requesting Server...")
        
    except Exception as e:
        bot.send_message(MY_CHAT_ID, f"⚠️ Local Error: {e}")
        is_position_open = False

# --- 5. COMMANDS ---
@bot.message_handler(commands=['trade'])
def ask_asset(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTCUSD)", "Gold (XAUUSD)")
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
    
    bot.send_message(message.chat.id, f"🚀 Debug Bot Started: {SELECTED_SYMBOL}", reply_markup=types.ReplyKeyboardRemove())
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
            if bias == "buy" and authorized: 
                send_proposal(ws, bias, current_stake)
                # Response aane ka wait karo before next logic
                time.sleep(15) 
            
            time.sleep(1) 
            
        except Exception as e:
            time.sleep(5)
    
    ws.close()

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
