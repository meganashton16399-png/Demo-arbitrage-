import telebot
from telebot import types
import ccxt
import pandas as pd
import pandas_ta as ta
import time
import os
from flask import Flask
import threading
import random

# --- 1. SETUP ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

if not TELE_TOKEN:
    TELE_TOKEN = "YOUR_TOKEN"
    MY_CHAT_ID = "YOUR_ID"

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- GLOBAL SETTINGS ---
is_trading = False
SELECTED_ASSET = ""
SELECTED_SYMBOL = "" # Kraken Symbol
TIMEFRAME = "1m"     # 1 Minute Live Candles
TP_PERCENT = 0.002   # 0.2% Profit Target
SL_PERCENT = 0.003   # 0.3% Stop Loss

# Initialize Kraken (Public Data Only - No Keys Needed)
exchange = ccxt.kraken()

wallet = {
    "balance": 10000.0, 
    "positions": [],    
    "history": []       
}

# Real-Time Assets (Kraken Pairs)
ASSETS = {
    "Bitcoin (BTC)": "BTC/USD",
    "Gold (XAU)": "XAU/USD"  # Yes, Kraken has Spot Gold!
}

# --- 2. SERVER ---
@app.route('/')
def home():
    return "Bot is Live with CCXT Data!"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web_server)
    t.start()

# --- 3. LIVE MARKET DATA ---
def get_live_data(symbol):
    try:
        # Fetch OHLCV (Open, High, Low, Close, Volume)
        # Limit 50 candles is enough for indicators
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=50)
        
        if not ohlcv: return None
        
        # Convert to Pandas DataFrame
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # Calculate Indicators
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        return df.iloc[-1] # Return Latest Candle
    except Exception as e:
        print(f"Kraken API Error: {e}")
        return None

def check_exit_conditions(current_price):
    global wallet
    for pos in wallet["positions"][:]: 
        entry_price = pos['entry']
        quantity = pos['qty']
        direction = pos['type']
        
        if direction == "BUY":
            pnl = (current_price - entry_price) * quantity
            pnl_percent = (current_price - entry_price) / entry_price
        else:
            pnl = (entry_price - current_price) * quantity
            pnl_percent = (entry_price - current_price) / entry_price
            
        if pnl_percent >= TP_PERCENT or pnl_percent <= -SL_PERCENT:
            wallet["balance"] += (pos['margin'] + pnl)
            wallet["positions"].remove(pos)
            
            status = "🟢 WIN" if pnl > 0 else "🔴 LOSS"
            wallet["history"].append({"result": status, "pnl": pnl})
            
            msg = (f"{status} | {SELECTED_ASSET}\n"
                   f"💵 P/L: ${round(pnl, 2)}\n"
                   f"🏦 Bal: ${round(wallet['balance'], 2)}\n"
                   f"⚡ Price: {current_price}")
            bot.send_message(MY_CHAT_ID, msg)

# --- 4. MAIN LOOP ---
def trading_loop():
    global is_trading
    print(f"🔥 Live Monitor Started on {SELECTED_SYMBOL}")
    
    while is_trading:
        try:
            # 1. Get FAST Data
            data = get_live_data(SELECTED_SYMBOL)
            if data is None: 
                time.sleep(2) # Retry fast
                continue
                
            current_price = float(data['close'])
            
            # 2. Check Exits
            if len(wallet["positions"]) > 0:
                check_exit_conditions(current_price)
                
            # 3. Check Entries
            if len(wallet["positions"]) == 0:
                ema9 = data['ema9']
                ema21 = data['ema21']
                rsi = data['rsi']
                
                signal = None
                # Strategy Logic
                if ema9 > ema21 and rsi > 55: signal = "BUY"
                elif ema9 < ema21 and rsi < 45: signal = "SELL"
                
                if signal:
                    stake = 100.0 
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
                        f"🔫 {signal} ORDER\n"
                        f"Asset: {SELECTED_ASSET}\n"
                        f"Price: ${current_price}\n"
                        f"RSI: {round(rsi, 2)}")
                    
                    time.sleep(30) # 30 sec cooldown
            
            # Fast Polling (Real API allows faster checks)
            time.sleep(3) 
            
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

# --- 5. COMMANDS ---
@bot.message_handler(commands=['trade'])
def start_trade(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTC)", "Gold (XAU)")
    msg = bot.send_message(message.chat.id, "Select Asset (Live Data):", reply_markup=markup)
    bot.register_next_step_handler(msg, set_asset)

def set_asset(message):
    global is_trading, SELECTED_ASSET, SELECTED_SYMBOL
    
    if message.text not in ASSETS:
        bot.send_message(message.chat.id, "❌ Invalid.")
        return
        
    SELECTED_ASSET = message.text
    SELECTED_SYMBOL = ASSETS[message.text]
    is_trading = True
    
    bot.send_message(message.chat.id, 
        f"🚀 Bot Started on {SELECTED_ASSET}!\n"
        f"📡 Source: Kraken Public API (Real-Time)\n"
        f"🏦 Virtual Bal: ${wallet['balance']}")
    
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['stats'])
def get_stats(message):
    wins = len([x for x in wallet["history"] if x["result"] == "🟢 WIN"])
    losses = len([x for x in wallet["history"] if x["result"] == "🔴 LOSS"])
    
    report = (f"📊 **LIVE STATS**\n"
              f"🏦 Bal: ${round(wallet['balance'], 2)}\n"
              f"✅ W: {wins} | ❌ L: {losses}\n"
              f"🔓 Open: {len(wallet['positions'])}")
    bot.send_message(message.chat.id, report, parse_mode="Markdown")

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.send_message(message.chat.id, "🛑 Stopped.")

@bot.message_handler(commands=['reset'])
def reset_bal(message):
    wallet["balance"] = 10000.0
    wallet["positions"] = []
    wallet["history"] = []
    bot.send_message(message.chat.id, "🔄 Reset Done.")

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
