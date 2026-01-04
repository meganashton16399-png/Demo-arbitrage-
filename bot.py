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
API_TOKEN = "97TGFzZ36ZBulqy" # Hardcoded Deriv Token

# Telegram Env se
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

if not TELE_TOKEN:
    TELE_TOKEN = "8472550297:AAE05TUxFHedmwh8g0hrx4EnNjFaCo_LJ8E"
    MY_CHAT_ID = "8559974035"

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- GLOBAL VARIABLES ---
is_trading = False
SELECTED_SYMBOL = ""
initial_stake = 1.0     
current_stake = 1.0     
martingale_factor = 2.1 
ticks_history = []
ws_connected = False 
is_position_open = False # Yeh sabse zaroori flag hai
authorized = False 
contract_duration = 30   # Default 30 Seconds

# Stats
stats = {
    "start_bal": 0.0, "current_bal": 0.0,
    "wins": 0, "losses": 0,
    "current_streak": 0, "max_streak": 0, "total_profit": 0.0
}

ASSETS = {
    "Volatility 100 (1s) Index": "1HZ100V", 
    "Bitcoin (BTCUSD)": "cryBTCUSD",        
    "Gold (XAUUSD)": "frxXAUUSD"            
}

# --- 2. SERVER ---
@app.route('/')
def home():
    return "Bot is Live! Instant Execution Mode."

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

    # Quick Scalping Logic (Fast Response)
    buy_score = 0
    sell_score = 0
    
    if ema9 > ema21: buy_score += 1
    else: sell_score += 1
    
    if rsi > 50: buy_score += 1
    else: sell_score += 1
    
    if current > prev: buy_score += 1
    else: sell_score += 1

    # 2/3 Confirmation for Speed
    if buy_score >= 2: return "buy"   
    if sell_score >= 2: return "sell"
    return None

# --- 4. DERIV HANDLERS ---
def on_open(ws):
    global ws_connected
    ws_connected = True
    print("🔌 Connecting...")
    auth_data = {"authorize": API_TOKEN}
    ws.send(json.dumps(auth_data))

def on_message(ws, message):
    global ticks_history, current_stake, is_position_open, authorized, stats
    try:
        data = json.loads(message)

        if 'error' in data:
            err_msg = data['error']['message']
            bot.send_message(MY_CHAT_ID, f"⚠️ Error: {err_msg}")
            # Agar trade fail hui to flag hatao taaki ruk na jaye
            if "Input validation" in err_msg or "Trading is not offered" in err_msg:
                 is_position_open = False 
            return

        if 'authorize' in data:
            authorized = True
            if stats["start_bal"] == 0:
                stats["start_bal"] = float(data['authorize']['balance'])
            stats["current_bal"] = float(data['authorize']['balance'])
            
            bot.send_message(MY_CHAT_ID, f"✅ Login Success! Ready to Snipe 🔫\nAsset: {SELECTED_SYMBOL}")
            ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))

        if 'tick' in data:
            price = data['tick']['quote']
            ticks_history.append(price)
            if len(ticks_history) > 100: ticks_history.pop(0)

        # Step 2: Buy the Proposal
        if 'proposal' in data:
            proposal_id = data['proposal']['id']
            ws.send(json.dumps({"buy": proposal_id, "price": 1000}))

        if 'buy' in data:
            # Trade lag gayi, ab bas result ka wait hai
            pass 

        # Step 3: INSTANT RESULT HANDLER
        if 'proposal_open_contract' in data:
            contract = data['proposal_open_contract']
            
            # Jab contract BIK jaye (Expired)
            if contract['is_sold']:
                profit = float(contract['profit'])
                stats["total_profit"] += profit
                stats["current_bal"] += profit 

                if profit > 0:
                    status = "🟢 WIN"
                    stats["wins"] += 1
                    stats["current_streak"] = 0 
                    current_stake = initial_stake # Back to Normal
                else:
                    status = "🔴 LOSS"
                    stats["losses"] += 1
                    stats["current_streak"] += 1
                    if stats["current_streak"] > stats["max_streak"]:
                        stats["max_streak"] = stats["current_streak"]
                    current_stake = round(current_stake * martingale_factor, 2) # Instant Recovery Amount

                msg = (f"{status} | P/L: ${profit}\n"
                       f"🔥 Next Stake: ${current_stake}")
                bot.send_message(MY_CHAT_ID, msg)
                
                # 🚀 CRITICAL: Flag false karte hi Loop agli trade utha lega
                is_position_open = False 

    except Exception as e:
        print(f"Error: {e}")

