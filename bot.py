import telebot
from telebot import types
import ccxt
import time
import os
import threading
from flask import Flask

# --- 1. CONFIGURATION ---
# Hum purane variable names use kar rahe hain taaki tumhe Render par change na karna pade
API_KEY = os.environ.get("BYBIT_API")   # Bitget Key yahan hai
API_SECRET = os.environ.get("BYBIT_SC") # Bitget Secret yahan hai
API_PASS = os.environ.get("API_PASS")   # Bitget Passphrase (New Variable)

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
MART_FACTOR = 2.0  # Loss hone par Stake 2x hoga
TP_PERCENT = 0.0015
SL_PERCENT = 0.0025

# --- 2. BITGET CONNECTION ---
exchange = None
try:
    exchange = ccxt.bitget({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'password': API_PASS,
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'} # Futures
    })
    # Demo Mode Enable
    exchange.headers = {'x-simulated-trading': '1'}
    print("✅ Bitget Simulator Configured")
except Exception as e:
    print(f"❌ Connection Setup Error: {e}")

# --- 3. SERVER (Keep Alive) ---
@app.route('/')
def home(): return "Master Bot is Live"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- 4. SMART HELPERS ---

def get_real_balance():
    """Ye function khud dhoondta hai ki paisa kis naam se pada hai"""
    try:
        bal = exchange.fetch_balance()
        
        # Method 1: Direct Check
        if 'SUSDT' in bal and bal['SUSDT']['free'] > 0: return float(bal['SUSDT']['free']), "SUSDT"
        if 'S-USDT' in bal and bal['S-USDT']['free'] > 0: return float(bal['S-USDT']['free']), "S-USDT"
        if 'USDT' in bal and bal['USDT']['free'] > 0: return float(bal['USDT']['free']), "USDT"
        
        # Method 2: Scan Total
        if 'total' in bal:
            for coin, amount in bal['total'].items():
                if amount > 5: # Ignore dust
                    return float(amount), coin
                    
        return 0.0, "USDT"
    except Exception as e:
        print(f"Bal Error: {e}")
        return 0.0, "Error"

def get_market_direction(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=2)
        # Close > Open = BUY
        direction = "buy" if ohlcv[-2][4] > ohlcv[-2][1] else "sell"
        return direction, ohlcv[-2][4]
    except:
        return None, None

# --- 5. MACHINE GUN LOGIC ---
def trade_engine():
    global is_trading, CURRENT_STAKE
    
    bot.send_message(MY_CHAT_ID, f"🔫 **MACHINE GUN STARTED**\nAsset: {SELECTED_SYMBOL}\nStake: ${CURRENT_STAKE}")
    
    # Try setting leverage
    try: exchange.set_leverage(LEVERAGE, SELECTED_SYMBOL)
    except: pass

    while is_trading:
        try:
            # Step A: Check agar koi trade pehle se chal raha hai
            positions = exchange.fetch_positions([SELECTED_SYMBOL])
            active = [p for p in positions if float(p['contracts']) > 0]
            
            if len(active) > 0:
                time.sleep(5) # Wait for close
                continue
            
            # Step B: Naya Signal dhoondo
            side, price = get_market_direction(SELECTED_SYMBOL)
            
            if side:
                # Step C: Balance Check
                bal, coin = get_real_balance()
                if bal < 5:
                    bot.send_message(MY_CHAT_ID, f"⚠️ Low Balance: {bal}. Stopping.")
                    is_trading = False
                    break
                
                # Martingale Reset Logic if Balance is healthy
                if CURRENT_STAKE > bal: CURRENT_STAKE = INITIAL_STAKE

                # Step D: Trade Execute
                qty = CURRENT_STAKE / price
                tp = price * (1 + TP_PERCENT) if side == "buy" else price * (1 - TP_PERCENT)
                sl = price * (1 - SL_PERCENT) if side == "buy" else price * (1 + SL_PERCENT)
                
                params = {'takeProfit': {'triggerPrice': tp}, 'stopLoss': {'triggerPrice': sl}}
                
                exchange.create_order(SELECTED_SYMBOL, 'market', side, qty, params)
                
                bot.send_message(MY_CHAT_ID, f"🚀 **FIRED!** {side.upper()}\n💰 Stake: ${CURRENT_STAKE}\n⚡ Price: {price}")
                
                # Step E: Wait for Result (Loop until closed)
                start_bal = bal
                while True:
                    time.sleep(5)
                    p = exchange.fetch_positions([SELECTED_SYMBOL])
                    if not [x for x in p if float(x['contracts']) > 0]:
                        break # Closed
                
                # Step F: Win/Loss Check
                end_bal, _ = get_real_balance()
                if end_bal > start_bal:
                    # Win
                    CURRENT_STAKE = INITIAL_STAKE
                    bot.send_message(MY_CHAT_ID, f"✅ **WIN!** Profit: +${round(end_bal - start_bal, 2)}")
                else:
                    # Loss -> Martingale
                    CURRENT_STAKE = CURRENT_STAKE * MART_FACTOR
                    bot.send_message(MY_CHAT_ID, f"❌ **LOSS.** Martingale x2 -> ${CURRENT_STAKE}")
            
            time.sleep(2)

        except Exception as e:
            bot.send_message(MY_CHAT_ID, f"⚠️ Error in Loop: {e}")
            time.sleep(10)

