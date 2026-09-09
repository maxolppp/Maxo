import os
import sqlite3
import logging
import random
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.error import TelegramError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= تنظیمات =================
# برای تست روی ترموکس: توکن و آیدی عددیت رو مستقیم همینجا بنویس.
# روی ریلوی بهتره از Environment Variables استفاده کنی (این‌ها رو خالی بذار، env بهشون اولویت داره).
BOT_TOKEN_HARDCODED = ""     # روی Railway خالی بذار و از Environment Variables استفاده کن
ADMIN_IDS_HARDCODED = ""     # روی Railway خالی بذار و از Environment Variables استفاده کن

BOT_TOKEN = os.environ.get("BOT_TOKEN", BOT_TOKEN_HARDCODED)
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", ADMIN_IDS_HARDCODED).split(",") if x.strip().isdigit()]
CARD_NUMBER_DEFAULT = os.environ.get("CARD_NUMBER", "6219861351165898")
DB_PATH = os.environ.get("DB_PATH", "bot.db")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN تنظیم نشده! یا در Environment Variables وارد کن یا مقدار BOT_TOKEN_HARDCODED بالای همین فایل رو پر کن.")
if not ADMIN_IDS:
    raise RuntimeError("❌ ADMIN_IDS تنظیم نشده! یا در Environment Variables وارد کن یا مقدار ADMIN_IDS_HARDCODED بالای همین فایل رو پر کن.")

# اگه DB_PATH داخل یه پوشه باشه (مثلا /data/bot.db روی ولیوم ریلوی) پوشه رو بساز
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)
# ================================================================

BACK_BTN = "بازگشت 🔙"

PLAN_CATEGORIES = ["حجمی", "نامحدود"]
CATEGORY_LABELS = {
    "حجمی": "حجمی 📊",
    "نامحدود": "نامحدود ♾",
}