def send_proposal(ws, direction, amount):
    global is_position_open, authorized
    if not authorized: return

    try:
        contract = "CALL" if direction == "buy" else "PUT"
        
        proposal_msg = {
            "proposal": 1,
            "amount": amount,
            "basis": "stake",
            "contract_type": contract, 
            "currency": "USD",
            "symbol": SELECTED_SYMBOL,
            "duration": 30,     # Fixed 30 Seconds
            "duration_unit": "s" 
        }
        
        ws.send(json.dumps(proposal_msg))
        is_position_open = True # LOCK: Taaki ek hi time pe 2 trade na lage
        bot.send_message(MY_CHAT_ID, f"⏳ Trade: {direction.upper()} (${amount})")
        
    except Exception as e:
        bot.send_message(MY_CHAT_ID, f"⚠️ Proposal Error: {e}")
        is_position_open = False

# --- 5. COMMANDS ---

@bot.message_handler(commands=['stats'])
def show_stats(message):
    try:
        roi = stats["current_bal"] - stats["start_bal"]
        report = (
            f"📊 **LIVE STATS** 📊\n"
            f"💰 Bal: ${round(stats['current_bal'], 2)} ({round(roi, 2)})\n"
            f"✅ {stats['wins']} | ❌ {stats['losses']}\n"
            f"🔥 Next: ${current_stake}"
        )
        bot.send_message(message.chat.id, report, parse_mode="Markdown")
    except:
        bot.reply_to(message, "No data.")

@bot.message_handler(commands=['trade'])
def ask_asset(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True, row_width=1)
    markup.add("Volatility 100 (1s) Index", "Bitcoin (BTCUSD)", "Gold (XAUUSD)")
    msg = bot.send_message(message.chat.id, "Select Asset:", reply_markup=markup)
    bot.register_next_step_handler(msg, ask_stake)

def ask_stake(message):
    global SELECTED_SYMBOL
    if message.text not in ASSETS:
        bot.send_message(message.chat.id, "❌ Invalid. Try /trade again.")
        return
    SELECTED_SYMBOL = ASSETS[message.text]
    msg = bot.send_message(message.chat.id, "💰 Lot Size (e.g. 1, 2):", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, start_bot_process)

def start_bot_process(message):
    global is_trading, initial_stake, current_stake, ticks_history, is_position_open, authorized, stats
    try:
        user_stake = float(message.text)
    except:
        bot.send_message(message.chat.id, "⚠️ Invalid number.")
        return

    if is_trading:
        bot.send_message(message.chat.id, "Already Running.")
        return

    initial_stake = user_stake
    current_stake = user_stake
    is_trading = True
    ticks_history = [] 
    is_position_open = False # Start fresh
    authorized = False 
    
    # Stats Reset
    stats = {
        "start_bal": 0.0, "current_bal": 0.0,
        "wins": 0, "losses": 0,
        "current_streak": 0, "max_streak": 0, "total_profit": 0.0
    }
    
    bot.send_message(message.chat.id, f"🚀 FAST MODE: {SELECTED_SYMBOL}\nStake: ${initial_stake} | Time: 30s")
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.reply_to(message, "🛑 Stopped.")

# --- 6. MAIN LOOP (NO SLEEP) ---
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
            # 🚀 SPEED LOGIC:
            # Agar trade chal rahi hai (is_position_open = True), to bas check karo.
            # Agar False hai, to TURANT nayi trade dhoondo.
            
            if is_position_open:
                time.sleep(0.5) # Sirf 0.5s check delay taaki CPU na jale
                continue
            
            # Wait for enough data (sirf start me)
            if len(ticks_history) < 20:
                time.sleep(1)
                continue
            
            bias = get_bias()
            if bias and authorized: 
                send_proposal(ws, bias, current_stake)
                # Yahan koi sleep nahi hai! Proposal bhejte hi 'is_position_open' True ho jayega.
                # Loop wapas upar jayega aur trade khatam hone ka wait karega.
            
            time.sleep(1) 
            
        except Exception as e:
            time.sleep(5)
    
    ws.close()

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
