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
    TELE_TOKEN = "YOUR_BOT_TOKEN_HERE" 
    MY_CHAT_ID = "YOUR_CHAT_ID_HERE"

bot = telebot.TeleBot(TELE_TOKEN)
app = Flask(__name__)

# --- GLOBAL VARIABLES ---
is_trading = False
SELECTED_ASSET = ""
HANDLER_CONFIG = {} 
TIMEFRAME = Interval.INTERVAL_1_MINUTE 
CURRENT_STAKE = 100.0 # Default, will be changed by user

# Wallet & Stats Tracking
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
    "current_loss_streak": 0,
    "max_loss_streak": 0
}

# Assets Configuration (TradingView)
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

# --- 2. SERVER (Render Keep-Alive) ---
@app.route('/')
def home():
    return "Pro TradingView Bot is Live!"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web_server)
    t.start()

# --- 3. TRADING LOGIC ---
def get_tv_analysis():
    try:
        handler = TA_Handler(
            symbol=HANDLER_CONFIG["symbol"],
            screener=HANDLER_CONFIG["screener"],
            exchange=HANDLER_CONFIG["exchange"],
            interval=TIMEFRAME
        )
        return handler.get_analysis()
    except Exception as e:
        print(f"TV API Error: {e}")
        return None

def check_exit_conditions(current_price):
    global wallet, stats
    # TP: 0.2%, SL: 0.3%
    TP_PERCENT = 0.002
    SL_PERCENT = 0.003
    
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
            
        if pnl_pct >= TP_PERCENT or pnl_pct <= -SL_PERCENT:
            wallet["balance"] += (margin + pnl_amt)
            wallet["positions"].remove(pos)
            
            # Update Stats
            if pnl_amt > 0:
                status = "🟢 WIN"
                stats["wins"] += 1
                stats["current_loss_streak"] = 0
            else:
                status = "🔴 LOSS"
                stats["losses"] += 1
                stats["current_loss_streak"] += 1
                if stats["current_loss_streak"] > stats["max_loss_streak"]:
                    stats["max_loss_streak"] = stats["current_loss_streak"]
            
            wallet["history"].append({"result": status, "pnl": pnl_amt})
            
            bot.send_message(MY_CHAT_ID, 
                f"{status} | {SELECTED_ASSET}\n"
                f"💵 P/L: ${round(pnl_amt, 2)}\n"
                f"🏦 Bal: ${round(wallet['balance'], 2)}\n"
                f"📉 Streak: {stats['current_loss_streak']}")

def trading_loop():
    global is_trading
    print(f"🔥 Bot Running on {SELECTED_ASSET} with Stake ${CURRENT_STAKE}")
    
    while is_trading:
        try:
            analysis = get_tv_analysis()
            if not analysis:
                time.sleep(2)
                continue
                
            current_price = analysis.indicators["close"]
            rsi = analysis.indicators["RSI"]
            recommendation = analysis.summary["RECOMMENDATION"] 
            
            # Check Exits
            if len(wallet["positions"]) > 0:
                check_exit_conditions(current_price)
                
            # Check Entry (Only if no position)
            if len(wallet["positions"]) == 0:
                signal = None
                
                # Logic: Strong Buy/Sell from TV + RSI Filter
                if "BUY" in recommendation and rsi < 70:
                    signal = "BUY"
                elif "SELL" in recommendation and rsi > 30:
                    signal = "SELL"
                
                if signal:
                    # Use User Defined Stake
                    qty = CURRENT_STAKE / current_price
                    
                    wallet["balance"] -= CURRENT_STAKE
                    wallet["positions"].append({
                        "entry": current_price,
                        "qty": qty,
                        "type": signal,
                        "margin": CURRENT_STAKE
                    })
                    
                    # Update Max Stake Stat
                    if CURRENT_STAKE > stats["max_stake"]:
                        stats["max_stake"] = CURRENT_STAKE

                    bot.send_message(MY_CHAT_ID,
                        f"🚀 TV SIGNAL: {recommendation}\n"
                        f"🔫 Order: {signal}\n"
                        f"💰 Stake: ${CURRENT_STAKE}\n"
                        f"⚡ Price: {current_price}")
                    
                    time.sleep(60) # Cooldown
            
            time.sleep(4) 
            
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