# ============================================================
#                        دیتابیس
# ============================================================

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        wallet INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0,
        ban_reason TEXT,
        joined_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS admins(
        user_id INTEGER PRIMARY KEY
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS plans(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, price INTEGER, duration_days INTEGER,
        is_test INTEGER DEFAULT 0,
        category TEXT DEFAULT 'حجمی'
    )""")
    try:
        c.execute("ALTER TABLE plans ADD COLUMN category TEXT DEFAULT 'حجمی'")
    except sqlite3.OperationalError:
        pass  # ستون از قبل وجود داره (دیتابیس قدیمی)
    c.execute("""CREATE TABLE IF NOT EXISTS plan_configs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER, content_type TEXT,
        text_content TEXT, photo_file_id TEXT, caption TEXT,
        used INTEGER DEFAULT 0, used_by INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS test_configs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_type TEXT, text_content TEXT, photo_file_id TEXT, caption TEXT,
        used INTEGER DEFAULT 0, used_by INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS test_usage(
        user_id INTEGER PRIMARY KEY, used_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, plan_id INTEGER, plan_title TEXT,
        price INTEGER, pay_method TEXT, status TEXT,
        config_id INTEGER, created_at TEXT, expires_at TEXT, is_renew INTEGER DEFAULT 0,
        hidden INTEGER DEFAULT 0
    )""")
    try:
        c.execute("ALTER TABLE orders ADD COLUMN hidden INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # ستون از قبل وجود داره (دیتابیس قدیمی)
    c.execute("""CREATE TABLE IF NOT EXISTS wallet_charges(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, amount INTEGER, status TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, user_msg TEXT, status TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS discount_codes(
        code TEXT PRIMARY KEY, amount INTEGER, percent INTEGER,
        expires_at TEXT, max_uses INTEGER, used_count INTEGER DEFAULT 0, active INTEGER DEFAULT 1
    )""")
    try:
        c.execute("ALTER TABLE discount_codes ADD COLUMN percent INTEGER")
    except sqlite3.OperationalError:
        pass  # ستون از قبل وجود داره (دیتابیس قدیمی)
    c.execute("""CREATE TABLE IF NOT EXISTS discount_usage(
        code TEXT, user_id INTEGER, PRIMARY KEY(code, user_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS required_channels(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_username TEXT, channel_title TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS owners(
        user_id INTEGER PRIMARY KEY
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS bot_settings(
        id INTEGER PRIMARY KEY CHECK (id=1),
        card_number TEXT,
        card_holder TEXT,
        maintenance_mode INTEGER DEFAULT 0
    )""")
    c.execute("INSERT OR IGNORE INTO bot_settings(id, card_number, card_holder, maintenance_mode) VALUES (1,?,?,0)",
              (CARD_NUMBER_DEFAULT, ""))
    c.execute("""CREATE TABLE IF NOT EXISTS wheel_settings(
        id INTEGER PRIMARY KEY CHECK (id=1),
        enabled INTEGER DEFAULT 0,
        cooldown_hours INTEGER DEFAULT 24
    )""")
    c.execute("INSERT OR IGNORE INTO wheel_settings(id, enabled, cooldown_hours) VALUES (1,0,24)")
    c.execute("""CREATE TABLE IF NOT EXISTS wheel_prizes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prize_type TEXT, weight INTEGER,
        discount_percent INTEGER, discount_hours INTEGER,
        config_gb REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS wheel_prize_configs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prize_id INTEGER, content_type TEXT,
        text_content TEXT, photo_file_id TEXT, caption TEXT,
        used INTEGER DEFAULT 0, used_by INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS wheel_spins(
        user_id INTEGER PRIMARY KEY, last_spin_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS admin_permissions(
        user_id INTEGER, permission TEXT,
        PRIMARY KEY (user_id, permission)
    )""")
    for aid in ADMIN_IDS:
        c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (aid,))
        c.execute("INSERT OR IGNORE INTO owners(user_id) VALUES (?)", (aid,))
    conn.commit()
    conn.close()


# ============================================================
#                     توابع کمکی دیتابیس
# ============================================================

def is_admin(user_id):
    conn = get_conn()
    r = conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return r is not None


def is_owner(user_id):
    conn = get_conn()
    r = conn.execute("SELECT 1 FROM owners WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return r is not None


def ensure_user(user):
    conn = get_conn()
    r = conn.execute("SELECT 1 FROM users WHERE user_id=?", (user.id,)).fetchone()
    if not r:
        conn.execute("INSERT INTO users(user_id, username, wallet, banned, joined_at) VALUES (?,?,0,0,?)",
                     (user.id, user.username or "", datetime.now().isoformat()))
        conn.commit()
    else:
        conn.execute("UPDATE users SET username=? WHERE user_id=?", (user.username or "", user.id))
        conn.commit()
    conn.close()


def is_banned(user_id):
    conn = get_conn()
    r = conn.execute("SELECT banned FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return r is not None and r["banned"] == 1


def get_ban_reason(user_id):
    conn = get_conn()
    r = conn.execute("SELECT ban_reason FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return r["ban_reason"] if r and r["ban_reason"] else "دلیلی ثبت نشده است"


def get_wallet(user_id):
    conn = get_conn()
    r = conn.execute("SELECT wallet FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return r["wallet"] if r else 0


def change_wallet(user_id, delta):
    conn = get_conn()
    conn.execute("UPDATE users SET wallet = wallet + ? WHERE user_id=?", (delta, user_id))
    conn.commit()
    conn.close()


def plan_capacity(plan_id):
    conn = get_conn()
    r = conn.execute("SELECT COUNT(*) as c FROM plan_configs WHERE plan_id=? AND used=0", (plan_id,)).fetchone()
    conn.close()
    return r["c"]


def get_all_admin_ids():
    conn = get_conn()
    rows = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    return [r['user_id'] for r in rows]


async def notify_all_admins_photo(context, photo_file_id, caption, reply_markup=None):
    """عکس رو برای همه ادمین‌ها می‌فرسته؛ اگه یکیشون چت نداشته باشه بقیه رو متوقف نمی‌کنه."""
    for admin_id in get_all_admin_ids():
        try:
            await context.bot.send_photo(admin_id, photo_file_id, caption=caption, reply_markup=reply_markup)
        except TelegramError as e:
            logger.warning(f"ارسال رسید به ادمین {admin_id} ناموفق بود: {e}")


async def notify_all_admins_text(context, text, reply_markup=None):
    """پیام متنی رو برای همه ادمین‌ها می‌فرسته؛ اگه یکیشون چت نداشته باشه بقیه رو متوقف نمی‌کنه."""
    for admin_id in get_all_admin_ids():
        try:
            await context.bot.send_message(admin_id, text, reply_markup=reply_markup)
        except TelegramError as e:
            logger.warning(f"ارسال پیام به ادمین {admin_id} ناموفق بود: {e}")


def get_plan_duration(plan_id):
    conn = get_conn()
    r = conn.execute("SELECT duration_days FROM plans WHERE id=?", (plan_id,)).fetchone()
    conn.close()
    return r['duration_days'] if r else 30


# ---------- تنظیمات ربات (شماره کارت / حالت خاموش) ----------
def get_bot_settings():
    conn = get_conn()
    row = conn.execute("SELECT * FROM bot_settings WHERE id=1").fetchone()
    conn.close()
    return row


def get_card_info():
    s = get_bot_settings()
    return (s['card_number'], s['card_holder']) if s else (CARD_NUMBER_DEFAULT, "")


def set_card_info(number, holder):
    conn = get_conn()
    conn.execute("UPDATE bot_settings SET card_number=?, card_holder=? WHERE id=1", (number, holder))
    conn.commit()
    conn.close()


def is_maintenance_on():
    s = get_bot_settings()
    return bool(s['maintenance_mode']) if s else False


def set_maintenance(flag):
    conn = get_conn()
    conn.execute("UPDATE bot_settings SET maintenance_mode=? WHERE id=1", (int(flag),))
    conn.commit()
    conn.close()


# ---------- دسترسی‌های ادمین‌ها ----------
PERMISSIONS = {
    "plans": "🗂 مدیریت پلن‌ها",
    "test": "🎁 مدیریت کانفیگ تست",
    "users": "👥 کاربران، پیام مستقیم و مسدودسازی",
    "wallet": "💳 شارژ/کسر کیف پول کاربران",
    "broadcast": "پیام همگانی 📢",
    "discount": "🏷 کد تخفیف",
    "channels": "📢 کانال‌های اجباری",
    "admins": "👤 افزودن ادمین جدید",
    "card": "💳 تغییر شماره کارت",
    "maintenance": "🔧 خاموش/روشن کردن ربات",
}


def get_admin_permissions(user_id):
    if is_owner(user_id):
        return set(PERMISSIONS.keys())
    conn = get_conn()
    rows = conn.execute("SELECT permission FROM admin_permissions WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    return {r['permission'] for r in rows}


def has_permission(user_id, perm):
    if is_owner(user_id):
        return True
    return perm in get_admin_permissions(user_id)


def set_admin_permissions(user_id, perms):
    conn = get_conn()
    conn.execute("DELETE FROM admin_permissions WHERE user_id=?", (user_id,))
    for p in perms:
        if p in PERMISSIONS:
            conn.execute("INSERT OR IGNORE INTO admin_permissions(user_id, permission) VALUES (?,?)", (user_id, p))
    conn.commit()
    conn.close()


# ---------- گردونه شانس ----------
WHEEL_PRIZE_LABELS = {
    "discount": "🏷 کد تخفیف",
    "config": "🎁 کانفیگ رایگان",
    "respin": "🔄 یک دور دیگر",
    "none": "پوچ ❌",
}


def get_wheel_settings():
    conn = get_conn()
    row = conn.execute("SELECT * FROM wheel_settings WHERE id=1").fetchone()
    conn.close()
    return row


def is_wheel_enabled():
    s = get_wheel_settings()
    return bool(s['enabled']) if s else False


def set_wheel_enabled(flag):
    conn = get_conn()
    conn.execute("UPDATE wheel_settings SET enabled=? WHERE id=1", (int(flag),))
    conn.commit()
    conn.close()


def set_wheel_cooldown(hours):
    conn = get_conn()
    conn.execute("UPDATE wheel_settings SET cooldown_hours=? WHERE id=1", (hours,))
    conn.commit()
    conn.close()


def get_wheel_prizes():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM wheel_prizes ORDER BY id").fetchall()
    conn.close()
    return rows


def add_wheel_prize(prize_type, weight, discount_percent=None, discount_hours=None, config_gb=None):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO wheel_prizes(prize_type, weight, discount_percent, discount_hours, config_gb) VALUES (?,?,?,?,?)",
        (prize_type, weight, discount_percent, discount_hours, config_gb)
    )
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid


def delete_wheel_prize(prize_id):
    conn = get_conn()
    conn.execute("DELETE FROM wheel_prizes WHERE id=?", (prize_id,))
    conn.execute("DELETE FROM wheel_prize_configs WHERE prize_id=?", (prize_id,))
    conn.commit()
    conn.close()


def pick_weighted_prize(prizes):
    total = sum(p['weight'] for p in prizes) or 1
    r = random.randint(1, total)
    upto = 0
    for p in prizes:
        upto += p['weight']
        if r <= upto:
            return p
    return prizes[-1]


def check_wheel_cooldown(user_id):
    """اگه هنوز نوبتش نشده، دقیقه‌های باقی‌مونده رو برمی‌گردونه؛ وگرنه None."""
    settings = get_wheel_settings()
    conn = get_conn()
    row = conn.execute("SELECT * FROM wheel_spins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    if not row or not row['last_spin_at']:
        return None
    last = datetime.fromisoformat(row['last_spin_at'])
    passed = datetime.now() - last
    remain = timedelta(hours=settings['cooldown_hours']) - passed
    if remain.total_seconds() > 0:
        return remain
    return None


def update_wheel_spin_time(user_id):
    conn = get_conn()
    conn.execute("INSERT INTO wheel_spins(user_id, last_spin_at) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET last_spin_at=excluded.last_spin_at",
                 (user_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def generate_wheel_discount_code():
    return "WHL" + "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))


def mark_discount_used(code, user_id):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO discount_usage(code, user_id) VALUES (?,?)", (code, user_id))
    conn.execute("UPDATE discount_codes SET used_count = used_count + 1 WHERE code=?", (code,))
    conn.commit()
    conn.close()


def validate_discount(code, user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM discount_codes WHERE code=? AND active=1", (code,)).fetchone()
    if not row:
        conn.close()
        return None, "کد تخفیف نامعتبر است ❌"
    if row['expires_at'] and datetime.now() > datetime.fromisoformat(row['expires_at']):
        conn.close()
        return None, "کد تخفیف منقضی شده است ❌"
    if row['max_uses'] != -1 and row['used_count'] >= row['max_uses']:
        conn.close()
        return None, "ظرفیت استفاده از این کد تمام شده است ❌"
    used = conn.execute("SELECT 1 FROM discount_usage WHERE code=? AND user_id=?", (code, user_id)).fetchone()
    conn.close()
    if used:
        return None, "شما قبلا از این کد استفاده کرده‌اید ❌"
    return row, None


def clear_state(context):
    context.user_data['state'] = None
    context.user_data['data'] = {}


async def reply_or_edit(update, text, reply_markup=None):
    """اگه از دکمه شیشه‌ای اومده باشه ادیت می‌کنه، اگه از متن اومده باشه پیام جدید می‌فرسته."""
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)


# ============================================================
#                   عضویت اجباری در کانال‌ها
# ============================================================

def get_required_channels():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM required_channels").fetchall()
    conn.close()
    return rows


async def get_missing_channels(context, user_id):
    channels = get_required_channels()
    missing = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(f"@{ch['channel_username']}", user_id)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except TelegramError:
            # اگه چک نشد (ربات ادمین کانال نیست یا کاربر هیچوقت استارت نداده) بازم به عنوان عضونشده در نظر می‌گیریم
            missing.append(ch)
    return missing


async def send_join_prompt(update, context, missing):
    kb = []
    for ch in missing:
        kb.append([InlineKeyboardButton(f"عضویت در {ch['channel_title']} 📢", url=f"https://t.me/{ch['channel_username']}")])
    kb.append([InlineKeyboardButton("عضو شدم، بررسی کن ✅", callback_data="check_join")])
    text = "برای استفاده از ربات، ابتدا باید در کانال‌های زیر عضو شوید:"
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        except TelegramError:
            await context.bot.send_message(update.effective_chat.id, text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def cb_check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    missing = await get_missing_channels(context, user_id)
    if missing:
        # به جای ارسال دوباره‌ی پیام عضویت (که هر بار یه پیام جدید می‌ساخت)، فقط یه هشدار کوچیک نشون می‌ده
        await query.answer("❌ کاربر گرامی، شما هنوز عضو تمام کانال‌های اجباری نشده‌اید. لطفا عضو شوید و دوباره تلاش کنید.", show_alert=True)
        return
    await query.edit_message_text("✅ عضویت شما تایید شد. حالا می‌توانید از ربات استفاده کنید.")
    await context.bot.send_message(update.effective_chat.id, "یکی از گزینه‌ها رو انتخاب کنید:", reply_markup=main_menu_kb(user_id))


# ============================================================
#                        کیبوردها
# ============================================================

def main_menu_kb(user_id):
    rows = [
        ["خرید کانفیگ 🛒", "دریافت تست 🎁"],
        ["کیف پول 💰", "کانفیگ های من 📦"],
        ["پیام به پشتیبانی 📞"]
    ]
    if is_wheel_enabled() and get_wheel_prizes():
        rows.append(["گردونه شانس 🎡"])
    if is_admin(user_id):
        rows.append(["مدیریت 🛠"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_menu_kb(user_id):
    owner = is_owner(user_id)
    perms = get_admin_permissions(user_id)

    def allowed(p):
        return owner or p in perms

    rows = []
    if allowed("plans"):
        rows.append(["افزودن پلن ➕", "ویرایش پلن ✏️"])
        rows.append(["حذف پلن 🗑"])
    if allowed("test"):
        rows.append(["افزودن کانفیگ تست 🎁", "لیست کانفیگ تست 📋"])
        rows.append(["ویرایش کانفیگ تست ✏️", "حذف کانفیگ تست 🗑"])
    if allowed("users"):
        rows.append(["کاربران 👥", "پیام مستقیم ✉️"])
        rows.append(["مسدود کردن 🚫", "رفع مسدودی ✅"])
    if allowed("wallet"):
        rows.append(["شارژ کیف پول 💳", "کسر از کیف پول ➖"])
    if allowed("broadcast"):
        rows.append(["پیام همگانی 📢"])
    if allowed("discount"):
        rows.append(["کد تخفیف جدید 🏷", "باطل کردن کد تخفیف ❌"])
    if allowed("channels"):
        rows.append(["کانال اجباری ➕", "حذف کانال اجباری 🗑"])
    if allowed("card"):
        rows.append(["تغییر شماره کارت 💳"])
    if allowed("maintenance"):
        toggle_label = "روشن کردن ربات 🟢" if is_maintenance_on() else "خاموش کردن ربات 🔴"
        rows.append([toggle_label])
    if allowed("admins"):
        rows.append(["افزودن ادمین 👤"])
    rows.append(["لیست ادمین‌ها 📋"])
    if owner:
        rows.append(["حذف ادمین 🗑", "ویرایش دسترسی ادمین‌ها 🔑"])
        rows.append(["انتقال مالکیت ♻️"])
        wheel_toggle = "خاموش کردن گردونه 🎡" if is_wheel_enabled() else "روشن کردن گردونه 🎡"
        rows.append([wheel_toggle, "مدیریت جایزه‌های گردونه ⚙️"])
        rows.append(["دریافت فایل بکاپ 💾", "بازگردانی از بکاپ 📥"])
    rows.append([BACK_BTN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("لغو ❌", callback_data="cancel_action")]])


ADMIN_MENU_TEXTS = {
    "افزودن پلن ➕", "ویرایش پلن ✏️", "حذف پلن 🗑", "افزودن کانفیگ تست 🎁",
    "لیست کانفیگ تست 📋", "ویرایش کانفیگ تست ✏️", "حذف کانفیگ تست 🗑",
    "کاربران 👥", "پیام همگانی 📢", "پیام مستقیم ✉️", "شارژ کیف پول 💳",
    "کسر از کیف پول ➖", "مسدود کردن 🚫", "رفع مسدودی ✅", "کد تخفیف جدید 🏷",
    "باطل کردن کد تخفیف ❌", "افزودن ادمین 👤", "حذف ادمین 🗑",
    "کانال اجباری ➕", "حذف کانال اجباری 🗑", "لیست ادمین‌ها 📋",
    "تغییر شماره کارت 💳", "خاموش کردن ربات 🔴", "روشن کردن ربات 🟢",
    "انتقال مالکیت ♻️", "ویرایش دسترسی ادمین‌ها 🔑",
    "روشن کردن گردونه 🎡", "خاموش کردن گردونه 🎡", "مدیریت جایزه‌های گردونه ⚙️",
    "دریافت فایل بکاپ 💾", "بازگردانی از بکاپ 📥",
}

MAIN_MENU_TEXTS = {
    "خرید کانفیگ 🛒", "دریافت تست 🎁", "کیف پول 💰", "کانفیگ های من 📦",
    "پیام به پشتیبانی 📞", "مدیریت 🛠", "گردونه شانس 🎡",
}

ALL_MENU_TEXTS = MAIN_MENU_TEXTS | ADMIN_MENU_TEXTS | {BACK_BTN}


# ============================================================
#                     /start و روتر متن
# ============================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)
    if is_banned(user.id):
        await update.message.reply_text(f"کاربر محترم شما از طرف ادمین مسدود شده اید ❌\nدلیل: {get_ban_reason(user.id)}")
        return
    if is_maintenance_on() and not is_admin(user.id):
        await update.message.reply_text("🔧 ربات موقتاً برای تعمیرات خاموش است. لطفا کمی بعد دوباره تلاش کنید.")
        return
    clear_state(context)
    if not is_admin(user.id):
        missing = await get_missing_channels(context, user.id)
        if missing:
            await send_join_prompt(update, context, missing)
            return
    await update.message.reply_text(
        "به ربات فروش کانفیگ 𝗡𝘆𝗿𝗼𝘅 خوش آمدید 🌟\nیکی از گزینه‌ها رو انتخاب کنید:",
        reply_markup=main_menu_kb(user.id)
    )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)
    if is_banned(user.id):
        await update.message.reply_text(f"کاربر محترم شما از طرف ادمین مسدود شده اید ❌\nدلیل: {get_ban_reason(user.id)}")
        return
    if is_maintenance_on() and not is_admin(user.id):
        await update.message.reply_text("🔧 ربات موقتاً برای تعمیرات خاموش است. لطفا کمی بعد دوباره تلاش کنید.")
        return

    text = update.message.text.strip() if update.message.text else ""
    state = context.user_data.get('state')

    # ---------- دکمه بازگشت از پنل مدیریت ----------
    if text == BACK_BTN:
        clear_state(context)
        await update.message.reply_text("بازگشت به منوی اصلی:", reply_markup=main_menu_kb(user.id))
        return

    # ---------- اگه وسط یه مرحله بود ولی رو یکی از دکمه‌های منو زد، خودکار سوییچ کن ----------
    if state and text in ALL_MENU_TEXTS:
        clear_state(context)
        state = None

    # ---------- هندل حالت‌های چندمرحله‌ای در جریان (فقط اگه دکمه منو نبود) ----------
    if state:
        handled = await handle_stateful_text(update, context, state, text)
        if handled:
            return

    # ---------- عضویت اجباری (برای کاربر عادی، قبل از هر عمل دیگه) ----------
    if not is_admin(user.id) and text not in ("مدیریت 🛠",):
        missing = await get_missing_channels(context, user.id)
        if missing:
            await send_join_prompt(update, context, missing)
            return

    # ---------- منوی مدیریت (اگه ادمینه و دکمه‌های پنل رو زده) ----------
    if is_admin(user.id) and text in ADMIN_MENU_TEXTS:
        await admin_menu_dispatch(update, context, text)
        return

    # ---------- منوی اصلی ----------
    if text == "خرید کانفیگ 🛒":
        await ask_buy_category(update, context)
    elif text == "دریافت تست 🎁":
        await handle_get_test(update, context)
    elif text == "کیف پول 💰":
        await show_wallet(update, context)
    elif text == "کانفیگ های من 📦":
        await show_my_configs(update, context)
    elif text == "گردونه شانس 🎡":
        await show_wheel_entry(update, context)
    elif text == "پیام به پشتیبانی 📞":
        context.user_data['state'] = "waiting_support_msg"
        await update.message.reply_text("لطفا پیام خودتون رو برای پشتیبانی بنویسید و ارسال کنید:")
    elif text == "مدیریت 🛠":
        if not is_admin(user.id):
            await update.message.reply_text("شما دسترسی ادمین ندارید ❌")
        elif is_owner(user.id) and not get_card_info()[1]:
            await update.message.reply_text(
                "👋 خوش اومدی! قبل از هر چیز، برای شروع باید شماره کارت دریافت وجه رو تنظیم کنی.\n\n"
                "شماره کارت رو ارسال کن:"
            )
            context.user_data['state'] = "admin_card_number"
            context.user_data['data'] = {}
        else:
            await update.message.reply_text("🛠 پنل مدیریت — یکی از گزینه‌ها رو انتخاب کنید:", reply_markup=admin_menu_kb(user.id))
    else:
        if not state:
            await update.message.reply_text("لطفا از دکمه‌های منو استفاده کنید 👇", reply_markup=main_menu_kb(user.id))


# ============================================================
#                 خرید کانفیگ / تست / کیف پول
# ============================================================

async def ask_buy_category(update, context):
    kb = [[InlineKeyboardButton(CATEGORY_LABELS[c], callback_data=f"buycat_{c}")] for c in PLAN_CATEGORIES]
    kb.append([InlineKeyboardButton("انصراف ❌", callback_data="cancel_action")])
    await update.message.reply_text("کدوم دسته کانفیگ رو می‌خوای؟ 👇", reply_markup=InlineKeyboardMarkup(kb))


async def show_plans_in_category(update, context, category, mode="buy"):
    conn = get_conn()
    plans = conn.execute("SELECT * FROM plans WHERE is_test=0 AND category=?", (category,)).fetchall()
    conn.close()
    label = CATEGORY_LABELS.get(category, category)
    if not plans:
        text = f"در حال حاضر پلنی در دسته «{label}» ثبت نشده است."
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return
    kb = []
    prefix = "buyplan" if mode == "buy" else "renewplan"
    for p in plans:
        cap = plan_capacity(p['id'])
        plan_label = f"{p['title']} | {p['price']:,} تومان | موجودی: {cap}"
        kb.append([InlineKeyboardButton(plan_label, callback_data=f"{prefix}_{p['id']}")])
    kb.append([InlineKeyboardButton("بازگشت 🔙", callback_data="cancel_action")])
    text = f"{label}\nیکی از پلن‌ها رو انتخاب کنید:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def cb_select_plan(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id, is_renew=False):
    query = update.callback_query
    conn = get_conn()
    plan = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    conn.close()
    if not plan:
        await query.edit_message_text("این پلن دیگر موجود نیست ❌")
        return
    if plan_capacity(plan_id) <= 0:
        await query.edit_message_text("ظرفیت این پلن تمام شده است ❌")
        return

    context.user_data['data'] = {
        "plan_id": plan_id, "plan_title": plan['title'],
        "price": plan['price'], "is_renew": is_renew,
        "discount_code": None, "discount_amount": 0
    }
    context.user_data['state'] = "ask_discount"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("دارم ✅", callback_data="discount_yes"),
         InlineKeyboardButton("ندارم ❌", callback_data="discount_no")]
    ])
    await query.edit_message_text(f"پلن انتخابی: {plan['title']}\nقیمت: {plan['price']:,} تومان\n\nآیا کد تخفیف دارید؟", reply_markup=kb)


async def cb_discount_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, has_code: bool):
    query = update.callback_query
    if not has_code:
        await ask_payment_method(update, context)
        return
    context.user_data['state'] = "waiting_discount_code"
    await query.edit_message_text("لطفا کد تخفیف رو ارسال کنید:")


async def ask_payment_method(update, context):
    d = context.user_data['data']
    price = d['price'] - d.get('discount_amount', 0)
    if price < 0:
        price = 0
    d['final_price'] = price
    context.user_data['state'] = "waiting_payment_method"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("کیف پول 💰", callback_data="pay_wallet"),
         InlineKeyboardButton("کارت به کارت 💳", callback_data="pay_card")]
    ])
    txt = f"مبلغ قابل پرداخت: {price:,} تومان\nروش خرید رو انتخاب کنید:"
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, reply_markup=kb)
    else:
        await update.message.reply_text(txt, reply_markup=kb)


async def cb_pay_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    user_id = user.id
    d = context.user_data.get('data', {})
    price = d.get('final_price', d.get('price', 0))
    balance = get_wallet(user_id)
    if balance < price:
        await query.edit_message_text(f"موجودی کیف پول کافی نیست ❌\nموجودی شما: {balance:,} تومان")
        clear_state(context)
        return
    ok = await deliver_config_and_finalize(context, user_id, d, pay_method="کیف پول", auto_approved=True)
    if ok:
        change_wallet(user_id, -price)
        await query.edit_message_text("✅ خرید با موفقیت انجام شد و کانفیگ ارسال گردید.")
        if d.get('discount_code'):
            discount_line = f"🏷 کد تخفیف: {d['discount_code']} (تخفیف: {d.get('discount_amount', 0):,} تومان)"
        else:
            discount_line = "🏷 بدون کد تخفیف"
        await notify_all_admins_text(
            context,
            f"💰 خرید جدید با کیف پول\nکاربر: {user_id} (@{user.username or '-'})\n"
            f"پلن: {d['plan_title']}\n{discount_line}\nمبلغ پرداختی: {price:,} تومان\n"
            f"موجودی باقی‌مانده: {get_wallet(user_id):,} تومان"
        )
    else:
        await query.edit_message_text("ظرفیت این پلن تمام شده است ❌")
    clear_state(context)


async def cb_pay_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data['state'] = "waiting_receipt_photo"
    card_number, card_holder = get_card_info()
    holder_line = f"\n👤 به نام: {card_holder}" if card_holder else ""
    await query.edit_message_text(
        f"لطفا مبلغ ({context.user_data['data'].get('final_price', 0):,} تومان) رو به شماره کارت زیر واریز کنید و رسید رو ارسال کنید:\n\n"
        f"💳 {card_number}{holder_line}\n\nلطفا رسید (عکس) رو برای ربات ارسال کنید 🧾♥️"
    )


async def deliver_config_and_finalize(context, user_id, data, pay_method, auto_approved=False):
    plan_id = data['plan_id']
    conn = get_conn()
    cfg = conn.execute("SELECT * FROM plan_configs WHERE plan_id=? AND used=0 LIMIT 1", (plan_id,)).fetchone()
    if not cfg:
        conn.close()
        return False
    conn.execute("UPDATE plan_configs SET used=1, used_by=? WHERE id=?", (user_id, cfg['id']))
    expires = (datetime.now() + timedelta(days=get_plan_duration(plan_id))).isoformat()
    conn.execute("""INSERT INTO orders(user_id, plan_id, plan_title, price, pay_method, status, config_id, created_at, expires_at, is_renew)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (user_id, plan_id, data['plan_title'], data.get('final_price', data['price']),
                  pay_method, "تحویل شد", cfg['id'], datetime.now().isoformat(), expires, int(data.get('is_renew', False))))
    conn.commit()
    conn.close()
    if data.get('discount_code'):
        mark_discount_used(data['discount_code'], user_id)
    await send_config_to_user(context, user_id, cfg)
    return True


async def send_config_to_user(context, user_id, cfg_row):
    if cfg_row['content_type'] == 'photo':
        await context.bot.send_photo(user_id, cfg_row['photo_file_id'], caption=cfg_row['caption'] or "")
    else:
        await context.bot.send_message(user_id, cfg_row['text_content'])


async def handle_get_test(update, context):
    user_id = update.effective_user.id
    conn = get_conn()
    used = conn.execute("SELECT 1 FROM test_usage WHERE user_id=?", (user_id,)).fetchone()
    if used:
        conn.close()
        await update.message.reply_text("شما قبلا از تست استفاده کرده اید ❌🫵")
        return
    rows = conn.execute("SELECT * FROM test_configs WHERE used=0").fetchall()
    if not rows:
        conn.close()
        await update.message.reply_text("در حال حاضر کانفیگ تستی موجود نیست.")
        return
    chosen = random.choice(rows)
    conn.execute("UPDATE test_configs SET used=1, used_by=? WHERE id=?", (user_id, chosen['id']))
    conn.execute("INSERT INTO test_usage(user_id, used_at) VALUES (?,?)", (user_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    await send_config_to_user(context, user_id, chosen)
    await update.message.reply_text("✅ کانفیگ تست برای شما ارسال شد.")


async def show_wallet(update, context):
    user_id = update.effective_user.id
    balance = get_wallet(user_id)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("شارژ کیف پول 💳", callback_data="charge_wallet")]])
    await update.message.reply_text(f"💰 موجودی کیف پول شما: {balance:,} تومان", reply_markup=kb)


async def cb_charge_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data['state'] = "waiting_charge_amount"
    await query.edit_message_text("مبلغ مورد نظر برای شارژ کیف پول رو به تومان وارد کنید (مثلا 100000):")


async def show_my_configs(update, context):
    user_id = update.effective_user.id
    conn = get_conn()
    orders = conn.execute("SELECT * FROM orders WHERE user_id=? AND hidden=0 ORDER BY id DESC", (user_id,)).fetchall()
    conn.close()
    if not orders:
        await update.message.reply_text("شما تاکنون هیچ کانفیگی خریداری نکرده‌اید.")
        return
    for o in orders:
        expires = datetime.fromisoformat(o['expires_at']) if o['expires_at'] else None
        if expires:
            remaining = expires - datetime.now()
            status = "✅ فعال" if remaining.total_seconds() > 0 else "❌ منقضی شده"
            remain_txt = f"{remaining.days} روز مانده" if remaining.total_seconds() > 0 else "تمام شده"
        else:
            status, remain_txt = "در انتظار تایید", "-"
        kb_rows = [[InlineKeyboardButton("تمدید این پلن 🔄", callback_data=f"renewplan_{o['plan_id']}")]]
        row2 = []
        if o['config_id']:
            row2.append(InlineKeyboardButton("مشاهده کانفیگ 👁", callback_data=f"vieworder_{o['id']}"))
        row2.append(InlineKeyboardButton("حذف از لیست 🗑", callback_data=f"hideorder_{o['id']}"))
        kb_rows.append(row2)
        await update.message.reply_text(
            f"📦 پلن: {o['plan_title']}\n💵 قیمت: {o['price']:,} تومان\nوضعیت: {status}\n⏳ {remain_txt}",
            reply_markup=InlineKeyboardMarkup(kb_rows)
        )


async def cb_view_order(update, context, order_id):
    query = update.callback_query
    user_id = update.effective_user.id
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user_id)).fetchone()
    cfg = conn.execute("SELECT * FROM plan_configs WHERE id=?", (order['config_id'],)).fetchone() if order and order['config_id'] else None
    conn.close()
    if not order or not cfg:
        await query.answer("کانفیگ این خرید دیگر در دسترس نیست.", show_alert=True)
        return
    await query.answer()
    if cfg['content_type'] == 'photo':
        await context.bot.send_photo(user_id, cfg['photo_file_id'], caption=cfg['caption'] or "")
    else:
        await context.bot.send_message(user_id, cfg['text_content'])


async def cb_hide_order(update, context, order_id):
    query = update.callback_query
    user_id = update.effective_user.id
    conn = get_conn()
    conn.execute("UPDATE orders SET hidden=1 WHERE id=? AND user_id=?", (order_id, user_id))
    conn.commit()
    conn.close()
    await query.answer("از لیست حذف شد ✅", show_alert=True)
    try:
        await query.message.delete()
    except Exception:
        pass


# ---------- گردونه شانس (سمت کاربر) ----------
async def show_wheel_entry(update, context):
    if not is_wheel_enabled() or not get_wheel_prizes():
        await update.message.reply_text("گردونه شانس در حال حاضر فعال نیست.")
        return
    remain = check_wheel_cooldown(update.effective_user.id)
    if remain:
        hours = int(remain.total_seconds() // 3600)
        minutes = int((remain.total_seconds() % 3600) // 60)
        await update.message.reply_text(f"⏳ برای چرخوندن دوباره گردونه باید صبر کنی.\nزمان باقی‌مانده: {hours} ساعت و {minutes} دقیقه")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("بچرخون 🎡", callback_data="wheel_spin")]])
    await update.message.reply_text("🎡 آماده‌ای شانستو امتحان کنی؟", reply_markup=kb)


async def cb_wheel_spin(update, context):
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_wheel_enabled():
        await query.edit_message_text("گردونه شانس در حال حاضر فعال نیست.")
        return
    remain = check_wheel_cooldown(user_id)
    if remain:
        hours = int(remain.total_seconds() // 3600)
        minutes = int((remain.total_seconds() % 3600) // 60)
        await query.answer(f"⏳ باید صبر کنی. زمان باقی‌مانده: {hours} ساعت و {minutes} دقیقه", show_alert=True)
        return
    prizes = get_wheel_prizes()
    if not prizes:
        await query.edit_message_text("گردونه شانس در حال حاضر فعال نیست.")
        return
    prize = pick_weighted_prize(prizes)
    ptype = prize['prize_type']

    if ptype == "respin":
        await query.edit_message_text("🔄 شانس آوردی! یه دور دیگه بچرخون 🎡", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بچرخون 🎡", callback_data="wheel_spin")]]))
        return  # کولداون آپدیت نمی‌شه، همین الان دوباره می‌تونه بزنه

    update_wheel_spin_time(user_id)

    if ptype == "none":
        await query.edit_message_text("😢 پوچ! دفعه بعد شانستو امتحان کن.")
        return

    if ptype == "discount":
        code = generate_wheel_discount_code()
        expires = (datetime.now() + timedelta(hours=prize['discount_hours'])).isoformat()
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO discount_codes(code, amount, percent, expires_at, max_uses, used_count, active) VALUES (?,?,?,?,?,0,1)",
                     (code, 0, prize['discount_percent'], expires, 1))
        conn.commit()
        conn.close()
        await query.edit_message_text(
            f"🎉 تبریک! برنده‌ی {prize['discount_percent']}% تخفیف شدی.\n"
            f"کد تخفیفت (بزن روش کپی بشه):\n`{code}`\n\n⏳ اعتبار: {prize['discount_hours']} ساعت",
            parse_mode="Markdown"
        )
        return

    if ptype == "config":
        conn = get_conn()
        cfg = conn.execute("SELECT * FROM wheel_prize_configs WHERE prize_id=? AND used=0 LIMIT 1", (prize['id'],)).fetchone()
        if cfg:
            conn.execute("UPDATE wheel_prize_configs SET used=1, used_by=? WHERE id=?", (user_id, cfg['id']))
            conn.commit()
        conn.close()
        if not cfg:
            await query.edit_message_text("🎉 برنده‌ی یک کانفیگ رایگان شدی، ولی موجودیش تموم شده! لطفا با پشتیبانی تماس بگیر.")
            return
        await query.edit_message_text(f"🎉 تبریک! برنده‌ی {prize['config_gb']:g} گیگ کانفیگ رایگان شدی 🎁")
        if cfg['content_type'] == 'photo':
            await context.bot.send_photo(user_id, cfg['photo_file_id'], caption=cfg['caption'] or "")
        else:
            await context.bot.send_message(user_id, cfg['text_content'])
        return


# ============================================================
#                     دریافت عکس رسید
# ============================================================

async def photo_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        return
    state = context.user_data.get('state')
    photo_file_id = update.message.photo[-1].file_id

    if state == "waiting_receipt_photo":
        d = context.user_data['data']
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO orders(user_id, plan_id, plan_title, price, pay_method, status, config_id, created_at, expires_at, is_renew) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user.id, d['plan_id'], d['plan_title'], d.get('final_price', d['price']), "کارت به کارت", "در انتظار تایید", None,
             datetime.now().isoformat(), "", int(d.get('is_renew', False)))
        )
        order_id = cur.lastrowid
        conn.commit()
        conn.close()

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("تایید رسید ✅", callback_data=f"approve_order_{order_id}"),
             InlineKeyboardButton("رد رسید ❌", callback_data=f"reject_order_{order_id}")]
        ])
        caption = (f"🧾 رسید جدید\nکاربر: {user.id} (@{user.username or '-'})\n"
                   f"پلن: {d['plan_title']}\nمبلغ: {d.get('final_price', d['price']):,} تومان")
        await notify_all_admins_photo(context, photo_file_id, caption, kb)

        await update.message.reply_text("رسید شما دریافت شد و برای بررسی به ادمین ارسال گردید. لطفا منتظر بمانید ⏳")
        clear_state(context)
        return

    if state == "waiting_wallet_receipt_photo":
        d = context.user_data['data']
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO wallet_charges(user_id, amount, status, created_at) VALUES (?,?,?,?)",
            (user.id, d['charge_amount'], "در انتظار تایید", datetime.now().isoformat())
        )
        charge_id = cur.lastrowid
        conn.commit()
        conn.close()

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("تایید شارژ ✅", callback_data=f"approve_charge_{charge_id}"),
             InlineKeyboardButton("رد شارژ ❌", callback_data=f"reject_charge_{charge_id}")]
        ])
        caption = f"🧾 درخواست شارژ کیف پول\nکاربر: {user.id} (@{user.username or '-'})\nمبلغ: {d['charge_amount']:,} تومان"
        await notify_all_admins_photo(context, photo_file_id, caption, kb)

        await update.message.reply_text("رسید شما دریافت شد و برای بررسی به ادمین ارسال گردید. لطفا منتظر بمانید ⏳")
        clear_state(context)
        return

    await handle_admin_photo_states(update, context, state, photo_file_id)


