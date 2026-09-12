"""
بوت نقل تلقائي من قناة @GTAVIStar إلى قناة @GTA6AR
- أول تشغيل: ينشر آخر 5 منشورات من قناة المصدر
- بعد ذلك: ينشر فقط المنشورات الجديدة (بالاعتماد على state.json)
- يعيد صياغة كل منشور بالعربي (لأن قناة المصدر روسية أو بلغة أجنبية)
- يضيف رابط قناتك في نهاية كل منشور
"""

import os
import json
import time
import requests
from telethon.sync import TelegramClient

# ==================== الإعدادات (تُقرأ من متغيرات البيئة / GitHub Secrets) ====================
TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_SESSION = os.environ["TG_SESSION"]          # نفس السيشن المستخدم بالبوتات الثانية

BOT_TOKEN = os.environ["BOT_TOKEN"]            # توكن بوت جديد خاص بقناة GTA6AR (من BotFather)
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]  # مفتاح Gemini جديد خاص بهذا البوت

SOURCE_CHANNEL = "GTAVIStar"
TARGET_CHAT = "@GTA6AR"
CHANNEL_LINK = "https://t.me/GTA6AR"

GEMINI_MODEL = "gemini-flash-lite-latest"
STATE_FILE = "state.json"
INITIAL_POST_COUNT = 5

BOT_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ==================== إدارة الحالة (آخر منشور تم نشره) ====================
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_message_id": 0}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ==================== إعادة الصياغة عبر Gemini ====================
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

    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"[Gemini error] {e} -- سيتم نشر النص الأصلي بدون إعادة صياغة")
        return text


# ==================== النشر عبر Telegram Bot API ====================
def send_text(caption: str):
    requests.post(
        f"{BOT_API_URL}/sendMessage",
        data={"chat_id": TARGET_CHAT, "text": caption, "parse_mode": "HTML"},
        timeout=30,
    )


def send_photo(file_path: str, caption: str):
    with open(file_path, "rb") as f:
        requests.post(
            f"{BOT_API_URL}/sendPhoto",
            data={"chat_id": TARGET_CHAT, "caption": caption, "parse_mode": "HTML"},
            files={"photo": f},
            timeout=60,
        )


def send_video(file_path: str, caption: str):
    with open(file_path, "rb") as f:
        requests.post(
            f"{BOT_API_URL}/sendVideo",
            data={"chat_id": TARGET_CHAT, "caption": caption, "parse_mode": "HTML"},
            files={"video": f},
            timeout=120,
        )


# ==================== معالجة رسالة واحدة ====================
def process_message(client, message):
    text = message.message or ""
    rewritten = rewrite_to_arabic(text)

    if rewritten:
        caption = f"{rewritten}\n\n📌 {CHANNEL_LINK}"
    else:
        caption = f"📌 {CHANNEL_LINK}"

    if message.photo:
        path = client.download_media(message, file="temp_media")
        try:
            send_photo(path, caption)
        finally:
            if path and os.path.exists(path):
                os.remove(path)
    elif message.video:
        path = client.download_media(message, file="temp_media")
        try:
            send_video(path, caption)
        finally:
            if path and os.path.exists(path):
                os.remove(path)
    else:
        # تجاهل الرسائل بدون نص وبدون وسائط (مثل رسائل الخدمة)
        if not text.strip():
            return
        send_text(caption)

    print(f"[OK] تم نشر الرسالة {message.id}")


# ==================== التشغيل الرئيسي ====================
def main():
    state = load_state()
    last_id = state.get("last_message_id", 0)

    with TelegramClient(TG_SESSION, TG_API_ID, TG_API_HASH) as client:
        if last_id == 0:
            # أول تشغيل: انشر آخر 5 منشورات (من الأقدم إلى الأحدث)
            messages = list(client.iter_messages(SOURCE_CHANNEL, limit=INITIAL_POST_COUNT))
            messages.reverse()
        else:
            # التشغيلات التالية: انشر فقط ما هو أحدث من آخر رسالة تم نشرها
            messages = list(client.iter_messages(SOURCE_CHANNEL, min_id=last_id))
            messages.reverse()

        if not messages:
            print("لا توجد منشورات جديدة.")
            return

        new_last_id = last_id
        for msg in messages:
            try:
                process_message(client, msg)
                new_last_id = max(new_last_id, msg.id)
                time.sleep(3)  # فاصل بسيط بين كل نشر وآخر
            except Exception as e:
                print(f"[ERROR] فشل نشر الرسالة {msg.id}: {e}")

        save_state({"last_message_id": new_last_id})


if __name__ == "__main__":
    main()
