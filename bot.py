import telebot
from telebot import types
from tradingview_ta import TA_Handler, Interval, Exchange
import time
import os
from flask import Flask
import threading

# --- 1. SETUP ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("CHAT_ID")

if not TELE_TOKEN:
    TELE_TOKEN = "YOUR_TOKEN"
    MY_CHAT_ID = "YOUR_ID"

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- CONFIGURATION (FAST SCALPING) ---
# 0.0004 = 0.04% = Approx 10 Pips on Gold
TP_PERCENT = 0.0004 
SL_PERCENT = 0.0004 
MARTINGALE_FACTOR = 2.5 # Loss cover + Profit

# Global State
is_trading = False
SELECTED_ASSET = ""
HANDLER_CONFIG = {} 
TIMEFRAME = Interval.INTERVAL_1_MINUTE 

# Money Management
INITIAL_STAKE = 100.0 # Default Start
CURRENT_STAKE = 100.0 

wallet = {
    "balance": 10000.0,
    "positions": [],
    "history": []
}

stats = {
    "start_balance": 10000.0,
    "wins": 0,
    "losses": 0,
    "max_stake": 0.0,
    "current_loss_streak": 0
}

# Assets
ASSETS = {
    "Bitcoin (BTC)": {
        "symbol": "BTCUSDT",
        "screener": "crypto",
        "exchange": "BINANCE"
    },
    "Gold (XAU)": {
        "symbol": "GOLD",
        "screener": "cfd",
        "exchange": "TVC" 
    }
}

# --- 2. SERVER ---
@app.route('/')
def home():
    return "Scalper Bot Active!"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web_server)
    t.start()

# --- 3. LOGIC ---
def get_tv_analysis():
    try:
        handler = TA_Handler(
            symbol=HANDLER_CONFIG["symbol"],
            screener=HANDLER_CONFIG["screener"],
            exchange=HANDLER_CONFIG["exchange"],
            interval=TIMEFRAME
        )
        return handler.get_analysis()
    except:
        return None

def check_exit_conditions(current_price):
    global wallet, stats, CURRENT_STAKE
    
    for pos in wallet["positions"][:]:
        entry = pos['entry']
        qty = pos['qty']
        side = pos['type']
        margin = pos['margin']
        
        # Calculate PnL
        if side == "BUY":
            pnl_amt = (current_price - entry) * qty
            pnl_pct = (current_price - entry) / entry
        else:
            pnl_amt = (entry - current_price) * qty
            pnl_pct = (entry - current_price) / entry
            
        # HIT TP or SL?
        if pnl_pct >= TP_PERCENT or pnl_pct <= -SL_PERCENT:
            wallet["balance"] += (margin + pnl_amt)
            wallet["positions"].remove(pos)
            
            if pnl_amt > 0:
                status = "🟢 WIN (TP Hit)"
                stats["wins"] += 1
                stats["current_loss_streak"] = 0
                # Win hua, wapas Normal Stake par aao
                CURRENT_STAKE = INITIAL_STAKE 
            else:
                status = "🔴 LOSS (SL Hit)"
                stats["losses"] += 1
                stats["current_loss_streak"] += 1
                # Loss hua, Martingale lagao (2.5x)
                CURRENT_STAKE = margin * MARTINGALE_FACTOR
            
            # Message Update
            bot.send_message(MY_CHAT_ID, 
                f"{status} | {SELECTED_ASSET}\n"
                f"💵 P/L: ${round(pnl_amt, 2)}\n"
                f"🏦 Bal: ${round(wallet['balance'], 2)}\n"
                f"🔄 Next Stake: ${round(CURRENT_STAKE, 2)}")

