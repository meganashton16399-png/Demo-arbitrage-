import telebot
from telebot import types
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import time
import os
from flask import Flask
import threading  # ✅ FIXED: Sahi Import
import random

# --- 1. SETUP ---
# Telegram Keys
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

# Fallback (Agar Env fail ho to)
if not TELE_TOKEN:
    TELE_TOKEN = "YOUR_BOT_TOKEN_HERE" 
    MY_CHAT_ID = "YOUR_CHAT_ID_HERE"

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- GLOBAL SETTINGS ---
is_trading = False
SELECTED_ASSET = ""
SELECTED_TICKER = ""
TIMEFRAME = "1m" # 1 Minute Candles
TP_PERCENT = 0.002 # 0.2% Profit
SL_PERCENT = 0.003 # 0.3% Stop Loss

# Virtual Wallet
wallet = {
    "balance": 10000.0, 
    "positions": [],    
    "history": []       
}

# Assets Map
ASSETS = {
    "Bitcoin (BTC)": "BTC-USD",
    "Gold (XAU)": "GC=F" 
}

# --- 2. SERVER ---
@app.route('/')
def home():
    return "Virtual Trading Bot is Live!"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web_server) # ✅ Fixed usage
    t.start()

# --- 3. MARKET DATA & STRATEGY ---
def get_live_data(ticker):
    try:
        # Fetch last 1 day data (1m interval)
        df = yf.download(ticker, period="1d", interval=TIMEFRAME, progress=False)
        if len(df) < 20: return None
        
        # Flatten columns if MultiIndex (yfinance update fix)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Calculate Indicators
        df['ema9'] = ta.ema(df['Close'], length=9)
        df['ema21'] = ta.ema(df['Close'], length=21)
        df['rsi'] = ta.rsi(df['Close'], length=14)
        
        return df.iloc[-1] # Latest Candle
    except Exception as e:
        print(f"Data Error: {e}")
        return None

def check_exit_conditions(current_price):
    global wallet
    # Check all open positions
    for pos in wallet["positions"][:]: # Iterate copy
        entry_price = pos['entry']
        quantity = pos['qty']
        direction = pos['type']
        
        # Calculate P/L
        if direction == "BUY":
            pnl = (current_price - entry_price) * quantity
            pnl_percent = (current_price - entry_price) / entry_price
        else:
            pnl = (entry_price - current_price) * quantity
            pnl_percent = (entry_price - current_price) / entry_price
            
        # TP or SL Hit?
        if pnl_percent >= TP_PERCENT or pnl_percent <= -SL_PERCENT:
            wallet["balance"] += (pos['margin'] + pnl)
            wallet["positions"].remove(pos)
            
            status = "🟢 WIN" if pnl > 0 else "🔴 LOSS"
            wallet["history"].append({"result": status, "pnl": pnl})
            
            msg = (f"{status} | {SELECTED_ASSET}\n"
                   f"💰 P/L: ${round(pnl, 2)}\n"
                   f"🏦 New Bal: ${round(wallet['balance'], 2)}")
            bot.send_message(MY_CHAT_ID, msg)

# --- 4. TRADING LOOP ---
def trading_loop():
    global is_trading
    print("Market Monitor Started...")
    
    while is_trading:
        try:
            # 1. Get Data
            data = get_live_data(SELECTED_TICKER)
            if data is None: 
                time.sleep(5)
                continue
                
            current_price = float(data['Close'])
            
            # 2. Check Exits (SL/TP)
            if len(wallet["positions"]) > 0:
                check_exit_conditions(current_price)
                
            # 3. Check Entries (Strategy)
            # Only enter if no position open (1 trade at a time)
            if len(wallet["positions"]) == 0:
                ema9 = data['ema9']
                ema21 = data['ema21']
                rsi = data['rsi']
                
                # Logic
                signal = None
                if ema9 > ema21 and rsi > 50: signal = "BUY"
                elif ema9 < ema21 and rsi < 50: signal = "SELL"
                
                if signal:
                    # Execute Virtual Trade
                    stake = 100.0 # $100 per trade
                    qty = stake / current_price
                    
                    trade = {
                        "entry": current_price,
                        "qty": qty,
                        "type": signal,
                        "margin": stake,
                        "time": time.time()
                    }
                    wallet["balance"] -= stake
                    wallet["positions"].append(trade)
                    
                    bot.send_message(MY_CHAT_ID, 
                        f"🔫 {signal} ORDER EXECUTED\n"
                        f"Asset: {SELECTED_ASSET}\n"
                        f"Price: ${round(current_price, 2)}\n"
                        f"Target: +{TP_PERCENT*100}% | Stop: -{SL_PERCENT*100}%")
                    
                    time.sleep(60) # Wait 1 min to avoid spam
            
            time.sleep(5) 
            
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

# --- 5. COMMANDS ---
@bot.message_handler(commands=['trade'])
def start_trade(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTC)", "Gold (XAU)")
    msg = bot.send_message(message.chat.id, "Select Asset for Virtual Trading:", reply_markup=markup)
    bot.register_next_step_handler(msg, set_asset)

def set_asset(message):
    global is_trading, SELECTED_ASSET, SELECTED_TICKER
    
    if message.text not in ASSETS:
        bot.send_message(message.chat.id, "❌ Invalid Asset.")
        return
        
    SELECTED_ASSET = message.text
    SELECTED_TICKER = ASSETS[message.text]
    is_trading = True
    
    bot.send_message(message.chat.id, 
        f"🚀 Virtual Bot Started on {SELECTED_ASSET}!\n"
        f"🏦 Demo Balance: ${wallet['balance']}\n"
        f"📡 Data Source: Yahoo Finance Live")
    
    # Start Loop
    threading.Thread(target=trading_loop).start() # ✅ FIXED: Sahi usage

@bot.message_handler(commands=['stats'])
def get_stats(message):
    wins = len([x for x in wallet["history"] if x["result"] == "🟢 WIN"])
    losses = len([x for x in wallet["history"] if x["result"] == "🔴 LOSS"])
    
    report = (f"📊 **VIRTUAL ACCOUNT STATS**\n"
              f"🏦 Balance: ${round(wallet['balance'], 2)}\n"
              f"✅ Wins: {wins} | ❌ Losses: {losses}\n"
              f"🔓 Open Trades: {len(wallet['positions'])}")
    bot.send_message(message.chat.id, report, parse_mode="Markdown")

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.send_message(message.chat.id, "🛑 Trading Stopped.")

@bot.message_handler(commands=['reset'])
def reset_bal(message):
    wallet["balance"] = 10000.0
    wallet["positions"] = []
    wallet["history"] = []
    bot.send_message(message.chat.id, "🔄 Wallet Reset to $10,000.")

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
