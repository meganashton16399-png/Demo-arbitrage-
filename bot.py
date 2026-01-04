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

# Telegram Env se
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

if not TELE_TOKEN:
    TELE_TOKEN = "8472550297:AAE05TUxFHedmwh8g0hrx4EnNjFaCo_LJ8E"
    MY_CHAT_ID = "8559974035"

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- GLOBAL VARIABLES & STATS ---
is_trading = False
SELECTED_SYMBOL = ""
initial_stake = 1.0     # User defined start stake
current_stake = 1.0     # Martingale stake
martingale_factor = 2.1 
ticks_history = []
ws_connected = False 
is_position_open = False
authorized = False 

# Stats Tracking
stats = {
    "start_bal": 0.0,
    "current_bal": 0.0,
    "wins": 0,
    "losses": 0,
    "current_streak": 0,
    "max_streak": 0,
    "total_profit": 0.0
}

# Assets
ASSETS = {
    "Volatility 100 (1s) Index": "1HZ100V", 
    "Bitcoin (BTCUSD)": "cryBTCUSD"         
}

# --- 2. SERVER ---
@app.route('/')
def home():
    return "Bot is Live! Stats Module Added."

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# --- 3. TRADING LOGIC (3-Min Price Action) ---
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

    if buy_score == 3: return "buy"   
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
    global ticks_history, current_stake, is_position_open, authorized, stats
    try:
        data = json.loads(message)

        if 'error' in data:
            err_msg = data['error']['message']
            bot.send_message(MY_CHAT_ID, f"⚠️ Error: {err_msg}")
            is_position_open = False 
            return

        if 'authorize' in data:
            authorized = True
            # Initial Balance Set (If 0)
            if stats["start_bal"] == 0:
                stats["start_bal"] = float(data['authorize']['balance'])
            stats["current_bal"] = float(data['authorize']['balance'])
            
            bot.send_message(MY_CHAT_ID, f"✅ Login Success!\nBase Stake: ${initial_stake}")
            ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))

        if 'tick' in data:
            price = data['tick']['quote']
            ticks_history.append(price)
            if len(ticks_history) > 100: ticks_history.pop(0)

        # Execute Trade
        if 'proposal' in data:
            proposal_id = data['proposal']['id']
            ws.send(json.dumps({"buy": proposal_id, "price": 1000}))

        if 'buy' in data:
            bot.send_message(MY_CHAT_ID, f"🔫 Trade Placed (3 Min)...")

        # Trade Result & Stats Update
        if 'proposal_open_contract' in data:
            contract = data['proposal_open_contract']
            
            if contract['is_sold']:
                is_position_open = False 
                profit = float(contract['profit'])
                stats["total_profit"] += profit
                
                # Update Stats
                if profit > 0:
                    status = "🟢 WIN"
                    stats["wins"] += 1
                    stats["current_streak"] = 0 # Streak Reset
                    current_stake = initial_stake # Reset Stake
                else:
                    status = "🔴 LOSS"
                    stats["losses"] += 1
                    stats["current_streak"] += 1
                    # Update Max Streak
                    if stats["current_streak"] > stats["max_streak"]:
                        stats["max_streak"] = stats["current_streak"]
                    
                    current_stake = round(current_stake * martingale_factor, 2)
                
                # Balance Update (Approx)
                stats["current_bal"] += profit 

                msg = (f"{status}\n"
                       f"💵 Profit: ${profit}\n"
                       f"📈 Total P/L: ${round(stats['total_profit'], 2)}\n"
                       f"🔄 Next Stake: ${current_stake}")
                bot.send_message(MY_CHAT_ID, msg)

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
            "duration": 3,      
            "duration_unit": "m" 
        }
        
        ws.send(json.dumps(proposal_msg))
        is_position_open = True 
        bot.send_message(MY_CHAT_ID, f"⏳ Signal: {direction.upper()} | Stake: ${amount}")
        
    except Exception as e:
        bot.send_message(MY_CHAT_ID, f"⚠️ Error: {e}")
        is_position_open = False

# --- 5. COMMANDS ---

# Command 1: /stats
@bot.message_handler(commands=['stats'])
def show_stats(message):
    try:
        if stats["start_bal"] == 0:
            bot.reply_to(message, "⚠️ No data yet. Start trading first.")
            return
            
        roi = stats["current_bal"] - stats["start_bal"]
        
        report = (
            f"📊 **CURRENT SESSION STATS** 📊\n\n"
            f"💰 Start Balance: ${stats['start_bal']}\n"
            f"🤑 Current Balance: ${round(stats['current_bal'], 2)}\n"
            f"💹 Net Profit: ${round(roi, 2)}\n\n"
            f"✅ Wins: {stats['wins']}\n"
            f"❌ Losses: {stats['losses']}\n"
            f"💀 Highest Losing Streak: {stats['max_streak']}\n"
            f"🔥 Current Stake: ${current_stake}"
        )
        bot.send_message(message.chat.id, report, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"Error fetching stats: {e}")

# Command 2: /trade (Updated flow)
@bot.message_handler(commands=['trade'])
def ask_asset(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Volatility 100 (1s) Index", "Bitcoin (BTCUSD)")
    msg = bot.send_message(message.chat.id, "Select Asset:", reply_markup=markup)
    bot.register_next_step_handler(msg, ask_stake)

def ask_stake(message):
    global SELECTED_SYMBOL
    if message.text not in ASSETS:
        bot.send_message(message.chat.id, "❌ Invalid Asset. Try /trade again.")
        return
    
    SELECTED_SYMBOL = ASSETS[message.text]
    msg = bot.send_message(message.chat.id, "💰 Enter Initial Lot Size (e.g. 0.5, 1, 5):", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, start_bot_process)

def start_bot_process(message):
    global is_trading, initial_stake, current_stake, ticks_history, is_position_open, authorized, stats
    
    try:
        user_stake = float(message.text)
        if user_stake < 0.35:
            bot.send_message(message.chat.id, "⚠️ Minimum stake is $0.35. Try /trade again.")
            return
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ Invalid number. Try /trade again.")
        return

    if is_trading:
        bot.send_message(message.chat.id, "Bot already running!")
        return

    # Reset & Start
    initial_stake = user_stake
    current_stake = user_stake
    is_trading = True
    ticks_history = [] 
    is_position_open = False
    authorized = False 
    
    # Reset Stats for new run
    stats = {
        "start_bal": 0.0, "current_bal": 0.0,
        "wins": 0, "losses": 0,
        "current_streak": 0, "max_streak": 0, "total_profit": 0.0
    }
    
    bot.send_message(message.chat.id, f"🚀 Bot Started on {SELECTED_SYMBOL}\nStake: ${initial_stake}")
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.reply_to(message, "🛑 Stopped. Use /stats to see final report.")

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
                time.sleep(180) # 3 Min Wait
            
            time.sleep(1) 
            
        except Exception as e:
            time.sleep(5)
    
    ws.close()

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
