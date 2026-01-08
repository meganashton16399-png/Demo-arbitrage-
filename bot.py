import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask
import pandas as pd

# --- 1. CONFIG ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)
market = ccxt.kraken({'enableRateLimit': True})

users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 10000.0, "total_lost": 0.0, "wins": 0, "losses": 0,
            "initial_stake": 50.0, "current_stake": 50.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USD"
        }
    return users[chat_id]

# --- 2. DATA FETCHER WITH TG LOGS ---
def get_market_data(symbol, chat_id):
    try:
        # Step 1: Data Fetching
        tf5 = market.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        tf1 = market.fetch_ohlcv(symbol, timeframe='1m', limit=30)
        ticker = market.fetch_ticker(symbol)
        
        if not tf5 or not tf1:
            bot.send_message(chat_id, "⚠️ Kraken ne khali data bheja hai. Restarting scan...")
            return None

        df5 = pd.DataFrame(tf5, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(tf1, columns=['t','o','h','l','c','v'])
        
        e20 = df5['c'].ewm(span=20).mean().iloc[-1]
        e50 = df5['c'].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if e20 > e50 else "SELL"
        
        delta = df1['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain / loss).iloc[-1]))
        
        spread = ticker['ask'] - ticker['bid']
        return bias, rsi, spread, ticker['last'], df1.iloc[-2].to_dict()
    except Exception as e:
        bot.send_message(chat_id, f"❌ Data Fetch Error: {str(e)[:50]}")
        return None

# --- 3. AI LOGIC WITH TG LOGS ---
def get_ai_v10(symbol, chat_id):
    data = get_market_data(symbol, chat_id)
    if not data: return None, None, "Data Error", 0
    
    bias, rsi, spread, price, last_c = data
    spread_pips = (spread / price) * 10000

    if spread_pips > 1.2:
        bot.send_message(chat_id, f"⏳ Spread high hai ({round(spread_pips,2)} pips). Waiting...")
        return "SKIP", price, "High Spread", 0

    # Notify TG that AI is processing
    status_msg = bot.send_message(chat_id, "🧠 Groq AI setup analyze kar raha hai...")

    prompt = (
        f"BIAS: {bias}. RSI: {round(rsi,1)}. PRICE: {price}. "
        f"LAST_C: O:{last_c['o']} C:{last_c['c']}. "
        f"TASK: Rate setup 0-100. Need {bias} alignment + RSI 40-60. "
        f"OUTPUT ONLY: [SIDE/SKIP] | [SCORE] | [REASON 5 WORDS]"
    )

    try:
        res = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
        ).choices[0].message.content.strip().upper()
        
        bot.delete_message(chat_id, status_msg.message_id) # Remove "thinking" msg
        
        parts = res.split("|")
        side = parts[0].strip()
        score = int(''.join(filter(str.isdigit, parts[1]))) if len(parts) > 1 else 0
        reason = parts[2].strip() if len(parts) > 2 else "Scanning"
        
        if score < 45 or "SKIP" in side:
            return "SKIP", price, reason, score
        return side, price, reason, score
    except Exception as e:
        bot.delete_message(chat_id, status_msg.message_id)
        bot.send_message(chat_id, f"❌ AI Timeout/Error: {str(e)[:50]}")
        return None, None, "AI Error", 0

# --- 4. ENGINE WITH LIVE FEEDBACK ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, "🚀 **V10.2 Live Engine Chalu Ho Gaya Hai!**")
    
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                # 1. Update status
                side, price, reason, score = get_ai_v10(u["symbol"], chat_id)
                
                if not side:
                    time.sleep(10); continue
                
                if side == "SKIP":
                    # Optionally notify about skips every few minutes to avoid spam
                    time.sleep(10); continue

                # 2. Order Placement
                tp_dist = 10.0 if "BTC" in u["symbol"] else 0.8
                tp = price + tp_dist if "BUY" in side else price - tp_dist
                sl = price - tp_dist if "BUY" in side else price + tp_dist

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **SNIPER ENTRY: {side} ({score}%)**\nReason: {reason}\nStake: ${round(u['current_stake'],2)}")

            else:
                # 3. Monitoring
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    total_recovery = t['stake'] + u["total_lost"] + (u["initial_stake"] * 0.1)
                    u["balance"] += total_recovery
                    u["wins"] += 1
                    u["total_lost"] = 0
                    u["current_stake"] = u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN!** Balance: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["total_lost"] += t['stake']
                    u["losses"] += 1
                    u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.6
                    bot.send_message(chat_id, f"❌ **LOSS.** Next stake: ${round(u['current_stake'],2)}")
                    u["active_trade"] = None
            
            time.sleep(5)
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Loop Glitch: {str(e)[:50]}")
            time.sleep(10)

# --- (Other Commands: /start, /check, /status, /reset, /trade, /stop remain same) ---
# ... (same command handlers as V10.1) ...

@bot.message_handler(commands=['start', 'help'])
def help_cmd(message):
    h = ("🤖 **V10.2 LIVE SNIPER**\n\n"
         "/trade - Start Engine\n"
         "/stop - Stop Engine\n"
         "/status - View Stats\n"
         "/check - API Health\n"
         "/reset - Reset $10k")
    bot.reply_to(message, h)

@bot.message_handler(commands=['check'])
def check_status(m):
    try:
        p = market.fetch_ticker('BTC/USD')['last']
        groq_client.chat.completions.create(messages=[{"role":"user","content":"Hi"}], model="llama-3.3-70b-versatile")
        bot.reply_to(m, f"✅ **ALL GREEN**\nKraken Price: ${p}\nGroq AI: Connected")
    except Exception as e:
        bot.reply_to(m, f"❌ **ERROR:** {str(e)}")

@bot.message_handler(commands=['status'])
def report(m):
    u = get_user(m.chat.id)
    total = u['wins'] + u['losses']
    rate = (u['wins']/total*100) if total > 0 else 0
    bot.send_message(m.chat.id, f"📊 **STATS**\nBal: ${round(u['balance'],2)}\nWins: {u['wins']} | Losses: {u['losses']}\nWin Rate: {round(rate,1)}%\nNext Lot: ${round(u['current_stake'],2)}")

@bot.message_handler(commands=['reset'])
def reset_bot(m):
    users[m.chat.id] = {"balance": 10000.0, "total_lost": 0.0, "wins": 0, "losses": 0, "initial_stake": 50.0, "current_stake": 50.0, "active_trade": None, "is_trading": False, "symbol": "BTC/USD"}
    bot.reply_to(m, "🔄 **System Reset Done.**")

@bot.message_handler(commands=['trade'])
def trade_init(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("BTC/USD", "ETH/USD")
    msg = bot.send_message(m.chat.id, "Asset select karein:", reply_markup=kb)
    bot.register_next_step_handler(msg, lambda msg: bot.register_next_step_handler(bot.send_message(m.chat.id, "Base Stake (e.g. 50):"), lambda s: start_engine(msg, s)))

def start_engine(m, s):
    u = get_user(m.chat.id)
    try:
        u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
        bot.send_message(m.chat.id, f"🚀 **V10.2 ENGINE STARTED**\nTarget: {u['symbol']}")
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()
    except:
        bot.send_message(m.chat.id, "❌ Invalid stake value.")

@bot.message_handler(commands=['stop'])
def stop_loop(m): get_user(m.chat.id)["is_trading"] = False; bot.send_message(m.chat.id, "🛑 Stopping...")

@app.route('/')
def home(): return "V10.2 Pulse Active", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
    except: pass
    bot.polling(non_stop=True)
