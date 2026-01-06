import telebot
import ccxt
import os
import time
from flask import Flask
import threading

# --- 1. GET KEYS FROM ENV ---
# Make sure Render Env Vars are updated!
API_KEY = os.environ.get("BYBIT_API")
API_SECRET = os.environ.get("BYBIT_SC")
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

# Setup Bot
bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- 2. BYBIT CONNECTION SETUP ---
print("🔌 Initializing Bybit Connection...")

# Global Exchange Object
exchange = None
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
    exchange.set_sandbox_mode(True) # ✅ Testnet Mode
    print("✅ CCXT Object Created")
except Exception as e:
    print(f"❌ CCXT Setup Failed: {e}")

# --- 3. SERVER (Keep Alive) ---
@app.route('/')
def home(): 
    return "Debugger is Live! Check Telegram."

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web_server)
    t.start()

# --- 4. TELEGRAM COMMANDS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "🛠 **DEBUGGER ONLINE**\n\nCommand dabao: /check\nMain Bybit se connect karke bataunga sab sahi hai ya nahi.")

@bot.message_handler(commands=['check'])
def check_system(message):
    bot.send_message(message.chat.id, "🕵️‍♂️ **Running System Diagnostics...**")
    
    report = ""
    status = "✅ PASS"
    
    # CHECK 1: API Keys Present?
    if API_KEY and API_SECRET:
        report += "🔑 Keys Found in Env: YES\n"
    else:
        report += "🔑 Keys Found in Env: ❌ NO (Check Render Vars)\n"
        status = "❌ FAIL"

    # CHECK 2: Connect to Bybit & Get Balance
    try:
        balance = exchange.fetch_balance()
        usdt = balance['USDT']['free']
        report += f"🏦 Bybit Connection: SUCCESS\n"
        report += f"💰 Wallet Balance: ${round(usdt, 2)}\n"
    except Exception as e:
        report += f"🏦 Bybit Connection: ❌ FAILED\n"
        report += f"⚠️ Error: {str(e)}\n"
        status = "❌ FAIL"

    # CHECK 3: Market Data Access
    try:
        ticker = exchange.fetch_ticker('BTC/USDT:USDT')
        price = ticker['last']
        report += f"📈 Market Data: Working (BTC: ${price})\n"
    except Exception as e:
        report += f"📈 Market Data: ❌ FAILED\n"
        status = "❌ FAIL"

    # Final Message
    final_msg = f"**DIAGNOSTIC REPORT**\nStatus: {status}\n\n{report}"
    bot.send_message(message.chat.id, final_msg, parse_mode="Markdown")

# --- 5. MAIN LOOP (With Conflict Fix) ---
if __name__ == "__main__":
    keep_alive()
    
    # 409 Conflict Fix: Clear old pending updates
    print("🧹 Cleaning old sessions...")
    try:
        bot.delete_webhook(drop_pending_updates=True)
    except:
        pass
        
    print("🚀 Bot Polling Started...")
    bot.polling(non_stop=True)
