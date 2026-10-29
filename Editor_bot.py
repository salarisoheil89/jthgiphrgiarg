import sqlite3
import json
import time
import base64
from io import BytesIO
from datetime import datetime

import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # اگر نصب نشده: pip install openai

# ==================================================
# تنظیمات ربات
# ==================================================

TOKEN = "8829613268:AAFhxk2PsJZfsBNwVBfARswqnhFXHrxI29o"
ADMIN_ID = 1405044119  # شناسه عددی تلگرام خودت
ADMIN_USERNAME = "soheil_316"  # نام کاربری پشتیبانی، بدون @

bot = telebot.TeleBot(TOKEN)
DB_NAME = "bot_data.db"

# ==================================================
# تنظیمات هوش مصنوعی
# ==================================================
# کلید OpenAI رو از اینجا بگیر: https://platform.openai.com/api-keys
# (باید حساب بسازی و برای استفاده از API شارژ/کارت بانکی بین‌المللی وصل کنی)
# برای: تغییر لحن متن، ادیت عکس، طراحی لوگو، ساخت تصویر با پرامپت
OPENAI_API_KEY = "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"

# کلید Replicate رو از اینجا بگیر: https://replicate.com/account/api-tokens
# (ثبت‌نام رایگانه، فقط برای استفاده باید شارژ کنی)
# فقط برای: سرویس «کیفیت عکس (4K)» استفاده می‌شود
REPLICATE_API_TOKEN = "r8_QD425GWPxMosjTEMVSDYHqqwj4A5oBs0oK3fg"

# نسخه مدل آپ‌اسکیل روی Replicate (در صورت خطا، نسخه جدید رو از
# https://replicate.com/nightmareai/real-esrgan/api بردار و جایگزین کن)
REPLICATE_UPSCALE_MODEL = (
    "nightmareai/real-esrgan:"
    "42fed1c4974146d4d2414e2be2c5277c7fcf05fcc3a73abf41610695738c1d7b"
)

# انواع سفارشی که بعد از ثبت، به‌صورت خودکار با هوش مصنوعی پردازش و تحویل می‌شوند
AI_AUTOMATED_ORDER_TYPES = {"ادیت عکس", "طراحی لوگو", "ساخت تصویر با پرامپت"}

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