# ============================================================
#              تایید / رد رسید خرید و شارژ کیف پول
# ============================================================

async def cb_approve_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id):
    query = update.callback_query
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or order['status'] != "در انتظار تایید":
        conn.close()
        await query.edit_message_caption("این رسید قبلا بررسی شده است.")
        return
    plan_id = order['plan_id']
    cfg = conn.execute("SELECT * FROM plan_configs WHERE plan_id=? AND used=0 LIMIT 1", (plan_id,)).fetchone()
    if not cfg:
        conn.execute("UPDATE orders SET status='ظرفیت تمام' WHERE id=?", (order_id,))
        conn.commit()
        conn.close()
        await query.edit_message_caption("ظرفیت این پلن تمام شده — نمی‌توان تایید کرد ❌")
        await context.bot.send_message(order['user_id'], "متاسفانه ظرفیت این پلن تمام شده. مبلغ به شما بازگردانده خواهد شد، با پشتیبانی در تماس باشید.")
        return
    expires = (datetime.now() + timedelta(days=get_plan_duration(plan_id))).isoformat()
    conn.execute("UPDATE plan_configs SET used=1, used_by=? WHERE id=?", (order['user_id'], cfg['id']))
    conn.execute("UPDATE orders SET status='تحویل شد', config_id=?, expires_at=? WHERE id=?", (cfg['id'], expires, order_id))
    conn.commit()
    conn.close()
    await send_config_to_user(context, order['user_id'], cfg)
    await context.bot.send_message(order['user_id'], "✅ رسید شما تایید شد و کانفیگ ارسال گردید.")
    await query.edit_message_caption(query.message.caption + "\n\n✅ تایید شد.")


async def cb_reject_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id):
    query = update.callback_query
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or order['status'] != "در انتظار تایید":
        conn.close()
        await query.edit_message_caption("این رسید قبلا بررسی شده است.")
        return
    conn.execute("UPDATE orders SET status='رد شد' WHERE id=?", (order_id,))
    conn.commit()
    conn.close()
    await context.bot.send_message(order['user_id'], "کاربر گرامی رسید شما رد شد لطفا به پشتیبانی پیام بدهید !")
    await query.edit_message_caption(query.message.caption + "\n\n❌ رد شد.")


async def cb_approve_charge(update: Update, context: ContextTypes.DEFAULT_TYPE, charge_id):
    query = update.callback_query
    conn = get_conn()
    charge = conn.execute("SELECT * FROM wallet_charges WHERE id=?", (charge_id,)).fetchone()
    if not charge or charge['status'] != "در انتظار تایید":
        conn.close()
        await query.edit_message_caption("این درخواست قبلا بررسی شده است.")
        return
    conn.execute("UPDATE wallet_charges SET status='تایید شد' WHERE id=?", (charge_id,))
    conn.commit()
    conn.close()
    change_wallet(charge['user_id'], charge['amount'])
    await context.bot.send_message(charge['user_id'], f"✅ کیف پول شما به مبلغ {charge['amount']:,} تومان شارژ شد.")
    await query.edit_message_caption(query.message.caption + "\n\n✅ تایید شد.")


async def cb_reject_charge(update: Update, context: ContextTypes.DEFAULT_TYPE, charge_id):
    query = update.callback_query
    conn = get_conn()
    charge = conn.execute("SELECT * FROM wallet_charges WHERE id=?", (charge_id,)).fetchone()
    if not charge or charge['status'] != "در انتظار تایید":
        conn.close()
        await query.edit_message_caption("این درخواست قبلا بررسی شده است.")
        return
    conn.execute("UPDATE wallet_charges SET status='رد شد' WHERE id=?", (charge_id,))
    conn.commit()
    conn.close()
    await context.bot.send_message(charge['user_id'], "کاربر گرامی رسید شارژ کیف پول شما رد شد لطفا به پشتیبانی پیام بدهید !")
    await query.edit_message_caption(query.message.caption + "\n\n❌ رد شد.")


# ============================================================
#            هندل متن‌های چندمرحله‌ای (سطح کاربر عادی)
# ============================================================

