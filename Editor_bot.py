import sqlite3
import json
from datetime import datetime

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ==================================================
# تنظیمات ربات
# ==================================================

TOKEN = "8829613268:AAHHZW-VZJEyzewwiMKad5yacq1hbOHG2vY"
ADMIN_ID = 1405044119  # شناسه عددی تلگرام خودت
ADMIN_USERNAME = "soheil_316"  # نام کاربری پشتیبانی، بدون @

bot = telebot.TeleBot(TOKEN)
DB_NAME = "bot_data.db"

# وضعیت موقت مراحل ثبت‌نام و سفارش
states = {}


# ==================================================
# دیتابیس
# ==================================================

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                full_name TEXT,
                username TEXT,
                order_type TEXT,
                sample_text TEXT,
                sample_msg_id INTEGER,
                item_msg_ids TEXT,
                status TEXT DEFAULT 'در انتظار انجام',
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                full_name TEXT,
                comment TEXT,
                created_at TEXT
            )
        """)


def get_user(tg_id):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.execute(
            "SELECT * FROM users WHERE tg_id = ?",
            (tg_id,)
        ).fetchone()


def get_order(order_id):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()


def is_admin(tg_id):
    return tg_id == ADMIN_ID


# ==================================================
# منوها
# ==================================================

def make_menu(rows):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(*[
        InlineKeyboardButton(text, callback_data=data)
        for text, data in rows
    ])
    return markup


def main_menu(tg_id):
    rows = [
        ("🎧 پشتیبانی", "support"),
        ("✨ خدمات ربات", "services"),
    ]

    if is_admin(tg_id):
        rows.append(("⚙️ پنل مدیریت", "admin"))

    return make_menu(rows)


def admin_menu():
    return make_menu([
        ("👥 کاربران ثبت‌نام‌شده", "admin_users"),
        ("📦 درخواست‌های کاربران", "admin_orders"),
        ("📤 ارسال ادیت نهایی", "admin_send"),
        ("💬 نظرات کاربران", "admin_comments"),
        ("🔙 منوی اصلی", "home"),
    ])


def services_menu():
    return make_menu([
        ("🎬 ادیت ویدئو", "video"),
        ("🖼 ادیت عکس", "photo"),
        ("🎨 طراحی لوگو", "logo"),
        ("🔙 بازگشت", "home"),
    ])


def support_menu():
    return make_menu([
        ("📞 تماس با پشتیبانی", "contact"),
        ("💬 پشتیبانی آنلاین", "online_support"),
        ("📝 نظر شما", "opinion"),
        ("🔙 بازگشت", "home"),
    ])


def order_menu():
    return make_menu([
        ("➕ افزودن فایل دیگر", "add_file"),
        ("✅ ثبت درخواست", "submit_order"),
        ("❌ لغو سفارش", "cancel_order"),
    ])


# ==================================================
# شروع و ثبت‌نام
# ==================================================

@bot.message_handler(commands=["start"])
def start(message):
    uid = message.from_user.id

    if is_admin(uid):
        bot.send_message(
            uid,
            "سلام مدیر عزیز! 👋\nبه پنل مدیریت ربات خوش آمدی.",
            reply_markup=admin_menu()
        )
        return

    user = get_user(uid)

    if user:
        bot.send_message(
            uid,
            f"سلام {user[1]} عزیز! 👋\nبه ربات خدمات ادیت خوش آمدی.",
            reply_markup=main_menu(uid)
        )
        return

    states[uid] = {"step": "name"}

    bot.send_message(
        uid,
        "سلام! 👋 به ربات خدمات ادیت خوش آمدی.\n\n"
        "برای شروع، لطفاً نام و نام خانوادگی خودت را وارد کن."
    )


# ==================================================
# مدیریت دکمه‌ها
# ==================================================

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    uid = call.from_user.id
    data = call.data

    bot.answer_callback_query(call.id)

    # منوها
    if data == "home":
        states.pop(uid, None)
        bot.send_message(
            uid, "🏠 منوی اصلی:",
            reply_markup=main_menu(uid)
        )

    elif data == "services":
        bot.send_message(
            uid, "✨ یکی از خدمات زیر را انتخاب کن:",
            reply_markup=services_menu()
        )

    elif data == "support":
        bot.send_message(
            uid, "🎧 بخش پشتیبانی:",
            reply_markup=support_menu()
        )

    # شروع سفارش
    elif data in ("video", "photo", "logo"):
        order_types = {
            "video": "ادیت ویدئو",
            "photo": "ادیت عکس",
            "logo": "طراحی لوگو",
        }

        states[uid] = {
            "step": "sample",
            "order_type": order_types[data],
            "sample_text": "",
            "sample_msg_id": None,
            "item_msg_ids": [],
        }

        bot.send_message(
            uid,
            f"📌 ثبت سفارش {order_types[data]}\n\n"
            "ابتدا نمونه ادیتی که می‌خواهی را بفرست.\n"
            "می‌توانی عکس، ویدئو، فایل یا توضیحات متنی ارسال کنی."
        )

    # دریافت نمونه
    elif data == "add_file":
        state = states.get(uid)

        if state and state.get("step") == "items":
            bot.send_message(
                uid,
                "فایل بعدی را ارسال کن. بعد از اتمام، "
                "روی «ثبت درخواست» بزن."
            )

    elif data == "submit_order":
        state = states.get(uid)

        if not state or state.get("step") != "items":
            bot.send_message(uid, "سفارشی برای ثبت پیدا نشد.")
            return

        if (
            state["order_type"] != "طراحی لوگو"
            and not state["item_msg_ids"]
        ):
            bot.send_message(
                uid,
                "لطفاً ابتدا فایل‌هایی را که باید ادیت شوند بفرست."
            )
            return

        user = get_user(uid)

        if not user:
            bot.send_message(uid, "ابتدا ثبت‌نام کن: /start")
            states.pop(uid, None)
            return

        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.execute("""
                INSERT INTO orders (
                    tg_id, full_name, username, order_type,
                    sample_text, sample_msg_id, item_msg_ids,
                    status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                uid,
                user[1],
                user[2],
                state["order_type"],
                state["sample_text"],
                state["sample_msg_id"],
                json.dumps(state["item_msg_ids"]),
                "در انتظار انجام",
                datetime.now().strftime("%Y-%m-%d %H:%M")
            ))

            order_id = cursor.lastrowid

        # اطلاع به مدیر
        notify_admin_order(order_id)

        states.pop(uid, None)

        bot.send_message(
            uid,
            f"✅ درخواست شما با موفقیت ثبت شد.\n\n"
            f"🧾 کد سفارش: {order_id}\n"
            "⏳ شما در صف انتظار هستید. لطفاً برای انجام پروژه "
            "خود صبر کنید.\n\n"
            "📤 ادیت نهایی شما پس از آماده شدن، از طریق همین "
            "ربات برایتان ارسال خواهد شد.",
            reply_markup=main_menu(uid)
        )

    elif data == "cancel_order":
        states.pop(uid, None)
        bot.send_message(
            uid, "سفارش لغو شد.",
            reply_markup=main_menu(uid)
        )
        # پشتیبانی
    elif data == "contact":
        bot.send_message(
            uid,
            "📞 برای تماس با پشتیبانی روی لینک زیر بزن:\n"
            f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"
        )

    elif data == "online_support":
        states[uid] = {"step": "support_message"}
        bot.send_message(
            uid,
            "💬 پیام خود را برای پشتیبانی ارسال کن.\n"
            "پیامت برای مدیر ربات فرستاده می‌شود."
        )

    elif data == "opinion":
        states[uid] = {"step": "comment"}
        bot.send_message(uid, "📝 لطفاً نظر خودت را بنویس.")

    # پنل مدیریت
    elif data == "admin":
        if not is_admin(uid):
            return

        bot.send_message(
            uid, "⚙️ پنل مدیریت:",
            reply_markup=admin_menu()
        )

    elif data == "admin_users":
        if not is_admin(uid):
            return

        with sqlite3.connect(DB_NAME) as conn:
            users = conn.execute(
                "SELECT tg_id, full_name, username, created_at "
                "FROM users ORDER BY created_at DESC"
            ).fetchall()

        bot.send_message(uid, f"👥 تعداد کاربران: {len(users)}")

        for u in users:
            bot.send_message(
                uid,
                f"👤 نام: {u[1]}\n"
                f"🔹 نام کاربری: @{u[2]}\n"
                f"🆔 شناسه: {u[0]}\n"
                f"📅 تاریخ ثبت‌نام: {u[3]}"
            )

    elif data == "admin_orders":
        if not is_admin(uid):
            return

        with sqlite3.connect(DB_NAME) as conn:
            orders = conn.execute(
                "SELECT id, tg_id, full_name, order_type, "
                "status, created_at FROM orders ORDER BY id DESC"
            ).fetchall()

        if not orders:
            bot.send_message(uid, "هنوز سفارشی ثبت نشده است.")
            return

        for o in orders:
            bot.send_message(
                uid,
                f"📦 سفارش شماره: {o[0]}\n"
                f"👤 نام مشتری: {o[2]}\n"
                f"🆔 شناسه مشتری: {o[1]}\n"
                f"🛠 نوع سفارش: {o[3]}\n"
                f"📌 وضعیت: {o[4]}\n"
                f"📅 تاریخ: {o[5]}"
            )

    elif data == "admin_send":
        if not is_admin(uid):
            return

        states[uid] = {"step": "admin_order_id"}

        bot.send_message(
            uid,
            "📤 کد سفارش را وارد کن تا فایل نهایی را برای "
            "صاحب آن سفارش بفرستی."
        )

    elif data == "admin_comments":
        if not is_admin(uid):
            return

        with sqlite3.connect(DB_NAME) as conn:
            comments = conn.execute(
                "SELECT tg_id, full_name, comment, created_at "
                "FROM comments ORDER BY id DESC LIMIT 50"
            ).fetchall()

        if not comments:
            bot.send_message(uid, "هنوز نظری ثبت نشده است.")
            return

        for c in comments:
            bot.send_message(
                uid,
                f"💬 نظر کاربر:\n{c[2]}\n\n"
                f"👤 نام: {c[1]}\n"
                f"🆔 شناسه تلگرام: {c[0]}\n"
                f"📅 تاریخ: {c[3]}"
            )

    # پاسخ مدیر به پیام پشتیبانی
    elif data.startswith("reply_"):
        if not is_admin(uid):
            return

        target_id = int(data.split("_", 1)[1])
        states[uid] = {
            "step": "admin_reply",
            "target_id": target_id
        }

        bot.send_message(
            uid,
            f"✉️ پیام پاسخ را برای کاربر {target_id} ارسال کن."
        )


