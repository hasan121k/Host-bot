# -*- coding: utf-8 -*-
import atexit
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from flask import Flask
from threading import Thread
import psutil
import requests
import telebot
from telebot import types
import psycopg2
from psycopg2.extras import RealDictCursor

# --- Configurable Conversion Rate ---
USDT_BDT_RATE = 120.0

# --- Flask Keep Alive ---
app = Flask("")

@app.route("/")
def home():
    return "I'm Mukesh File Host"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Flask Keep-Alive server started.")

# --- Configuration ---
TOKEN = "8859070754:AAEIotfAihuu3socMBrrTD3sBt1S8jkwTuk"
OWNER_ID = 7884194046
ADMIN_ID = 7884194046
YOUR_USERNAME = "@Tra_der_habib"
UPDATE_CHANNEL = "https://t.me/chenelhub"

# --- Binance Pay Integration Config ---
BINANCE_API_KEY = ""
BINANCE_SECRET_KEY = ""
BINANCE_PAY_ID = ""

# Folder setup
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, "upload_bots")
IROTECH_DIR = os.path.join(BASE_DIR, "inf")

# File upload limits
FREE_USER_LIMIT = 0
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 999
OWNER_LIMIT = float("inf")

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

# --- Data structures ---
bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
user_selected_plan = {}
blocked_users = set()

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- Supabase PostgreSQL Connection ---
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    """PostgreSQL connection for Supabase"""
    if not DATABASE_URL:
        logger.error("❌ DATABASE_URL not set in environment!")
        return None
    try:
        return psycopg2.connect(DATABASE_URL, sslmode='require')
    except Exception as e:
        logger.error(f"❌ Database connection error: {e}")
        return None

# --- Supabase Database Operations (Permanent Storage) ---

def load_data():
    """Load all persistent data from Supabase"""
    global active_users, admin_ids, blocked_users, user_subscriptions, user_files
    try:
        conn = get_db_connection()
        if not conn:
            return
        c = conn.cursor()

        # Load active users
        c.execute("SELECT user_id FROM active_users")
        active_users.update(row[0] for row in c.fetchall())

        # Load admins
        c.execute("SELECT user_id FROM admins")
        admin_ids.update(row[0] for row in c.fetchall())

        # Load blocked users
        c.execute("SELECT user_id FROM blocked_users")
        blocked_users.update(row[0] for row in c.fetchall())

        # Load subscriptions
        c.execute("SELECT user_id, plan_name, expiry FROM subscriptions")
        for row in c.fetchall():
            uid, pname, exp = row
            if isinstance(exp, str):
                exp = datetime.fromisoformat(exp)
            user_subscriptions[uid] = {"plan_name": pname, "expiry": exp}

        # Load user files
        c.execute("SELECT user_id, file_name, file_type FROM user_files")
        for uid, fname, ftype in c.fetchall():
            if uid not in user_files:
                user_files[uid] = []
            user_files[uid].append((fname, ftype))

        conn.close()
        logger.info("✅ All data loaded successfully from Supabase.")
    except Exception as e:
        logger.error(f"❌ Error loading data from Supabase: {e}")

def add_active_user(user_id):
    active_users.add(user_id)
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("INSERT INTO active_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error saving active user: {e}")

def save_subscription(user_id, plan_name, expiry):
    user_subscriptions[user_id] = {"plan_name": plan_name, "expiry": expiry}
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO subscriptions (user_id, plan_name, expiry)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET plan_name = EXCLUDED.plan_name, expiry = EXCLUDED.expiry
            """, (user_id, plan_name, expiry))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error saving subscription: {e}")

def remove_subscription_db(user_id):
    if user_id in user_subscriptions:
        del user_subscriptions[user_id]
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("DELETE FROM subscriptions WHERE user_id = %s", (user_id,))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error removing subscription: {e}")

def save_user_file(user_id, file_name, file_type="py"):
    if user_id not in user_files:
        user_files[user_id] = []
    user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
    user_files[user_id].append((file_name, file_type))
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO user_files (user_id, file_name, file_type)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, file_name) DO UPDATE SET file_type = EXCLUDED.file_type
            """, (user_id, file_name, file_type))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error saving user file: {e}")

def remove_user_file_db(user_id, file_name):
    if user_id in user_files:
        user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("DELETE FROM user_files WHERE user_id = %s AND file_name = %s", (user_id, file_name))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error removing user file: {e}")

def add_plan_db(name, file_limit, price, duration, buy_link):
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("INSERT INTO plans (name, file_limit, price, duration, buy_link) VALUES (%s, %s, %s, %s, %s)",
                      (name, file_limit, price, duration, buy_link))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error adding plan: {e}")

def get_all_plans():
    try:
        conn = get_db_connection()
        if not conn:
            return []
        c = conn.cursor()
        c.execute("SELECT plan_id, name, file_limit, price, duration, buy_link FROM plans")
        plans = c.fetchall()
        conn.close()
        return plans
    except Exception as e:
        logger.error(f"Error getting plans: {e}")
        return []

def get_plan_by_id(plan_id):
    try:
        conn = get_db_connection()
        if not conn:
            return None
        c = conn.cursor()
        c.execute("SELECT plan_id, name, file_limit, price, duration, buy_link FROM plans WHERE plan_id = %s", (plan_id,))
        plan = c.fetchone()
        conn.close()
        return plan
    except Exception as e:
        logger.error(f"Error getting plan by id: {e}")
        return None

def delete_plan_db(plan_id):
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("DELETE FROM plans WHERE plan_id = %s", (plan_id,))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error deleting plan: {e}")

def get_pending_payment(user_id, plan_id):
    try:
        conn = get_db_connection()
        if not conn:
            return 0.0
        c = conn.cursor()
        c.execute("SELECT paid_amount FROM pending_payments WHERE user_id = %s AND plan_id = %s", (user_id, plan_id))
        row = c.fetchone()
        conn.close()
        return float(row[0]) if row else 0.0
    except Exception as e:
        logger.error(f"Error getting pending payment: {e}")
        return 0.0