async def handle_stateful_text(update, context, state, text) -> bool:
    user_id = update.effective_user.id

    if state == "waiting_support_msg":
        conn = get_conn()
        cur = conn.execute("INSERT INTO tickets(user_id, user_msg, status, created_at) VALUES (?,?,?,?)",
                            (user_id, text, "باز", datetime.now().isoformat()))
        ticket_id = cur.lastrowid
        conn.commit()
        conn.close()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("ارسال پاسخ به کاربر ↩️", callback_data=f"reply_ticket_{ticket_id}")]])
        await notify_all_admins_text(
            context,
            f"📩 پیام پشتیبانی جدید\nکاربر: {user_id}\nپیام: {text}",
            kb
        )
        await update.message.reply_text("پیام شما برای پشتیبانی ارسال شد. منتظر پاسخ باشید ⏳")
        clear_state(context)
        return True

    if state == "waiting_discount_code":
        row, err = validate_discount(text.strip(), user_id)
        if err:
            await update.message.reply_text(err)
            clear_state(context)
            return True
        d = context.user_data['data']
        d['discount_code'] = row['code']
        if row['percent']:
            d['discount_amount'] = (d['price'] * row['percent']) // 100
        else:
            d['discount_amount'] = row['amount']
        await ask_payment_method(update, context)
        return True

    if state == "waiting_charge_amount":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("لطفا فقط عدد معتبر وارد کنید (مثلا 100000):")
            return True
        context.user_data['data'] = {"charge_amount": int(text)}
        context.user_data['state'] = "waiting_wallet_receipt_photo"
        card_number, card_holder = get_card_info()
        holder_line = f"\n👤 به نام: {card_holder}" if card_holder else ""
        await update.message.reply_text(
            f"لطفا مبلغ {int(text):,} تومان رو به شماره کارت زیر واریز کنید و رسید رو ارسال کنید:\n\n"
            f"💳 {card_number}{holder_line}\n\nلطفا رسید (عکس) رو ارسال کنید 🧾♥️"
        )
        return True

    handled = await handle_admin_text_states(update, context, state, text)
    return handled


async def cb_reply_ticket(update, context, ticket_id):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("شما دسترسی ادمین ندارید ❌", show_alert=True)
        return
    context.user_data['state'] = "admin_reply_ticket_text"
    context.user_data['data'] = {"ticket_id": ticket_id}
    await context.bot.send_message(query.message.chat_id, "متن پاسخ برای کاربر رو ارسال کنید:")


# ============================================================
#              روتر دکمه‌های شیشه‌ای (Callback Query)
# ============================================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    await query.answer()

    if data == "check_join":
        await cb_check_join(update, context)
        return

    if is_banned(user_id):
        await query.edit_message_text(f"کاربر محترم شما از طرف ادمین مسدود شده اید ❌\nدلیل: {get_ban_reason(user_id)}")
        return

    if is_maintenance_on() and not is_admin(user_id):
        await query.answer("🔧 ربات موقتاً برای تعمیرات خاموش است. لطفا کمی بعد دوباره تلاش کنید.", show_alert=True)
        return

    if data == "cancel_action":
        clear_state(context)
        await query.edit_message_text("عملیات لغو شد.", reply_markup=None)
        return

    # ---------- عضویت اجباری: قبل از هر عمل خرید/تخفیف/پرداخت هم چک بشه ----------
    if not is_admin(user_id):
        missing = await get_missing_channels(context, user_id)
        if missing:
            await query.answer("❌ کاربر گرامی، شما هنوز عضو تمام کانال‌های اجباری نشده‌اید. لطفا عضو شوید و دوباره تلاش کنید.", show_alert=True)
            return

    if data.startswith("buycat_"):
        await show_plans_in_category(update, context, data[len("buycat_"):], mode="buy")
    elif data.startswith("buyplan_"):
        await cb_select_plan(update, context, int(data.split("_")[1]), is_renew=False)
    elif data.startswith("renewplan_"):
        await cb_select_plan(update, context, int(data.split("_")[1]), is_renew=True)
    elif data.startswith("vieworder_"):
        await cb_view_order(update, context, int(data.split("_")[1]))
    elif data.startswith("hideorder_"):
        await cb_hide_order(update, context, int(data.split("_")[1]))
    elif data == "discount_yes":
        await cb_discount_choice(update, context, True)
    elif data == "discount_no":
        await cb_discount_choice(update, context, False)
    elif data == "pay_wallet":
        await cb_pay_wallet(update, context)
    elif data == "pay_card":
        await cb_pay_card(update, context)
    elif data == "charge_wallet":
        await cb_charge_wallet(update, context)
    elif data.startswith("approve_order_"):
        await cb_approve_order(update, context, int(data.split("_")[2]))
    elif data.startswith("reject_order_"):
        await cb_reject_order(update, context, int(data.split("_")[2]))
    elif data.startswith("approve_charge_"):
        await cb_approve_charge(update, context, int(data.split("_")[2]))
    elif data.startswith("reject_charge_"):
        await cb_reject_charge(update, context, int(data.split("_")[2]))
    elif data.startswith("reply_ticket_"):
        await cb_reply_ticket(update, context, int(data.split("_")[2]))
    else:
        await admin_callback_router(update, context, data)


# ============================================================
#                   بخش مدیریت (ادمین)
# ============================================================

def admin_only(func):
    async def wrapper(update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_admin(user_id):
            if update.callback_query:
                await update.callback_query.answer("شما دسترسی ادمین ندارید ❌", show_alert=True)
            else:
                await update.message.reply_text("شما دسترسی ادمین ندارید ❌")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def owner_only(func):
    async def wrapper(update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_owner(user_id):
            if update.callback_query:
                await update.callback_query.answer("این قابلیت فقط برای مالک اصلی ربات است ❌", show_alert=True)
            else:
                await update.message.reply_text("این قابلیت فقط برای مالک اصلی ربات است ❌")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def requires_permission(perm):
    """فقط مالک یا ادمینی که این دسترسی رو داره می‌تونه از این تابع استفاده کنه."""
    def decorator(func):
        async def wrapper(update, context, *args, **kwargs):
            user_id = update.effective_user.id
            if not is_admin(user_id):
                if update.callback_query:
                    await update.callback_query.answer("شما دسترسی ادمین ندارید ❌", show_alert=True)
                else:
                    await update.message.reply_text("شما دسترسی ادمین ندارید ❌")
                return
            if not has_permission(user_id, perm):
                msg = f"⛔️ شما دسترسی «{PERMISSIONS.get(perm, perm)}» رو ندارید.\nاز مالک ربات بخواهید این دسترسی رو بهتون بده."
                if update.callback_query:
                    await update.callback_query.answer(msg, show_alert=True)
                else:
                    await update.message.reply_text(msg)
                return
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator


async def admin_menu_dispatch(update, context, text):
    mapping = {
        "افزودن پلن ➕": admin_add_plan_start,
        "ویرایش پلن ✏️": admin_edit_plan_start,
        "حذف پلن 🗑": admin_del_plan_start,
        "افزودن کانفیگ تست 🎁": admin_add_test_start,
        "لیست کانفیگ تست 📋": admin_list_test_configs,
        "ویرایش کانفیگ تست ✏️": admin_edit_test_start,
        "حذف کانفیگ تست 🗑": admin_delete_test_start,
        "لیست ادمین‌ها 📋": admin_list_admins,
        "کاربران 👥": admin_show_users,
        "پیام همگانی 📢": admin_broadcast_start,
        "پیام مستقیم ✉️": admin_direct_start,
        "شارژ کیف پول 💳": admin_chargewallet_start,
        "کسر از کیف پول ➖": admin_decrease_wallet_start,
        "مسدود کردن 🚫": admin_ban_start,
        "رفع مسدودی ✅": admin_unban_list,
        "کد تخفیف جدید 🏷": admin_add_discount_start,
        "باطل کردن کد تخفیف ❌": admin_revoke_discount_start,
        "افزودن ادمین 👤": admin_add_admin_start,
        "حذف ادمین 🗑": admin_del_admin_start,
        "کانال اجباری ➕": admin_addchannel_start,
        "حذف کانال اجباری 🗑": admin_delchannel_start,
        "تغییر شماره کارت 💳": admin_change_card_start,
        "خاموش کردن ربات 🔴": admin_maintenance_toggle_start,
        "روشن کردن ربات 🟢": admin_maintenance_toggle_start,
        "انتقال مالکیت ♻️": admin_transfer_ownership_start,
        "ویرایش دسترسی ادمین‌ها 🔑": admin_edit_permissions_start,
        "روشن کردن گردونه 🎡": admin_wheel_toggle,
        "خاموش کردن گردونه 🎡": admin_wheel_toggle,
        "مدیریت جایزه‌های گردونه ⚙️": admin_wheel_manage,
        "دریافت فایل بکاپ 💾": admin_backup_start,
        "بازگردانی از بکاپ 📥": admin_restore_start,
    }
    func = mapping.get(text)
    if func:
        await func(update, context)


# ---------- افزودن پلن ----------
@requires_permission("plans")
async def admin_add_plan_start(update, context):
    context.user_data['state'] = "admin_new_plan_title"
    context.user_data['data'] = {}
    await update.message.reply_text("عنوان (تایتل) پلن جدید رو ارسال کنید:", reply_markup=cancel_kb())


@admin_only
async def admin_new_plan_finish(update, context, category):
    query = update.callback_query
    d = context.user_data.get('data', {})
    if not d.get('title') or 'price' not in d or 'duration' not in d:
        await query.edit_message_text("خطا: اطلاعات پلن ناقص است، لطفا دوباره از منوی «➕ افزودن پلن» شروع کنید.")
        clear_state(context)
        return
    conn = get_conn()
    cur = conn.execute("INSERT INTO plans(title, price, duration_days, is_test, category) VALUES (?,?,?,0,?)",
                        (d['title'], d['price'], d['duration'], category))
    new_plan_id = cur.lastrowid
    conn.commit()
    conn.close()
    await query.edit_message_text(
        f"✅ پلن «{d['title']}» ({CATEGORY_LABELS.get(category, category)}) با قیمت {d['price']:,} تومان و "
        f"{d['duration']} روز اعتبار ساخته شد.\n\nحالا کانفیگ‌های این پلن رو یکی‌یکی وارد کنید:"
    )
    context.user_data['data'] = {"edit_plan_id": new_plan_id, "bulk_add": True, "bulk_count": 0}
    await prompt_next_bulk_config(update, context)


async def prompt_next_bulk_config(update, context):
    d = context.user_data.get('data', {})
    count = d.get('bulk_count', 0)
    context.user_data['state'] = "admin_bulkcfg_choose"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("متنی 📝", callback_data="cfgtype_text"),
         InlineKeyboardButton("عکس + کپشن 🖼", callback_data="cfgtype_photo")],
        [InlineKeyboardButton(f"پایان (تعداد اضافه‌شده: {count}) ✅", callback_data="bulkcfg_done")],
    ])
    text = "کانفیگ بعدی رو وارد کنید یا پایان بدید:"
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)


async def cb_bulkcfg_done(update, context):
    d = context.user_data.get('data', {})
    count = d.get('bulk_count', 0)
    clear_state(context)
    await update.callback_query.edit_message_text(f"✅ تمام. {count} کانفیگ به این پلن اضافه شد.")


# ---------- ویرایش پلن ----------
@requires_permission("plans")
async def admin_edit_plan_start(update, context):
    kb = [[InlineKeyboardButton(CATEGORY_LABELS[c], callback_data=f"editcat_{c}")] for c in PLAN_CATEGORIES]
    kb.append([InlineKeyboardButton("لغو ❌", callback_data="cancel_action")])
    await update.message.reply_text("ویرایش پلن — ابتدا دسته رو انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_edit_plan_list_by_category(update, context, category):
    query = update.callback_query
    conn = get_conn()
    plans = conn.execute("SELECT * FROM plans WHERE is_test=0 AND category=?", (category,)).fetchall()
    conn.close()
    label = CATEGORY_LABELS.get(category, category)
    if not plans:
        await query.edit_message_text(f"هیچ پلنی در دسته «{label}» ثبت نشده است.", reply_markup=cancel_kb())
        return
    kb = [[InlineKeyboardButton(p['title'], callback_data=f"editplan_sel_{p['id']}")] for p in plans]
    kb.append([InlineKeyboardButton("لغو ❌", callback_data="cancel_action")])
    await query.edit_message_text(f"{label}\nکدام پلن رو می‌خواهید ویرایش کنید؟", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_edit_plan_menu(update, context, plan_id):
    query = update.callback_query
    context.user_data['data'] = {"edit_plan_id": plan_id}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("تغییر عنوان ✏️", callback_data=f"editfield_title_{plan_id}")],
        [InlineKeyboardButton("تغییر قیمت 💵", callback_data=f"editfield_price_{plan_id}")],
        [InlineKeyboardButton("تغییر مدت اعتبار 📅", callback_data=f"editfield_duration_{plan_id}")],
        [InlineKeyboardButton("تغییر دسته 🏷", callback_data=f"editfield_category_{plan_id}")],
        [InlineKeyboardButton("افزودن کانفیگ به این پلن ➕", callback_data=f"editfield_addcfg_{plan_id}")],
        [InlineKeyboardButton("مشاهده/حذف کانفیگ‌های این پلن 📋", callback_data=f"editfield_listcfg_{plan_id}")],
        [InlineKeyboardButton("لغو ❌", callback_data="cancel_action")],
    ])
    await query.edit_message_text("چه چیزی را می‌خواهید ویرایش کنید؟", reply_markup=kb)


@admin_only
async def admin_edit_field_start(update, context, field, plan_id):
    query = update.callback_query
    context.user_data['data'] = {"edit_plan_id": plan_id, "edit_field": field}
    prompts = {
        "title": "عنوان جدید را ارسال کنید:",
        "price": "قیمت جدید را به تومان ارسال کنید (فقط عدد):",
        "duration": "مدت اعتبار جدید را به روز ارسال کنید (فقط عدد):",
    }
    if field == "addcfg":
        context.user_data['state'] = "admin_addcfg_to_plan_type"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("متنی 📝", callback_data="cfgtype_text"),
             InlineKeyboardButton("عکس + کپشن 🖼", callback_data="cfgtype_photo")]
        ])
        await query.edit_message_text("نوع کانفیگ رو انتخاب کنید:", reply_markup=kb)
        return
    if field == "listcfg":
        await show_plan_configs_list(update, context, plan_id)
        return
    if field == "category":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(CATEGORY_LABELS[c], callback_data=f"setcat_{plan_id}_{c}")] for c in PLAN_CATEGORIES])
        await query.edit_message_text("دسته جدید این پلن رو انتخاب کنید:", reply_markup=kb)
        return
    context.user_data['state'] = f"admin_edit_{field}"
    await query.edit_message_text(prompts[field], reply_markup=cancel_kb())


