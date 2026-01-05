import telebot
from telebot import types
import ccxt
import pandas as pd
import pandas_ta as ta
import time
import os
from flask import Flask
import threading

# --- 1. SECURE CONFIGURATION (ENV VARS) ---
API_KEY = os.environ.get("BYBIT_API")
API_SECRET = os.environ.get("BYBIT_SC")
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

# Error Handling for missing keys
if not API_KEY or not API_SECRET:
    print("⚠️ Warning: Bybit API Keys are missing in Environment Variables!")

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- GLOBAL SETTINGS ---
is_trading = False
SELECTED_SYMBOL = "" 
INITIAL_STAKE = 10.0  # USDT
CURRENT_STAKE = 10.0
LEVERAGE = 10         # 10x Leverage

# Scalping Rules (Price Change %)
# 0.001 = 0.1% Price Move (Approx $2-$3 move in Gold) -> Fast Scalp
TP_PERCENT = 0.001 
SL_PERCENT = 0.002
MARTINGALE_FACTOR = 2.5

# Bybit Setup (CCXT)
exchange = ccxt.bybit({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',  # Futures Trading
        'adjustForTimeDifference': True
    }
})
exchange.set_sandbox_mode(True) # ✅ TESTNET MODE ACTIVE

# Stats
stats = {
    "wins": 0,
    "losses": 0,
    "current_streak": 0
}

# Assets Map
ASSETS = {
    "Bitcoin (BTC)": "BTC/USDT:USDT",
    "Gold (XAU)": "XAU/USDT:USDT"
}

# --- 2. SERVER (Keep Alive) ---
@app.route('/')
def home():
    return "Bybit Scalper Bot is Running 24/7!"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web_server)
    t.start()

# --- 3. TRADING LOGIC ---
def get_market_data(symbol):
    try:
        # Fetch last 50 candles (1 Minute)
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=50)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # Calculate Indicators
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        return df.iloc[-1]
    except Exception as e:
        print(f"Data Error: {e}")
        return None

def check_and_execute_trade():
    global CURRENT_STAKE, stats
    
    # 1. Check Open Positions
    try:
        positions = exchange.fetch_positions([SELECTED_SYMBOL])
        active_pos = [p for p in positions if float(p['contracts']) > 0]
        
        # Agar trade chal rahi hai, toh kuch mat karo (Wait for TP/SL)
        if len(active_pos) > 0:
            return
            
        # Agar trade nahi hai, check karo last trade ka result (Martingale Logic)
        # Note: CCXT me PnL history fetch karna complex hai, 
        # isliye hum simple logic rakhenge: 
        # "Agar position close ho gayi, matlab TP/SL hit hua"
        # Real PnL track karne ke liye hum balance check kar sakte hain, 
        # par abhi ke liye simple signal follow karte hain.
        
    except Exception as e:
        print(f"Position Check Error: {e}")
        return

    # 2. Get Analysis
    data = get_market_data(SELECTED_SYMBOL)
    if data is None: return

    ema9 = data['ema9']
    ema21 = data['ema21']
    rsi = data['rsi']
    current_price = data['close']
    
    signal = None
    if ema9 > ema21 and rsi > 55: signal = "buy"
    elif ema9 < ema21 and rsi < 45: signal = "sell"
    
    if signal:
        # 3. Calculate TP / SL Prices
        if signal == "buy":
            tp_price = current_price * (1 + TP_PERCENT)
            sl_price = current_price * (1 - SL_PERCENT)
        else:
            tp_price = current_price * (1 - TP_PERCENT)
            sl_price = current_price * (1 + SL_PERCENT)
            
        # 4. Place Order with OTCO (One-Cancels-Other) Params
        try:
            # Amount Calculation (USDT to Coins)
            amount = CURRENT_STAKE / current_price 
            
            # Bybit specific params for TP/SL
            params = {
                'takeProfit': tp_price,
                'stopLoss': sl_price,
            }
            
            order = exchange.create_order(
                symbol=SELECTED_SYMBOL,
                type='market',
                side=signal,
                amount=amount,
                params=params
            )
            
            bot.send_message(MY_CHAT_ID, 
                f"🔫 **ORDER EXECUTED**\n"
                f"Asset: {SELECTED_SYMBOL}\n"
                f"Side: {signal.upper()}\n"
                f"Stake: ${CURRENT_STAKE}\n"
                f"Price: {current_price}\n"
                f"🎯 TP: {round(tp_price, 2)} | 🛑 SL: {round(sl_price, 2)}"
            )
            
            # Cooldown to avoid double entry
            time.sleep(60) 
            
        except Exception as e:
            bot.send_message(MY_CHAT_ID, f"⚠️ Order Failed: {e}")
            time.sleep(10)

def trade_loop():
    global is_trading
    bot.send_message(MY_CHAT_ID, f"🚀 Bot Started on Bybit Testnet!\nAsset: {SELECTED_SYMBOL}")
    
    # Set Leverage (One time)
    try:
        exchange.set_leverage(LEVERAGE, SELECTED_SYMBOL)
    except: pass

    while is_trading:
        try:
            check_and_execute_trade()
            time.sleep(10) # 10 Seconds poll
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(10)

# --- 4. COMMANDS ---

@bot.message_handler(commands=['trade'])
def start_trade(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTC)", "Gold (XAU)")
    msg = bot.send_message(message.chat.id, "Select Asset:", reply_markup=markup)
    bot.register_next_step_handler(msg, set_asset)

def set_asset(message):
    global SELECTED_SYMBOL
    if message.text not in ASSETS:
        bot.send_message(message.chat.id, "Invalid Asset.")
        return
    SELECTED_SYMBOL = ASSETS[message.text]
    msg = bot.send_message(message.chat.id, "Enter Initial Stake (USDT):")
    bot.register_next_step_handler(msg, set_stake)

def set_stake(message):
    global INITIAL_STAKE, CURRENT_STAKE, is_trading
    try:
        INITIAL_STAKE = float(message.text)
        CURRENT_STAKE = INITIAL_STAKE
        is_trading = True
        threading.Thread(target=trade_loop).start()
    except:
        bot.send_message(message.chat.id, "Invalid number.")

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.send_message(message.chat.id, "🛑 Trading Stopped.")

@bot.message_handler(commands=['bal'])
def check_balance(message):
    try:
        bal = exchange.fetch_balance()
        usdt = bal['USDT']['free']
        bot.send_message(message.chat.id, f"🏦 Wallet Balance: ${round(usdt, 2)}")
    except Exception as e:
        bot.send_message(message.chat.id, f"Error: {e}")

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)

