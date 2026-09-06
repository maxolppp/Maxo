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

# ================= تنظیمات (از Environment Variables خونده می‌شه) =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
CARD_NUMBER = os.environ.get("CARD_NUMBER", "6219861351165898")
DB_PATH = os.environ.get("DB_PATH", "bot.db")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN تنظیم نشده! آن را در Environment Variables (تنظیمات Railway) وارد کنید.")
if not ADMIN_IDS:
    raise RuntimeError("❌ ADMIN_IDS تنظیم نشده! حداقل یک آیدی عددی ادمین (مالک اصلی) در Environment Variables وارد کنید.")

# اگه DB_PATH داخل یه پوشه باشه (مثلا /data/bot.db روی ولیوم ریلوی) پوشه رو بساز
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)
# ================================================================

BACK_BTN = "🔙 بازگشت"

PLAN_CATEGORIES = ["حجمی", "نامحدود"]
CATEGORY_LABELS = {
    "حجمی": "📊 حجمی",
    "نامحدود": "♾ نامحدود",
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
        config_id INTEGER, created_at TEXT, expires_at TEXT, is_renew INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS wallet_charges(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, amount INTEGER, status TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, user_msg TEXT, status TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS discount_codes(
        code TEXT PRIMARY KEY, amount INTEGER, expires_at TEXT,
        max_uses INTEGER, used_count INTEGER DEFAULT 0, active INTEGER DEFAULT 1
    )""")
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


def get_plan_duration(plan_id):
    conn = get_conn()
    r = conn.execute("SELECT duration_days FROM plans WHERE id=?", (plan_id,)).fetchone()
    conn.close()
    return r['duration_days'] if r else 30


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
        kb.append([InlineKeyboardButton(f"📢 عضویت در {ch['channel_title']}", url=f"https://t.me/{ch['channel_username']}")])
    kb.append([InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data="check_join")])
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
        ["🛒 خرید کانفیگ", "🎁 دریافت تست"],
        ["💰 کیف پول", "📦 کانفیگ های من"],
        ["📞 پیام به پشتیبانی"]
    ]
    if is_admin(user_id):
        rows.append(["🛠 مدیریت"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_menu_kb(user_id):
    rows = [
        ["➕ افزودن پلن", "✏️ ویرایش پلن"],
        ["🗑 حذف پلن", "🎁 افزودن کانفیگ تست"],
        ["📋 لیست کانفیگ تست", "✏️ ویرایش کانفیگ تست"],
        ["🗑 حذف کانفیگ تست"],
        ["👥 کاربران", "📢 پیام همگانی"],
        ["✉️ پیام مستقیم", "💳 شارژ کیف پول"],
        ["➖ کسر از کیف پول", "🚫 مسدود کردن"],
        ["✅ رفع مسدودی", "🏷 کد تخفیف جدید"],
        ["❌ باطل کردن کد تخفیف"],
        ["➕ کانال اجباری", "🗑 حذف کانال اجباری"],
        ["📋 لیست ادمین‌ها"],
    ]
    if is_owner(user_id):
        rows.append(["👤 افزودن ادمین", "🗑 حذف ادمین"])
    rows.append([BACK_BTN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_action")]])


ADMIN_MENU_TEXTS = {
    "➕ افزودن پلن", "✏️ ویرایش پلن", "🗑 حذف پلن", "🎁 افزودن کانفیگ تست",
    "📋 لیست کانفیگ تست", "✏️ ویرایش کانفیگ تست", "🗑 حذف کانفیگ تست",
    "👥 کاربران", "📢 پیام همگانی", "✉️ پیام مستقیم", "💳 شارژ کیف پول",
    "➖ کسر از کیف پول", "🚫 مسدود کردن", "✅ رفع مسدودی", "🏷 کد تخفیف جدید",
    "❌ باطل کردن کد تخفیف", "👤 افزودن ادمین", "🗑 حذف ادمین",
    "➕ کانال اجباری", "🗑 حذف کانال اجباری", "📋 لیست ادمین‌ها",
}

MAIN_MENU_TEXTS = {
    "🛒 خرید کانفیگ", "🎁 دریافت تست", "💰 کیف پول", "📦 کانفیگ های من",
    "📞 پیام به پشتیبانی", "🛠 مدیریت",
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
    if not is_admin(user.id) and text not in ("🛠 مدیریت",):
        missing = await get_missing_channels(context, user.id)
        if missing:
            await send_join_prompt(update, context, missing)
            return

    # ---------- منوی مدیریت (اگه ادمینه و دکمه‌های پنل رو زده) ----------
    if is_admin(user.id) and text in ADMIN_MENU_TEXTS:
        await admin_menu_dispatch(update, context, text)
        return

    # ---------- منوی اصلی ----------
    if text == "🛒 خرید کانفیگ":
        await ask_buy_category(update, context)
    elif text == "🎁 دریافت تست":
        await handle_get_test(update, context)
    elif text == "💰 کیف پول":
        await show_wallet(update, context)
    elif text == "📦 کانفیگ های من":
        await show_my_configs(update, context)
    elif text == "📞 پیام به پشتیبانی":
        context.user_data['state'] = "waiting_support_msg"
        await update.message.reply_text("لطفا پیام خودتون رو برای پشتیبانی بنویسید و ارسال کنید:")
    elif text == "🛠 مدیریت":
        if is_admin(user.id):
            await update.message.reply_text("🛠 پنل مدیریت — یکی از گزینه‌ها رو انتخاب کنید:", reply_markup=admin_menu_kb(user.id))
        else:
            await update.message.reply_text("شما دسترسی ادمین ندارید ❌")
    else:
        if not state:
            await update.message.reply_text("لطفا از دکمه‌های منو استفاده کنید 👇", reply_markup=main_menu_kb(user.id))


# ============================================================
#                 خرید کانفیگ / تست / کیف پول
# ============================================================

async def ask_buy_category(update, context):
    kb = [[InlineKeyboardButton(CATEGORY_LABELS[c], callback_data=f"buycat_{c}")] for c in PLAN_CATEGORIES]
    kb.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel_action")])
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
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_action")])
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
        [InlineKeyboardButton("✅ دارم", callback_data="discount_yes"),
         InlineKeyboardButton("❌ ندارم", callback_data="discount_no")]
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
        [InlineKeyboardButton("💰 کیف پول", callback_data="pay_wallet"),
         InlineKeyboardButton("💳 کارت به کارت", callback_data="pay_card")]
    ])
    txt = f"مبلغ قابل پرداخت: {price:,} تومان\nروش خرید رو انتخاب کنید:"
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, reply_markup=kb)
    else:
        await update.message.reply_text(txt, reply_markup=kb)


async def cb_pay_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
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
    else:
        await query.edit_message_text("ظرفیت این پلن تمام شده است ❌")
    clear_state(context)


async def cb_pay_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data['state'] = "waiting_receipt_photo"
    await query.edit_message_text(
        f"لطفا مبلغ ({context.user_data['data'].get('final_price', 0):,} تومان) رو به شماره کارت زیر واریز کنید و رسید رو ارسال کنید:\n\n"
        f"💳 {CARD_NUMBER}\n\nلطفا رسید (عکس) رو برای ربات ارسال کنید 🧾♥️"
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
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 شارژ کیف پول", callback_data="charge_wallet")]])
    await update.message.reply_text(f"💰 موجودی کیف پول شما: {balance:,} تومان", reply_markup=kb)


async def cb_charge_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data['state'] = "waiting_charge_amount"
    await query.edit_message_text("مبلغ مورد نظر برای شارژ کیف پول رو به تومان وارد کنید (مثلا 100000):")


async def show_my_configs(update, context):
    user_id = update.effective_user.id
    conn = get_conn()
    orders = conn.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
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
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تمدید این پلن", callback_data=f"renewplan_{o['plan_id']}")]])
        await update.message.reply_text(
            f"📦 پلن: {o['plan_title']}\n💵 قیمت: {o['price']:,} تومان\nوضعیت: {status}\n⏳ {remain_txt}",
            reply_markup=kb
        )


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
            [InlineKeyboardButton("✅ تایید رسید", callback_data=f"approve_order_{order_id}"),
             InlineKeyboardButton("❌ رد رسید", callback_data=f"reject_order_{order_id}")]
        ])
        caption = (f"🧾 رسید جدید\nکاربر: {user.id} (@{user.username or '-'})\n"
                   f"پلن: {d['plan_title']}\nمبلغ: {d.get('final_price', d['price']):,} تومان")
        for admin_id in get_all_admin_ids():
            await context.bot.send_photo(admin_id, photo_file_id, caption=caption, reply_markup=kb)

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
            [InlineKeyboardButton("✅ تایید شارژ", callback_data=f"approve_charge_{charge_id}"),
             InlineKeyboardButton("❌ رد شارژ", callback_data=f"reject_charge_{charge_id}")]
        ])
        caption = f"🧾 درخواست شارژ کیف پول\nکاربر: {user.id} (@{user.username or '-'})\nمبلغ: {d['charge_amount']:,} تومان"
        for admin_id in get_all_admin_ids():
            await context.bot.send_photo(admin_id, photo_file_id, caption=caption, reply_markup=kb)

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
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ ارسال پاسخ به کاربر", callback_data=f"reply_ticket_{ticket_id}")]])
        for admin_id in get_all_admin_ids():
            await context.bot.send_message(
                admin_id,
                f"📩 پیام پشتیبانی جدید\nکاربر: {user_id}\nپیام: {text}",
                reply_markup=kb
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
        d['discount_amount'] = row['amount']
        await ask_payment_method(update, context)
        return True

    if state == "waiting_charge_amount":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("لطفا فقط عدد معتبر وارد کنید (مثلا 100000):")
            return True
        context.user_data['data'] = {"charge_amount": int(text)}
        context.user_data['state'] = "waiting_wallet_receipt_photo"
        await update.message.reply_text(
            f"لطفا مبلغ {int(text):,} تومان رو به شماره کارت زیر واریز کنید و رسید رو ارسال کنید:\n\n"
            f"💳 {CARD_NUMBER}\n\nلطفا رسید (عکس) رو ارسال کنید 🧾♥️"
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


async def admin_menu_dispatch(update, context, text):
    mapping = {
        "➕ افزودن پلن": admin_add_plan_start,
        "✏️ ویرایش پلن": admin_edit_plan_start,
        "🗑 حذف پلن": admin_del_plan_start,
        "🎁 افزودن کانفیگ تست": admin_add_test_start,
        "📋 لیست کانفیگ تست": admin_list_test_configs,
        "✏️ ویرایش کانفیگ تست": admin_edit_test_start,
        "🗑 حذف کانفیگ تست": admin_delete_test_start,
        "📋 لیست ادمین‌ها": admin_list_admins,
        "👥 کاربران": admin_show_users,
        "📢 پیام همگانی": admin_broadcast_start,
        "✉️ پیام مستقیم": admin_direct_start,
        "💳 شارژ کیف پول": admin_chargewallet_start,
        "➖ کسر از کیف پول": admin_decrease_wallet_start,
        "🚫 مسدود کردن": admin_ban_start,
        "✅ رفع مسدودی": admin_unban_list,
        "🏷 کد تخفیف جدید": admin_add_discount_start,
        "❌ باطل کردن کد تخفیف": admin_revoke_discount_start,
        "👤 افزودن ادمین": admin_add_admin_start,
        "🗑 حذف ادمین": admin_del_admin_start,
        "➕ کانال اجباری": admin_addchannel_start,
        "🗑 حذف کانال اجباری": admin_delchannel_start,
    }
    func = mapping.get(text)
    if func:
        await func(update, context)


# ---------- افزودن پلن ----------
@admin_only
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
    conn.execute("INSERT INTO plans(title, price, duration_days, is_test, category) VALUES (?,?,?,0,?)",
                 (d['title'], d['price'], d['duration'], category))
    conn.commit()
    conn.close()
    clear_state(context)
    await query.edit_message_text(
        f"✅ پلن «{d['title']}» ({CATEGORY_LABELS.get(category, category)}) با قیمت {d['price']:,} تومان و "
        f"{d['duration']} روز اعتبار ساخته شد.\nحالا از منوی ویرایش پلن، کانفیگ به آن اضافه کنید."
    )


# ---------- ویرایش پلن ----------
@admin_only
async def admin_edit_plan_start(update, context):
    kb = [[InlineKeyboardButton(CATEGORY_LABELS[c], callback_data=f"editcat_{c}")] for c in PLAN_CATEGORIES]
    kb.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_action")])
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
    kb.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_action")])
    await query.edit_message_text(f"{label}\nکدام پلن رو می‌خواهید ویرایش کنید؟", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_edit_plan_menu(update, context, plan_id):
    query = update.callback_query
    context.user_data['data'] = {"edit_plan_id": plan_id}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تغییر عنوان", callback_data=f"editfield_title_{plan_id}")],
        [InlineKeyboardButton("💵 تغییر قیمت", callback_data=f"editfield_price_{plan_id}")],
        [InlineKeyboardButton("📅 تغییر مدت اعتبار", callback_data=f"editfield_duration_{plan_id}")],
        [InlineKeyboardButton("🏷 تغییر دسته", callback_data=f"editfield_category_{plan_id}")],
        [InlineKeyboardButton("➕ افزودن کانفیگ به این پلن", callback_data=f"editfield_addcfg_{plan_id}")],
        [InlineKeyboardButton("📋 مشاهده/حذف کانفیگ‌های این پلن", callback_data=f"editfield_listcfg_{plan_id}")],
        [InlineKeyboardButton("❌ لغو", callback_data="cancel_action")],
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
            [InlineKeyboardButton("📝 متنی", callback_data="cfgtype_text"),
             InlineKeyboardButton("🖼 عکس + کپشن", callback_data="cfgtype_photo")]
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
    kb = []
    for c in cfgs:
        label = f"#{c['id']} - {'متنی' if c['content_type']=='text' else 'عکس'}"
        kb.append([InlineKeyboardButton(f"🗑 حذف {label}", callback_data=f"delcfg_{c['id']}_{plan_id}")])
    kb.append([InlineKeyboardButton("❌ بستن", callback_data="cancel_action")])
    await query.edit_message_text(f"تعداد کانفیگ‌های موجود: {len(cfgs)}\nبرای حذف روی دکمه بزنید:", reply_markup=InlineKeyboardMarkup(kb))


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
@admin_only
async def admin_del_plan_start(update, context):
    kb = [[InlineKeyboardButton(CATEGORY_LABELS[c], callback_data=f"delcat_{c}")] for c in PLAN_CATEGORIES]
    kb.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_action")])
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
    kb = [[InlineKeyboardButton(f"🗑 {p['title']}", callback_data=f"delplan_confirm_{p['id']}")] for p in plans]
    kb.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_action")])
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
@admin_only
async def admin_add_test_start(update, context):
    context.user_data['state'] = "admin_addtest_type"
    context.user_data['data'] = {}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 متنی", callback_data="testtype_text"),
         InlineKeyboardButton("🖼 عکس + کپشن", callback_data="testtype_photo")]
    ])
    await update.message.reply_text("نوع کانفیگ تست رو انتخاب کنید:", reply_markup=kb)


# ---------- لیست کانفیگ‌های تست ----------
@admin_only
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
@admin_only
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
        kb.append([InlineKeyboardButton(f"🗑 #{r['id']} ({kind})", callback_data=f"deltest_{r['id']}")])
    kb.append([InlineKeyboardButton("❌ بستن", callback_data="cancel_action")])
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
        kb.append([InlineKeyboardButton(f"🗑 #{r['id']} ({kind})", callback_data=f"deltest_{r['id']}")])
    kb.append([InlineKeyboardButton("❌ بستن", callback_data="cancel_action")])
    await query.edit_message_text("کدام کانفیگ تست حذف شود؟", reply_markup=InlineKeyboardMarkup(kb))


# ---------- ویرایش کانفیگ تست ----------
@admin_only
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
        kb.append([InlineKeyboardButton(f"✏️ #{r['id']} ({kind})", callback_data=f"edittest_sel_{r['id']}")])
    kb.append([InlineKeyboardButton("❌ بستن", callback_data="cancel_action")])
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


# ---------- ساخت کد تخفیف ----------
@admin_only
async def admin_add_discount_start(update, context):
    context.user_data['state'] = "admin_discount_code"
    context.user_data['data'] = {}
    await update.message.reply_text("کد تخفیف رو ارسال کنید (مثلا OFF10):", reply_markup=cancel_kb())


# ---------- باطل کردن کد تخفیف ----------
@admin_only
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
        kb.append([InlineKeyboardButton(f"❌ باطل کردن {c['code']} (مصرف: {used_txt})", callback_data=f"revokecode_{c['code']}")])
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
@admin_only
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
            kb_rows.append([InlineKeyboardButton("📦 مشاهده کانفیگ‌های خریداری‌شده", callback_data=f"viewuserorders_{u['user_id']}")])
        kb_rows.append([InlineKeyboardButton("💬 مشاهده کاربر", url=f"tg://user?id={u['user_id']}")])
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
@admin_only
async def admin_direct_start(update, context):
    context.user_data['state'] = "admin_direct_userid"
    context.user_data['data'] = {}
    await update.message.reply_text("آیدی عددی کاربر مورد نظر رو ارسال کنید:", reply_markup=cancel_kb())


# ---------- پیام همگانی ----------
@admin_only
async def admin_broadcast_start(update, context):
    context.user_data['state'] = "admin_broadcast_text"
    context.user_data['data'] = {}
    await update.message.reply_text("متن پیام همگانی رو ارسال کنید (برای همه کاربران ارسال می‌شود):", reply_markup=cancel_kb())


# ---------- شارژ دستی کیف پول (افزایش) ----------
@admin_only
async def admin_chargewallet_start(update, context):
    context.user_data['state'] = "admin_chargewallet_userid"
    context.user_data['data'] = {}
    await update.message.reply_text("آیدی عددی کاربر مورد نظر رو ارسال کنید:", reply_markup=cancel_kb())


# ---------- کسر از کیف پول ----------
@admin_only
async def admin_decrease_wallet_start(update, context):
    context.user_data['state'] = "admin_decwallet_userid"
    context.user_data['data'] = {}
    await update.message.reply_text("آیدی عددی کاربری که می‌خواهید از کیف پولش کسر کنید رو ارسال کنید:", reply_markup=cancel_kb())


# ---------- مسدودسازی (با دلیل) ----------
@admin_only
async def admin_ban_start(update, context):
    context.user_data['state'] = "admin_ban_userid"
    context.user_data['data'] = {}
    await update.message.reply_text("آیدی عددی کاربری که می‌خواهید مسدود کنید رو ارسال کنید:", reply_markup=cancel_kb())


@admin_only
async def admin_unban_list(update, context):
    conn = get_conn()
    banned = conn.execute("SELECT * FROM users WHERE banned=1").fetchall()
    conn.close()
    if not banned:
        await update.message.reply_text("هیچ کاربر مسدودی وجود ندارد.")
        return
    kb = [[InlineKeyboardButton(f"✅ رفع مسدودی {u['user_id']}", callback_data=f"unban_{u['user_id']}")] for u in banned]
    kb.append([InlineKeyboardButton("❌ بستن", callback_data="cancel_action")])
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
    kb = [[InlineKeyboardButton(f"✅ رفع مسدودی {u['user_id']}", callback_data=f"unban_{u['user_id']}")] for u in banned]
    kb.append([InlineKeyboardButton("❌ بستن", callback_data="cancel_action")])
    await query.edit_message_text("کاربران مسدود:", reply_markup=InlineKeyboardMarkup(kb))


# ---------- افزودن / حذف ادمین ----------
@owner_only
async def admin_add_admin_start(update, context):
    context.user_data['state'] = "admin_add_admin_id"
    context.user_data['data'] = {}
    await update.message.reply_text("آیدی عددی کاربری که می‌خواهید ادمین شود رو ارسال کنید:", reply_markup=cancel_kb())


@owner_only
async def admin_del_admin_start(update, context):
    conn = get_conn()
    admins = conn.execute("SELECT * FROM admins").fetchall()
    conn.close()
    kb = []
    for a in admins:
        if is_owner(a['user_id']):
            continue
        kb.append([InlineKeyboardButton(f"🗑 حذف {a['user_id']}", callback_data=f"removeadmin_{a['user_id']}")])
    if not kb:
        await update.message.reply_text("ادمین دیگری (غیر از مالک اصلی) برای حذف وجود ندارد.")
        return
    kb.append([InlineKeyboardButton("❌ بستن", callback_data="cancel_action")])
    await update.message.reply_text("کدام ادمین حذف شود؟", reply_markup=InlineKeyboardMarkup(kb))


@owner_only
async def admin_del_admin_do(update, context, target_id):
    query = update.callback_query
    if is_owner(target_id):
        await query.answer("امکان حذف مالک اصلی وجود ندارد ❌", show_alert=True)
        return
    conn = get_conn()
    conn.execute("DELETE FROM admins WHERE user_id=?", (target_id,))
    conn.commit()
    conn.close()
    try:
        await context.bot.send_message(target_id, "دسترسی ادمین شما توسط مالک ربات حذف شد.")
    except TelegramError:
        pass
    await query.edit_message_text(f"✅ ادمین {target_id} حذف شد.")


# ---------- کانال‌های اجباری ----------
@admin_only
async def admin_addchannel_start(update, context):
    context.user_data['state'] = "admin_addchannel_username"
    context.user_data['data'] = {}
    await update.message.reply_text(
        "یوزرنیم کانال رو بدون @ ارسال کنید (مثلا mychannel).\n"
        "⚠️ ربات باید ادمین آن کانال باشد تا بتواند عضویت را چک کند.",
        reply_markup=cancel_kb()
    )


@admin_only
async def admin_delchannel_start(update, context):
    channels = get_required_channels()
    if not channels:
        await update.message.reply_text("هیچ کانال اجباری‌ای ثبت نشده است.")
        return
    kb = [[InlineKeyboardButton(f"🗑 {c['channel_title']}", callback_data=f"delchannel_{c['id']}")] for c in channels]
    kb.append([InlineKeyboardButton("❌ بستن", callback_data="cancel_action")])
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
    elif data == "cfgtype_text":
        context.user_data['state'] = "admin_addcfg_text"
        await update.callback_query.edit_message_text("متن کانفیگ رو ارسال کنید:", reply_markup=cancel_kb())
    elif data == "cfgtype_photo":
        context.user_data['state'] = "admin_addcfg_photo"
        await update.callback_query.edit_message_text("عکس + کپشن (متن دلخواه) رو ارسال کنید:", reply_markup=cancel_kb())
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

    if state == "admin_addcfg_text":
        conn = get_conn()
        conn.execute("INSERT INTO plan_configs(plan_id, content_type, text_content) VALUES (?,?,?)",
                     (d['edit_plan_id'], 'text', text))
        conn.commit()
        conn.close()
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
        d['target_id'] = int(text)
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
        conn = get_conn()
        conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (new_admin_id,))
        conn.commit()
        conn.close()
        try:
            await context.bot.send_message(new_admin_id, "🎉 شما توسط مالک ربات به عنوان ادمین اضافه شدید.")
        except TelegramError:
            pass
        await update.message.reply_text(f"✅ کاربر {new_admin_id} به عنوان ادمین اضافه شد.")
        clear_state(context)
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
        clear_state(context)
        await update.message.reply_text("✅ کانفیگ عکسی به پلن اضافه شد. (ظرفیت +۱)")
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(CallbackQueryHandler(callback_router))

    logger.info("ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