async def show_plan_configs_list(update, context, plan_id):
    query = update.callback_query
    conn = get_conn()
    cfgs = conn.execute("SELECT * FROM plan_configs WHERE plan_id=? AND used=0", (plan_id,)).fetchall()
    conn.close()
    if not cfgs:
        await query.edit_message_text("کانفیگ فعالی (استفاده‌نشده) برای این پلن وجود ندارد.", reply_markup=cancel_kb())
        return
    lines = [f"تعداد کانفیگ‌های موجود: {len(cfgs)}\n"]
    kb = []
    for c in cfgs:
        if c['content_type'] == 'text':
            preview = (c['text_content'] or "").strip().replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:60] + "…"
            lines.append(f"#{c['id']} 📝 — {preview}")
            kb.append([InlineKeyboardButton(f"حذف #{c['id']} 🗑", callback_data=f"delcfg_{c['id']}_{plan_id}")])
        else:
            lines.append(f"#{c['id']} 🖼 — عکس (برای دیدنش دکمه‌ی مشاهده رو بزن)")
            kb.append([
                InlineKeyboardButton(f"مشاهده #{c['id']} 👁", callback_data=f"viewcfg_{c['id']}"),
                InlineKeyboardButton(f"حذف #{c['id']} 🗑", callback_data=f"delcfg_{c['id']}_{plan_id}"),
            ])
    kb.append([InlineKeyboardButton("بستن ❌", callback_data="cancel_action")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_view_single_config(update, context, cfg_id):
    query = update.callback_query
    conn = get_conn()
    c = conn.execute("SELECT * FROM plan_configs WHERE id=?", (cfg_id,)).fetchone()
    conn.close()
    if not c:
        await query.answer("این کانفیگ دیگر وجود ندارد.", show_alert=True)
        return
    await query.answer()
    if c['content_type'] == 'photo':
        await context.bot.send_photo(update.effective_user.id, c['photo_file_id'], caption=c['caption'] or "")
    else:
        await context.bot.send_message(update.effective_user.id, c['text_content'])


@admin_only
async def admin_delete_single_config(update, context, cfg_id, plan_id):
    query = update.callback_query
    conn = get_conn()
    conn.execute("DELETE FROM plan_configs WHERE id=?", (cfg_id,))
    conn.commit()
    conn.close()
    await query.answer("کانفیگ حذف شد ✅", show_alert=True)
    await show_plan_configs_list(update, context, plan_id)


@admin_only
async def admin_set_plan_category(update, context, plan_id, category):
    query = update.callback_query
    if category not in PLAN_CATEGORIES:
        await query.answer("دسته نامعتبر است ❌", show_alert=True)
        return
    conn = get_conn()
    conn.execute("UPDATE plans SET category=? WHERE id=?", (category, plan_id))
    conn.commit()
    conn.close()
    await query.edit_message_text(f"✅ دسته‌ی این پلن به «{CATEGORY_LABELS.get(category, category)}» تغییر کرد.")


# ---------- حذف پلن ----------
@requires_permission("plans")
async def admin_del_plan_start(update, context):
    kb = [[InlineKeyboardButton(CATEGORY_LABELS[c], callback_data=f"delcat_{c}")] for c in PLAN_CATEGORIES]
    kb.append([InlineKeyboardButton("لغو ❌", callback_data="cancel_action")])
    await update.message.reply_text("حذف پلن — ابتدا دسته رو انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_del_plan_list_by_category(update, context, category):
    query = update.callback_query
    conn = get_conn()
    plans = conn.execute("SELECT * FROM plans WHERE is_test=0 AND category=?", (category,)).fetchall()
    conn.close()
    label = CATEGORY_LABELS.get(category, category)
    if not plans:
        await query.edit_message_text(f"هیچ پلنی در دسته «{label}» ثبت نشده است.", reply_markup=cancel_kb())
        return
    kb = [[InlineKeyboardButton(f"{p['title']} 🗑", callback_data=f"delplan_confirm_{p['id']}")] for p in plans]
    kb.append([InlineKeyboardButton("لغو ❌", callback_data="cancel_action")])
    await query.edit_message_text(f"{label}\nکدام پلن حذف شود؟", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_del_plan_confirm(update, context, plan_id):
    query = update.callback_query
    conn = get_conn()
    conn.execute("DELETE FROM plans WHERE id=?", (plan_id,))
    conn.execute("DELETE FROM plan_configs WHERE plan_id=?", (plan_id,))
    conn.commit()
    conn.close()
    await query.edit_message_text("✅ پلن و تمام کانفیگ‌های مرتبط با آن حذف شد. (ظرفیت به‌طور خودکار صفر شد)")


# ---------- افزودن کانفیگ تست ----------
@requires_permission("test")
async def admin_add_test_start(update, context):
    context.user_data['state'] = "admin_addtest_type"
    context.user_data['data'] = {}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("متنی 📝", callback_data="testtype_text"),
         InlineKeyboardButton("عکس + کپشن 🖼", callback_data="testtype_photo")]
    ])
    await update.message.reply_text("نوع کانفیگ تست رو انتخاب کنید:", reply_markup=kb)


# ---------- لیست کانفیگ‌های تست ----------
@requires_permission("test")
async def admin_list_test_configs(update, context):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM test_configs ORDER BY id DESC").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("هیچ کانفیگ تستی ثبت نشده است.")
        return
    lines = [f"🎁 کانفیگ‌های تست ({len(rows)} مورد):\n"]
    for r in rows:
        status = "✅ استفاده‌نشده" if not r['used'] else f"❌ استفاده‌شده (توسط {r['used_by']})"
        kind = "متنی 📝" if r['content_type'] == 'text' else "عکس 🖼"
        lines.append(f"#{r['id']} — {kind} — {status}")
    await update.message.reply_text("\n".join(lines))


# ---------- حذف کانفیگ تست ----------
@requires_permission("test")
async def admin_delete_test_start(update, context):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM test_configs WHERE used=0 ORDER BY id DESC").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("کانفیگ تست فعال (استفاده‌نشده)‌ای برای حذف وجود ندارد.")
        return
    kb = []
    for r in rows:
        kind = "متنی" if r['content_type'] == 'text' else "عکس"
        kb.append([InlineKeyboardButton(f"#{r['id']} ({kind}) 🗑", callback_data=f"deltest_{r['id']}")])
    kb.append([InlineKeyboardButton("بستن ❌", callback_data="cancel_action")])
    await update.message.reply_text("کدام کانفیگ تست حذف شود؟", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_delete_test_do(update, context, cfg_id):
    query = update.callback_query
    conn = get_conn()
    conn.execute("DELETE FROM test_configs WHERE id=?", (cfg_id,))
    conn.commit()
    conn.close()
    await query.answer("کانفیگ تست حذف شد ✅", show_alert=True)
    conn = get_conn()
    rows = conn.execute("SELECT * FROM test_configs WHERE used=0 ORDER BY id DESC").fetchall()
    conn.close()
    if not rows:
        await query.edit_message_text("کانفیگ تست فعال دیگری باقی نمانده است.")
        return
    kb = []
    for r in rows:
        kind = "متنی" if r['content_type'] == 'text' else "عکس"
        kb.append([InlineKeyboardButton(f"#{r['id']} ({kind}) 🗑", callback_data=f"deltest_{r['id']}")])
    kb.append([InlineKeyboardButton("بستن ❌", callback_data="cancel_action")])
    await query.edit_message_text("کدام کانفیگ تست حذف شود؟", reply_markup=InlineKeyboardMarkup(kb))


# ---------- ویرایش کانفیگ تست ----------
@requires_permission("test")
async def admin_edit_test_start(update, context):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM test_configs WHERE used=0 ORDER BY id DESC").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("کانفیگ تست فعال (استفاده‌نشده)‌ای برای ویرایش وجود ندارد.")
        return
    kb = []
    for r in rows:
        kind = "متنی" if r['content_type'] == 'text' else "عکس"
        kb.append([InlineKeyboardButton(f"#{r['id']} ({kind}) ✏️", callback_data=f"edittest_sel_{r['id']}")])
    kb.append([InlineKeyboardButton("بستن ❌", callback_data="cancel_action")])
    await update.message.reply_text("کدام کانفیگ تست ویرایش شود؟", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_edit_test_select(update, context, cfg_id):
    query = update.callback_query
    conn = get_conn()
    row = conn.execute("SELECT * FROM test_configs WHERE id=?", (cfg_id,)).fetchone()
    conn.close()
    if not row:
        await query.edit_message_text("این کانفیگ تست دیگر وجود ندارد.")
        return
    context.user_data['data'] = {"edit_test_id": cfg_id}
    if row['content_type'] == 'text':
        context.user_data['state'] = "admin_edittest_text"
        await query.edit_message_text("متن جدید کانفیگ تست رو ارسال کنید:")
    else:
        context.user_data['state'] = "admin_edittest_photo"
        await query.edit_message_text("عکس جدید + کپشن رو ارسال کنید (کپشن رو داخل خود عکس بنویسید):")


# ---------- لیست ادمین‌ها ----------
@admin_only
async def admin_list_admins(update, context):
    conn = get_conn()
    admins = conn.execute("SELECT * FROM admins").fetchall()
    owners = {r['user_id'] for r in conn.execute("SELECT user_id FROM owners").fetchall()}
    conn.close()
    if not admins:
        await update.message.reply_text("هیچ ادمینی ثبت نشده است.")
        return
    lines = ["👤 لیست ادمین‌های ربات:\n"]
    for a in admins:
        role = "👑 مالک اصلی" if a['user_id'] in owners else "🛠 ادمین"
        lines.append(f"• {a['user_id']} — {role}")
    await update.message.reply_text("\n".join(lines))


# ---------- تغییر شماره کارت ----------
@requires_permission("card")
async def admin_change_card_start(update, context):
    context.user_data['state'] = "admin_card_number"
    context.user_data['data'] = {}
    number, holder = get_card_info()
    await update.message.reply_text(
        f"شماره کارت فعلی: {number}\nبه نام: {holder or '—'}\n\nشماره کارت جدید رو ارسال کنید:",
        reply_markup=cancel_kb()
    )


# ---------- خاموش/روشن کردن ربات ----------
@requires_permission("maintenance")
async def admin_maintenance_toggle_start(update, context):
    if is_maintenance_on():
        set_maintenance(False)
        await broadcast_maintenance(context, "✅ ربات دوباره فعال شد و می‌توانید از آن استفاده کنید.")
        await update.message.reply_text("✅ ربات روشن شد و پیام اطلاع‌رسانی برای کاربران ارسال شد.")
    else:
        context.user_data['state'] = "admin_maintenance_off_text"
        context.user_data['data'] = {}
        await update.message.reply_text(
            "متنی که می‌خواهید قبل از خاموش شدن ربات برای همه کاربران ارسال شود رو بنویسید:",
            reply_markup=cancel_kb()
        )


async def broadcast_maintenance(context, text):
    conn = get_conn()
    users = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    for u in users:
        try:
            await context.bot.send_message(u['user_id'], text)
        except TelegramError:
            pass


# ---------- انتقال مالکیت ----------
@owner_only
async def admin_transfer_ownership_start(update, context):
    conn = get_conn()
    admins = conn.execute("SELECT * FROM admins").fetchall()
    conn.close()
    kb = []
    for a in admins:
        if is_owner(a['user_id']):
            continue
        kb.append([InlineKeyboardButton(f"{a['user_id']} ♻️", callback_data=f"transferown_{a['user_id']}")])
    if not kb:
        await update.message.reply_text("ادمین دیگری برای انتقال مالکیت به او وجود ندارد. اول یک ادمین اضافه کنید.")
        return
    kb.append([InlineKeyboardButton("لغو ❌", callback_data="cancel_action")])
    await update.message.reply_text(
        "⚠️ توجه: با انتقال مالکیت، شما ادمین معمولی می‌شوید و دیگر دسترسی مالک اصلی را نخواهید داشت.\n\nمالکیت به کدام ادمین منتقل شود؟",
        reply_markup=InlineKeyboardMarkup(kb)
    )


@owner_only
async def admin_transfer_ownership_confirm(update, context, target_id):
    query = update.callback_query
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("بله، مطمئنم ✅", callback_data=f"transferownok_{target_id}")],
        [InlineKeyboardButton("لغو ❌", callback_data="cancel_action")],
    ])
    await query.edit_message_text(
        f"⚠️ آیا مطمئنید می‌خواهید مالکیت ربات رو به کاربر {target_id} منتقل کنید؟\nاین کار قابل بازگشت نیست مگر اینکه مالک جدید دوباره شما را مالک کند.",
        reply_markup=kb
    )


@owner_only
async def admin_transfer_ownership_do(update, context, target_id):
    query = update.callback_query
    old_owner_id = update.effective_user.id
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (target_id,))
    conn.execute("INSERT OR IGNORE INTO owners(user_id) VALUES (?)", (target_id,))
    conn.execute("DELETE FROM owners WHERE user_id=?", (old_owner_id,))
    conn.commit()
    conn.close()
    await query.edit_message_text(f"✅ مالکیت ربات به کاربر {target_id} منتقل شد. شما اکنون ادمین معمولی هستید.")
    try:
        await context.bot.send_message(target_id, "🎉 مالکیت ربات به شما منتقل شد. شما اکنون مالک اصلی ربات هستید.")
    except TelegramError:
        pass


# ---------- گردونه شانس (مدیریت) ----------
@owner_only
async def admin_wheel_toggle(update, context):
    if is_wheel_enabled():
        set_wheel_enabled(False)
        await update.message.reply_text("🎡 گردونه شانس خاموش شد. دکمه گردونه دیگه برای کاربرا نمایش داده نمی‌شه.", reply_markup=admin_menu_kb(update.effective_user.id))
    else:
        set_wheel_enabled(True)
        prizes = get_wheel_prizes()
        if not prizes:
            await update.message.reply_text("✅ گردونه روشن شد. حالا باید جایزه‌هاش رو تنظیم کنی:")
            await start_wheel_prize_wizard(update, context)
        else:
            await update.message.reply_text("✅ گردونه شانس روشن شد و برای کاربرا نمایش داده می‌شه.", reply_markup=admin_menu_kb(update.effective_user.id))


@owner_only
async def admin_wheel_manage(update, context):
    prizes = get_wheel_prizes()
    settings = get_wheel_settings()
    status = "روشن ✅" if settings['enabled'] else "خاموش ❌"
    lines = [f"وضعیت گردونه: {status}", f"فاصله زمانی هر کاربر: هر {settings['cooldown_hours']} ساعت", f"تعداد جایزه‌ها: {len(prizes)}"]
    kb = [
        [InlineKeyboardButton("افزودن جایزه ➕", callback_data="wheel_addprize")],
        [InlineKeyboardButton("لیست/حذف جایزه‌ها 📋", callback_data="wheel_listprizes")],
        [InlineKeyboardButton("تغییر فاصله زمانی ⏱", callback_data="wheel_setcooldown")],
        [InlineKeyboardButton("بستن ❌", callback_data="cancel_action")],
    ]
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))


async def start_wheel_prize_wizard(update, context):
    context.user_data['state'] = "wheel_addprize_type"
    context.user_data['data'] = {}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(WHEEL_PRIZE_LABELS["discount"], callback_data="wheeltype_discount")],
        [InlineKeyboardButton(WHEEL_PRIZE_LABELS["config"], callback_data="wheeltype_config")],
        [InlineKeyboardButton(WHEEL_PRIZE_LABELS["respin"], callback_data="wheeltype_respin")],
        [InlineKeyboardButton(WHEEL_PRIZE_LABELS["none"], callback_data="wheeltype_none")],
        [InlineKeyboardButton("پایان ✅", callback_data="wheel_wizard_done")],
    ])
    text = "نوع جایزه بعدی رو انتخاب کن یا پایان بده:"
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)


