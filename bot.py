import telebot
from telebot import types
import ccxt
import time
import os
import threading
from flask import Flask

# --- 1. CONFIGURATION (Jugad: Using Old Variable Names) ---
# Hum Bitget ki keys purane variable names se hi utha lenge
API_KEY = os.environ.get("BYBIT_API")   # Isme Bitget Key hai
API_SECRET = os.environ.get("BYBIT_SC") # Isme Bitget Secret hai
API_PASS = os.environ.get("API_PASS")   # ✅ YE NEW HAI (Bitget Passphrase)

TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- GLOBAL SETTINGS ---
is_trading = False
SELECTED_SYMBOL = "" 
INITIAL_STAKE = 10.0  # $10 se shuru
CURRENT_STAKE = 10.0
LEVERAGE = 10         

# 🔥 SNIPER SETTINGS
TP_PERCENT = 0.0015
SL_PERCENT = 0.0025
MART_FACTOR = 2.0    # Loss ke baad stake double

# --- 2. BITGET CONNECTION ---
print("🔌 Connecting to Bitget...")
try:
    exchange = ccxt.bitget({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'password': API_PASS,  # Bitget needs this!
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap', # Futures
        }
    })
    
    # ✅ SIMULATOR MODE ON (Demo Trading)
    # Ye line Bitget ko bolti hai ki nakli S-USDT use karo
    exchange.headers = {
        'x-simulated-trading': '1'
    }
    print("✅ Bitget Simulator Connected!")
    
except Exception as e:
    print(f"❌ Connection Error: {e}")

# --- 3. SERVER (Keep Alive) ---
@app.route('/')
def home(): return "Bitget Machine Gun is Live"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- 4. TRADING LOGIC ---

def get_bitget_balance():
    try:
        # Bitget Simulator mein coin ka naam 'SUSDT' ya 'S-USDT' hota hai
        bal = exchange.fetch_balance()
        
        # Try finding S-USDT (Demo money)
        if 'SUSDT' in bal: return float(bal['SUSDT']['free'])
        if 'S-USDT' in bal: return float(bal['S-USDT']['free'])
        if 'USDT' in bal: return float(bal['USDT']['free']) # Fallback
        
        # Agar kuch na mile toh total check karo
        if 'total' in bal:
            for coin, amount in bal['total'].items():
                if 'USDT' in coin and amount > 0:
                    return float(amount)
        return 0.0
    except Exception as e:
        print(f"Bal Error: {e}")
        return 0.0

def get_last_move(symbol):
    try:
        # Last 2 candles to check trend
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=2)
        open_p = ohlcv[-2][1]
        close_p = ohlcv[-2][4]
        return "buy" if close_p > open_p else "sell", close_p
    except:
        return None, None