def save_user_from_telegram(user):
    """ثبت خودکار کاربر با اطلاعاتی که تلگرام در اختیار ربات می‌گذارد."""
    full_name = user.first_name or "کاربر"
    username = user.username or ""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            INSERT INTO users (tg_id, full_name, username, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                full_name = excluded.full_name,
                username = excluded.username
        """, (
            user.id,
            full_name,
            username,
            datetime.now().strftime("%Y-%m-%d %H:%M")
        ))


def get_order(order_id):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()


def is_admin(tg_id):
    return tg_id == ADMIN_ID


# ==================================================
# هوش مصنوعی (OpenAI + Replicate)
# ==================================================

def openai_client():
    if not OpenAI:
        raise RuntimeError("کتابخانه openai نصب نیست. دستور: pip install openai")
    if not OPENAI_API_KEY or "PASTE_YOUR" in OPENAI_API_KEY:
        raise RuntimeError("کلید OPENAI_API_KEY تنظیم نشده است.")
    return OpenAI(api_key=OPENAI_API_KEY)


def download_telegram_file(file_id):
    """دانلود فایل ارسالی کاربر در تلگرام و برگرداندن بایت‌های آن."""
    file_info = bot.get_file(file_id)
    return bot.download_file(file_info.file_path)


def extract_file_id(message):
    """گرفتن file_id قابل‌دانلود از یک پیام (فقط عکس/فایل عکسی)."""
    if message.content_type == "photo":
        return message.photo[-1].file_id
    if message.content_type == "document":
        mime = (message.document.mime_type or "")
        if mime.startswith("image/"):
            return message.document.file_id
    return None


def ai_generate_image(prompt, size="1024x1024"):
    """ساخت عکس جدید از روی توضیح متنی (برای لوگو و ساخت تصویر بدون عکس پایه)."""
    client = openai_client()
    result = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size=size,
    )
    return base64.b64decode(result.data[0].b64_json)


def ai_edit_image(image_bytes, prompt, size="1024x1024"):
    """ادیت یک عکس موجود بر اساس یک دستور متنی."""
    client = openai_client()
    result = client.images.edit(
        model="gpt-image-1",
        image=("image.png", BytesIO(image_bytes), "image/png"),
        prompt=prompt,
        size=size,
    )
    return base64.b64decode(result.data[0].b64_json)


def ai_upscale_image(image_bytes):
    """آپ‌اسکیل عکس به کیفیت بالا (تا ۴ برابر) با Replicate (Real-ESRGAN)."""
    if not REPLICATE_API_TOKEN or "PASTE_YOUR" in REPLICATE_API_TOKEN:
        raise RuntimeError("کلید REPLICATE_API_TOKEN تنظیم نشده است.")

    data_uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    model_name, version = REPLICATE_UPSCALE_MODEL.split(":", 1)

    create_resp = requests.post(
        "https://api.replicate.com/v1/predictions",
        headers=headers,
        json={
            "version": version,
            "input": {"image": data_uri, "scale": 4, "face_enhance": False},
        },
        timeout=60,
    )
    create_resp.raise_for_status()
    status_url = create_resp.json()["urls"]["get"]

    for _ in range(40):
        time.sleep(3)
        poll = requests.get(status_url, headers=headers, timeout=30).json()
        status = poll.get("status")

        if status == "succeeded":
            output_url = poll["output"]
            img_resp = requests.get(output_url, timeout=60)
            img_resp.raise_for_status()
            return img_resp.content

        if status == "failed":
            raise RuntimeError("پردازش آپ‌اسکیل تصویر با خطا مواجه شد.")

    raise RuntimeError("زمان پردازش تصویر بیش از حد معمول طول کشید.")


def ai_rewrite_text(text, tone_label, entity_map):
    """بازنویسی متن با لحن دلخواه و جایگزینی اسامی مشخص‌شده توسط کاربر."""
    client = openai_client()

    if entity_map:
        entity_lines = "\n".join(
            f"- «{old}» را به «{new}» تبدیل کن" for old, new in entity_map.items()
        )
    else:
        entity_lines = "(کاربر اسم خاصی برای جایگزینی مشخص نکرده است)"

    system_prompt = (
        "تو یک ویراستار حرفه‌ای متن فارسی هستی. وظیفه تو بازنویسی متن ورودی کاربر "
        f"با همان معنا و محتوا، اما با لحن «{tone_label}» است. "
        "اگر در دستور زیر برای برخی اسم‌های افراد، سازمان‌ها یا شرکت‌ها جایگزین "
        "مشخص شده، دقیقاً همان جایگزینی را در متن نهایی اعمال کن. سایر بخش‌های "
        "متن را دست‌نخورده و با همان معنا نگه دار. فقط و فقط متن نهایی "
        "بازنویسی‌شده را برگردان، بدون هیچ توضیح یا مقدمه اضافه."
    )
    user_prompt = (
        f"متن اصلی:\n{text}\n\n"
        f"دستور جایگزینی اسامی:\n{entity_lines}"
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


def parse_entity_map(text):
    """تبدیل متن ورودی کاربر (هر خط: اسم قدیمی = اسم جدید) به دیکشنری."""
    text = (text or "").strip()
    if not text or text in ("ندارم", "خیر", "نه", "-", "هیچی"):
        return {}

    entity_map = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" in line:
            old, new = line.split("=", 1)
        elif ":" in line:
            old, new = line.split(":", 1)
        else:
            continue
        old, new = old.strip(), new.strip()
        if old and new:
            entity_map[old] = new

    return entity_map


def process_and_deliver_ai_order(order_id, uid, state):
    """پردازش خودکار سفارش (عکس/لوگو/پرامپت) با هوش مصنوعی و ارسال نتیجه به کاربر."""
    order_type = state.get("order_type")

    bot.send_message(
        uid,
        "⏳ سفارش شما در حال پردازش با هوش مصنوعی است... چند لحظه صبر کن."
    )

    try:
        if order_type == "ادیت عکس":
            results = ai_process_photo_edit_order(state)
        elif order_type == "طراحی لوگو":
            results = ai_process_logo_order(state)
        elif order_type == "ساخت تصویر با پرامپت":
            results = ai_process_prompt_image_order(state)
        else:
            results = []

        if not results:
            raise RuntimeError("خروجی معتبری از هوش مصنوعی دریافت نشد.")

        for img_bytes in results:
            bot.send_photo(
                uid,
                BytesIO(img_bytes),
                caption="✅ نتیجه سفارش شما (ساخته‌شده با هوش مصنوعی)."
            )

        with sqlite3.connect(DB_NAME) as conn:
            conn.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                ("تحویل‌شده (هوش مصنوعی)", order_id)
            )

        notify_admin_order(order_id, ai_status="done")

    except Exception as e:
        print("خطای پردازش سفارش با هوش مصنوعی:", e)
        bot.send_message(
            uid,
            "⚠️ پردازش خودکار سفارش شما با مشکل مواجه شد. سفارشت برای "
            "بررسی و انجام دستی برای پشتیبانی ارسال شد."
        )
        notify_admin_order(order_id, ai_status="failed")


def ai_process_photo_edit_order(state):
    prompt = state.get("sample_text") or "این عکس را به‌صورت حرفه‌ای و زیبا ادیت کن."
    outputs = []

    for item in state.get("item_msg_ids", []):
        file_id = item.get("file_id") if isinstance(item, dict) else None
        if not file_id:
            continue
        image_bytes = download_telegram_file(file_id)
        outputs.append(ai_edit_image(image_bytes, prompt))

    if not outputs:
        raise RuntimeError("هیچ عکس قابل‌پردازشی در سفارش پیدا نشد.")

    return outputs


def ai_process_logo_order(state):
    description = state.get("sample_text") or "یک لوگوی مدرن، مینیمال و حرفه‌ای"
    prompt = (
        "یک لوگوی حرفه‌ای، تمیز و باکیفیت بر اساس این توضیحات طراحی کن "
        f"(بدون متن اضافه روی تصویر مگر اینکه خواسته شده باشد): {description}"
    )
    return [ai_generate_image(prompt, size="1024x1024")]


def ai_process_prompt_image_order(state):
    prompt = state.get("sample_text") or "این تصویر را به شکلی خلاقانه تغییر بده."
    file_id = None

    for item in state.get("item_msg_ids", []):
        if isinstance(item, dict) and item.get("file_id"):
            file_id = item["file_id"]
            break

    if file_id:
        image_bytes = download_telegram_file(file_id)
        result = ai_edit_image(image_bytes, prompt)
    else:
        result = ai_generate_image(prompt)

    return [result]


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
        ("🖼 ادیت عکس (با هوش مصنوعی)", "photo"),
        ("🎨 طراحی لوگو (با هوش مصنوعی)", "logo"),
        ("🪄 ساخت تصویر با پرامپت", "prompt_image"),
        ("📸 کیفیت عکس (4K)", "quality"),
        ("📝 تغییر لحن متن", "text_style"),
        ("🔙 بازگشت", "home"),
    ])


TONE_OPTIONS = {
    "tone_office": "اداری",
    "tone_formal": "رسمی",
    "tone_casual": "خودمونی",
    "tone_friendly": "دوستانه",
    "tone_news": "خبری",
    "tone_sad": "غمگین",
}


def tone_menu():
    return make_menu([(label, key) for key, label in TONE_OPTIONS.items()])


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

    # ذخیره خودکار شناسه عددی و اطلاعات عمومی حساب تلگرام
    save_user_from_telegram(message.from_user)
    states.pop(uid, None)

    if is_admin(uid):
        bot.send_message(
            uid,
            "سلام مدیر عزیز! 👋به پنل مدیریت ربات خوش آمدی.",
            reply_markup=admin_menu()
        )
        return

    user = get_user(uid)
    display_name = user[1] if user else (message.from_user.first_name or "دوست من")
    bot.send_message(
        uid,
        f"سلام {display_name} عزیز! 👋"
        "به ربات خدمات ادیت خوش آمدی."
        "حساب کاربری‌ات به‌صورت خودکار ثبت شد.",
        reply_markup=main_menu(uid)
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
    elif data == "prompt_image":
        states[uid] = {
            "step": "prompt_text",
            "order_type": "ساخت تصویر با پرامپت",
            "sample_text": "",
            "sample_msg_id": None,
            "item_msg_ids": [],
        }
        bot.send_message(
            uid,
            "🪄 ساخت تصویر با پرامپت"
            "مرحله ۱ از ۲: لطفاً پرامپت یا توضیح دقیق تصویری را "
            "که می‌خواهی ساخته شود، به‌صورت متن ارسال کن."
        )

    # کیفیت عکس (آپ‌اسکیل ۴K با هوش مصنوعی)
    elif data == "quality":
        states[uid] = {"step": "quality_photo"}
        bot.send_message(
            uid,
            "📸 کیفیت عکس (4K)\n\n"
            "عکسی که می‌خواهی کیفیتش با هوش مصنوعی بهبود پیدا کند را ارسال کن."
        )

    # تغییر لحن متن
    elif data == "text_style":
        states[uid] = {"step": "text_style_input"}
        bot.send_message(
            uid,
            "📝 تغییر لحن متن\n\n"
            "متنی که می‌خواهی لحنش تغییر کند را ارسال کن."
        )

    # انتخاب لحن جدید متن
    elif data in TONE_OPTIONS:
        state = states.get(uid)
        if not state or state.get("step") != "text_style_tone":
            return

        state["tone"] = TONE_OPTIONS[data]
        state["step"] = "text_style_names"

        bot.send_message(
            uid,
            f"✅ لحن انتخابی: {state['tone']}\n\n"
            "اگر در متن اسم شخص، سازمان یا شرکت خاصی هست که باید با اسم "
            "دیگری جایگزین شود، به این شکل بفرست (هر مورد در یک خط):\n\n"
            "اسم قدیمی = اسم جدید\n\n"
            "اگر نیازی به جایگزینی نیست، فقط بنویس «ندارم»."
        )

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

        states.pop(uid, None)

        if state["order_type"] in AI_AUTOMATED_ORDER_TYPES:
            bot.send_message(
                uid,
                f"✅ درخواست شما با موفقیت ثبت شد.\n"
                f"🧾 کد سفارش: {order_id}\n\n"
                "سفارش تو الان با هوش مصنوعی پردازش می‌شود.",
                reply_markup=main_menu(uid)
            )
            process_and_deliver_ai_order(order_id, uid, state)
        else:
            # اطلاع به مدیر (فقط برای ادیت ویدئو که دستی انجام می‌شود)
            notify_admin_order(order_id)

            bot.send_message(
                uid,
                f"✅ درخواست شما با موفقیت ثبت شد."
                f"🧾 کد سفارش: {order_id}\n"
                "⏳ شما در صف انتظار هستید. لطفاً برای انجام پروژه "
                "خود صبر کنید."
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

def notify_admin_order(order_id, ai_status=None):
    """
    ai_status:
      None   -> سفارش دستی (مثل ادیت ویدئو)، دکمه «ارسال ادیت نهایی» نشان داده می‌شود
      "done" -> هوش مصنوعی خودش پردازش و برای کاربر ارسال کرد (فقط اطلاع‌رسانی)
      "failed" -> پردازش خودکار شکست خورد و باید دستی انجام شود
    """
    order = get_order(order_id)

    if not order:
        return

    status_note = {
        "done": "🤖 این سفارش به‌صورت خودکار با هوش مصنوعی پردازش و برای کاربر ارسال شد.",
        "failed": "⚠️ پردازش خودکار این سفارش شکست خورد؛ لطفاً خودت دستی انجامش بده.",
    }.get(ai_status, "")

    markup = None
    if ai_status != "done":
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
        f"📌 وضعیت: {order[8]}"
        + (f"\n\n{status_note}" if status_note else ""),
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

        for item in item_ids:
            msg_id = item["msg_id"] if isinstance(item, dict) else item
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
            "مثال: @Your_ID"
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

    # دریافت پرامپت برای ساخت تصویر
    elif step == "prompt_text":
        if message.content_type != "text" or not message.text.strip():
            bot.send_message(uid, "لطفاً پرامپت را به‌صورت متن ارسال کن.")
            return

        state["sample_text"] = message.text.strip()
        state["step"] = "prompt_image"
        bot.send_message(
            uid,
            "✅ پرامپت دریافت شد.\\n\\n"
            "مرحله ۲ از ۲: حالا عکسی را که می‌خواهی بر اساس این پرامپت "
            "روی آن کار شود، به‌صورت عکس ارسال کن."
        )

    # دریافت عکس مربوط به پرامپت
    elif step == "prompt_image":
        if message.content_type != "photo":
            bot.send_message(
                uid,
                "لطفاً تصویر موردنظرت را با گزینه ارسال عکس بفرست؛ "
                "بعد از دریافت عکس می‌توانی سفارش را ثبت کنی."
            )
            return

        state["item_msg_ids"].append({
            "msg_id": message.message_id,
            "file_id": extract_file_id(message),
        })
        state["step"] = "items"
        bot.send_message(
            uid,
            "✅ پرامپت و عکس دریافت شدند.\\n\\n"
            "اگر فایل دیگری هم باید همراه سفارش باشد، ارسال کن؛ "
            "در غیر این صورت روی «ثبت درخواست» بزن.",
            reply_markup=order_menu()
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
        state["sample_file_id"] = extract_file_id(message)

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
        state["item_msg_ids"].append({
            "msg_id": message.message_id,
            "file_id": extract_file_id(message),
        })

        bot.send_message(
            uid,
            "✅ دریافت شد.\n"
            "اگر فایل دیگری داری ارسال کن؛ در غیر این صورت "
            "ثبت درخواست را بزن.",
            reply_markup=order_menu()
        )

    # دریافت عکس برای بهبود کیفیت (4K)
    elif step == "quality_photo":
        file_id = extract_file_id(message)

        if not file_id:
            bot.send_message(
                uid,
                "لطفاً عکس موردنظرت را با گزینه ارسال عکس بفرست."
            )
            return

        bot.send_message(uid, "⏳ در حال بهبود کیفیت عکس با هوش مصنوعی...")
        states.pop(uid, None)

        try:
            image_bytes = download_telegram_file(file_id)
            result_bytes = ai_upscale_image(image_bytes)

            bot.send_document(
                uid,
                (f"quality_{message.message_id}.png", BytesIO(result_bytes)),
                caption="✅ عکس با کیفیت بهبودیافته (برای حفظ کیفیت، به‌صورت فایل ارسال شد)."
            )
        except Exception as e:
            print("خطای بهبود کیفیت عکس:", e)
            bot.send_message(
                uid,
                "⚠️ متاسفانه بهبود کیفیت این عکس با مشکل مواجه شد. "
                "لطفاً بعداً دوباره امتحان کن یا با پشتیبانی در ارتباط باش."
            )

        bot.send_message(uid, "🏠 منوی اصلی:", reply_markup=main_menu(uid))

    # دریافت متن برای تغییر لحن
    elif step == "text_style_input":
        if message.content_type != "text" or not message.text.strip():
            bot.send_message(uid, "لطفاً متن موردنظرت را به‌صورت نوشته ارسال کن.")
            return

        state["original_text"] = message.text.strip()
        state["step"] = "text_style_tone"

        bot.send_message(
            uid,
            "✅ متن دریافت شد.\n\n"
            "حالا لحن موردنظرت را انتخاب کن:",
            reply_markup=tone_menu()
        )

    # دریافت اسامی جایگزین و اجرای بازنویسی نهایی
    elif step == "text_style_names":
        if message.content_type != "text":
            bot.send_message(uid, "لطفاً پاسخ را به‌صورت متن ارسال کن.")
            return

        entity_map = parse_entity_map(message.text)
        bot.send_message(uid, "⏳ در حال بازنویسی متن با هوش مصنوعی...")

        original_text = state.get("original_text", "")
        tone_label = state.get("tone", "")
        states.pop(uid, None)

        try:
            new_text = ai_rewrite_text(original_text, tone_label, entity_map)
            bot.send_message(uid, f"✅ متن بازنویسی‌شده:\n\n{new_text}")
        except Exception as e:
            print("خطای تغییر لحن متن:", e)
            bot.send_message(
                uid,
                "⚠️ متاسفانه بازنویسی متن با مشکل مواجه شد. "
                "لطفاً بعداً دوباره امتحان کن یا با پشتیبانی در ارتباط باش."
            )

        bot.send_message(uid, "🏠 منوی اصلی:", reply_markup=main_menu(uid))

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