# --- 6. ALL COMMANDS ---

@bot.message_handler(commands=['start', 'help'])
def send_help(message):
    help_text = (
        "🤖 **COMMAND LIST**\n\n"
        "🟢 /trade - Start Machine Gun Trading\n"
        "🔴 /stop - Emergency Stop\n"
        "💰 /bal - Check Wallet Balance\n"
        "📊 /status - Show Current Settings\n"
        "🛠 /check - System Diagnostic (Fixes)\n"
        "ℹ️ /help - Show this menu"
    )
    bot.reply_to(message, help_text)

@bot.message_handler(commands=['check'])
def diagnostic(message):
    bot.send_message(message.chat.id, "🕵️‍♂️ **Running Diagnostics...**")
    
    # 1. Check Balance
    bal, coin = get_real_balance()
    
    # 2. Check Market Data
    try:
        price = exchange.fetch_ticker('BTC/USDT:USDT')['last']
        market = f"✅ Live (${price})"
    except Exception as e:
        market = f"❌ Error: {e}"

    msg = (
        f"🔍 **DIAGNOSTIC REPORT**\n"
        f"------------------------\n"
        f"🏦 Balance: **{bal} {coin}**\n"
        f"📈 Market Data: {market}\n"
        f"------------------------\n"
        f"Passphrase Loaded: {'✅ Yes' if API_PASS else '❌ NO (Check Render)'}"
    )
    bot.send_message(message.chat.id, msg)

@bot.message_handler(commands=['trade'])
def trade_menu(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTC)", "Ethereum (ETH)")
    msg = bot.send_message(message.chat.id, "Select Asset for Bitget Demo:", reply_markup=markup)
    bot.register_next_step_handler(msg, start_trading)

def start_trading(message):
    global SELECTED_SYMBOL, is_trading
    if "Bitcoin" in message.text: SELECTED_SYMBOL = "BTC/USDT:USDT"
    elif "Ethereum" in message.text: SELECTED_SYMBOL = "ETH/USDT:USDT"
    else: 
        bot.send_message(message.chat.id, "Invalid Selection")
        return
    
    is_trading = True
    threading.Thread(target=trade_engine).start()

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.send_message(message.chat.id, "🛑 **STOPPING...** Safe to close.")

@bot.message_handler(commands=['bal'])
def show_balance(message):
    bal, coin = get_real_balance()
    bot.send_message(message.chat.id, f"💰 Wallet: **{bal} {coin}**")

@bot.message_handler(commands=['status'])
def show_status(message):
    status = "🟢 RUNNING" if is_trading else "🔴 STOPPED"
    bot.send_message(message.chat.id, 
        f"📊 **CURRENT STATUS**\n"
        f"Mode: {status}\n"
        f"Asset: {SELECTED_SYMBOL}\n"
        f"Next Stake: ${CURRENT_STAKE}\n"
        f"Strategy: Martingale (x{MART_FACTOR})"
    )

# --- 7. ROBUST STARTUP (Fixes Telegram Conflict) ---
if __name__ == "__main__":
    # Start Web Server
    threading.Thread(target=run_web_server).start()
    
    print("🧹 Cleaning old Telegram sessions...")
    try:
        bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"Webhook Error (Ignorable): {e}")

    print("🚀 Bot Started Polling...")
    while True:
        try:
            bot.polling(non_stop=True, interval=2)
        except Exception as e:
            print(f"⚠️ Polling Error: {e}")
            time.sleep(5)