@owner_only
async def cb_wheel_choose_type(update, context, ptype):
    query = update.callback_query
    d = context.user_data.setdefault('data', {})
    d['prize_type'] = ptype
    if ptype == "discount":
        context.user_data['state'] = "wheel_discount_percent"
        await query.edit_message_text("چند درصد تخفیف بده؟ (فقط عدد، مثلا 20):")
    elif ptype == "config":
        context.user_data['state'] = "wheel_config_gb"
        await query.edit_message_text("این کانفیگ رایگان چند گیگ باشه؟ (فقط عدد، مثلا 5):")
    else:
        context.user_data['state'] = "wheel_weight"
        await query.edit_message_text("شانس (احتمال) این جایزه چند درصد باشه؟ (فقط عدد، مثلا 30):")


@owner_only
async def cb_wheel_wizard_done(update, context):
    clear_state(context)
    prizes = get_wheel_prizes()
    total = sum(p['weight'] for p in prizes)
    lines = [f"✅ تنظیم جایزه‌ها تمام شد. تعداد کل: {len(prizes)}"]
    if total != 100:
        lines.append(f"⚠️ توجه: مجموع شانس‌ها {total}% است (بهتره مجموعاً ۱۰۰٪ باشه، ولی مشکلی هم نداره).")
    await update.callback_query.edit_message_text("\n".join(lines))


@owner_only
async def cb_wheel_list_prizes(update, context):
    query = update.callback_query
    prizes = get_wheel_prizes()
    if not prizes:
        await query.edit_message_text("هیچ جایزه‌ای تعریف نشده.", reply_markup=cancel_kb())
        return
    lines = ["📋 جایزه‌های گردونه:\n"]
    kb = []
    for p in prizes:
        if p['prize_type'] == "discount":
            desc = f"{WHEEL_PRIZE_LABELS['discount']} {p['discount_percent']}% ({p['discount_hours']} ساعته)"
        elif p['prize_type'] == "config":
            conn = get_conn()
            cnt = conn.execute("SELECT COUNT(*) c FROM wheel_prize_configs WHERE prize_id=? AND used=0", (p['id'],)).fetchone()['c']
            conn.close()
            desc = f"{WHEEL_PRIZE_LABELS['config']} {p['config_gb']:g} گیگ (موجودی: {cnt})"
        else:
            desc = WHEEL_PRIZE_LABELS.get(p['prize_type'], p['prize_type'])
        lines.append(f"#{p['id']} — {desc} — شانس: {p['weight']}%")
        row = [InlineKeyboardButton(f"حذف #{p['id']} 🗑", callback_data=f"wheeldelprize_{p['id']}")]
        if p['prize_type'] == "config":
            row.append(InlineKeyboardButton(f"افزودن کانفیگ #{p['id']} ➕", callback_data=f"wheeladdcfg_{p['id']}"))
        kb.append(row)
    kb.append([InlineKeyboardButton("بستن ❌", callback_data="cancel_action")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))


@owner_only
async def cb_wheel_delete_prize(update, context, prize_id):
    delete_wheel_prize(prize_id)
    await update.callback_query.answer("جایزه حذف شد ✅", show_alert=True)
    await cb_wheel_list_prizes(update, context)


@owner_only
async def cb_wheel_addcfg_start(update, context, prize_id):
    query = update.callback_query
    context.user_data['data'] = {"wheel_prize_id": prize_id}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("متنی 📝", callback_data="wheelcfgtype_text"),
         InlineKeyboardButton("عکس + کپشن 🖼", callback_data="wheelcfgtype_photo")],
        [InlineKeyboardButton("پایان ✅", callback_data="cancel_action")],
    ])
    await query.edit_message_text("کانفیگ این جایزه رو وارد کنید (نوعش رو انتخاب کنید):", reply_markup=kb)


@owner_only
async def cb_wheel_setcooldown_start(update, context):
    context.user_data['state'] = "wheel_cooldown_hours"
    await update.callback_query.edit_message_text("هر چند ساعت یک‌بار هر کاربر بتونه گردونه بزنه؟ (فقط عدد، مثلا 24):")



async def hourly_backup_job(context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(DB_PATH):
        return
    conn = get_conn()
    owners = conn.execute("SELECT user_id FROM owners").fetchall()
    conn.close()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    file_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for o in owners:
        try:
            with open(DB_PATH, "rb") as f:
                await context.bot.send_document(
                    o['user_id'], document=f, filename=f"autobackup_{file_ts}.db",
                    caption=f"💾 بکاپ خودکار ساعتی — {ts}"
                )
        except Exception as e:
            logger.warning(f"ارسال بکاپ خودکار به مالک {o['user_id']} ناموفق بود: {e}")


@owner_only
async def admin_backup_start(update, context):
    if not os.path.exists(DB_PATH):
        await update.message.reply_text("فایل دیتابیس پیدا نشد ❌")
        return
    await update.message.reply_text("⏳ در حال آماده‌سازی فایل بکاپ...")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{ts}.db"
    try:
        with open(DB_PATH, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=backup_name,
                caption="💾 فایل بکاپ کامل دیتابیس ربات.\nاین فایل رو یه جای امن نگه‌دار — برای بازگردانی، از دکمه «بازگردانی از بکاپ 📥» استفاده کن."
            )
    except Exception as e:
        await update.message.reply_text(f"خطا در ارسال بکاپ: {e}")


@owner_only
async def admin_restore_start(update, context):
    context.user_data['state'] = "admin_restore_wait_file"
    context.user_data['data'] = {}
    await update.message.reply_text(
        "⚠️ توجه: با ارسال فایل بکاپ، تمام اطلاعات فعلی ربات (کاربران، پلن‌ها، سفارش‌ها، کیف پول‌ها و...) با اطلاعات داخل فایل جایگزین می‌شه و قابل بازگشت نیست.\n\n"
        "فایل بکاپ (.db) رو ارسال کنید:",
        reply_markup=cancel_kb()
    )


async def document_router(update, context):
    user = update.effective_user
    if not is_owner(user.id):
        return
    if context.user_data.get('state') != "admin_restore_wait_file":
        return
    doc = update.message.document
    if not doc:
        return
    await update.message.reply_text("⏳ در حال دریافت و بررسی فایل...")
    tmp_path = DB_PATH + ".restore_tmp"
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(tmp_path)
    except Exception as e:
        await update.message.reply_text(f"خطا در دریافت فایل: {e}")
        return
    try:
        with open(tmp_path, "rb") as f:
            header = f.read(16)
        if not header.startswith(b"SQLite format 3"):
            os.remove(tmp_path)
            await update.message.reply_text("❌ فایل ارسالی یک فایل دیتابیس معتبر (sqlite) نیست.")
            clear_state(context)
            return
    except Exception as e:
        await update.message.reply_text(f"خطا در بررسی فایل: {e}")
        return
    try:
        if os.path.exists(DB_PATH):
            os.replace(DB_PATH, DB_PATH + ".before_restore.bak")
        os.replace(tmp_path, DB_PATH)
    except Exception as e:
        await update.message.reply_text(f"خطا در جایگزینی فایل: {e}")
        return
    clear_state(context)
    await update.message.reply_text(
        "✅ اطلاعات با موفقیت بازگردانی شد.\n"
        "پیشنهاد می‌کنیم برای اطمینان کامل، یک بار ربات رو ری‌استارت کنید (نسخه‌ی قبل از بازگردانی هم با پسوند .before_restore.bak کنارش نگه داشته شده)."
    )


# ---------- ساخت کد تخفیف ----------
@requires_permission("discount")
async def admin_add_discount_start(update, context):
    context.user_data['state'] = "admin_discount_code"
    context.user_data['data'] = {}
    await update.message.reply_text("کد تخفیف رو ارسال کنید (مثلا OFF10):", reply_markup=cancel_kb())


# ---------- باطل کردن کد تخفیف ----------
@requires_permission("discount")
async def admin_revoke_discount_start(update, context):
    conn = get_conn()
    codes = conn.execute("SELECT * FROM discount_codes WHERE active=1").fetchall()
    conn.close()
    if not codes:
        await update.message.reply_text("هیچ کد تخفیف فعالی وجود ندارد.")
        return
    kb = []
    for c in codes:
        used_txt = f"{c['used_count']}/{c['max_uses'] if c['max_uses'] != -1 else '∞'}"
        kb.append([InlineKeyboardButton(f"باطل کردن {c['code']} (مصرف: {used_txt}) ❌", callback_data=f"revokecode_{c['code']}")])
    kb.append([InlineKeyboardButton("بستن", callback_data="cancel_action")])
    await update.message.reply_text("کدام کد تخفیف باطل شود؟", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_revoke_discount_do(update, context, code):
    query = update.callback_query
    conn = get_conn()
    conn.execute("UPDATE discount_codes SET active=0 WHERE code=?", (code,))
    conn.commit()
    conn.close()
    await query.edit_message_text(f"✅ کد تخفیف «{code}» باطل شد.")


# ---------- لیست کاربران (با کانفیگ‌ها و لینک تماس) ----------
@requires_permission("users")
async def admin_show_users(update, context):
    conn = get_conn()
    users = conn.execute("SELECT * FROM users ORDER BY joined_at DESC").fetchall()
    conn.close()
    if not users:
        await update.message.reply_text("هیچ کاربری ثبت نشده است.")
        return
    await update.message.reply_text(f"👥 تعداد کل کاربران: {len(users)}")
    for u in users:
        conn = get_conn()
        orders = conn.execute("SELECT * FROM orders WHERE user_id=? AND status='تحویل شد'", (u['user_id'],)).fetchall()
        conn.close()
        status = "🚫 مسدود" if u['banned'] else "✅ فعال"
        text = (f"آیدی: {u['user_id']}\nیوزرنیم: @{u['username'] or '-'}\n"
                f"کیف پول: {u['wallet']:,} تومان\nوضعیت: {status}\nتعداد خرید موفق: {len(orders)}")
        kb_rows = []
        if orders:
            kb_rows.append([InlineKeyboardButton("مشاهده کانفیگ‌های خریداری‌شده 📦", callback_data=f"viewuserorders_{u['user_id']}")])
        kb_rows.append([InlineKeyboardButton("مشاهده کاربر 💬", url=f"tg://user?id={u['user_id']}")])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))


@admin_only
async def admin_view_user_orders(update, context, target_id):
    query = update.callback_query
    conn = get_conn()
    orders = conn.execute("SELECT * FROM orders WHERE user_id=? AND status='تحویل شد' ORDER BY id DESC", (target_id,)).fetchall()
    conn.close()
    if not orders:
        await query.answer("این کاربر کانفیگ خریداری‌شده‌ای ندارد.", show_alert=True)
        return
    lines = [f"📦 کانفیگ‌های خریداری‌شده کاربر {target_id}:\n"]
    for o in orders:
        expires = datetime.fromisoformat(o['expires_at']) if o['expires_at'] else None
        remain_txt = "-"
        if expires:
            remaining = expires - datetime.now()
            remain_txt = f"{remaining.days} روز مانده" if remaining.total_seconds() > 0 else "منقضی شده"
        lines.append(f"• {o['plan_title']} | {o['price']:,} تومان | {remain_txt}")
    await context.bot.send_message(query.message.chat_id, "\n".join(lines))
    await query.answer()


# ---------- پیام مستقیم ----------
@requires_permission("users")
async def admin_direct_start(update, context):
    context.user_data['state'] = "admin_direct_userid"
    context.user_data['data'] = {}
    await update.message.reply_text("آیدی عددی کاربر مورد نظر رو ارسال کنید:", reply_markup=cancel_kb())


# ---------- پیام همگانی ----------
@requires_permission("broadcast")
async def admin_broadcast_start(update, context):
    context.user_data['state'] = "admin_broadcast_text"
    context.user_data['data'] = {}
    await update.message.reply_text("متن پیام همگانی رو ارسال کنید (برای همه کاربران ارسال می‌شود):", reply_markup=cancel_kb())


# ---------- شارژ دستی کیف پول (افزایش) ----------
@requires_permission("wallet")
async def admin_chargewallet_start(update, context):
    context.user_data['state'] = "admin_chargewallet_userid"
    context.user_data['data'] = {}
    await update.message.reply_text("آیدی عددی کاربر مورد نظر رو ارسال کنید:", reply_markup=cancel_kb())


# ---------- کسر از کیف پول ----------
@requires_permission("wallet")
async def admin_decrease_wallet_start(update, context):
    context.user_data['state'] = "admin_decwallet_userid"
    context.user_data['data'] = {}
    await update.message.reply_text("آیدی عددی کاربری که می‌خواهید از کیف پولش کسر کنید رو ارسال کنید:", reply_markup=cancel_kb())


# ---------- مسدودسازی (با دلیل) ----------
@requires_permission("users")
async def admin_ban_start(update, context):
    context.user_data['state'] = "admin_ban_userid"
    context.user_data['data'] = {}
    await update.message.reply_text("آیدی عددی کاربری که می‌خواهید مسدود کنید رو ارسال کنید:", reply_markup=cancel_kb())


