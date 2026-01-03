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

# --- 1. NEW CREDENTIALS ---
APP_ID = 119348  # Generic App ID
API_TOKEN = "3QNHozkAw8IhdMV" # ✅ Updated Deriv Token
TELE_TOKEN = "8472550297:AAE05TUxFHedmwh8g0hrx4EnNjFaCo_LJ8E" # ✅ Updated Bot Token
MY_CHAT_ID = "8559974035" 

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# Global Variables
is_trading = False
SELECTED_SYMBOL = ""
current_stake = 1.0  # Starting Stake $1
multiplier_val = 100 # Leverage x100
martingale_factor = 2.0 # Loss recovery multiplier
ticks_history = []
ws_connected = False 
is_position_open = False # Strict rule: 1 trade at a time

# ✅ ASSETS (Use Bitcoin for Weekend)
ASSETS = {
    "Bitcoin (BTCUSD)": "cryBTCUSD", 
    "Gold (XAUUSD)": "frxXAUUSD"     
}

# --- 2. UPTIME SERVER ---
@app.route('/')
def home():
    return "Bot is Live! Strategy: Multipliers."

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# --- 3. TRADING LOGIC (Trend + Momentum) ---
def get_bias():
    global ticks_history
    if len(ticks_history) < 20: return None 
    
    df = pd.DataFrame(ticks_history, columns=['close'])
    
    # EMA Crossover
    ema9 = ta.ema(df['close'], length=9).iloc[-1]
    ema21 = ta.ema(df['close'], length=21).iloc[-1]
    
    # RSI
    rsi = ta.rsi(df['close'], length=14).iloc[-1]
    
    current = df['close'].iloc[-1]
    prev = df['close'].iloc[-2]

    buy_score = 0
    sell_score = 0
    
    if ema9 > ema21: buy_score += 1
    else: sell_score += 1
    
    if rsi > 50: buy_score += 1
    else: sell_score += 1
    
    if current > prev: buy_score += 1
    else: sell_score += 1

    # Need 2 out of 3 confirmations
    if buy_score >= 2: return "buy"
    if sell_score >= 2: return "sell"
    return None

# --- 4. DERIV HANDLERS ---
def on_open(ws):
    global ws_connected
    ws_connected = True
    auth_data = {"authorize": API_TOKEN}
    ws.send(json.dumps(auth_data))

def on_message(ws, message):
    global ticks_history, current_stake, is_position_open
    try:
        data = json.loads(message)

        if 'error' in data:
            err_msg = data['error']['message']
            bot.send_message(MY_CHAT_ID, f"⚠️ Error: {err_msg}")
            if "Market is closed" in err_msg:
                is_position_open = False
            return

        if 'balance' in data:
            bal = data['balance']['balance']
            # Balance spam rokne ke liye yahan print nahi kar rahe, /bal command se milega

        if 'tick' in data:
            price = data['tick']['quote']
            ticks_history.append(price)
            if len(ticks_history) > 100: ticks_history.pop(0)

        # ✅ TRADE RESULT MONITORING
        if 'proposal_open_contract' in data:
            contract = data['proposal_open_contract']
            
            # Jab Trade Close ho (TP/SL Hit)
            if contract['is_sold']:
                is_position_open = False # ✅ Ready for next trade
                
                profit = float(contract['profit'])
                
                if profit > 0:
                    status = "🟢 TP HIT (WIN)"
                    current_stake = 1.0 # Reset to $1
                else:
                    status = "🔴 SL HIT (LOSS)"
                    current_stake = round(current_stake * martingale_factor, 2) # Double for recovery
                
                msg = (f"{status}\n"
                       f"📊 Profit/Loss: ${profit}\n"
                       f"🔄 Next Stake: ${current_stake}")
                bot.send_message(MY_CHAT_ID, msg)

    except Exception as e:
        print(f"Error: {e}")

def place_order(ws, direction, amount):
    global is_position_open
    try:
        # ✅ STRATEGY: Take Profit @ 60% | Stop Loss @ 80%
        take_profit_amt = round(amount * 0.6, 2)
        stop_loss_amt = round(amount * 0.8, 2)
        
        trade_msg = {
            "buy": 1,
            "price": amount,
            "parameters": {
                "amount": amount,
                "basis": "stake",
                "contract_type": "multiplier", # REAL TRADING
                "currency": "USD",
                "symbol": SELECTED_SYMBOL,
                "multiplier": multiplier_val,
                "take_profit": take_profit_amt,
                "stop_loss": stop_loss_amt
            }
        }
        ws.send(json.dumps(trade_msg))
        is_position_open = True # Lock trade
        bot.send_message(MY_CHAT_ID, f"🔫 Entry: {direction.upper()}\nStake: ${amount} (x{multiplier_val})\nTP: +${take_profit_amt} | SL: -${stop_loss_amt}")
        
    except Exception as e:
        bot.send_message(MY_CHAT_ID, f"⚠️ Order Fail: {e}")
        is_position_open = False

# --- 5. COMMANDS ---
@bot.message_handler(commands=['trade'])
def ask_asset(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTCUSD)", "Gold (XAUUSD)")
    bot.send_message(message.chat.id, "Select Market (Weekend = Bitcoin):", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ASSETS.keys())
def start_bot(message):
    global is_trading, SELECTED_SYMBOL, current_stake, ticks_history, is_position_open
    if is_trading:
        bot.send_message(message.chat.id, "Bot already running!")
        return

    SELECTED_SYMBOL = ASSETS[message.text]
    is_trading = True
    ticks_history = [] 
    current_stake = 1.0 # Reset stake on new start
    is_position_open = False
    
    bot.send_message(message.chat.id, f"🚀 Real Strategy Active: {SELECTED_SYMBOL}\nWaiting for Entry Signal...", reply_markup=types.ReplyKeyboardRemove())
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['bal'])
def check_balance(message):
    if not ws_connected:
        bot.reply_to(message, "⚠️ Bot disconnected. Start /trade first.")
        return
    bot.reply_to(message, "🏦 Checking Balance...")
    # WebSocket response will be handled in loop

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
    ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1})) # Listen for trade Close
    ws.send(json.dumps({"balance": 1, "subscribe": 1}))

    while is_trading:
        try:
            # 1. Agar Trade Open hai, toh Intezaar karo (Strict Rule)
            if is_position_open:
                time.sleep(1)
                continue

            # 2. Data Collection (Need 20 ticks for EMA)
            if len(ticks_history) < 20:
                time.sleep(1)
                continue
            
            # 3. Analysis & Entry
            bias = get_bias()
            if bias:
                place_order(ws, bias, current_stake)
                time.sleep(10) # Thoda pause taaki order process ho jaye
            
            time.sleep(1) 
            
        except Exception as e:
            time.sleep(5)
    
    ws.close()

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