def trading_loop():
    global is_trading
    print(f"🔥 Scalping Started on {SELECTED_ASSET}")
    
    while is_trading:
        try:
            analysis = get_tv_analysis()
            if not analysis:
                time.sleep(1)
                continue
                
            current_price = analysis.indicators["close"]
            rsi = analysis.indicators["RSI"]
            recommendation = analysis.summary["RECOMMENDATION"] 
            
            # Check Exits (Fast Speed)
            if len(wallet["positions"]) > 0:
                check_exit_conditions(current_price)
                
            # Entry (Only if empty)
            if len(wallet["positions"]) == 0:
                signal = None
                
                # Scalping Logic: Needs Strong Signal OR Extreme RSI
                # Buy: TV says BUY + RSI < 70 (Room to grow)
                if "BUY" in recommendation and rsi < 70:
                    signal = "BUY"
                # Sell: TV says SELL + RSI > 30 (Room to fall)
                elif "SELL" in recommendation and rsi > 30:
                    signal = "SELL"
                
                if signal:
                    # Check Balance Safety
                    if CURRENT_STAKE > wallet["balance"]:
                        bot.send_message(MY_CHAT_ID, "⚠️ Balance Low for Martingale! Resetting Stake.")
                        CURRENT_STAKE = INITIAL_STAKE

                    qty = CURRENT_STAKE / current_price
                    wallet["balance"] -= CURRENT_STAKE
                    
                    wallet["positions"].append({
                        "entry": current_price,
                        "qty": qty,
                        "type": signal,
                        "margin": CURRENT_STAKE
                    })
                    
                    # Max Stake Record
                    if CURRENT_STAKE > stats["max_stake"]:
                        stats["max_stake"] = CURRENT_STAKE

                    bot.send_message(MY_CHAT_ID,
                        f"🔫 FAST {signal} EXECUTION\n"
                        f"💰 Stake: ${round(CURRENT_STAKE, 2)}\n"
                        f"⚡ Price: {current_price}\n"
                        f"🎯 Target: +10 Pips")
                    
                    # Scalping me cooldown kam rakho (30s)
                    time.sleep(30) 
            
            time.sleep(2) # 2 Sec Check (Fast)
            
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

# --- 4. COMMANDS ---

@bot.message_handler(commands=['trade'])
def trade_step_1(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTC)", "Gold (XAU)")
    msg = bot.send_message(message.chat.id, "📉 Select Asset for 10-Pip Scalping:", reply_markup=markup)
    bot.register_next_step_handler(msg, trade_step_2)

def trade_step_2(message):
    global SELECTED_ASSET, HANDLER_CONFIG
    if message.text not in ASSETS:
        bot.send_message(message.chat.id, "❌ Invalid.")
        return
    
    SELECTED_ASSET = message.text
    HANDLER_CONFIG = ASSETS[message.text]
    
    msg = bot.send_message(message.chat.id, 
        f"Selected: {SELECTED_ASSET}\n"
        f"💰 Enter Base Stake Amount (First trade size):",
        reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, trade_step_3)

def trade_step_3(message):
    global is_trading, INITIAL_STAKE, CURRENT_STAKE
    try:
        amount = float(message.text)
    except:
        bot.send_message(message.chat.id, "⚠️ Invalid Number.")
        return

    INITIAL_STAKE = amount
    CURRENT_STAKE = amount
    is_trading = True
    
    bot.send_message(message.chat.id, 
        f"✅ **SCALPER STARTED**\n"
        f"🎯 Target: ~10 Pips (0.04%)\n"
        f"🛡️ Martingale: 2.5x (Aggressive Recovery)\n"
        f"💸 Base Stake: ${INITIAL_STAKE}")
    
    threading.Thread(target=trading_loop).start()

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.send_message(message.chat.id, "🛑 Stopped.")

@bot.message_handler(commands=['status'])
def status_report(message):
    total = stats["wins"] + stats["losses"]
    wr = (stats["wins"]/total*100) if total > 0 else 0
    
    report = (
        f"📊 **SCALPING STATS**\n"
        f"🏦 Bal: ${round(wallet['balance'], 2)}\n"
        f"✅ Win Rate: {round(wr, 1)}%\n"
        f"🔥 Max Loss Streak: {stats['current_loss_streak']}\n"
        f"💎 Highest Martingale: ${round(stats['max_stake'], 2)}"
    )
    bot.send_message(message.chat.id, report, parse_mode="Markdown")

@bot.message_handler(commands=['reset'])
def reset_wallet(message):
    wallet["balance"] = 10000.0
    wallet["positions"] = []
    stats["wins"] = 0
    stats["losses"] = 0
    bot.send_message(message.chat.id, "🔄 Balance Reset to $10,000.")

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