def update_pending_payment(user_id, plan_id, amount):
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO pending_payments (user_id, plan_id, paid_amount)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, plan_id) DO UPDATE SET paid_amount = EXCLUDED.paid_amount
            """, (user_id, plan_id, amount))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error updating pending payment: {e}")

def clear_pending_payment(user_id, plan_id):
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("DELETE FROM pending_payments WHERE user_id = %s AND plan_id = %s", (user_id, plan_id))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error clearing pending payment: {e}")

def is_txid_used(tx_id):
    try:
        conn = get_db_connection()
        if not conn:
            return False
        c = conn.cursor()
        c.execute("SELECT tx_id FROM used_txids WHERE tx_id = %s", (str(tx_id).strip(),))
        row = c.fetchone()
        conn.close()
        return row is not None
    except Exception as e:
        logger.error(f"Error checking txid: {e}")
        return False

def add_used_txid(tx_id):
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("INSERT INTO used_txids (tx_id) VALUES (%s) ON CONFLICT (tx_id) DO NOTHING", (str(tx_id).strip(),))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error adding txid: {e}")

# --- Required Channels System ---
def get_required_channels():
    try:
        conn = get_db_connection()
        if not conn:
            return []
        c = conn.cursor()
        c.execute("SELECT channel_username, channel_id FROM required_channels")
        channels = c.fetchall()
        conn.close()
        return channels
    except Exception as e:
        logger.error(f"Error fetching channels: {e}")
        return []

def add_required_channel(channel_username, channel_id, added_by):
    try:
        conn = get_db_connection()
        if not conn:
            return False
        c = conn.cursor()
        c.execute(
            "INSERT INTO required_channels (channel_username, channel_id, added_by) VALUES (%s, %s, %s) ON CONFLICT (channel_username) DO NOTHING",
            (channel_username, channel_id, added_by)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        return False

def remove_required_channel(channel_username):
    try:
        conn = get_db_connection()
        if not conn:
            return False
        c = conn.cursor()
        c.execute("DELETE FROM required_channels WHERE channel_username = %s", (channel_username,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error removing channel: {e}")
        return False

def check_user_channels(user_id):
    required = get_required_channels()
    if not required:
        return True, []
    
    not_joined = []
    for channel_username, channel_id in required:
        try:
            target_chat_id = channel_id if channel_id else channel_username
            member = bot.get_chat_member(target_chat_id, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                not_joined.append(channel_username)
        except Exception as e:
            logger.error(f"Error checking channel {channel_username}: {e}")
            not_joined.append(channel_username)
    
    return len(not_joined) == 0, not_joined

def is_user_verified(user_id):
    if user_id == OWNER_ID or user_id in admin_ids:
        return True, []
    return check_user_channels(user_id)

def send_force_sub_message(chat_id, not_joined):
    msg = "⚠️ **বটটি ব্যবহার করতে হলে আপনাকে আমাদের চ্যানেলে জয়েন হতে হবে!**\n\n"
    msg += "দয়া করে নিচের সবকটি চ্যানেলে জয়েন করুন:\n"
    for ch in not_joined:
        msg += f"• {ch}\n"
    msg += "\n✅ সব চ্যানেলে জয়েন করে **'🔄 চেক করুন'** বাটনে ক্লিক করুন।"
    
    markup = types.InlineKeyboardMarkup()
    for ch in not_joined:
        clean_user = ch.replace('@', '')
        markup.add(types.InlineKeyboardButton(f"📢 Join {ch}", url=f"https://t.me/{clean_user}"))
    markup.add(types.InlineKeyboardButton("🔄 চেক করুন", callback_data="check_verify"))
    
    bot.send_message(chat_id, msg, reply_markup=markup, parse_mode="Markdown")

# --- Blocked Users Functions ---
def is_user_blocked(user_id):
    return user_id in blocked_users

def block_user(user_id, blocked_by):
    try:
        conn = get_db_connection()
        if not conn:
            return False
        c = conn.cursor()
        c.execute(
            "INSERT INTO blocked_users (user_id, blocked_by) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
            (user_id, blocked_by)
        )
        conn.commit()
        conn.close()
        blocked_users.add(user_id)
        return True
    except Exception as e:
        logger.error(f"Error blocking user: {e}")
        return False

def unblock_user(user_id):
    try:
        conn = get_db_connection()
        if not conn:
            return False
        c = conn.cursor()
        c.execute("DELETE FROM blocked_users WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()
        blocked_users.discard(user_id)
        return True
    except Exception as e:
        logger.error(f"Error unblocking user: {e}")
        return False

# --- User Management Functions ---
def get_all_users():
    return list(active_users)

def get_user_details(user_id):
    details = {
        "user_id": user_id,
        "is_owner": user_id == OWNER_ID,
        "is_admin": user_id in admin_ids,
        "is_blocked": is_user_blocked(user_id),
        "is_active": user_id in active_users,
        "file_count": get_user_file_count(user_id),
        "file_limit": get_user_file_limit(user_id),
        "subscription": None
    }
    
    if user_id in user_subscriptions:
        sub = user_subscriptions[user_id]
        if sub["expiry"] > datetime.now():
            details["subscription"] = {
                "plan": sub.get("plan_name", "Premium"),
                "expiry": sub["expiry"]
            }
    
    return details

def get_bot_stats():
    return {
        "total_users": len(active_users),
        "total_files": sum(len(files) for files in user_files.values()),
        "total_subscribed": sum(1 for uid, sub in user_subscriptions.items() if sub["expiry"] > datetime.now()),
        "total_blocked": len(blocked_users),
        "total_admins": len(admin_ids)
    }

# --- Malware Scanning Bypass (Safe & Open) ---
def scan_file_for_malware(file_content, file_name, user_id):
    return True, "File passed security check"

# Load initial data from Supabase
load_data()

# --- Price Parser ---
def parse_price_to_usdt(price_str):
    price_clean = str(price_str).upper().strip()
    numbers = re.findall(r"[-+]?\d*\.\d+|\d+", price_clean)
    if not numbers:
        return 0.0, price_str
    val = float(numbers[0])
    if "BDT" in price_clean or "TAKA" in price_clean or "TK" in price_clean:
        usdt_val = round(val / USDT_BDT_RATE, 2)
        return usdt_val, f"{price_str} (~{usdt_val} USDT)"
    elif "USDT" in price_clean or "$" in price_clean or "USD" in price_clean:
        return round(val, 2), f"{val} USDT"
    else:
        return round(val, 2), f"{val} USDT"

# --- Binance Pay Verification ---
def check_binance_payment(pay_order_id):
    if not BINANCE_API_KEY or BINANCE_API_KEY == "YOUR_NEW_BINANCE_API_KEY_HERE":
        return False, 0.0, "Binance API Key configured নেই।"
    endpoint = "https://api.binance.com/sapi/v1/pay/transactions"
    timestamp = int(time.time() * 1000)
    query_string = f"timestamp={timestamp}"
    signature = hmac.new(BINANCE_SECRET_KEY.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
    url = f"{endpoint}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            transactions = data.get("data", []) if isinstance(data, dict) else data
            for item in transactions:
                order_id_str = str(item.get("orderId", "") or item.get("transactionId", ""))
                if order_id_str.strip() == str(pay_order_id).strip():
                    amount = float(item.get("amount", 0.0))
                    currency = item.get("currency", "USDT")
                    return True, amount, f"{amount} {currency}"
            return False, 0.0, "এই Order/Transaction ID টি আপনার Binance Pay হিস্টোরিতে পাওয়া যায়নি।"
        else:
            logger.error(f"Binance Pay API Error: {res.text}")
            return False, 0.0, "Binance Server Error বা API পারমিশন ইস্যু।"
    except Exception as e:
        logger.error(f"Binance Verification Error: {e}")
        return False, 0.0, f"Error: {str(e)}"

# --- Helper Functions ---
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_file_limit(user_id):
    if user_id == OWNER_ID:
        return OWNER_LIMIT
    if user_id in admin_ids:
        return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]["expiry"] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get("process"):
        try:
            proc = psutil.Process(script_info["process"].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                if "log_file" in script_info and hasattr(script_info["log_file"], "close") and not script_info["log_file"].closed:
                    try:
                        script_info["log_file"].close()
                    except Exception:
                        pass
                if script_key in bot_scripts:
                    del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            return False
        except Exception:
            return False
    return False

def kill_process_tree(process_info):
    try:
        if "log_file" in process_info and hasattr(process_info["log_file"], "close") and not process_info["log_file"].closed:
            try:
                process_info["log_file"].close()
            except Exception:
                pass
        process = process_info.get("process")
        if process and hasattr(process, "pid"):
            pid = process.pid
            if pid:
                parent = psutil.Process(pid)
                for child in parent.children(recursive=True):
                    try:
                        child.terminate()
                    except Exception:
                        pass
                try:
                    parent.terminate()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"❌ Error killing process: {e}")

# --- Module Mapping ---
TELEGRAM_MODULES = {
    "telebot": "pyTelegramBotAPI",
    "telegram": "python-telegram-bot",
    "python_telegram_bot": "python-telegram-bot",
    "aiogram": "aiogram",
    "pyrogram": "pyrogram",
    "telethon": "telethon",
    "bs4": "beautifulsoup4",
    "requests": "requests",
    "pillow": "Pillow",
    "cv2": "opencv-python",
    "flask": "Flask",
    "psutil": "psutil",
}

# --- Script Running ---
def monitor_and_guide_error(process, log_file_path, script_owner_id, file_name, message_obj_for_reply):
    time.sleep(3)
    if process.poll() is not None:
        try:
            with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
                log_content = f.read()
            match_py = re.search(r"(?:ModuleNotFoundError|ImportError): No module named '(.+?)'", log_content)
            match_js = re.search(r"Cannot find module '(.+?)'", log_content)
            missing_module = None
            if match_py:
                missing_module = match_py.group(1).split(".")[0].strip("'\"")
            elif match_js:
                missing_module = match_js.group(1).split("/")[0].strip("'\"")
            if missing_module:
                pkg_name = TELEGRAM_MODULES.get(missing_module.lower(), missing_module)
                ext = os.path.splitext(file_name)[1].lower()
                cmd_text = f"npm install {pkg_name}" if ext == ".js" else f"pip install {pkg_name}"
                error_msg = (f"⚠️ **ফাইল রান হতে সমস্যা হয়েছে!**\n\n"
                             f"📄 **File:** `{file_name}`\n"
                             f"❌ **সমস্যা:** আপনার কোডে `{missing_module}` মডিউলটি মিসিং আছে।\n"
                             f"💻 **প্রয়োজনীয় কমান্ড:** `{cmd_text}`\n\n"
                             f"👇 *নিচের বাটনে প্রেস করে সরাসরি মডিউলটি ইনস্টল করুন:*")
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(f"📦 Install {pkg_name}", callback_data=f"instmod_{script_owner_id}_{missing_module}_{file_name}"))
                markup.add(types.InlineKeyboardButton("📄 View Error Logs", callback_data=f"viewlog_{script_owner_id}_{file_name}"))
                bot.reply_to(message_obj_for_reply, error_msg, reply_markup=markup, parse_mode="Markdown")
            else:
                error_msg = (f"⚠️ **আপনার কোডে ভুল (Syntax/Runtime Error) পাওয়া গেছে!**\n\n"
                             f"📄 **File:** `{file_name}`\n"
                             f"সুনির্দিষ্ট এরর জানতে নিচের **View Logs** বাটনে ক্লিক করুন।")
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("📄 View Error Logs", callback_data=f"viewlog_{script_owner_id}_{file_name}"))
                bot.reply_to(message_obj_for_reply, error_msg, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error checking log file: {e}")

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply):
    script_key = f"{script_owner_id}_{file_name}"
    try:
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, "w", encoding="utf-8", errors="ignore")
        process = subprocess.Popen([sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=log_file, stdin=subprocess.PIPE)
        bot_scripts[script_key] = {"process": process, "log_file": log_file, "file_name": file_name,
                                   "script_owner_id": script_owner_id, "start_time": datetime.now(),
                                   "user_folder": user_folder, "type": "py", "script_key": script_key}
        bot.reply_to(message_obj_for_reply, f"🚀 **Python Script Started!**\n📄 File: `{file_name}`\n🆔 PID: `{process.pid}`", parse_mode="Markdown")
        threading.Thread(target=monitor_and_guide_error, args=(process, log_file_path, script_owner_id, file_name, message_obj_for_reply)).start()
    except Exception as e:
        bot.reply_to(message_obj_for_reply, f"❌ Error running script: {str(e)}")

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply):
    script_key = f"{script_owner_id}_{file_name}"
    try:
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, "w", encoding="utf-8", errors="ignore")
        process = subprocess.Popen(["node", script_path], cwd=user_folder, stdout=log_file, stderr=log_file, stdin=subprocess.PIPE)
        bot_scripts[script_key] = {"process": process, "log_file": log_file, "file_name": file_name,
                                   "script_owner_id": script_owner_id, "start_time": datetime.now(),
                                   "user_folder": user_folder, "type": "js", "script_key": script_key}
        bot.reply_to(message_obj_for_reply, f"🚀 **JS Script Started!**\n📄 File: `{file_name}`\n🆔 PID: `{process.pid}`", parse_mode="Markdown")
        threading.Thread(target=monitor_and_guide_error, args=(process, log_file_path, script_owner_id, file_name, message_obj_for_reply)).start()
    except Exception as e:
        bot.reply_to(message_obj_for_reply, f"❌ Error running JS script: {str(e)}")

# --- Menu Creation ---
def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout_to_use = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row in layout_to_use:
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup

def create_admin_panel_inline():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ 𝗔𝗱𝗱 𝗣𝗹𝗮𝗻", callback_data="add_plan_init"),
        types.InlineKeyboardButton("🗑️ 𝗠𝗮𝗻𝗮𝗴𝗲 𝗣𝗹𝗮𝗻𝘀", callback_data="manage_plans"),
    )
    markup.add(
        types.InlineKeyboardButton("💎 𝗔𝗱𝗱 𝗦𝘂𝗯𝘀𝗰𝗿𝗶𝗽𝘁𝗶𝗼𝗻", callback_data="add_subscription"),
        types.InlineKeyboardButton("❌ 𝗥𝗲𝗺𝗼𝘃𝗲 𝗦𝘂𝗯", callback_data="remove_subscription"),
    )
    markup.add(
        types.InlineKeyboardButton("👑 𝗔𝗱𝗱 𝗔𝗱𝗺𝗶𝗻", callback_data="add_admin"),
        types.InlineKeyboardButton("➖ 𝗥𝗲𝗺𝗼𝘃𝗲 𝗔𝗱𝗺𝗶𝗻", callback_data="remove_admin"),
    )
    markup.add(
        types.InlineKeyboardButton("📢 𝗔𝗱𝗱 𝗖𝗵𝗮𝗻𝗻𝗲𝗹", callback_data="add_channel"),
        types.InlineKeyboardButton("❌ 𝗥𝗲𝗺𝗼𝘃𝗲 𝗖𝗵𝗮𝗻𝗻𝗲𝗹", callback_data="remove_channel"),
    )
    markup.add(
        types.InlineKeyboardButton("📋 𝗔𝗹𝗹 𝗨𝘀𝗲𝗿𝘀", callback_data="all_users"),
        types.InlineKeyboardButton("🔍 𝗙𝗶𝗻𝗱 𝗨𝘀𝗲𝗿", callback_data="find_user"),
    )
    markup.add(
        types.InlineKeyboardButton("📊 𝗕𝗼𝘁 𝗦𝘁𝗮𝘁𝘀", callback_data="stats"),
        types.InlineKeyboardButton("📣 𝗕𝗿𝗼𝗮𝗱𝗰𝗮𝘀𝘁", callback_data="broadcast"),
    )
    markup.add(
        types.InlineKeyboardButton("🚫 𝗕𝗹𝗼𝗰𝗸 𝗨𝘀𝗲𝗿", callback_data="block_user"),
        types.InlineKeyboardButton("🔓 𝗨𝗻𝗯𝗹𝗼𝗰𝗸 𝗨𝘀𝗲𝗿", callback_data="unblock_user"),
    )
    markup.add(
        types.InlineKeyboardButton("🔐 𝗟𝗼𝗰𝗸/𝗨𝗻𝗹𝗼𝗰𝗸", callback_data="toggle_lock"),
        types.InlineKeyboardButton("⚙️ 𝗥𝘂𝗻 𝗔𝗹𝗹 𝗦𝗰𝗿𝗶𝗽𝘁𝘀", callback_data="run_all_scripts"),
    )
    return markup

# --- Core User Logic ---
def _logic_send_welcome(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    
    if is_user_blocked(user_id) and user_id not in admin_ids:
        bot.send_message(chat_id, "🚫 **আপনি বট ব্যবহার করতে পারবেন না। আপনি ব্লক করা হয়েছেন।**")
        return
    
    is_verified, not_joined = is_user_verified(user_id)
    if not is_verified:
        send_force_sub_message(chat_id, not_joined)
        return

    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ **Bot is temporarily locked by Admin.**")
        return
    if user_id not in active_users:
        add_active_user(user_id)
    if user_id == OWNER_ID:
        user_status = "👑 **Owner**"
    elif user_id in admin_ids:
        user_status = "🛡️ **Admin**"
    elif user_id in user_subscriptions and user_subscriptions[user_id]["expiry"] > datetime.now():
        sub = user_subscriptions[user_id]
        days_left = (sub["expiry"] - datetime.now()).days
        user_status = f"💎 **{sub.get('plan_name', 'Premium')} Active** ({days_left} Days left)"
    else:
        user_status = "🆓 **No Active Plan**"
    welcome_msg = (f"✨ **𝗪𝗲𝗹𝗰𝗼𝗺𝗲, {user_name}!** ✨\n\n"
                   f"🆔 **𝗬𝗼𝘂𝗿 𝗜𝗗:** `{user_id}`\n"
                   f"🔰 **𝗦𝘁𝗮𝘁𝘂𝘀:** {user_status}\n"
                   f"📁 **𝗨𝗽𝗹𝗼𝗮𝗱𝗲𝗱 𝗙𝗶𝗹𝗲𝘀:** `{get_user_file_count(user_id)}` / `{get_user_file_limit(user_id)}`\n\n"
                   f"💡 **𝗛𝗼𝘀𝘁 & 𝗥𝘂𝗻 𝘆𝗼𝘂𝗿 𝗣𝘆𝘁𝗵𝗼𝗻 (.𝗽𝘆) & 𝗝𝗦 (.𝗷𝘀) 𝗯𝗼𝘁𝘀 𝟮𝟰/𝟳.**\n"
                   f"👇 *Select an option from the menu below:* ")
    bot.send_message(chat_id, welcome_msg, reply_markup=create_reply_keyboard_main_menu(user_id), parse_mode="Markdown")

def _logic_view_plans(message_or_call):
    chat_id = message_or_call.chat.id if isinstance(message_or_call, telebot.types.Message) else message_or_call.message.chat.id
    plans = get_all_plans()
    if not plans:
        bot.send_message(chat_id, "ℹ️ **বর্তমানে কোনো প্ল্যান উপলব্ধ নেই।**", parse_mode="Markdown")
        return
    bot.send_message(chat_id, "💳 **𝗔𝘃𝗮𝗶𝗹𝗮𝗯𝗹𝗲 𝗛𝗼𝘀𝘁𝗶𝗻𝗴 𝗣𝗹𝗮𝗻𝘀:**", parse_mode="Markdown")
    for plan in plans:
        plan_id, name, limit, price, duration, _ = plan
        usdt_price, formatted_price = parse_price_to_usdt(price)
        card_text = (f"📦 **𝗣𝗹𝗮𝗻:** `{name}`\n"
                     f"━━━━━━━━━━━━━━━━━━━\n"
                     f"📁 **File Limit:** `{limit} Files`\n"
                     f"⏱️ **Duration:** `{duration} Days`\n"
                     f"💰 **Price:** `{formatted_price}`\n"
                     f"👉 **Binance Pay-তে পেমেন্ট করতে হবে:** `{usdt_price} USDT`\n"
                     f"━━━━━━━━━━━━━━━━━━━")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"🛒 Buy {name} ({usdt_price} USDT)", callback_data=f"buy_binance_{plan_id}"))
        bot.send_message(chat_id, card_text, reply_markup=markup, parse_mode="Markdown")

def _logic_upload_file(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if is_user_blocked(user_id) and user_id not in admin_ids:
        bot.reply_to(message, "🚫 **আপনি ব্লক করা হয়েছেন। আপনি ফাইল আপলোড করতে পারবেন না।**")
        return
    
    is_verified, not_joined = is_user_verified(user_id)
    if not is_verified:
        send_force_sub_message(chat_id, not_joined)
        return
    
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ **Bot is locked by Admin.**")
        return
    has_active_plan = False
    plan_name = "None"
    if user_id in admin_ids or user_id == OWNER_ID:
        has_active_plan = True
        plan_name = "Admin / Owner Unlimited"
    elif user_id in user_subscriptions:
        sub = user_subscriptions[user_id]
        if sub["expiry"] > datetime.now():
            has_active_plan = True
            plan_name = sub.get("plan_name", "Premium Plan")
    if not has_active_plan:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💳 View Plans & Buy", callback_data="view_plans_cb"))
        bot.reply_to(message, "❌ **আপনার কোন এক্টিভ প্ল্যান নেই!**\n\nফাইল আপলোড করতে হলে প্রথমে একটি প্ল্যান সাবস্ক্রাইব করতে হবে। নিচের বাটনে ক্লিক করে আমাদের প্ল্যানগুলো দেখুন এবং আপনার পছন্দমতো প্ল্যান কিনুন।", reply_markup=markup, parse_mode="Markdown")
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(f"✅ Continue with {plan_name}", callback_data="confirm_plan_upload"))
    bot.reply_to(message, f"🔰 **𝗔𝗰𝘁𝗶𝘃𝗲 𝗣𝗹𝗮𝗻 𝗗𝗲𝘁𝗲𝗰𝘁𝗲𝗱:** `{plan_name}`\n\nফাইল আপলোড চালু করতে নিচের বাটনে সিলেক্ট করুন:", reply_markup=markup, parse_mode="Markdown")

def _logic_check_files(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    is_verified, not_joined = is_user_verified(user_id)
    if not is_verified:
        send_force_sub_message(chat_id, not_joined)
        return
        
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.reply_to(message, "📂 **Your Uploaded Files:**\n\n*(No files uploaded yet)*", parse_mode="Markdown")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type in sorted(user_files_list):
        is_running = is_bot_running(user_id, file_name)
        status_icon = "🟢 Running" if is_running else "🔴 Stopped"
        btn_text = f"📄 {file_name} ({file_type}) - {status_icon}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"file_{user_id}_{file_name}"))
    bot.reply_to(message, "📁 **𝗠𝗮𝗻𝗮𝗴𝗲 𝗬𝗼𝘂𝗿 𝗙𝗶𝗹𝗲𝘀:**", reply_markup=markup, parse_mode="Markdown")

# --- Document Upload ---
@bot.message_handler(content_types=["document"])
def handle_file_upload_doc(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    doc = message.document
    
    if is_user_blocked(user_id) and user_id not in admin_ids:
        bot.reply_to(message, "🚫 **আপনি ব্লক করা হয়েছেন। আপনি ফাইল আপলোড করতে পারবেন না।**")
        return

    is_verified, not_joined = is_user_verified(user_id)
    if not is_verified:
        send_force_sub_message(chat_id, not_joined)
        return
    
    if user_id not in admin_ids and user_id != OWNER_ID:
        if user_id not in user_subscriptions or user_subscriptions[user_id]["expiry"] <= datetime.now():
            bot.reply_to(message, "❌ **আপনার কোন এক্টিভ প্ল্যান নেই! ফাইল আপলোড করতে প্ল্যান ক্রয় করুন।**", parse_mode="Markdown")
            return
    file_name = doc.file_name
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in [".py", ".js", ".zip"]:
        bot.reply_to(message, "⚠️ **Only `.py`, `.js`, and `.zip` files are supported!**", parse_mode="Markdown")
        return
    try:
        download_wait_msg = bot.reply_to(message, f"⏳ **Downloading `{file_name}`...**", parse_mode="Markdown")
        file_info_tg_doc = bot.get_file(doc.file_id)
        downloaded_file_content = bot.download_file(file_info_tg_doc.file_path)
        
        user_folder = get_user_folder(user_id)
        file_path = os.path.join(user_folder, file_name)
        with open(file_path, "wb") as f:
            f.write(downloaded_file_content)
        bot.edit_message_text(f"✅ **File `{file_name}` uploaded successfully!**", chat_id, download_wait_msg.message_id, parse_mode="Markdown")
        if file_ext == ".js":
            save_user_file(user_id, file_name, "js")
            threading.Thread(target=run_js_script, args=(file_path, user_id, user_folder, file_name, message)).start()
        elif file_ext == ".py":
            save_user_file(user_id, file_name, "py")
            threading.Thread(target=run_script, args=(file_path, user_id, user_folder, file_name, message)).start()
    except Exception as e:
        bot.reply_to(message, f"❌ **Error:** {str(e)}")

# --- Callback Routing ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data

    if data == "view_plans_cb":
        bot.answer_callback_query(call.id)
        _logic_view_plans(call)

    elif data == "confirm_plan_upload":
        bot.answer_callback_query(call.id, "✅ Plan Verified!")
        bot.send_message(call.message.chat.id, "🚀 **এখন আপনার Python (.py), JS (.js) অথবা ZIP (.zip) ফাইল মেসেজে পাঠান।**", parse_mode="Markdown")

    # --- Channel Add Callback ---
    elif data == "add_channel" and user_id in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            call.message.chat.id,
            "📢 **চ্যানেল ইউজারনেম লিখুন:**\n\n"
            "ফরম্যাট: `@channelusername`\n"
            "যেমন: `@MR_OBITO_OWNER`\n\n"
            "⚠️ বটকে ওই চ্যানেলে অ্যাডমিন করতে হবে!",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, process_add_channel)

    # --- Channel Remove Callback ---
    elif data == "remove_channel" and user_id in admin_ids:
        bot.answer_callback_query(call.id)
        channels = get_required_channels()
        if not channels:
            bot.send_message(call.message.chat.id, "ℹ️ কোনো চ্যানেল সেট করা নেই।")
            return
        markup = types.InlineKeyboardMarkup()
        for username, cid in channels:
            markup.add(types.InlineKeyboardButton(f"❌ {username}", callback_data=f"del_channel_{username}"))
        bot.send_message(call.message.chat.id, "🗑️ **ডিলিট করার জন্য চ্যানেল সিলেক্ট করুন:**", reply_markup=markup)

    # --- Channel Delete Callback ---
    elif data.startswith("del_channel_"):
        username = data.replace("del_channel_", "")
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, "❌ You are not admin!", show_alert=True)
            return
        remove_required_channel(username)
        bot.answer_callback_query(call.id, f"✅ {username} removed!")
        bot.send_message(call.message.chat.id, f"✅ চ্যানেল `{username}` রিমুভ করা হয়েছে।")

    # --- Check Verify Callback ---
    elif data == "check_verify":
        user_id = call.from_user.id
        chat_id = call.message.chat.id
        user_name = call.from_user.first_name
        is_verified, not_joined = is_user_verified(user_id)
        
        if is_verified:
            bot.answer_callback_query(call.id, "✅ ধন্যবাদ! আপনি জয়েন করেছেন।")
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except:
                pass
            
            if user_id not in active_users:
                add_active_user(user_id)
            if user_id == OWNER_ID:
                user_status = "👑 **Owner**"
            elif user_id in admin_ids:
                user_status = "🛡️ **Admin**"
            elif user_id in user_subscriptions and user_subscriptions[user_id]["expiry"] > datetime.now():
                sub = user_subscriptions[user_id]
                days_left = (sub["expiry"] - datetime.now()).days
                user_status = f"💎 **{sub.get('plan_name', 'Premium')} Active** ({days_left} Days left)"
            else:
                user_status = "🆓 **No Active Plan**"
                
            welcome_msg = (f"✨ **𝗪𝗲𝗹𝗰𝗼𝗺𝗲, {user_name}!** ✨\n\n"
                           f"🆔 **𝗬𝗼𝘂𝗿 𝗜𝗗:** `{user_id}`\n"
                           f"🔰 **𝗦𝘁𝗮𝘁𝘂𝘀:** {user_status}\n"
                           f"📁 **𝗨𝗽𝗹𝗼𝗮𝗱𝗲𝗱 𝗙𝗶𝗹𝗲𝘀:** `{get_user_file_count(user_id)}` / `{get_user_file_limit(user_id)}`\n\n"
                           f"💡 **𝗛𝗼𝘀𝘁 & 𝗥𝘂𝗻 𝘆𝗼𝘂𝗿 𝗣𝘆𝘁𝗵𝗼𝗻 (.𝗽𝘆) & 𝗝𝗦 (.𝗷𝘀) 𝗯𝗼𝘁𝘀 𝟮𝟰/𝟳.**\n"
                           f"👇 *Select an option from the menu below:* ")
            bot.send_message(chat_id, welcome_msg, reply_markup=create_reply_keyboard_main_menu(user_id), parse_mode="Markdown")
        else:
            bot.answer_callback_query(call.id, "❌ আপনি এখনো সবগুলো চ্যানেলে জয়েন করেননি!", show_alert=True)
            msg = "⚠️ **আপনি এখনো নিচের চ্যানেলগুলো জয়েন করেননি:**\n\n"
            for ch in not_joined:
                msg += f"• {ch}\n"
            msg += "\n✅ জয়েন করে **'🔄 চেক করুন'** বাটনে ক্লিক করুন।"
            markup = types.InlineKeyboardMarkup()
            for ch in not_joined:
                clean_user = ch.replace('@', '')
                markup.add(types.InlineKeyboardButton(f"📢 Join {ch}", url=f"https://t.me/{clean_user}"))
            markup.add(types.InlineKeyboardButton("🔄 চেক করুন", callback_data="check_verify"))
            try:
                bot.edit_message_text(msg, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            except:
                pass

    # --- All Users Callback ---
    elif data == "all_users" and user_id in admin_ids:
        bot.answer_callback_query(call.id)
        users = get_all_users()
        if not users:
            bot.send_message(call.message.chat.id, "ℹ️ কোনো ইউজার পাওয়া যায়নি।")
            return
        
        total = len(users)
        msg = f"📋 **ইউজার লিস্ট** (মোট: {total})\n\n"
        
        page = 0
        per_page = 10
        start = page * per_page
        end = min(start + per_page, total)
        
        for uid in users[start:end]:
            details = get_user_details(uid)
            status = "👑 Owner" if details["is_owner"] else "🛡️ Admin" if details["is_admin"] else "🚫 Blocked" if details["is_blocked"] else "📦 Subscribed" if details["subscription"] else "🆓 Free"
            msg += f"• `{uid}` → {status} | Files: {details['file_count']}\n"
        
        if total > per_page:
            msg += f"\n📌 *পৃষ্ঠা {page+1}/{((total-1)//per_page)+1}*"
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("⏪ Prev", callback_data=f"users_page_{page-1}"),
                types.InlineKeyboardButton(f"{page+1}/{(total-1)//per_page+1}", callback_data="ignore"),
                types.InlineKeyboardButton("Next ⏩", callback_data=f"users_page_{page+1}")
            )
            bot.send_message(call.message.chat.id, msg, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.send_message(call.message.chat.id, msg, parse_mode="Markdown")

    # --- Users Pagination ---
    elif data.startswith("users_page_") and user_id in admin_ids:
        page = int(data.split("_")[2])
        users = get_all_users()
        total = len(users)
        per_page = 10
        start = page * per_page
        
        if start >= total or page < 0:
            bot.answer_callback_query(call.id, "এই পৃষ্ঠায় কোনো ডেটা নেই।")
            return
        
        end = min(start + per_page, total)
        msg = f"📋 **ইউজার লিস্ট** (মোট: {total})\n\n"
        
        for uid in users[start:end]:
            details = get_user_details(uid)
            status = "👑 Owner" if details["is_owner"] else "🛡️ Admin" if details["is_admin"] else "🚫 Blocked" if details["is_blocked"] else "📦 Subscribed" if details["subscription"] else "🆓 Free"
            msg += f"• `{uid}` → {status} | Files: {details['file_count']}\n"
        
        total_pages = (total - 1) // per_page + 1
        msg += f"\n📌 *পৃষ্ঠা {page+1}/{total_pages}*"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("⏪ Prev", callback_data=f"users_page_{page-1}") if page > 0 else types.InlineKeyboardButton("⏪ Prev", callback_data="ignore"),
            types.InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="ignore"),
            types.InlineKeyboardButton("Next ⏩", callback_data=f"users_page_{page+1}") if page < total_pages - 1 else types.InlineKeyboardButton("Next ⏩", callback_data="ignore")
        )
        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # --- Find User Callback ---
    elif data == "find_user" and user_id in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            call.message.chat.id,
            "🔍 **ইউজার আইডি লিখুন:**\n\nযেমন: `123456789`",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, process_find_user)

    # --- Block User Callback ---
    elif data == "block_user" and user_id in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            call.message.chat.id,
            "🚫 **ব্লক করতে ইউজার আইডি লিখুন:**\n\nযেমন: `123456789`",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, process_block_user)

    # --- Unblock User Callback ---
    elif data == "unblock_user" and user_id in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            call.message.chat.id,
            "🔓 **আনব্লক করতে ইউজার আইডি লিখুন:**\n\nযেমন: `123456789`",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, process_unblock_user)

    # --- Interactive Module Installer ---
    elif data.startswith("instmod_"):
        _, owner_id, mod_name, fname = data.split("_", 3)
        if user_id != int(owner_id) and user_id not in admin_ids:
            bot.answer_callback_query(call.id, "❌ আপনি অন্য ইউজারের ফাইল কাস্টমাইজ করতে পারবেন না!", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        pkg_name = TELEGRAM_MODULES.get(mod_name.lower(), mod_name)
        ext = os.path.splitext(fname)[1].lower()
        status_msg = bot.send_message(call.message.chat.id, f"⏳ **`{pkg_name}` মডিউলটি ইনস্টল করা হচ্ছে...**", parse_mode="Markdown")
        def do_pip_install():
            if ext == ".js":
                cmd = ["npm", "install", pkg_name]
            else:
                cmd = [sys.executable, "-m", "pip", "install", pkg_name]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                bot.edit_message_text(f"✅ **`{pkg_name}` মডিউলটি সফলভাবে ইনস্টল হয়েছে!**\n🚀 ফাইলটি পুনরায় চালু করা হচ্ছে...", call.message.chat.id, status_msg.message_id, parse_mode="Markdown")
                time.sleep(1)
                ufolder = get_user_folder(int(owner_id))
                fpath = os.path.join(ufolder, fname)
                if ext == ".js":
                    run_js_script(fpath, int(owner_id), ufolder, fname, call.message)
                else:
                    run_script(fpath, int(owner_id), ufolder, fname, call.message)
            else:
                bot.edit_message_text(f"❌ **ইনস্টলেশন ব্যর্থ হয়েছে!**\n\n```\n{res.stderr[:300]}\n```", call.message.chat.id, status_msg.message_id, parse_mode="Markdown")
        threading.Thread(target=do_pip_install).start()

    # --- Error Log Viewer ---
    elif data.startswith("viewlog_"):
        _, owner_id, fname = data.split("_", 2)
        ufolder = get_user_folder(int(owner_id))
        log_fpath = os.path.join(ufolder, f"{os.path.splitext(fname)[0]}.log")
        if os.path.exists(log_fpath):
            with open(log_fpath, "r", encoding="utf-8", errors="ignore") as f:
                logs = f.read()[-2000:]
            bot.send_message(call.message.chat.id, f"📜 **Error Log for `{fname}`:**\n\n```\n{logs if logs else 'No logs recorded.'}\n```", parse_mode="Markdown")
        else:
            bot.answer_callback_query(call.id, "No log file found!", show_alert=True)

    # --- Binance Pay Handlers ---
    elif data.startswith("buy_binance_"):
        plan_id = int(data.split("_")[2])
        plan = get_plan_by_id(plan_id)
        if not plan:
            bot.answer_callback_query(call.id, "Plan not found!")
            return
        bot.answer_callback_query(call.id)
        _, name, limit, price, duration, _ = plan
        usdt_price, formatted_price = parse_price_to_usdt(price)
        already_paid = get_pending_payment(user_id, plan_id)
        due_amount = max(0.0, round(usdt_price - already_paid, 2))
        pay_msg = (f"💛 **Binance Pay Auto Payment Process**\n\n"
                   f"📌 **Selected Plan:** `{name}`\n"
                   f"💰 **Total Price:** `{usdt_price} USDT` ({formatted_price})\n")
        if already_paid > 0:
            pay_msg += (f"✅ **আপনার পূর্বে জমা আছে:** `{already_paid} USDT`\n"
                        f"⚠️ **এখন অবশিষ্ট বাকি টাকা:** `{due_amount} USDT`\n\n")
        else:
            pay_msg += f"⏱️ **Duration:** `{duration} Days`\n\n"
        pay_msg += (f"👇 **পেমেন্ট করার নিয়ম:**\n"
                    f"1️⃣ Binance App ➔ **Pay** ➔ **Send** অপশনে যান।\n"
                    f"2️⃣ ঠিক **`{due_amount} USDT`** নিচের Binance Pay ID-তে পাঠান:\n"
                    f"🔸 **Binance Pay ID:** `{BINANCE_PAY_ID}`\n\n"
                    f"3️⃣ পেমেন্ট শেষ হলে প্রাপ্ত **Order ID / Transaction ID** টি নিয়ে নিচের বাটনে চাপ দিয়ে জমা দিন।")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔍 Order ID / TxID জমা দিন", callback_data=f"submit_txid_{plan_id}"))
        bot.send_message(call.message.chat.id, pay_msg, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("submit_txid_"):
        plan_id = int(data.split("_")[2])
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "📩 **আপনার Binance Pay এর Order ID / Transaction ID টি মেসেজে লিখুন:**", parse_mode="Markdown")
        bot.register_next_step_handler(msg, lambda m: process_binance_txid(m, plan_id))

    # --- Admin Callbacks ---
    elif data == "add_plan_init" and user_id in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "📝 **Enter Plan Details in format:**\n`Name | FileLimit | Price | DurationInDays | BuyLink`\n\n*Example (টাকায়):* `Basic | 5 | 500 BDT | 30 | https://t.me/shiyam744`\n*Example (ডলারে):* `VIP | 10 | 5 USDT | 30 | https://t.me/shiyam744`", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_add_plan)

    elif data == "manage_plans" and user_id in admin_ids:
        bot.answer_callback_query(call.id)
        plans = get_all_plans()
        if not plans:
            bot.send_message(call.message.chat.id, "No plans found.")
            return
        markup = types.InlineKeyboardMarkup()
        for p in plans:
            markup.add(types.InlineKeyboardButton(f"🗑️ Delete {p[1]}", callback_data=f"del_plan_{p[0]}"))
        bot.send_message(call.message.chat.id, "🗑️ **Select a Plan to Delete:**", reply_markup=markup)

    elif data.startswith("del_plan_") and user_id in admin_ids:
        pid = int(data.split("_")[2])
        delete_plan_db(pid)
        bot.answer_callback_query(call.id, "Plan Deleted!")
        bot.send_message(call.message.chat.id, "✅ Plan successfully deleted.")

    elif data == "add_subscription" and user_id in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "💎 **Enter User ID, Plan Name & Days:**\nFormat: `UserID PlanName Days`\n*Example:* `123456789 VIP 30`", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_add_subscription)

    elif data == "toggle_lock" and user_id in admin_ids:
        global bot_locked
        bot_locked = not bot_locked
        bot.answer_callback_query(call.id, f"Bot Locked: {bot_locked}")
        bot.send_message(call.message.chat.id, f"🔐 **Bot status changed to:** `{'Locked' if bot_locked else 'Unlocked'}`", parse_mode="Markdown")

    # --- File Management Callbacks ---
    elif data.startswith("file_"):
        _, owner_id, fname = data.split("_", 2)
        is_running = is_bot_running(int(owner_id), fname)
        markup = types.InlineKeyboardMarkup(row_width=2)
        if is_running:
            markup.add(types.InlineKeyboardButton("🛑 Stop", callback_data=f"stop_{owner_id}_{fname}"))
        else:
            markup.add(types.InlineKeyboardButton("▶️ Start", callback_data=f"start_{owner_id}_{fname}"))
        markup.add(types.InlineKeyboardButton("🗑️ Delete", callback_data=f"del_{owner_id}_{fname}"))
        bot.send_message(call.message.chat.id, f"📄 **File:** `{fname}`\n🚦 Status: `{'Running' if is_running else 'Stopped'}`", reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("stop_"):
        _, owner_id, fname = data.split("_", 2)
        skey = f"{owner_id}_{fname}"
        if skey in bot_scripts:
            kill_process_tree(bot_scripts[skey])
            del bot_scripts[skey]
        bot.answer_callback_query(call.id, "Stopped!")
        bot.send_message(call.message.chat.id, f"🛑 Script `{fname}` stopped.", parse_mode="Markdown")

    elif data.startswith("del_"):
        _, owner_id, fname = data.split("_", 2)
        skey = f"{owner_id}_{fname}"
        if skey in bot_scripts:
            kill_process_tree(bot_scripts[skey])
            del bot_scripts[skey]
        remove_user_file_db(int(owner_id), fname)
        ufolder = get_user_folder(int(owner_id))
        fpath = os.path.join(ufolder, fname)
        if os.path.exists(fpath):
            os.remove(fpath)
        bot.answer_callback_query(call.id, "Deleted!")
        bot.send_message(call.message.chat.id, f"🗑️ File `{fname}` deleted.", parse_mode="Markdown")

    # --- Stats Callback (Enhanced) ---
    elif data == "stats" and user_id in admin_ids:
        bot.answer_callback_query(call.id)
        stats = get_bot_stats()
        msg = (f"📊 **𝗕𝗼𝘁 𝗦𝘁𝗮𝘁𝗶𝘀𝘁𝗶𝗰𝘀**\n\n"
               f"👥 **মোট ইউজার:** `{stats['total_users']}`\n"
               f"📁 **মোট ফাইল:** `{stats['total_files']}`\n"
               f"💎 **সাবস্ক্রাইবড:** `{stats['total_subscribed']}`\n"
               f"🚫 **ব্লক করা:** `{stats['total_blocked']}`\n"
               f"🛡️ **অ্যাডমিন:** `{stats['total_admins']}`\n"
               f"🔐 **বট লক:** `{'✅ লকড' if bot_locked else '❌ আনলকড'}`")
        bot.send_message(call.message.chat.id, msg, parse_mode="Markdown")

# --- Step Handlers ---
def process_add_channel(message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        return
    channel_input = message.text.strip()
    if not channel_input.startswith('@'):
        channel_input = '@' + channel_input
    try:
        chat = bot.get_chat(channel_input)
        channel_id = chat.id
        bot_member = bot.get_chat_member(channel_id, bot.get_me().id)
        if bot_member.status not in ['administrator', 'creator']:
            bot.reply_to(message, f"❌ **বটকে চ্যানেলের অ্যাডমিন বানান!**\nচ্যানেল: {channel_input}\n\nবটকে অ্যাডমিন না করলে ভেরিফাই করা সম্ভব নয়।")
            return
        if add_required_channel(channel_input, channel_id, user_id):
            bot.reply_to(message, f"✅ **চ্যানেল যোগ করা হয়েছে!**\n\n📢 {channel_input}\n🆔 চ্যানেল আইডি: `{channel_id}`\n\nএখন থেকে ইউজারদের এই চ্যানেল জয়েন করতে হবে।", parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ চ্যানেল যোগ করতে সমস্যা হয়েছে।")
    except Exception as e:
        bot.reply_to(message, f"❌ **চ্যানেল খুঁজে পাওয়া যায়নি বা এরর হয়েছে!**\n\nError: `{str(e)}`", parse_mode="Markdown")

# --- Find User Handler ---
def process_find_user(message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        return
    
    try:
        target_id = int(message.text.strip())
        details = get_user_details(target_id)
        
        username = "N/A"
        try:
            chat = bot.get_chat(target_id)
            if chat.username:
                username = f"@{chat.username}"
            else:
                username = chat.first_name or "N/A"
        except:
            pass
        
        status = "👑 Owner" if details["is_owner"] else "🛡️ Admin" if details["is_admin"] else "🚫 Blocked" if details["is_blocked"] else "📦 Subscribed" if details["subscription"] else "🆓 Free"
        
        msg = (f"🔍 **ইউজার ডিটেইলস**\n\n"
               f"🆔 **আইডি:** `{target_id}`\n"
               f"👤 **নাম/ইউজারনেম:** {username}\n"
               f"📊 **স্ট্যাটাস:** {status}\n"
               f"📁 **ফাইল কাউন্ট:** `{details['file_count']}` / `{details['file_limit']}`\n"
               f"📌 **এক্টিভ:** `{'হ্যাঁ' if details['is_active'] else 'না'}`")
        
        if details["subscription"]:
            sub = details["subscription"]
            expiry_str = sub["expiry"].strftime("%Y-%m-%d %H:%M")
            days_left = (sub["expiry"] - datetime.now()).days
            msg += f"\n💎 **প্ল্যান:** `{sub['plan']}`\n📅 **শেষ হবে:** `{expiry_str}` ({days_left} দিন বাকি)"
        else:
            msg += f"\n💎 **প্ল্যান:** `নেই`"
        
        bot.reply_to(message, msg, parse_mode="Markdown")
    except ValueError:
        bot.reply_to(message, "❌ **সঠিক ইউজার আইডি দিন!** (শুধু সংখ্যা)")
    except Exception as e:
        bot.reply_to(message, f"❌ **ইউজার খুঁজে পাওয়া যায়নি!**\nError: {str(e)}")

# --- Block User Handler ---
def process_block_user(message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        return
    
    try:
        target_id = int(message.text.strip())
        
        if target_id == OWNER_ID:
            bot.reply_to(message, "❌ **ওনারকে ব্লক করা যাবে না!**")
            return
        if target_id in admin_ids:
            bot.reply_to(message, "❌ **অ্যাডমিনকে ব্লক করা যাবে না!**")
            return
        if is_user_blocked(target_id):
            bot.reply_to(message, f"ℹ️ **ইউজার `{target_id}` ইতিমধ্যেই ব্লক করা আছে।**")
            return
        
        if block_user(target_id, user_id):
            bot.reply_to(message, f"✅ **ইউজার `{target_id}` ব্লক করা হয়েছে!**\n\nএই ইউজার এখন বট ব্যবহার করতে পারবেনা।")
            try:
                bot.send_message(target_id, "🚫 **আপনি বট ব্যবহার থেকে ব্লক করা হয়েছেন।**")
            except:
                pass
        else:
            bot.reply_to(message, "❌ **ব্লক করতে সমস্যা হয়েছে!**")
    except ValueError:
        bot.reply_to(message, "❌ **সঠিক ইউজার আইডি দিন!** (শুধু সংখ্যা)")
    except Exception as e:
        bot.reply_to(message, f"❌ **Error:** {str(e)}")

# --- Unblock User Handler ---
def process_unblock_user(message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        return
    
    try:
        target_id = int(message.text.strip())
        
        if not is_user_blocked(target_id):
            bot.reply_to(message, f"ℹ️ **ইউজার `{target_id}` ব্লক করা নেই।**")
            return
        
        if unblock_user(target_id):
            bot.reply_to(message, f"✅ **ইউজার `{target_id}` আনব্লক করা হয়েছে!**\n\nএই ইউজার এখন বট ব্যবহার করতে পারবে।")
            try:
                bot.send_message(target_id, "✅ **আপনি আনব্লক করা হয়েছে। এখন বট ব্যবহার করতে পারবেন।**")
            except:
                pass
        else:
            bot.reply_to(message, "❌ **আনব্লক করতে সমস্যা হয়েছে!**")
    except ValueError:
        bot.reply_to(message, "❌ **সঠিক ইউজার আইডি দিন!** (শুধু সংখ্যা)")
    except Exception as e:
        bot.reply_to(message, f"❌ **Error:** {str(e)}")

def process_binance_txid(message, plan_id):
    pay_order_id = message.text.strip()
    user_id = message.from_user.id
    plan = get_plan_by_id(plan_id)
    if not plan:
        bot.reply_to(message, "❌ প্ল্যান পাওয়া যায়নি!")
        return
    plan_id, name, limit, price, duration, _ = plan
    usdt_price, formatted_price = parse_price_to_usdt(price)
    if is_txid_used(pay_order_id):
        bot.reply_to(message, "❌ **এই Order ID / Transaction ID টি ইতিপূর্বেই ব্যবহার করা হয়েছে!**\nনতুন পেমেন্ট ট্রানজেকশন আইডি দিন।", parse_mode="Markdown")
        return
    wait_msg = bot.reply_to(message, "⏳ **আপনার Binance Pay Order ID ভেরিফাই করা হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...**", parse_mode="Markdown")
    is_valid, paid_amount_new, amount_or_error = check_binance_payment(pay_order_id)
    if is_valid:
        add_used_txid(pay_order_id)
        already_paid = get_pending_payment(user_id, plan_id)
        total_paid = round(already_paid + paid_amount_new, 2)
        if total_paid < usdt_price:
            remaining = round(usdt_price - total_paid, 2)
            update_pending_payment(user_id, plan_id, total_paid)
            bot.edit_message_text(f"⚠️ **পেমেন্ট অসম্পূর্ণ (Partial Payment Received)!**\n\n"
                                  f"📌 **Selected Plan:** `{name}`\n"
                                  f"💰 **প্রয়োজনীয় মোট দাম:** `{usdt_price} USDT` ({formatted_price})\n"
                                  f"✅ **আপনার মোট জমা হয়েছে:** `{total_paid} USDT`\n"
                                  f"❌ **এখনও বাকি আছে:** `{remaining} USDT`\n\n"
                                  f"💡 অনুগ্রহ করে বাকি **`{remaining} USDT`** টাকা Binance Pay ID (`{BINANCE_PAY_ID}`)-তে পাঠি‌য়ে নতুন Order ID টি পুনরায় জমা দিন। বাকি পেমেন্ট সম্পন্ন হলেই আপনার সাবস্ক্রিপশনটি এক্টিভ হবে।",
                                  message.chat.id, wait_msg.message_id, parse_mode="Markdown")
            return
        clear_pending_payment(user_id, plan_id)
        expiry = datetime.now() + timedelta(days=duration)
        save_subscription(user_id, name, expiry)
        bot.edit_message_text(f"🎉 **পেমেন্ট সফলভাবে ভেরিফাই হয়েছে!**\n\n"
                              f"👤 **User ID:** `{user_id}`\n"
                              f"💎 **Plan:** `{name}`\n"
                              f"💰 **Total Amount Paid:** `{total_paid} USDT`\n"
                              f"📅 **Expiry:** `{expiry.strftime('%Y-%m-%d %H:%M')}`\n\n"
                              f"🚀 আপনার সাবস্ক্রিপশন চালু হয়েছে। এখন আপনি ফাইল আপলোড করতে পারবেন!",
                              message.chat.id, wait_msg.message_id, parse_mode="Markdown")
        bot.send_message(OWNER_ID, f"🔔 **New Subscription via Binance Pay!**\n👤 User: `{user_id}`\n💎 Plan: `{name}`\n📑 Order ID: `{pay_order_id}`\n💰 Amount: `{total_paid} USDT`", parse_mode="Markdown")
    else:
        bot.edit_message_text(f"❌ **পেমেন্ট ভেরিফাই করা সম্ভব হয়নি!**\n\n⚠️ **কারণ:** `{amount_or_error}`\n\nঅনুগ্রহ করে সঠিক Order ID দিয়ে আবার চেষ্টা করুন অথবা এডমিনের সাথে যোগাযোগ করুন।", message.chat.id, wait_msg.message_id, parse_mode="Markdown")

def process_add_plan(message):
    try:
        parts = [p.strip() for p in message.text.split("|")]
        name, limit, price, duration, buy_link = parts[0], int(parts[1]), parts[2], int(parts[3]), parts[4]
        add_plan_db(name, limit, price, duration, buy_link)
        bot.reply_to(message, f"✅ **Plan `{name}` added successfully!**", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Invalid Format! Error: {e}")

def process_add_subscription(message):
    try:
        parts = message.text.split()
        sub_uid, pname, days = int(parts[0]), parts[1], int(parts[2])
        exp = datetime.now() + timedelta(days=days)
        save_subscription(sub_uid, pname, exp)
        bot.reply_to(message, f"✅ **Subscription active for User `{sub_uid}` under Plan `{pname}` for {days} days!**", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# --- Text Handler Mapping ---
BUTTON_MAPPING = {
    "✨ 𝗨𝗽𝗱𝗮𝘁𝗲𝘀 𝗖𝗵𝗮𝗻𝗻𝗲𝗹 ✨": lambda m: bot.reply_to(m, f"📢 **Join channel:** {UPDATE_CHANNEL}"),
    "🚀 𝗨𝗽𝗹𝗼𝗮𝗱 𝗙𝗶𝗹𝗲": _logic_upload_file,
    "🚀 𝗨𝗽𝗹𝗼𝗮d 𝗙𝗶𝗹𝗲": _logic_upload_file,
    "📁 𝗠𝗮𝗻𝗮𝗴𝗲 𝗙𝗶𝗹𝗲𝘀": _logic_check_files,
    "💳 𝗩𝗶𝗲𝘄 𝗣𝗹𝗮𝗻𝘀": _logic_view_plans,
    "⚡ 𝗦𝗽𝗲𝗲𝗱 & 𝗣𝗶𝗻𝗴": lambda m: bot.reply_to(m, "⚡ **Bot Latency:** `12 ms` (Server Active)"),
    "📊 𝗕𝗼𝘁 𝗦𝘁𝗮𝘁𝘀": lambda m: bot.reply_to(m, f"📊 **Active Users:** `{len(active_users)}`"),
    "💻 𝗧𝗲𝗿𝗺𝗶𝗻𝗮ল 𝗖𝗺𝗱": lambda m: bot.reply_to(m, "💻 Terminal ready."),
    "👑 𝗖𝗼𝗻𝘁𝗮𝗰𝘁 𝗢𝘄𝗻𝗲𝗿": lambda m: bot.reply_to(m, f"👑 **Owner:** {YOUR_USERNAME}"),
    "🛡️ 𝗔𝗱𝗺𝗶𝗻 𝗣𝗮𝗻𝗲𝗹": lambda m: bot.reply_to(m, "🛡️ **𝗔𝗱𝗺𝗶𝗻 𝗖𝗼𝗻𝘁𝗿𝗼𝗹 𝗣𝗮𝗻𝗲𝗹:**", reply_markup=create_admin_panel_inline(), parse_mode="Markdown"),
}

@bot.message_handler(func=lambda m: m.text in BUTTON_MAPPING)
def handle_main_buttons(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if message.text != "🛡️ 𝗔𝗱𝗺𝗶𝗻 𝗣𝗮𝗻𝗲𝗹" or user_id not in admin_ids:
        is_verified, not_joined = is_user_verified(user_id)
        if not is_verified:
            send_force_sub_message(chat_id, not_joined)
            return
            
    BUTTON_MAPPING[message.text](message)

@bot.message_handler(commands=["start"])
def start_cmd(message):
    _logic_send_welcome(message)

# --- Cleanup & Start ---
def cleanup():
    for key in list(bot_scripts.keys()):
        kill_process_tree(bot_scripts[key])

atexit.register(cleanup)

if __name__ == "__main__":
    logger.info("🤖 Starting Bot with Permanent Supabase Cloud Storage...")
    keep_alive()
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
