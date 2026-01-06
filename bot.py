import telebot
import ccxt
import os
from flask import Flask
import threading

# --- 1. KEYS FROM ENV ---
API_KEY = os.environ.get("BYBIT_API")
API_SECRET = os.environ.get("BYBIT_SC")
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- 2. BYBIT CONNECTION SETUP ---
print("🔌 Attempting to connect to Bybit Testnet...")

try:
    exchange = ccxt.bybit({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    exchange.set_sandbox_mode(True) # ✅ Testnet Enable
    print("✅ CCXT Object Created")
except Exception as e:
    print(f"❌ CCXT Setup Failed: {e}")

# --- 3. SERVER ---
@app.route('/')
def home(): return "Debugger Bot is Alive"

def run_web_server():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))

def keep_alive():
    t = threading.Thread(target=run_web_server)
    t.start()

# --- 4. COMMANDS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "🤖 Debugger Bot Online!\nType /check to test Bybit connection.")

@bot.message_handler(commands=['check'])
def check_connection(message):
    bot.send_message(message.chat.id, "⏳ Connecting to Bybit...")
    
    try:
        # 1. Check Balance
        balance = exchange.fetch_balance()
        usdt = balance['USDT']['free']
        
        # 2. Check Market Price (BTC)
        ticker = exchange.fetch_ticker('BTC/USDT:USDT')
        price = ticker['last']
        
        msg = (
            f"✅ **CONNECTION SUCCESSFUL!**\n\n"
            f"🔑 API Key Loaded: Yes\n"
            f"🏦 Testnet Balance: ${usdt}\n"
            f"📈 BTC Price: ${price}\n\n"
            f"Ab hum Trading Code daal sakte hain!"
        )
        bot.send_message(message.chat.id, msg, parse_mode="Markdown")
        
    except Exception as e:
        # Ye sabse important part hai - Error kya hai?
        error_msg = f"❌ **CONNECTION FAILED**\n\nError: `{str(e)}`"
        bot.send_message(message.chat.id, error_msg, parse_mode="Markdown")
        print(error_msg)

if __name__ == "__main__":
    keep_alive()
    print("🤖 Bot Polling Started...")
    bot.polling(non_stop=True)
