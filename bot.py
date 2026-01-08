import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask

# --- 1. CONFIGURATION ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)
market = ccxt.kraken({'enableRateLimit': True}) # Better for Render IPs

# --- 2. UNIVERSAL MULTI-USER STORAGE ---
users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 10000.0, "pnl": 0.0, "initial_stake": 10.0, 
            "current_stake": 10.0, "wins": 0, "losses": 0, "max_stake": 0.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USD"
        }
    return users[chat_id]

# --- 3. THE BRAIN: AI DECISION ENGINE ---

def get_ai_decision(symbol, chat_id):
    try:
        # Fetching 20 candles for context
        ohlcv = market.fetch_ohlcv(symbol, timeframe='1m', limit=20)
        c = ohlcv[-2] # Last closed candle
        o, h, l, cl = c[1], c[2], c[3], c[4]
        
        # Strategy Math
        body = abs(cl - o)
        total_range = (h - l) if (h - l) > 0 else 0.01
        u_wick = h - max(o, cl)
        l_wick = min(o, cl) - l
        
        # Classification
        if body <= 0.4 * total_range: c_type = "REJECTION"
        elif body >= 0.6 * total_range: c_type = "MOMENTUM"
        else: c_type = "NEUTRAL"

        # Refined Prompt to prevent Bias
        prompt = (
            f"Market: {symbol}. O:{o}, H:{h}, L:{l}, C:{cl}. Type: {c_type}. "
            f"Wicks: Up {u_wick}, Low {l_wick}. Body: {body}. "
            f"Rules: Rejection with Long Lower Wick = BUY. Rejection with Long Upper Wick = SELL. "
            f"Momentum Bullish = SELL (pullback). Momentum Bearish = BUY (pullback). "
            f"Instructions: Decide BUY or SELL and give a 5-word reason. "
            f"Format: [SIDE] | [REASON]"
        )

        response = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
        )
        res = response.choices[0].message.content.strip().upper()
        
        side = "BUY" if "BUY" in res else "SELL"
        reason = res.split("|")[-1] if "|" in res else "Trend Analysis"
        return side, cl, reason
    except Exception as e:
        print(f"AI Error: {e}")
        return None, None, None

# --- 4. THE ENGINE: TRADE LOOP ---