@requires_permission("users")
async def admin_unban_list(update, context):
    conn = get_conn()
    banned = conn.execute("SELECT * FROM users WHERE banned=1").fetchall()
    conn.close()
    if not banned:
        await update.message.reply_text("هیچ کاربر مسدودی وجود ندارد.")
        return
    kb = [[InlineKeyboardButton(f"رفع مسدودی {u['user_id']} ✅", callback_data=f"unban_{u['user_id']}")] for u in banned]
    kb.append([InlineKeyboardButton("بستن ❌", callback_data="cancel_action")])
    await update.message.reply_text("کاربران مسدود:", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_unban_do(update, context, target_id):
    query = update.callback_query
    conn = get_conn()
    conn.execute("UPDATE users SET banned=0, ban_reason=NULL WHERE user_id=?", (target_id,))
    conn.commit()
    conn.close()
    await context.bot.send_message(target_id, "حالا می‌توانید از ربات استفاده کنید✅")
    await query.answer("رفع مسدودی انجام شد ✅", show_alert=True)
    conn = get_conn()
    banned = conn.execute("SELECT * FROM users WHERE banned=1").fetchall()
    conn.close()
    if not banned:
        await query.edit_message_text("هیچ کاربر مسدودی باقی نمانده است.")
        return
    kb = [[InlineKeyboardButton(f"رفع مسدودی {u['user_id']} ✅", callback_data=f"unban_{u['user_id']}")] for u in banned]
    kb.append([InlineKeyboardButton("بستن ❌", callback_data="cancel_action")])
    await query.edit_message_text("کاربران مسدود:", reply_markup=InlineKeyboardMarkup(kb))


# ---------- افزودن ادمین (با انتخاب از لیست کاربران + دسترسی‌ها) ----------
@requires_permission("admins")
async def admin_add_admin_start(update, context):
    conn = get_conn()
    users = conn.execute("SELECT * FROM users ORDER BY joined_at DESC LIMIT 30").fetchall()
    admin_ids = {r['user_id'] for r in conn.execute("SELECT user_id FROM admins").fetchall()}
    conn.close()
    candidates = [u for u in users if u['user_id'] not in admin_ids]
    kb = []
    for u in candidates:
        label = f"@{u['username']}" if u['username'] else str(u['user_id'])
        kb.append([InlineKeyboardButton(f"{label} 👤", callback_data=f"selnewadmin_{u['user_id']}")])
    kb.append([InlineKeyboardButton("لغو ❌", callback_data="cancel_action")])
    text = "یکی از کاربران رو برای ادمین کردن انتخاب کنید،" if candidates else "کاربر تازه‌ای (که ادمین نباشه) پیدا نشد."
    text += "\nیا آیدی عددی رو مستقیم بفرستید:"
    context.user_data['state'] = "admin_add_admin_id"
    context.user_data['data'] = {}
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def start_permission_toggle(update, context, target_id, is_new):
    actor_id = update.effective_user.id
    if is_owner(actor_id):
        allowed = list(PERMISSIONS.keys())
    else:
        allowed = [p for p in PERMISSIONS.keys() if has_permission(actor_id, p)]
    context.user_data['state'] = None
    context.user_data['data'] = {
        "perm_target": target_id,
        "perm_is_new": is_new,
        "perm_allowed": allowed,
        "perm_selected": set() if is_new else set(get_admin_permissions(target_id)),
    }
    await render_permission_toggle(update, context)


async def render_permission_toggle(update, context):
    d = context.user_data.get('data', {})
    target_id = d['perm_target']
    allowed = d['perm_allowed']
    selected = d['perm_selected']
    kb = []
    for p in allowed:
        mark = "✅" if p in selected else "❌"
        kb.append([InlineKeyboardButton(f"{mark} {PERMISSIONS[p]}", callback_data=f"permtoggle_{p}")])
    kb.append([InlineKeyboardButton("ذخیره ✅", callback_data="permsave")])
    kb.append([InlineKeyboardButton("لغو ❌", callback_data="cancel_action")])
    title = "دسترسی‌های ادمین جدید" if d['perm_is_new'] else "ویرایش دسترسی‌های ادمین"
    text = f"{title} (کاربر {target_id}):\nروی هرکدوم بزنید تا فعال/غیرفعال بشه."
    if not allowed:
        text += "\n\n⚠️ شما خودتون هیچ دسترسی‌ای برای واگذاری ندارید."
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def cb_permtoggle(update, context, perm):
    d = context.user_data.get('data', {})
    if 'perm_target' not in d or perm not in d.get('perm_allowed', []):
        await update.callback_query.answer("این عملیات منقضی شده یا دسترسی ندارید، دوباره تلاش کنید.", show_alert=True)
        return
    if perm in d['perm_selected']:
        d['perm_selected'].discard(perm)
    else:
        d['perm_selected'].add(perm)
    await render_permission_toggle(update, context)


async def cb_permsave(update, context):
    query = update.callback_query
    d = context.user_data.get('data', {})
    if 'perm_target' not in d:
        await query.answer("این عملیات منقضی شده، دوباره تلاش کنید.", show_alert=True)
        return
    target_id = d['perm_target']
    is_new = d['perm_is_new']
    selected = d['perm_selected']
    if is_new:
        conn = get_conn()
        conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (target_id,))
        conn.execute("UPDATE users SET banned=0, ban_reason=NULL WHERE user_id=?", (target_id,))
        conn.commit()
        conn.close()
    set_admin_permissions(target_id, selected)
    clear_state(context)
    await query.edit_message_text(f"✅ دسترسی‌های کاربر {target_id} ذخیره شد.")
    try:
        if is_new:
            await context.bot.send_message(target_id, "🎉 شما توسط ادمین ربات به عنوان ادمین اضافه شدید.")
        else:
            await context.bot.send_message(target_id, "🔄 دسترسی‌های ادمین شما بروزرسانی شد.")
    except TelegramError:
        pass


# ---------- ویرایش دسترسی ادمین‌ها (فقط مالک) ----------
@owner_only
async def admin_edit_permissions_start(update, context):
    conn = get_conn()
    admins = conn.execute("SELECT * FROM admins").fetchall()
    conn.close()
    kb = []
    for a in admins:
        if is_owner(a['user_id']):
            continue
        kb.append([InlineKeyboardButton(f"{a['user_id']} 🔑", callback_data=f"editadminperm_{a['user_id']}")])
    if not kb:
        await update.message.reply_text("ادمین دیگری (غیر از مالک) برای ویرایش دسترسی وجود ندارد.")
        return
    kb.append([InlineKeyboardButton("لغو ❌", callback_data="cancel_action")])
    await update.message.reply_text("دسترسی‌های کدام ادمین رو می‌خواهید ویرایش کنید؟", reply_markup=InlineKeyboardMarkup(kb))


@owner_only
async def admin_del_admin_start(update, context):
    conn = get_conn()
    admins = conn.execute("SELECT * FROM admins").fetchall()
    conn.close()
    kb = []
    for a in admins:
        if is_owner(a['user_id']):
            continue
        kb.append([InlineKeyboardButton(f"حذف {a['user_id']} 🗑", callback_data=f"removeadmin_{a['user_id']}")])
    if not kb:
        await update.message.reply_text("ادمین دیگری (غیر از مالک اصلی) برای حذف وجود ندارد.")
        return
    kb.append([InlineKeyboardButton("بستن ❌", callback_data="cancel_action")])
    await update.message.reply_text("کدام ادمین حذف شود؟", reply_markup=InlineKeyboardMarkup(kb))


@owner_only
async def admin_del_admin_do(update, context, target_id):
    query = update.callback_query
    if is_owner(target_id):
        await query.answer("امکان حذف مالک اصلی وجود ندارد ❌", show_alert=True)
        return
    conn = get_conn()
    conn.execute("DELETE FROM admins WHERE user_id=?", (target_id,))
    conn.execute("DELETE FROM admin_permissions WHERE user_id=?", (target_id,))
    conn.commit()
    conn.close()
    try:
        await context.bot.send_message(target_id, "دسترسی ادمین شما توسط مالک ربات حذف شد.")
    except TelegramError:
        pass
    await query.edit_message_text(f"✅ ادمین {target_id} حذف شد.")


# ---------- کانال‌های اجباری ----------
@requires_permission("channels")
async def admin_addchannel_start(update, context):
    context.user_data['state'] = "admin_addchannel_username"
    context.user_data['data'] = {}
    await update.message.reply_text(
        "یوزرنیم کانال رو بدون @ ارسال کنید (مثلا mychannel).\n"
        "⚠️ ربات باید ادمین آن کانال باشد تا بتواند عضویت را چک کند.",
        reply_markup=cancel_kb()
    )