# --- 4. COMMANDS ---

# /trade - Step 1: Asset
@bot.message_handler(commands=['trade'])
def trade_step_1(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Bitcoin (BTC)", "Gold (XAU)")
    msg = bot.send_message(message.chat.id, "📉 Select Asset to Trade:", reply_markup=markup)
    bot.register_next_step_handler(msg, trade_step_2)

# /trade - Step 2: Custom Lot Size
def trade_step_2(message):
    global SELECTED_ASSET, HANDLER_CONFIG
    if message.text not in ASSETS:
        bot.send_message(message.chat.id, "❌ Invalid Asset.")
        return
    
    SELECTED_ASSET = message.text
    HANDLER_CONFIG = ASSETS[message.text]
    
    msg = bot.send_message(message.chat.id, 
        f"Selected: {SELECTED_ASSET}\n"
        f"💰 Enter Lot Size / Stake Amount (e.g. 100, 500, 1000):",
        reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, trade_step_3)

# /trade - Step 3: Start
def trade_step_3(message):
    global is_trading, CURRENT_STAKE
    try:
        amount = float(message.text)
        if amount <= 0 or amount > wallet["balance"]:
            bot.send_message(message.chat.id, "⚠️ Invalid Amount or Insufficient Balance.")
            return
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ Please enter a valid number.")
        return

    CURRENT_STAKE = amount
    is_trading = True
    
    bot.send_message(message.chat.id, 
        f"✅ **BOT STARTED**\n"
        f"🎯 Asset: {SELECTED_ASSET}\n"
        f"💸 Stake: ${CURRENT_STAKE}\n"
        f"📡 Strategy: TradingView Technicals")
    
    threading.Thread(target=trading_loop).start()

# /stop
@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global is_trading
    is_trading = False
    bot.send_message(message.chat.id, "🛑 Trading Stopped. Loop Terminated.")

# /bal
@bot.message_handler(commands=['bal'])
def check_balance(message):
    bot.send_message(message.chat.id, f"🏦 Current Balance: **${round(wallet['balance'], 2)}**", parse_mode="Markdown")

# /status - Detailed Stats
@bot.message_handler(commands=['status'])
def status_report(message):
    total_trades = stats["wins"] + stats["losses"]
    win_ratio = 0
    if total_trades > 0:
        win_ratio = (stats["wins"] / total_trades) * 100
        
    pnl_diff = wallet["balance"] - stats["start_balance"]
    pnl_emoji = "🟢" if pnl_diff >= 0 else "🔴"
    
    report = (
        f"📊 **SESSION STATUS REPORT** 📊\n\n"
        f"🏦 Start Bal: ${stats['start_balance']}\n"
        f"💰 Curr Bal: ${round(wallet['balance'], 2)}\n"
        f"{pnl_emoji} Net P/L: ${round(pnl_diff, 2)}\n\n"
        f"🏆 Win Rate: {round(win_ratio, 1)}% ({stats['wins']}W / {stats['losses']}L)\n"
        f"🔥 Max Loss Streak: {stats['max_loss_streak']}\n"
        f"💎 Highest Stake Used: ${stats['max_stake']}"
    )
    bot.send_message(message.chat.id, report, parse_mode="Markdown")

# /reset - Restart 10k
@bot.message_handler(commands=['reset'])
def reset_wallet(message):
    global wallet, stats
    wallet["balance"] = 10000.0
    wallet["positions"] = []
    wallet["history"] = []
    
    stats = {
        "start_balance": 10000.0,
        "wins": 0,
        "losses": 0,
        "max_stake": 0.0,
        "current_loss_streak": 0,
        "max_loss_streak": 0
    }
    bot.send_message(message.chat.id, "🔄 Wallet & Stats Reset to $10,000 Demo.")

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