def trade_loop():
    global is_trading, CURRENT_STAKE
    bot.send_message(MY_CHAT_ID, f"🚀 **BITGET MACHINE GUN ON!**\nTarget: {SELECTED_SYMBOL}")

    # Set Leverage (Try block taaki error se ruke nahi)
    try: exchange.set_leverage(LEVERAGE, SELECTED_SYMBOL)
    except: pass

    while is_trading:
        try:
            # 1. Check if Trade Exists
            positions = exchange.fetch_positions([SELECTED_SYMBOL])
            open_pos = [p for p in positions if float(p['contracts']) > 0]

            if len(open_pos) > 0:
                time.sleep(5) # Wait for trade to close
                continue
            
            # 2. Find Signal
            side, price = get_last_move(SELECTED_SYMBOL)
            
            if side:
                # 3. Calculate TP/SL prices
                tp = price * (1 + TP_PERCENT) if side == "buy" else price * (1 - TP_PERCENT)
                sl = price * (1 - SL_PERCENT) if side == "buy" else price * (1 + SL_PERCENT)
                
                # Check Balance (Martingale Logic)
                bal = get_bitget_balance()
                if bal < 5:
                    bot.send_message(MY_CHAT_ID, "⚠️ Balance Low. Stopping.")
                    is_trading = False
                    break
                    
                if CURRENT_STAKE > bal: CURRENT_STAKE = INITIAL_STAKE

                # 4. Fire Order
                amount = CURRENT_STAKE / price 
                
                # Bitget Order Params
                params = {
                    'takeProfit': {'triggerPrice': tp}, 
                    'stopLoss': {'triggerPrice': sl}
                }
                
                exchange.create_order(SELECTED_SYMBOL, 'market', side, amount, params)
                
                bot.send_message(MY_CHAT_ID, 
                    f"🔫 **FIRED!** {side.upper()}\n"
                    f"💰 Stake: ${round(CURRENT_STAKE, 2)}\n"
                    f"⚡ Price: {price}")
                
                # Wait loop until trade closes
                counter = 0
                while True:
                    time.sleep(5)
                    counter += 1
                    p = exchange.fetch_positions([SELECTED_SYMBOL])
                    if not [x for x in p if float(x['contracts']) > 0]:
                        break # Trade Closed
                    if counter > 200: break # Safety break
                
                # 5. Check Result (Simple Logic)
                new_bal = get_bitget_balance()
                if new_bal < bal:
                    # LOSS -> Martingale
                    CURRENT_STAKE = CURRENT_STAKE * MART_FACTOR
                    bot.send_message(MY_CHAT_ID, f"⚠️ Loss Detected. Martingale: ${round(CURRENT_STAKE, 2)}")
                else:
                    # WIN -> Reset
                    profit = new_bal - bal
                    CURRENT_STAKE = INITIAL_STAKE
                    bot.send_message(MY_CHAT_ID, f"✅ Profit: +${round(profit, 2)}")
            
            time.sleep(2)
            
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

# --- 5. COMMANDS ---

@bot.message_handler(commands=['start', 'help'])
def welcome(message):
    bot.reply_to(message, 
        "🤖 **Bitget Machine Gun Bot**\n\n"
        "/check - Check Connection & Balance\n"
        "/trade - Start Auto Trading\n"
        "/stop - Stop Trading\n"
        "/bal - Show Wallet Balance")

@bot.message_handler(commands=['check'])
def diagnostic(message):
    bot.send_message(message.chat.id, "🕵️‍♂️ Checking Bitget Connection...")
    try:
        # Check Balance
        bal = get_bitget_balance()
        # Check Market Data
        ticker = exchange.fetch_ticker('BTC/USDT:USDT')
        price = ticker['last']
        
        msg = (f"✅ **CONNECTED**\n"
               f"🏦 Demo Balance: ${round(bal, 2)}\n"
               f"📈 BTC Price: ${price}")
        bot.send_message(message.chat.id, msg)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}\n(Did you add API_PASS in Render?)")

@bot.message_handler(commands=['trade'])
def start_trading_menu(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTC)", "Gold (XAU)") # Bitget doesn't always have XAU demo, try BTC first
    msg = bot.send_message(message.chat.id, "Select Asset:", reply_markup=markup)
    bot.register_next_step_handler(msg, set_asset)

def set_asset(message):
    global SELECTED_SYMBOL, is_trading
    # Bitget Symbols
    if "Bitcoin" in message.text:
        SELECTED_SYMBOL = "BTC/USDT:USDT"
    elif "Gold" in message.text:
        # Note: Bitget simulator may not have Gold. If fail, use ETH.
        SELECTED_SYMBOL = "ETH/USDT:USDT" 
        bot.send_message(message.chat.id, "⚠️ Note: Using ETH as Gold demo might be unavailable.")
    else:
        SELECTED_SYMBOL = "BTC/USDT:USDT"
    
    is_trading = True
    threading.Thread(target=trade_loop).start()

@bot.message_handler(commands=['stop'])
def stop_trading(message):
    global is_trading
    is_trading = False
    bot.send_message(message.chat.id, "🛑 Trading Stopped.")

@bot.message_handler(commands=['bal'])
def check_wallet(message):
    bal = get_bitget_balance()
    bot.send_message(message.chat.id, f"💰 Balance: ${round(bal, 2)}")

# --- 6. ROBUST STARTUP ---
if __name__ == "__main__":
    t = threading.Thread(target=run_web_server)
    t.start()
    
    # Anti-Conflict Logic
    try: bot.delete_webhook(drop_pending_updates=True)
    except: pass
    
    print("🚀 Bot Started...")
    bot.polling(non_stop=True)