@requires_permission("channels")
async def admin_delchannel_start(update, context):
    channels = get_required_channels()
    if not channels:
        await update.message.reply_text("هیچ کانال اجباری‌ای ثبت نشده است.")
        return
    kb = [[InlineKeyboardButton(f"{c['channel_title']} 🗑", callback_data=f"delchannel_{c['id']}")] for c in channels]
    kb.append([InlineKeyboardButton("بستن ❌", callback_data="cancel_action")])
    await update.message.reply_text("کدام کانال حذف شود؟", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_delchannel_do(update, context, channel_id):
    query = update.callback_query
    conn = get_conn()
    conn.execute("DELETE FROM required_channels WHERE id=?", (channel_id,))
    conn.commit()
    conn.close()
    await query.edit_message_text("✅ کانال حذف شد.")


# ================= روتر کلی دکمه‌های شیشه‌ای مدیریت =================
async def admin_callback_router(update, context, data):
    if data.startswith("editplan_sel_"):
        await admin_edit_plan_menu(update, context, int(data.split("_")[2]))
    elif data.startswith("editfield_"):
        parts = data.split("_")
        field, plan_id = parts[1], int(parts[2])
        await admin_edit_field_start(update, context, field, plan_id)
    elif data.startswith("delcfg_"):
        parts = data.split("_")
        await admin_delete_single_config(update, context, int(parts[1]), int(parts[2]))
    elif data.startswith("viewcfg_"):
        await admin_view_single_config(update, context, int(data.split("_")[1]))
    elif data.startswith("delplan_confirm_"):
        await admin_del_plan_confirm(update, context, int(data.split("_")[2]))
    elif data.startswith("revokecode_"):
        await admin_revoke_discount_do(update, context, data.split("_", 1)[1])
    elif data.startswith("viewuserorders_"):
        await admin_view_user_orders(update, context, int(data.split("_")[1]))
    elif data.startswith("unban_"):
        await admin_unban_do(update, context, int(data.split("_")[1]))
    elif data.startswith("removeadmin_"):
        await admin_del_admin_do(update, context, int(data.split("_")[1]))
    elif data.startswith("delchannel_"):
        await admin_delchannel_do(update, context, int(data.split("_")[1]))
    elif data.startswith("newplancat_"):
        await admin_new_plan_finish(update, context, data[len("newplancat_"):])
    elif data.startswith("editcat_"):
        await admin_edit_plan_list_by_category(update, context, data[len("editcat_"):])
    elif data.startswith("delcat_"):
        await admin_del_plan_list_by_category(update, context, data[len("delcat_"):])
    elif data.startswith("setcat_"):
        rest = data[len("setcat_"):]
        plan_id_str, category = rest.split("_", 1)
        await admin_set_plan_category(update, context, int(plan_id_str), category)
    elif data.startswith("deltest_"):
        await admin_delete_test_do(update, context, int(data.split("_")[1]))
    elif data.startswith("edittest_sel_"):
        await admin_edit_test_select(update, context, int(data.split("_")[2]))
    elif data.startswith("selnewadmin_"):
        target_id = int(data.split("_")[1])
        if is_admin(target_id):
            await update.callback_query.answer("این کاربر از قبل ادمین است.", show_alert=True)
            return
        await start_permission_toggle(update, context, target_id, is_new=True)
    elif data.startswith("editadminperm_"):
        target_id = int(data.split("_")[1])
        await start_permission_toggle(update, context, target_id, is_new=False)
    elif data.startswith("permtoggle_"):
        await cb_permtoggle(update, context, data[len("permtoggle_"):])
    elif data == "permsave":
        await cb_permsave(update, context)
    elif data.startswith("transferownok_"):
        await admin_transfer_ownership_do(update, context, int(data.split("_")[1]))
    elif data.startswith("transferown_"):
        await admin_transfer_ownership_confirm(update, context, int(data.split("_")[1]))
    elif data == "cfgtype_text":
        context.user_data['state'] = "admin_addcfg_text"
        await update.callback_query.edit_message_text("متن کانفیگ رو ارسال کنید:", reply_markup=cancel_kb())
    elif data == "cfgtype_photo":
        context.user_data['state'] = "admin_addcfg_photo"
        await update.callback_query.edit_message_text("عکس + کپشن (متن دلخواه) رو ارسال کنید:", reply_markup=cancel_kb())
    elif data == "bulkcfg_done":
        await cb_bulkcfg_done(update, context)
    elif data == "wheel_addprize":
        await start_wheel_prize_wizard(update, context)
    elif data.startswith("wheeltype_"):
        await cb_wheel_choose_type(update, context, data[len("wheeltype_"):])
    elif data == "wheel_wizard_done":
        await cb_wheel_wizard_done(update, context)
    elif data == "wheel_wizard_continue":
        clear_state(context)
        await update.callback_query.edit_message_text("✅ رد شد.")
        await start_wheel_prize_wizard(update, context)
    elif data == "wheel_listprizes":
        await cb_wheel_list_prizes(update, context)
    elif data.startswith("wheeldelprize_"):
        await cb_wheel_delete_prize(update, context, int(data.split("_")[1]))
    elif data.startswith("wheeladdcfg_"):
        await cb_wheel_addcfg_start(update, context, int(data.split("_")[1]))
    elif data == "wheelcfgtype_text":
        context.user_data['state'] = "wheel_addcfg_text"
        await update.callback_query.edit_message_text("متن کانفیگ رو ارسال کنید:", reply_markup=cancel_kb())
    elif data == "wheelcfgtype_photo":
        context.user_data['state'] = "wheel_addcfg_photo"
        await update.callback_query.edit_message_text("عکس + کپشن رو ارسال کنید:", reply_markup=cancel_kb())
    elif data == "wheel_setcooldown":
        await cb_wheel_setcooldown_start(update, context)
    elif data == "wheel_spin":
        await cb_wheel_spin(update, context)
    elif data == "testtype_text":
        context.user_data['state'] = "admin_addtest_text"
        await update.callback_query.edit_message_text("متن کانفیگ تست رو ارسال کنید:", reply_markup=cancel_kb())
    elif data == "testtype_photo":
        context.user_data['state'] = "admin_addtest_photo"
        await update.callback_query.edit_message_text("عکس + کپشن کانفیگ تست رو ارسال کنید:", reply_markup=cancel_kb())


# ================= هندل متن‌های چندمرحله‌ای ادمین =================
async def handle_admin_text_states(update, context, state, text) -> bool:
    if not is_admin(update.effective_user.id):
        return False
    d = context.user_data.get('data', {})

    if state == "admin_new_plan_title":
        d['title'] = text
        context.user_data['state'] = "admin_new_plan_price"
        await update.message.reply_text("قیمت پلن رو به تومان ارسال کنید (فقط عدد):")
        return True

    if state == "admin_new_plan_price":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط عدد ارسال کنید:")
            return True
        d['price'] = int(text)
        context.user_data['state'] = "admin_new_plan_duration"
        await update.message.reply_text("مدت اعتبار این پلن رو به روز ارسال کنید (فقط عدد، مثلا 30):")
        return True

    if state == "admin_new_plan_duration":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط عدد ارسال کنید:")
            return True
        d['duration'] = int(text)
        context.user_data['state'] = "admin_new_plan_category"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(CATEGORY_LABELS[c], callback_data=f"newplancat_{c}")] for c in PLAN_CATEGORIES])
        await update.message.reply_text("این پلن مربوط به کدوم دسته است؟ 👇", reply_markup=kb)
        return True

    if state == "admin_edit_title":
        conn = get_conn()
        conn.execute("UPDATE plans SET title=? WHERE id=?", (text, d['edit_plan_id']))
        conn.commit()
        conn.close()
        clear_state(context)
        await update.message.reply_text("✅ عنوان بروزرسانی شد.")
        return True

    if state == "admin_edit_price":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط عدد ارسال کنید:")
            return True
        conn = get_conn()
        conn.execute("UPDATE plans SET price=? WHERE id=?", (int(text), d['edit_plan_id']))
        conn.commit()
        conn.close()
        clear_state(context)
        await update.message.reply_text("✅ قیمت بروزرسانی شد.")
        return True

    if state == "admin_edit_duration":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط عدد ارسال کنید:")
            return True
        conn = get_conn()
        conn.execute("UPDATE plans SET duration_days=? WHERE id=?", (int(text), d['edit_plan_id']))
        conn.commit()
        conn.close()
        clear_state(context)
        await update.message.reply_text("✅ مدت اعتبار بروزرسانی شد.")
        return True

    if state == "wheel_discount_percent":
        if not text.isdigit() or not (1 <= int(text) <= 100):
            await update.message.reply_text("لطفا عددی بین ۱ تا ۱۰۰ ارسال کنید:")
            return True
        d['discount_percent'] = int(text)
        context.user_data['state'] = "wheel_discount_hours"
        await update.message.reply_text("این کد تخفیف چند ساعت اعتبار داشته باشه؟ (فقط عدد، مثلا 3):")
        return True

    if state == "wheel_discount_hours":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("لطفا فقط عدد معتبر ارسال کنید:")
            return True
        d['discount_hours'] = int(text)
        context.user_data['state'] = "wheel_weight"
        await update.message.reply_text("شانس (احتمال) این جایزه چند درصد باشه؟ (فقط عدد، مثلا 30):")
        return True

    if state == "wheel_config_gb":
        try:
            gb = float(text.strip().replace(",", "."))
            if gb <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("لطفا فقط عدد معتبر بزرگتر از صفر ارسال کنید:")
            return True
        d['config_gb'] = gb
        context.user_data['state'] = "wheel_weight"
        await update.message.reply_text("شانس (احتمال) این جایزه چند درصد باشه؟ (فقط عدد، مثلا 30):")
        return True

    if state == "wheel_weight":
        if not text.isdigit() or not (1 <= int(text) <= 100):
            await update.message.reply_text("لطفا عددی بین ۱ تا ۱۰۰ ارسال کنید:")
            return True
        weight = int(text)
        ptype = d.get('prize_type')
        prize_id = add_wheel_prize(
            ptype, weight,
            discount_percent=d.get('discount_percent'),
            discount_hours=d.get('discount_hours'),
            config_gb=d.get('config_gb'),
        )
        clear_state(context)
        await update.message.reply_text(f"✅ جایزه «{WHEEL_PRIZE_LABELS.get(ptype, ptype)}» با شانس {weight}% اضافه شد.")
        if ptype == "config":
            await update.message.reply_text("حالا کانفیگ‌های واقعی این جایزه رو وارد کن (یا از منوی مدیریت جایزه‌ها بعداً اضافه‌شون کن):")
            context.user_data['data'] = {"wheel_prize_id": prize_id}
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("متنی 📝", callback_data="wheelcfgtype_text"),
                 InlineKeyboardButton("عکس + کپشن 🖼", callback_data="wheelcfgtype_photo")],
                [InlineKeyboardButton("رد شدن ⏭", callback_data="wheel_wizard_continue")],
            ])
            await update.message.reply_text("نوع کانفیگ رو انتخاب کنید:", reply_markup=kb)
        else:
            await start_wheel_prize_wizard(update, context)
        return True

    if state == "wheel_cooldown_hours":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("لطفا فقط عدد معتبر ارسال کنید:")
            return True
        set_wheel_cooldown(int(text))
        clear_state(context)
        await update.message.reply_text(f"✅ فاصله زمانی هر کاربر روی {text} ساعت تنظیم شد.")
        return True

    if state == "wheel_addcfg_text":
        conn = get_conn()
        conn.execute("INSERT INTO wheel_prize_configs(prize_id, content_type, text_content) VALUES (?,?,?)",
                     (d['wheel_prize_id'], 'text', text))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ اضافه شد.")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("متنی 📝", callback_data="wheelcfgtype_text"),
             InlineKeyboardButton("عکس + کپشن 🖼", callback_data="wheelcfgtype_photo")],
            [InlineKeyboardButton("پایان ✅", callback_data="cancel_action")],
        ])
        await update.message.reply_text("کانفیگ بعدی یا پایان:", reply_markup=kb)
        return True

    if state == "admin_card_number":
        d['new_card_number'] = text.strip()
        context.user_data['state'] = "admin_card_holder"
        await update.message.reply_text("این شماره کارت به نام چه کسی است؟ (نام و نام خانوادگی رو ارسال کنید):", reply_markup=cancel_kb())
        return True

    if state == "admin_card_holder":
        holder = text.strip()
        set_card_info(d['new_card_number'], holder)
        clear_state(context)
        await update.message.reply_text(f"✅ شماره کارت بروزرسانی شد:\n💳 {d['new_card_number']}\n👤 به نام: {holder}")
        if is_owner(update.effective_user.id):
            await update.message.reply_text("🛠 پنل مدیریت — یکی از گزینه‌ها رو انتخاب کنید:", reply_markup=admin_menu_kb(update.effective_user.id))
        return True

    if state == "admin_maintenance_off_text":
        maintenance_text = text
        set_maintenance(True)
        clear_state(context)
        await update.message.reply_text("⏳ در حال ارسال پیام به همه کاربران...")
        await broadcast_maintenance(context, maintenance_text)
        await update.message.reply_text("🔴 ربات خاموش شد. کاربران عادی تا روشن‌شدن دوباره نمی‌توانند از ربات استفاده کنند.")
        return True

    if state == "admin_addcfg_text":
        conn = get_conn()
        conn.execute("INSERT INTO plan_configs(plan_id, content_type, text_content) VALUES (?,?,?)",
                     (d['edit_plan_id'], 'text', text))
        conn.commit()
        conn.close()
        if d.get('bulk_add'):
            d['bulk_count'] = d.get('bulk_count', 0) + 1
            await update.message.reply_text("✅ اضافه شد.")
            await prompt_next_bulk_config(update, context)
        else:
            clear_state(context)
            await update.message.reply_text("✅ کانفیگ متنی به پلن اضافه شد. (ظرفیت +۱)")
        return True

    if state == "admin_addtest_text":
        conn = get_conn()
        conn.execute("INSERT INTO test_configs(content_type, text_content) VALUES ('text', ?)", (text,))
        conn.commit()
        conn.close()
        clear_state(context)
        await update.message.reply_text("✅ کانفیگ تست متنی اضافه شد.")
        return True

    if state == "admin_edittest_text":
        conn = get_conn()
        conn.execute("UPDATE test_configs SET text_content=? WHERE id=?", (text, d['edit_test_id']))
        conn.commit()
        conn.close()
        clear_state(context)
        await update.message.reply_text("✅ کانفیگ تست متنی ویرایش شد.")
        return True

    if state == "admin_discount_code":
        d['code'] = text.strip()
        context.user_data['state'] = "admin_discount_amount"
        await update.message.reply_text("چقدر از قیمت کم شود؟ (به تومان، فقط عدد):")
        return True

    if state == "admin_discount_amount":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط عدد ارسال کنید:")
            return True
        d['amount'] = int(text)
        context.user_data['state'] = "admin_discount_days"
        await update.message.reply_text("چند روز اعتبار داشته باشد؟ (فقط عدد، برای نامحدود عدد 0 را بفرستید):")
        return True

    if state == "admin_discount_days":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط عدد ارسال کنید:")
            return True
        days = int(text)
        d['expires_at'] = "" if days == 0 else (datetime.now() + timedelta(days=days)).isoformat()
        context.user_data['state'] = "admin_discount_uses"
        await update.message.reply_text("برای چند نفر کار کند؟ (عدد بفرستید، یا کلمه نامحدود را ارسال کنید):")
        return True

    if state == "admin_discount_uses":
        if text.strip() == "نامحدود":
            max_uses = -1
        elif text.isdigit():
            max_uses = int(text)
        else:
            await update.message.reply_text("لطفا فقط عدد یا کلمه «نامحدود» ارسال کنید:")
            return True
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO discount_codes(code, amount, expires_at, max_uses, used_count, active) VALUES (?,?,?,?,0,1)",
                     (d['code'], d['amount'], d['expires_at'], max_uses))
        conn.commit()
        conn.close()
        clear_state(context)
        await update.message.reply_text(f"✅ کد تخفیف «{d['code']}» ساخته شد.\nمبلغ کاهش: {d['amount']:,} تومان")
        return True

    if state == "admin_direct_userid":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط آیدی عددی معتبر ارسال کنید:")
            return True
        d['target_id'] = int(text)
        context.user_data['state'] = "admin_direct_message"
        await update.message.reply_text("متن پیام رو ارسال کنید:")
        return True

    if state == "admin_direct_message":
        try:
            await context.bot.send_message(d['target_id'], text)
            await update.message.reply_text("✅ پیام ارسال شد.")
        except TelegramError as e:
            await update.message.reply_text(f"خطا در ارسال پیام: {e}")
        clear_state(context)
        return True

    if state == "admin_broadcast_text":
        conn = get_conn()
        users = conn.execute("SELECT user_id FROM users WHERE banned=0").fetchall()
        conn.close()
        sent, failed = 0, 0
        for u in users:
            try:
                await context.bot.send_message(u['user_id'], text)
                sent += 1
            except TelegramError:
                failed += 1
        await update.message.reply_text(f"✅ پیام همگانی ارسال شد.\nموفق: {sent} | ناموفق: {failed}")
        clear_state(context)
        return True

    if state == "admin_chargewallet_userid":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط آیدی عددی معتبر ارسال کنید:")
            return True
        d['target_id'] = int(text)
        context.user_data['state'] = "admin_chargewallet_amount"
        await update.message.reply_text(f"موجودی فعلی این کاربر: {get_wallet(d['target_id']):,} تومان\nمبلغ شارژ رو به تومان ارسال کنید (فقط عدد):")
        return True

    if state == "admin_chargewallet_amount":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط عدد ارسال کنید:")
            return True
        amount = int(text)
        change_wallet(d['target_id'], amount)
        try:
            await context.bot.send_message(d['target_id'], f"کیف پول شما از طرف مالک به مبلغ {amount:,} تومان شارژ شد.")
        except TelegramError:
            pass
        await update.message.reply_text(f"✅ کیف پول کاربر {d['target_id']} به مبلغ {amount:,} تومان شارژ شد.")
        clear_state(context)
        return True

    if state == "admin_decwallet_userid":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط آیدی عددی معتبر ارسال کنید:")
            return True
        d['target_id'] = int(text)
        context.user_data['state'] = "admin_decwallet_amount"
        await update.message.reply_text(f"موجودی فعلی این کاربر: {get_wallet(d['target_id']):,} تومان\nچقدر کم شود؟ (فقط عدد):")
        return True

    if state == "admin_decwallet_amount":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط عدد ارسال کنید:")
            return True
        amount = int(text)
        change_wallet(d['target_id'], -amount)
        # طبق درخواست، به کاربر پیامی مبنی بر کسر موجودی ارسال نمی‌شود
        await update.message.reply_text(f"✅ مبلغ {amount:,} تومان از کیف پول کاربر {d['target_id']} کسر شد.\nموجودی جدید: {get_wallet(d['target_id']):,} تومان")
        clear_state(context)
        return True

    if state == "admin_ban_userid":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط آیدی عددی معتبر ارسال کنید:")
            return True
        target_id = int(text)
        if is_admin(target_id):
            clear_state(context)
            await update.message.reply_text("⛔️ امکان مسدود کردن ادمین‌ها و مالک ربات وجود ندارد.")
            return True
        d['target_id'] = target_id
        context.user_data['state'] = "admin_ban_reason"
        await update.message.reply_text("دلیل مسدود کردن این کاربر رو ارسال کنید:")
        return True

    if state == "admin_ban_reason":
        target_id = d['target_id']
        conn = get_conn()
        conn.execute("INSERT OR IGNORE INTO users(user_id, wallet, banned) VALUES (?,0,1)", (target_id,))
        conn.execute("UPDATE users SET banned=1, ban_reason=? WHERE user_id=?", (text, target_id))
        conn.commit()
        conn.close()
        try:
            await context.bot.send_message(target_id, f"کاربر محترم شما از طرف ادمین مسدود شده اید ❌\nدلیل: {text}")
        except TelegramError:
            pass
        await update.message.reply_text(f"✅ کاربر {target_id} مسدود شد.\nدلیل ثبت‌شده: {text}")
        clear_state(context)
        return True

    if state == "admin_add_admin_id":
        if not text.isdigit():
            await update.message.reply_text("لطفا فقط آیدی عددی معتبر ارسال کنید:")
            return True
        new_admin_id = int(text)
        if is_admin(new_admin_id):
            clear_state(context)
            await update.message.reply_text("این کاربر از قبل ادمین است.")
            return True
        await start_permission_toggle(update, context, new_admin_id, is_new=True)
        return True

    if state == "admin_addchannel_username":
        username = text.strip().lstrip("@")
        try:
            chat = await context.bot.get_chat(f"@{username}")
            title = chat.title or username
        except TelegramError:
            title = username
        conn = get_conn()
        conn.execute("INSERT INTO required_channels(channel_username, channel_title) VALUES (?,?)", (username, title))
        conn.commit()
        conn.close()
        clear_state(context)
        await update.message.reply_text(f"✅ کانال «{title}» به لیست عضویت اجباری اضافه شد.\n⚠️ کاربرانی که قبلا عضو ربات بودن، از الان مجبورن این کانال جدید رو هم عضو بشن.")
        return True

    if state == "admin_reply_ticket_text":
        ticket_id = d['ticket_id']
        conn = get_conn()
        ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        conn.execute("UPDATE tickets SET status='پاسخ داده شد' WHERE id=?", (ticket_id,))
        conn.commit()
        conn.close()
        if ticket:
            try:
                await context.bot.send_message(ticket['user_id'], f"📩 پاسخ پشتیبانی:\n{text}")
            except TelegramError:
                pass
        await update.message.reply_text("✅ پاسخ برای کاربر ارسال شد.")
        clear_state(context)
        return True

    return False


# ================= هندل عکس‌های ادمین (افزودن کانفیگ عکسی) =================
async def handle_admin_photo_states(update, context, state, photo_file_id):
    if not is_admin(update.effective_user.id):
        return
    d = context.user_data.get('data', {})
    caption = update.message.caption or ""

    if state == "admin_addcfg_photo":
        conn = get_conn()
        conn.execute("INSERT INTO plan_configs(plan_id, content_type, photo_file_id, caption) VALUES (?,?,?,?)",
                     (d['edit_plan_id'], 'photo', photo_file_id, caption))
        conn.commit()
        conn.close()
        if d.get('bulk_add'):
            d['bulk_count'] = d.get('bulk_count', 0) + 1
            await update.message.reply_text("✅ اضافه شد.")
            await prompt_next_bulk_config(update, context)
        else:
            clear_state(context)
            await update.message.reply_text("✅ کانفیگ عکسی به پلن اضافه شد. (ظرفیت +۱)")
        return

    if state == "wheel_addcfg_photo":
        conn = get_conn()
        conn.execute("INSERT INTO wheel_prize_configs(prize_id, content_type, photo_file_id, caption) VALUES (?,?,?,?)",
                     (d['wheel_prize_id'], 'photo', photo_file_id, caption))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ اضافه شد.")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("متنی 📝", callback_data="wheelcfgtype_text"),
             InlineKeyboardButton("عکس + کپشن 🖼", callback_data="wheelcfgtype_photo")],
            [InlineKeyboardButton("پایان ✅", callback_data="cancel_action")],
        ])
        await update.message.reply_text("کانفیگ بعدی یا پایان:", reply_markup=kb)
        return

    if state == "admin_addtest_photo":
        conn = get_conn()
        conn.execute("INSERT INTO test_configs(content_type, photo_file_id, caption) VALUES ('photo', ?, ?)",
                     (photo_file_id, caption))
        conn.commit()
        conn.close()
        clear_state(context)
        await update.message.reply_text("✅ کانفیگ تست عکسی اضافه شد.")
        return

    if state == "admin_edittest_photo":
        conn = get_conn()
        conn.execute("UPDATE test_configs SET photo_file_id=?, caption=? WHERE id=?",
                     (photo_file_id, caption, d['edit_test_id']))
        conn.commit()
        conn.close()
        clear_state(context)
        await update.message.reply_text("✅ کانفیگ تست عکسی ویرایش شد.")
        return


# ================= main =================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, photo_router))
    app.add_handler(MessageHandler(filters.Document.ALL, document_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(CallbackQueryHandler(callback_router))

    if app.job_queue is not None:
        app.job_queue.run_repeating(hourly_backup_job, interval=3600, first=3600)
    else:
        logger.warning("job_queue فعال نیست — برای بکاپ خودکار باید pip install \"python-telegram-bot[job-queue]\" رو اجرا کنید.")

    logger.info("ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
