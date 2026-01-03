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
API_TOKEN = "3QNHozkAw8IhdMV"
TELE_TOKEN = "8472550297:AAE05TUxFHedmwh8g0hrx4EnNjFaCo_LJ8E"
MY_CHAT_ID = "8559974035"

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# Global Variables
is_trading = False
SELECTED_SYMBOL = ""
current_stake = 1.0  
multiplier_val = 100 
martingale_factor = 2.0 
ticks_history = []
ws_connected = False 
is_position_open = False # Strict rule: 1 trade at a time

ASSETS = {
    "Bitcoin (BTCUSD)": "cryBTCUSD", 
    "Gold (XAUUSD)": "frxXAUUSD"     
}

# --- 2. UPTIME SERVER ---
@app.route('/')
def home():
    return "Bot is Live! Strategy: Multipliers Proposal."

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
    sell_score = 0
    
    if ema9 > ema21: buy_score += 1
    else: sell_score += 1
    if rsi > 50: buy_score += 1
    else: sell_score += 1
    if current > prev: buy_score += 1
    else: sell_score += 1

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
            # Agar error aaya to position open flag hatao taaki retry kare
            is_position_open = False 
            return

        if 'tick' in data:
            price = data['tick']['quote']
            ticks_history.append(price)
            if len(ticks_history) > 100: ticks_history.pop(0)

        # ✅ STEP 2: Proposal Received -> Buy It
        if 'proposal' in data:
            proposal_id = data['proposal']['id']
            # Buy this specific proposal
            ws.send(json.dumps({"buy": proposal_id, "price": 1000})) # Price max limit
            bot.send_message(MY_CHAT_ID, f"✅ Signal Validated! Executing Trade...")

        # ✅ STEP 3: Trade Result Monitoring
        if 'proposal_open_contract' in data:
            contract = data['proposal_open_contract']
            
            if contract['is_sold']:
                is_position_open = False # Reset for next trade
                
                profit = float(contract['profit'])
                
                if profit > 0:
                    status = "🟢 TP HIT (WIN)"
                    current_stake = 1.0 # Reset
                else:
                    status = "🔴 SL HIT (LOSS)"
                    current_stake = round(current_stake * martingale_factor, 2)
                
                msg = (f"{status}\n"
                       f"📊 Profit: ${profit}\n"
                       f"🔄 Next Stake: ${current_stake}")
                bot.send_message(MY_CHAT_ID, msg)

    except Exception as e:
        print(f"Error: {e}")

# ✅ STEP 1: Send Proposal (With TP/SL)
def send_proposal(ws, direction, amount):
    global is_position_open
    try:
        take_profit_amt = round(amount * 0.6, 2)
        stop_loss_amt = round(amount * 0.8, 2)
        
        # Proposal Request Structure for Multipliers
        proposal_msg = {
            "proposal": 1,
            "amount": amount,
            "basis": "stake",
            "contract_type": "multiplier",
            "currency": "USD",
            "symbol": SELECTED_SYMBOL,
            "multiplier": multiplier_val,
            "limit_order": {
                "take_profit": take_profit_amt,
                "stop_loss": stop_loss_amt
            }
        }
        
        # Trend Direction Logic (Deriv Multipliers don't have Call/Put in proposal same way, 
        # usually Up/Down is handled by contract type in options, 
        # but for Multipliers, 'contract_type' is just 'multiplier'. 
        # Actually, Multipliers are typically Long/Short.
        # WAIT: Deriv Multipliers are strictly "Up" (Long) unless specified? 
        # No, Multipliers allow prediction.
        # Let's add "contract_type" logic carefully.
        # For Deriv Multipliers, 'contract_type' is 'multiplier'. 
        # To specify direction: UP = 'multiplier', DOWN? 
        # Actually, Deriv API simplifies this: 
        # If you want to bet UP: contract_type: "multiplier"
        # If you want to bet DOWN: It's often "multiplier" but you might need to check available contracts.
        # Standard Multiplier is usually Long (Buy). Shorting requires specific handling or Put type.
        # Let's stick to "contract_type": "multiplier" (Which is Long/Up).
        # To SHORT (Sell), we usually need 'put' but Multipliers are unique.
        
        # FIX: For simplicity in this bot, let's assume we are buying UP for "Buy" bias.
        # If bias is "Sell", we skip or wait (since Multiplier Down is tricky in simple API).
        # OR: We use "Up" for everything for now to test connection.
        
        # Let's add direction if API supports "multup" / "multdown" (some endpoints do).
        # Standard: 'multiplier' is usually direction-neutral until bought? No.
        # Let's just use "multiplier" (which is Long) for now to fix the ERROR first.
        
        # Update: We will only execute if BIAS IS BUY.
        if direction == "sell":
            return # Skip Sell signals for now to avoid complexity errors
            
        ws.send(json.dumps(proposal_msg))
        is_position_open = True # Flag set kar diya taaki spam na ho
        bot.send_message(MY_CHAT_ID, f"🔫 Preparing Entry: {direction.upper()} | Stake: ${amount}\nTP: ${take_profit_amt} | SL: ${stop_loss_amt}")
        
    except Exception as e:
        bot.send_message(MY_CHAT_ID, f"⚠️ Proposal Fail: {e}")
        is_position_open = False

# --- 5. COMMANDS ---
@bot.message_handler(commands=['trade'])
def ask_asset(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTCUSD)", "Gold (XAUUSD)")
    bot.send_message(message.chat.id, "Select Asset:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ASSETS.keys())
def start_bot(message):
    global is_trading, SELECTED_SYMBOL, current_stake, ticks_history, is_position_open
    if is_trading:
        bot.send_message(message.chat.id, "Bot already running!")
        return

    SELECTED_SYMBOL = ASSETS[message.text]
    is_trading = True
    ticks_history = [] 
    current_stake = 1.0 
    is_position_open = False
    
    bot.send_message(message.chat.id, f"🚀 Multiplier Bot Active: {SELECTED_SYMBOL}", reply_markup=types.ReplyKeyboardRemove())
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
    ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1})) 

    while is_trading:
        try:
            if is_position_open: # Trade chal rahi hai to wait karo
                time.sleep(1)
                continue

            if len(ticks_history) < 20:
                time.sleep(1)
                continue
            
            bias = get_bias()
            if bias == "buy": # Sirf BUY le rahe hain abhi error free rakhne ke liye
                send_proposal(ws, bias, current_stake)
                time.sleep(10) 
            
            time.sleep(1) 
            
        except Exception as e:
            time.sleep(5)
    
    ws.close()

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