# ==================================================
# اطلاع‌رسانی سفارش به مدیر
# ==================================================

def notify_admin_order(order_id):
    order = get_order(order_id)

    if not order:
        return

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "📤 ارسال ادیت نهایی",
            callback_data="admin_send"
        )
    )

    bot.send_message(
        ADMIN_ID,
        f"🆕 سفارش جدید!\n\n"
        f"🧾 کد سفارش: {order[0]}\n"
        f"👤 نام مشتری: {order[2]}\n"
        f"🔹 نام کاربری: @{order[3]}\n"
        f"🆔 شناسه: {order[1]}\n"
        f"🛠 نوع: {order[4]}\n"
        f"📝 توضیحات نمونه: {order[5]}\n"
        f"📌 وضعیت: {order[8]}",
        reply_markup=markup
    )

    # کپی نمونه و فایل‌های سفارش برای مدیر
    if order[6]:
        try:
            bot.copy_message(
                ADMIN_ID, order[1], order[6]
            )
        except Exception as e:
            print("خطا در ارسال نمونه:", e)

    try:
        item_ids = json.loads(order[7] or "[]")

        for msg_id in item_ids:
            bot.copy_message(
                ADMIN_ID, order[1], msg_id
            )
    except Exception as e:
        print("خطا در ارسال فایل سفارش:", e)