def trade_engine(chat_id):
    u = get_user(chat_id)
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                # Safety Check: Can we afford the next lot?
                if u["balance"] < u["current_stake"]:
                    bot.send_message(chat_id, "⚠️ **STOPPED:** Insufficient balance for next stake. Use /reset.")
                    u["is_trading"] = False
                    break

                side, price, reason = get_ai_decision(u["symbol"], chat_id)
                if not side:
                    time.sleep(10)
                    continue
                
                # Scalp Logic (8 Pips for BTC)
                tp_dist = 8.0 if "BTC" in u["symbol"] else 0.5
                tp = price + tp_dist if "BUY" in side else price - tp_dist
                sl = price - (tp_dist * 2) if "BUY" in side else price + (tp_dist * 2)

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                
                # Track Max Stake
                if u["current_stake"] > u["max_stake"]: u["max_stake"] = u["current_stake"]

                bot.send_message(chat_id, f"🔫 **AI FIRED {side}**\nEntry: {price}\nReason: {reason}")

            else:
                # Monitoring Active Trade
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    profit = t['stake'] * 0.15 
                    u["balance"] += (t['stake'] + profit)
                    u["pnl"] += profit
                    u["wins"] += 1
                    u["current_stake"] = u["initial_stake"] # Reset Martingale
                    bot.send_message(chat_id, f"✅ **WIN!** +${round(profit, 2)}\nBalance: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["pnl"] -= t['stake']
                    u["losses"] += 1
                    u["current_stake"] *= 2.0 # Double Up
                    bot.send_message(chat_id, f"❌ **LOSS.** Next Stake: ${u['current_stake']}")
                    u["active_trade"] = None
            
            time.sleep(5)
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(10)

# --- 5. INTERFACE: COMMAND HANDLERS ---

@bot.message_handler(commands=['check'])
def diagnostics(message):
    report = "🔍 **SYSTEM HEALTH CHECK**\n\n"
    # Test Kraken
    try:
        market.fetch_ticker('BTC/USD')
        report += "✅ **Kraken API:** Connected\n"
    except: report += "❌ **Kraken API:** Offline\n"
    # Test Groq
    try:
        test = groq_client.chat.completions.create(messages=[{"role":"user","content":"Hi"}], model="llama-3.3-70b-versatile")
        report += "✅ **Groq AI:** Linked\n"
    except: report += "❌ **Groq AI:** Unauthorized\n"
    
    report += "✅ **Telegram Bot:** Online"
    bot.reply_to(message, report)

@bot.message_handler(commands=['status'])
def account_status(message):
    u = get_user(message.chat.id)
    rate = (u['wins'] / (u['wins'] + u['losses']) * 100) if (u['wins'] + u['losses']) > 0 else 0
    pnl_icon = "🟢" if u['pnl'] >= 0 else "🔴"
    
    msg = (
        f"📊 **TRADING REPORT**\n"
        f"------------------------\n"
        f"💰 **Balance:** ${round(u['balance'], 2)}\n"
        f"💵 **PnL:** {pnl_icon} ${round(u['pnl'], 2)}\n"
        f"🏆 **Wins/Losses:** {u['wins']}W | {u['losses']}L\n"
        f"🎯 **Win Rate:** {round(rate, 1)}%\n"
        f"🔥 **Max Stake Used:** ${round(u['max_stake'], 2)}\n"
        f"------------------------\n"
        f"Mode: {'Running 🔫' if u['is_trading'] else 'Idle 😴'}"
    )
    bot.send_message(message.chat.id, msg)

@bot.message_handler(commands=['trade'])
def start_process(message):
    m = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    m.add("BTC/USD", "ETH/USD")
    msg = bot.send_message(message.chat.id, "Select Asset:", reply_markup=m)
    bot.register_next_step_handler(msg, set_stake)

def set_stake(message):
    u = get_user(message.chat.id)
    u["symbol"] = message.text
    msg = bot.send_message(message.chat.id, f"Enter Base Stake for {u['symbol']} (e.g. 10):")
    bot.register_next_step_handler(msg, finish_init)

def finish_init(message):
    try:
        u = get_user(message.chat.id)
        stake = float(message.text)
        u["initial_stake"] = stake
        u["current_stake"] = stake
        u["is_trading"] = True
        bot.send_message(message.chat.id, f"🚀 **ENGINE STARTED**\nStake: ${stake}\nAsset: {u['symbol']}")
        threading.Thread(target=trade_engine, args=(message.chat.id,), daemon=True).start()
    except:
        bot.send_message(message.chat.id, "❌ Invalid stake. Numbers only.")

@bot.message_handler(commands=['reset'])
def reset_user(message):
    u = get_user(message.chat.id)
    u.update({"balance": 10000.0, "pnl": 0.0, "wins": 0, "losses": 0, "max_stake": 0.0, "active_trade": None})
    bot.reply_to(message, "🔄 **WALLET RESET** to $10,000.00.")

@bot.message_handler(commands=['start', 'help', 'stop'])
def handle_basics(message):
    if "stop" in message.text:
        get_user(message.chat.id)["is_trading"] = False
        bot.send_message(message.chat.id, "🛑 Stopping engine...")
    else:
        bot.reply_to(message, "🤖 **AI ENGINE V4**\n/trade - Start\n/stop - Stop\n/status - Stats\n/check - Diagnostics\n/reset - Reset Funds")

# --- 6. RENDER BOILERPLATE ---
@app.route('/')
def home(): return "AI Master Engine Live", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    
    # FORCING SINGLE INSTANCE
    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
    except: pass
    
    print("🚀 Bot Started...")
    bot.polling(non_stop=True)
