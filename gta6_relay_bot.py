"""
بوت نقل مباشر (استماع فوري) من قناة @GTAVIStar إلى قناة @GTA6AR
- يستمع للرسائل الجديدة لحظة نزولها (events.NewMessage) بدل الفحص الدوري
- أول تشغيل: يبدأ من آخر 5 منشورات فقط بقناة المصدر
- كل منشور لازم يترجم للعربي قبل النشر - لو فشلت الترجمة، ينتظر ويعاد المحاولة، وما ينشر نص بدون ترجمة أبداً
- يضيف رابط القناة (بدون 📌) بنهاية كل منشور
"""

import os
import json
import time
import asyncio
import requests
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ==================== الإعدادات ====================
TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_SESSION = os.environ["TG_SESSION"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

SOURCE_CHANNEL = "GTAVIStar"
TARGET_CHAT = "@GTA6AR"
CHANNEL_LINK = "https://t.me/GTA6AR"

GEMINI_MODEL = "gemini-flash-lite-latest"
STATE_FILE = "state.json"
BOT_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

MAX_RUNTIME_SECONDS = 5 * 3600 + 40 * 60  # 5 ساعات و40 دقيقة
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_DELAY = 8

client = TelegramClient(StringSession(TG_SESSION), TG_API_ID, TG_API_HASH)


# ==================== إدارة الحالة ====================
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_message_id": 0}


def save_state(last_id: int):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_message_id": last_id}, f, ensure_ascii=False, indent=2)


# ==================== الترجمة عبر Gemini (إجبارية - بدون نشر نص أصلي بديل) ====================
def rewrite_to_arabic(text: str) -> str:
    if not text or not text.strip():
        return ""

    prompt = (
        "أعد صياغة الخبر التالي وترجمه إلى اللغة العربية بأسلوب صحفي طبيعي، "
        "وكأن عربي كتبه من الأساس وليس ترجمة حرفية. "
        "لا تضف مقدمات مثل 'إليك الترجمة' ولا أي تعليق إضافي، فقط النص المُعاد صياغته:\n\n"
        f"{text}"
    )
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    last_error = None
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if result:
                return result
            last_error = "رد فارغ من Gemini"
        except Exception as e:
            last_error = e
            print(f"[Gemini error] محاولة {attempt}/{GEMINI_MAX_RETRIES}: {e}")
            time.sleep(GEMINI_RETRY_DELAY)

    # فشلت كل المحاولات: لا ننشر النص الأصلي أبداً، نرفع استثناء ليعاد المحاولة لاحقاً
    raise RuntimeError(f"تعذرت الترجمة عبر Gemini بعد {GEMINI_MAX_RETRIES} محاولات: {last_error}")


# ==================== النشر عبر Telegram Bot API ====================
def _check_response(resp):
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def send_text(caption: str):
    resp = requests.post(
        f"{BOT_API_URL}/sendMessage",
        data={
            "chat_id": TARGET_CHAT,
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    _check_response(resp)


def send_photo(file_path: str, caption: str):
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{BOT_API_URL}/sendPhoto",
            data={"chat_id": TARGET_CHAT, "caption": caption, "parse_mode": "HTML"},
            files={"photo": f},
            timeout=60,
        )
    _check_response(resp)


def send_video(file_path: str, caption: str):
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{BOT_API_URL}/sendVideo",
            data={"chat_id": TARGET_CHAT, "caption": caption, "parse_mode": "HTML"},
            files={"video": f},
            timeout=120,
        )
    _check_response(resp)


# ==================== معالجة رسالة واحدة ====================
async def process_message(message):
    text = message.message or ""
    rewritten = rewrite_to_arabic(text)  # يرفع استثناء لو فشلت الترجمة كليًا

    if rewritten:
        caption = f"{rewritten}\n\n{CHANNEL_LINK}"
    else:
        caption = CHANNEL_LINK

    if message.photo:
        path = await client.download_media(message, file="temp_media")
        try:
            send_photo(path, caption)
        finally:
            if path and os.path.exists(path):
                os.remove(path)
    elif message.video:
        path = await client.download_media(message, file="temp_media")
        try:
            send_video(path, caption)
        finally:
            if path and os.path.exists(path):
                os.remove(path)
    else:
        if not text.strip():
            return
        send_text(caption)

    print(f"[OK] تم نشر الرسالة {message.id}")


async def handle_message(msg, state: dict):
    try:
        await process_message(msg)
        if msg.id > state["last_message_id"]:
            state["last_message_id"] = msg.id
            save_state(state["last_message_id"])
    except Exception as e:
        print(f"[ERROR] فشل نشر الرسالة {msg.id}: {e} -- سيُعاد المحاولة بالتشغيلة/الدورة الجاية")


# ==================== تعويض ما فات أثناء الانقطاع القصير بين التشغيلات ====================
async def catch_up(state: dict):
    last_id = state["last_message_id"]

    if last_id == 0:
        latest = await client.get_messages(SOURCE_CHANNEL, limit=1)
        if latest:
            last_id = max(latest[0].id - 5, 0)
            state["last_message_id"] = last_id
            save_state(last_id)
        messages = await client.get_messages(SOURCE_CHANNEL, min_id=last_id, limit=10)
    else:
        messages = await client.get_messages(SOURCE_CHANNEL, min_id=last_id, limit=50)

    for msg in reversed(list(messages)):
        await handle_message(msg, state)


# ==================== التشغيل الرئيسي ====================
async def main():
    state = load_state()
    await client.start()

    @client.on(events.NewMessage(chats=SOURCE_CHANNEL))
    async def live_handler(event):
        await handle_message(event.message, state)

    await catch_up(state)
    print("[LISTENING] البوت الآن يستمع مباشرة لأي منشور جديد...")

    start = time.time()
    while time.time() - start < MAX_RUNTIME_SECONDS:
        await asyncio.sleep(30)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
