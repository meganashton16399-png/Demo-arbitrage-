import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask
import numpy as np

# --- 1. CONFIGURATION ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)
market = ccxt.kraken({'enableRateLimit': True}) 

# --- 2. UNIVERSAL STORAGE ---
users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 10000.0, "pnl": 0.0, "wins": 0, "losses": 0,
            "initial_stake": 50.0, "current_stake": 50.0, "max_stake": 0.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USD"
        }
    return users[chat_id]

# --- 3. THE "PERFECT" AI PROMPT ENGINE ---

def get_indicators(prices):
    prices = np.array(prices)
    sma = np.mean(prices[-20:])
    std = np.std(prices[-20:])
    upper, lower = sma + (2 * std), sma - (2 * std)
    # Fast RSI (7)
    deltas = np.diff(prices)
    up = deltas[deltas >= 0].sum() / 7
    down = -deltas[deltas < 0].sum() / 7
    rsi = 100. - 100./(1. + (up/down if down != 0 else 1))
    return upper, lower, rsi

def get_ai_sniper_decision(symbol, chat_id):
    try:
        ohlcv = market.fetch_ohlcv(symbol, timeframe='1m', limit=30)
        prices = [x[4] for x in ohlcv]
        upper, lower, rsi = get_indicators(prices)
        curr_p = prices[-1]
        
        # Immediate Decision Prompt
        prompt = (
            f"SYSTEM: High-Frequency Scalp. Target 5 pips. "
            f"DATA: Price:{curr_p}, UpperBand:{round(upper,2)}, LowerBand:{round(lower,2)}, RSI:{round(rsi,1)}. "
            f"RULES: If Price >= UpperBand OR RSI > 70 -> SELL. If Price <= LowerBand OR RSI < 30 -> BUY. "
            f"MANDATORY: You MUST pick BUY or SELL. No waiting. "
            f"FORMAT: [SIDE] | [3-WORD REASON]"
        )

        response = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
        )
        res = response.choices[0].message.content.strip().upper()
        
        side = "BUY" if "BUY" in res else "SELL"
        reason = res.split("|")[-1] if "|" in res else "Price Action Spike"
        return side, curr_p, reason
    except Exception as e:
        print(f"AI Error: {e}")
        return None, None, None

# --- 4. THE EXECUTIONER ENGINE ---

def trade_engine(chat_id):
    u = get_user(chat_id)
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                side, price, reason = get_ai_sniper_decision(u["symbol"], chat_id)
                if not side:
                    time.sleep(2)
                    continue
                
                # 5-Pip Scalp Logic
                tp_dist = 5.0 if "BTC" in u["symbol"] else 0.4
                tp = price + tp_dist if "BUY" in side else price - tp_dist
                sl = price - (tp_dist * 2) if "BUY" in side else price + (tp_dist * 2)

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                
                # Track Max Stake
                if u["current_stake"] > u["max_stake"]: u["max_stake"] = u["current_stake"]

                bot.send_message(chat_id, f"🔫 **SNIPER FIRED {side}**\nPrice: {price}\nReason: {reason}")

            else:
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    profit = t['stake'] * 0.12 # Scalp Profit
                    u["balance"] += (t['stake'] + profit)
                    u["pnl"] += profit
                    u["wins"] += 1
                    u["current_stake"] = u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN!** +${round(profit, 2)}\nBalance: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["pnl"] -= t['stake']
                    u["losses"] += 1
                    u["current_stake"] *= 2.0 # NO CAP MARTINGALE
                    bot.send_message(chat_id, f"❌ **LOSS.** Next stake: ${u['current_stake']}")
                    u["active_trade"] = None
            
            time.sleep(2) # Ultra-fast 2s polling
        except:
            time.sleep(5)

# --- 5. COMMANDS ---

@bot.message_handler(commands=['check'])
def check_api(message):
    try:
        market.fetch_ticker('BTC/USD')
        test = groq_client.chat.completions.create(messages=[{"role":"user","content":"Hi"}], model="llama-3.3-70b-versatile")
        bot.reply_to(message, "✅ Kraken & Groq AI are LINKED and responding.")
    except Exception as e:
        bot.reply_to(message, f"❌ Connection Error: {str(e)}")

@bot.message_handler(commands=['status'])
def status_report(message):
    u = get_user(message.chat.id)
    rate = (u['wins'] / (u['wins'] + u['losses']) * 100) if (u['wins'] + u['losses']) > 0 else 0
    pnl_icon = "🟢" if u['pnl'] >= 0 else "🔴"
    bot.send_message(message.chat.id, 
        f"📊 **LIVE STATUS**\n"
        f"------------------------\n"
        f"💰 **Balance:** ${round(u['balance'], 2)}\n"
        f"💵 **PnL:** {pnl_icon} ${round(u['pnl'], 2)}\n"
        f"🏆 **Wins/Losses:** {u['wins']}W | {u['losses']}L\n"
        f"🎯 **Win Rate:** {round(rate, 1)}%\n"
        f"🔥 **Max Stake Used:** ${round(u['max_stake'], 2)}\n"
        f"------------------------\n"
        f"Mode: {'Running 🔫' if u['is_trading'] else 'Idle 😴'}")

@bot.message_handler(commands=['reset'])
def reset_funds(message):
    users[message.chat.id] = {"balance": 10000.0, "pnl": 0.0, "wins": 0, "losses": 0, "max_stake": 0.0, "active_trade": None, "is_trading": False}
    bot.reply_to(message, "🔄 Funds reset to **$10,000.00** virtual.")

@bot.message_handler(commands=['trade'])
def start_sniper(message):
    m = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    m.add("BTC/USD", "ETH/USD")
    msg = bot.send_message(message.chat.id, "Select Asset:", reply_markup=m)
    bot.register_next_step_handler(msg, get_stake_value)

def get_stake_value(message):
    u = get_user(message.chat.id)
    u["symbol"] = message.text
    msg = bot.send_message(message.chat.id, "Enter Base Stake (e.g. 50):")
    bot.register_next_step_handler(msg, launch_engine)

def launch_engine(message):
    try:
        u = get_user(message.chat.id)
        u["initial_stake"] = float(message.text)
        u["current_stake"] = u["initial_stake"]
        u["is_trading"] = True
        bot.send_message(message.chat.id, f"🌪️ **QUANTUM SNIPER LIVE**\nAsset: {u['symbol']}\nStake: ${u['initial_stake']}")
        threading.Thread(target=trade_engine, args=(message.chat.id,), daemon=True).start()
    except:
        bot.send_message(message.chat.id, "❌ Invalid stake value.")

@bot.message_handler(commands=['stop'])
def stop_loop(message):
    get_user(message.chat.id)["is_trading"] = False
    bot.send_message(message.chat.id, "🛑 Stopping engine...")

@bot.message_handler(commands=['help', 'start'])
def help_cmd(message):
    bot.reply_to(message, "🤖 **COMMANDS**\n/trade - Start\n/stop - Stop\n/status - Stats\n/check - API Test\n/reset - Reset $10k")

# --- 6. RENDER BOILERPLATE ---
@app.route('/')
def health(): return "Quantum Sniper V7 Live", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
    except: pass
    bot.polling(non_stop=True)
