import telebot
from telebot import types
import ccxt
import time
import os
from flask import Flask
import threading

# --- 1. CONFIGURATION ---
API_KEY = os.environ.get("BYBIT_API")
API_SECRET = os.environ.get("BYBIT_SC")
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- GLOBAL SETTINGS ---
is_trading = False
SELECTED_SYMBOL = "" 
INITIAL_STAKE = 10.0
CURRENT_STAKE = 10.0
LEVERAGE = 10         

# MACHINE GUN SETTINGS
TP_PERCENT = 0.0015
SL_PERCENT = 0.0025

# Bybit Connection
try:
    exchange = ccxt.bybit({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future', 
            'adjustForTimeDifference': True
        }
    })
    exchange.set_sandbox_mode(True) # ✅ Testnet
    print("✅ Connected to Bybit (EU Region)")
except Exception as e:
    print(f"Connection Error: {e}")

# --- 2. SERVER ---
@app.route('/')
def home(): return "Machine Gun Bot Running"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web_server)
    t.start()

# --- 3. HELPER FUNCTIONS ---
def get_safe_balance():
    try:
        bal = exchange.fetch_balance()
        # Handle different API structures safely
        if 'USDT' in bal:
            return float(bal['USDT']['free'])
        elif 'total' in bal and 'USDT' in bal['total']:
            return float(bal['total']['USDT'])
        else:
            return 0.0 # Return 0 instead of Crashing
    except Exception as e:
        print(f"Balance Check Error: {e}")
        return 0.0

def get_last_candle_direction(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=2)
        if not ohlcv: return None
        open_p = ohlcv[-2][1]
        close_p = ohlcv[-2][4]
        return "buy" if close_p > open_p else "sell"
    except:
        return None

# --- 4. TRADING LOOP ---
def trade_loop():
    global is_trading, CURRENT_STAKE
    
    # Check Balance First
    start_bal = get_safe_balance()
    bot.send_message(MY_CHAT_ID, f"🔫 Machine Gun ON (Germany)\nAsset: {SELECTED_SYMBOL}\n🏦 Balance: ${start_bal}")

    # Set Leverage
    try: exchange.set_leverage(LEVERAGE, SELECTED_SYMBOL)
    except: pass

    while is_trading:
        try:
            # 1. Check Active Positions
            positions = exchange.fetch_positions([SELECTED_SYMBOL])
            active = [p for p in positions if float(p['contracts']) > 0]

            if len(active) > 0:
                time.sleep(2) 
                continue
            
            # 2. No Trade? Find Direction
            direction = get_last_candle_direction(SELECTED_SYMBOL)
            price_data = exchange.fetch_ticker(SELECTED_SYMBOL)
            price = float(price_data['last'])
            
            if direction:
                # 3. Calculate TP/SL
                if direction == "buy":
                    tp = price * (1 + TP_PERCENT)
                    sl = price * (1 - SL_PERCENT)
                else:
                    tp = price * (1 - TP_PERCENT)
                    sl = price * (1 + SL_PERCENT)

                # 4. Check Balance Safely
                bal = get_safe_balance()
                
                if bal < 5.0: # Minimum $5 needed
                    bot.send_message(MY_CHAT_ID, "⚠️ Balance Low (<$5). Stopping.")
                    is_trading = False
                    break
                
                if CURRENT_STAKE > bal:
                    CURRENT_STAKE = INITIAL_STAKE # Reset Stake

                # 5. Execute Order
                amount = CURRENT_STAKE / price
                params = {'takeProfit': tp, 'stopLoss': sl}
                
                try:
                    exchange.create_order(SELECTED_SYMBOL, 'market', direction, amount, params)
                    
                    bot.send_message(MY_CHAT_ID, 
                        f"🚀 **FIRED!**\n"
                        f"Side: {direction.upper()}\n"
                        f"Stake: ${round(CURRENT_STAKE, 2)}\n"
                        f"Price: {price}\n"
                        f"🎯 TP: {round(tp, 2)} | 🛑 SL: {round(sl, 2)}")
                    
                    time.sleep(5) 
                    
                except Exception as e:
                    bot.send_message(MY_CHAT_ID, f"⚠️ Order Failed: {e}")
                    time.sleep(5)

            time.sleep(2)

        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

# --- 5. COMMANDS ---
ASSETS = {
    "Bitcoin (BTC)": "BTC/USDT:USDT",
    "Gold (XAU)": "XAU/USDT:USDT"
}

@bot.message_handler(commands=['trade'])
def start_trade(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTC)", "Gold (XAU)")
    msg = bot.send_message(message.chat.id, "Select Asset (EU Region 🇪🇺):", reply_markup=markup)
    bot.register_next_step_handler(msg, set_asset)

def set_asset(message):
    global SELECTED_SYMBOL, is_trading
    if message.text not in ASSETS:
        bot.send_message(message.chat.id, "Invalid.")
        return
    SELECTED_SYMBOL = ASSETS[message.text]
    is_trading = True
    threading.Thread(target=trade_loop).start()

@bot.message_handler(commands=['stop'])
def stop(message):
    global is_trading
    is_trading = False
    bot.send_message(message.chat.id, "🛑 Stopped.")

@bot.message_handler(commands=['bal'])
def check_bal(message):
    bal = get_safe_balance()
    bot.send_message(message.chat.id, f"🏦 Balance: ${round(bal, 2)}")

@bot.message_handler(commands=['check'])
def diagnostic(message):
    # Quick Diagnostic
    status = "✅ Connected"
    bal = get_safe_balance()
    try:
        price = exchange.fetch_ticker('BTC/USDT:USDT')['last']
        mkt = f"Working (${price})"
    except:
        mkt = "❌ Error"
        
    msg = f"🔍 **DIAGNOSTIC**\nConn: {status}\nBal: ${bal}\nMkt Data: {mkt}"
    bot.send_message(message.chat.id, msg)

if __name__ == "__main__":
    keep_alive()
    try: bot.delete_webhook(drop_pending_updates=True)
    except: pass
    bot.polling(non_stop=True)