# ==================================================
# دریافت پیام‌ها و مدیریت مراحل
# ==================================================

@bot.message_handler(content_types=[
    "text", "photo", "video", "document",
    "audio", "voice", "animation", "sticker"
])
def handle_messages(message):
    uid = message.from_user.id
    state = states.get(uid)

    if not state:
        return

    step = state.get("step")

    # ثبت نام و نام خانوادگی
    if step == "name":
        if not message.text or len(message.text.strip()) < 3:
            bot.send_message(
                uid, "لطفاً نام و نام خانوادگی خود را به‌صورت متنی وارد کن."
            )
            return

        state["full_name"] = message.text.strip()
        state["step"] = "username"

        bot.send_message(
            uid,
            "حالا نام کاربری تلگرام خودت را وارد کن.\n"
            "مثال: @soheil_316"
        )

    # ثبت نام کاربری
    elif step == "username":
        if not message.text:
            bot.send_message(uid, "نام کاربری را به‌صورت متن بفرست.")
            return

        username = message.text.strip().lstrip("@")

        if not username or " " in username:
            bot.send_message(uid, "نام کاربری معتبر وارد کن.")
            return

        real_username = message.from_user.username

        if not real_username:
            bot.send_message(
                uid,
                "❌ تو در حال حاضر نام کاربری (Username) در تلگرام نداری.\n"
                "لطفاً ابتدا از تنظیمات تلگرام یک نام کاربری برای خودت تنظیم کن، "
                "سپس دوباره آن را اینجا ارسال کن."
            )
            return

        if username.lower() != real_username.lower():
            bot.send_message(
                uid,
                "❌ نام کاربری وارد شده با نام کاربری تلگرام خودت مطابقت ندارد.\n"
                "لطفاً نام کاربری تلگرام خود را وارد کنید."
            )
            return

        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO users
                (tg_id, full_name, username, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                uid,
                state["full_name"],
                username,
                datetime.now().strftime("%Y-%m-%d %H:%M")
            ))

        states.pop(uid, None)

        bot.send_message(
            uid,
            "✅ ثبت‌نام شما با موفقیت انجام شد!",
            reply_markup=main_menu(uid)
        )

    # دریافت نمونه ادیت
    elif step == "sample":
        if message.content_type == "text":
            state["sample_text"] = message.text
        else:
            state["sample_text"] = message.caption or (
                "نمونه ارسال شده به‌صورت فایل"
            )

        state["sample_msg_id"] = message.message_id

        if state["order_type"] == "طراحی لوگو":
            state["step"] = "items"
            bot.send_message(
                uid,
                "🎨 توضیحات یا نمونه لوگو دریافت شد.\n"
                "اگر توضیحات بیشتری داری، آن را هم ارسال کن. "
                "سپس ثبت درخواست را بزن.",
                reply_markup=order_menu()
            )
        else:
            state["step"] = "items"
            bot.send_message(
                uid,
                "✅ نمونه دریافت شد.\n\n"
                "حالا تمام عکس‌ها یا کلیپ‌هایی را که می‌خواهی "
                "ادیت شوند، یکی‌یکی ارسال کن.\n"
                "بعد از اتمام، روی «ثبت درخواست» بزن.",
                reply_markup=order_menu()
            )

    # دریافت فایل‌های سفارش
    elif step == "items":
        state["item_msg_ids"].append(message.message_id)

        bot.send_message(
            uid,
            "✅ دریافت شد.\n"
            "اگر فایل دیگری داری ارسال کن؛ در غیر این صورت "
            "ثبت درخواست را بزن.",
            reply_markup=order_menu()
        )

    # دریافت نظر کاربر
    elif step == "comment":
        if not message.text:
            bot.send_message(uid, "لطفاً نظرت را به‌صورت متن ارسال کن.")
            return

        user = get_user(uid)
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("""
                INSERT INTO comments
                (tg_id, full_name, comment, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                uid,
                user[1] if user else message.from_user.first_name,
                message.text,
                datetime.now().strftime("%Y-%m-%d %H:%M")
            ))

        bot.send_message(
            ADMIN_ID,
            f"📝 نظر جدید کاربر:\n\n{message.text}\n\n"
            f"👤 نام: {user[1] if user else '-'}\n"
            f"🆔 شناسه: {uid}"
        )

        states.pop(uid, None)

        bot.send_message(
            uid,
            "🙏 ممنون از اینکه نظرت را با ما به اشتراک گذاشتی.",
            reply_markup=main_menu(uid)
        )

    # پیام پشتیبانی آنلاین
    elif step == "support_message":
        user = get_user(uid)

        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                "✉️ پاسخ به کاربر",
                callback_data=f"reply_{uid}"
            )
        )

        bot.send_message(
            ADMIN_ID,
            f"📨 پیام جدید پشتیبانی\n"
            f"👤 نام: {user[1] if user else '-'}\n"
            f"🆔 شناسه: {uid}\n"
            f"نوع پیام: {message.content_type}",
            reply_markup=markup
        )

        try:
            bot.copy_message(
                ADMIN_ID, uid, message.message_id
            )
        except Exception as e:
            print("خطا در کپی پیام پشتیبانی:", e)

        states.pop(uid, None)

        bot.send_message(
            uid,
            "✅ پیام شما برای پشتیبانی ارسال شد. منتظر پاسخ باشید.",
            reply_markup=main_menu(uid)
        )

    # پاسخ مدیر به کاربر
    elif step == "admin_reply":
        if not is_admin(uid):
            return

        target_id = state["target_id"]

        try:
            bot.copy_message(
                target_id, uid, message.message_id
            )
            bot.send_message(
                uid, "✅ پاسخ با موفقیت ارسال شد."
            )
        except Exception as e:
            bot.send_message(
                uid, f"❌ ارسال پاسخ ناموفق بود: {e}"
            )

        states.pop(uid, None)

    # انتخاب سفارش برای ارسال ادیت
    elif step == "admin_order_id":
        if not is_admin(uid):
            return

        if not message.text or not message.text.isdigit():
            bot.send_message(uid, "لطفاً کد عددی سفارش را وارد کن.")
            return

        order = get_order(int(message.text))

        if not order:
            bot.send_message(uid, "❌ سفارش پیدا نشد. کد دیگری وارد کن.")
            return

        states[uid] = {
            "step": "admin_final",
            "order_id": order[0],
            "target_id": order[1]
        }

        bot.send_message(
            uid,
            f"📦 سفارش: {order[0]}\n"
            f"👤 مشتری: {order[2]}\n"
            f"🛠 نوع: {order[4]}\n\n"
            "حالا فایل نهایی، عکس، ویدئو یا پیام تحویل را ارسال کن."
        )

    # ارسال فایل نهایی برای مشتری
    elif step == "admin_final":
        if not is_admin(uid):
            return

        order_id = state["order_id"]
        target_id = state["target_id"]

        try:
            bot.copy_message(
                target_id, uid, message.message_id
            )

            with sqlite3.connect(DB_NAME) as conn:
                conn.execute(
                    "UPDATE orders SET status = ? WHERE id = ?",
                    ("تحویل‌شده", order_id)
                )

            bot.send_message(
                uid,
                f"✅ فایل سفارش شماره {order_id} ارسال شد."
            )

        except Exception as e:
            bot.send_message(
                uid, f"❌ ارسال فایل انجام نشد: {e}"
            )

        states.pop(uid, None)


# ==================================================
# اجرای ربات
# ==================================================

if __name__ == "__main__":
    init_db()
    print("ربات روشن شد...")
    bot.infinity_polling(skip_pending=True)
