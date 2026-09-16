"""
بوت نقل مباشر (استماع فوري) من قناة @GTAVIStar إلى قناة @GTA6AR
- يستمع للرسائل الجديدة لحظة نزولها (events.NewMessage) بدل الفحص الدوري
- أول تشغيل: يبدأ من آخر 5 منشورات فقط بقناة المصدر
- كل منشور لازم يترجم للعربي قبل النشر - لو فشلت الترجمة، ينتظر ويعاد المحاولة، وما ينشر نص بدون ترجمة أبداً
- يزيل أي ذكر للقناة المصدر أو ترويج تبعها، ويضيف رابط قناتك بنهاية كل منشور
- إذا كان منشور المصدر يحتوي أكثر من صورة/فيديو (ألبوم)، يُنشر كمنشور واحد بكل الوسائط مع نص واحد
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
ALBUM_WAIT_SECONDS = 2.5  # مدة الانتظار لتجميع كل صور/فيديوهات المنشور الواحد قبل النشر

client = TelegramClient(StringSession(TG_SESSION), TG_API_ID, TG_API_HASH)
pending_albums: dict = {}


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
        "أنت محرر أخبار عربي. أعد كتابة الخبر التالي بالعربية من جديد، وكأن محرر عربي كتبه من الصفر "
        "وليس ترجمة - أسلوب صحفي طبيعي وسلس.\n\n"
        "قواعد صارمة:\n"
        "- احذف نهائيًا أي ذكر لاسم القناة المصدر، شعارها، رابطها، أو أي دعوة للانضمام لها أو لمجموعة/چات خاص فيها.\n"
        "- احذف أي عبارات ترويجية أو تفاعلية لا علاقة لها بالخبر نفسه (مثل طلب لايك/مشاركة/تعليق، أو إعلانات مسابقات).\n"
        "- احذف أي هاشتاغات أو إيموجيات ترويجية لا تخدم مضمون الخبر.\n"
        "- لا تضف أي مقدمة مثل 'إليك الترجمة' ولا أي تعليق إضافي، فقط نص الخبر النهائي بالعربي.\n\n"
        f"النص الأصلي:\n{text}"
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


def send_media_group(media_paths_types, caption: str):
    """ينشر أكثر من صورة/فيديو كمنشور واحد (ألبوم) مع نص واحد على أول عنصر."""
    media = []
    files = {}
    try:
        for idx, (path, mtype) in enumerate(media_paths_types):
            key = f"file{idx}"
            item = {"type": mtype, "media": f"attach://{key}"}
            if idx == 0:
                item["caption"] = caption
                item["parse_mode"] = "HTML"
            media.append(item)
            files[key] = open(path, "rb")

        resp = requests.post(
            f"{BOT_API_URL}/sendMediaGroup",
            data={"chat_id": TARGET_CHAT, "media": json.dumps(media)},
            files=files,
            timeout=120,
        )
        _check_response(resp)
    finally:
        for f in files.values():
            f.close()


# ==================== معالجة مجموعة رسائل (منشور واحد قد يحتوي أكثر من وسائط) ====================
async def process_group(messages):
    text = ""
    for m in messages:
        if m.message and m.message.strip():
            text = m.message
            break

    rewritten = rewrite_to_arabic(text)
    caption = f"{rewritten}\n\n{CHANNEL_LINK}" if rewritten else CHANNEL_LINK

    media_items = []
    try:
        for m in messages:
            if m.photo:
                path = await client.download_media(m, file="temp_media")
                if path:
                    media_items.append((path, "photo"))
            elif m.video:
                path = await client.download_media(m, file="temp_media")
                if path:
                    media_items.append((path, "video"))

        if len(media_items) >= 2:
            send_media_group(media_items, caption)
        elif len(media_items) == 1:
            path, mtype = media_items[0]
            if mtype == "photo":
                send_photo(path, caption)
            else:
                send_video(path, caption)
        else:
            if not text.strip():
                return
            send_text(caption)
    finally:
        for path, _ in media_items:
            if path and os.path.exists(path):
                os.remove(path)

    ids = ", ".join(str(m.id) for m in messages)
    print(f"[OK] تم نشر المنشور (الرسائل: {ids})")


async def handle_group(messages, state: dict):
    try:
        await process_group(messages)
        max_id = max(m.id for m in messages)
        if max_id > state["last_message_id"]:
            state["last_message_id"] = max_id
            save_state(state["last_message_id"])
    except Exception as e:
        ids = ", ".join(str(m.id) for m in messages)
        print(f"[ERROR] فشل نشر المنشور (الرسائل: {ids}): {e} -- سيُعاد المحاولة بالتشغيلة/الدورة الجاية")


# ==================== تجميع الألبومات أثناء الاستماع المباشر ====================
async def flush_album(grouped_id, state: dict):
    await asyncio.sleep(ALBUM_WAIT_SECONDS)
    entry = pending_albums.pop(grouped_id, None)
    if not entry:
        return
    messages = sorted(entry["messages"], key=lambda m: m.id)
    await handle_group(messages, state)


async def on_new_message(event, state: dict):
    msg = event.message
    if msg.grouped_id:
        gid = msg.grouped_id
        if gid not in pending_albums:
            pending_albums[gid] = {
                "messages": [msg],
                "timer": asyncio.create_task(flush_album(gid, state)),
            }
        else:
            pending_albums[gid]["messages"].append(msg)
    else:
        await handle_group([msg], state)


# ==================== تجميع الألبومات عند تعويض ما فات (catch_up) ====================
def group_messages(messages):
    groups = []
    seen = {}
    for m in messages:
        if m.grouped_id:
            if m.grouped_id in seen:
                seen[m.grouped_id].append(m)
            else:
                lst = [m]
                seen[m.grouped_id] = lst
                groups.append(lst)
        else:
            groups.append([m])
    return groups


async def catch_up(state: dict):
    last_id = state["last_message_id"]

    if last_id == 0:
        latest = await client.get_messages(SOURCE_CHANNEL, limit=1)
        if latest:
            last_id = max(latest[0].id - 5, 0)
            state["last_message_id"] = last_id
            save_state(last_id)
        messages = await client.get_messages(SOURCE_CHANNEL, min_id=last_id, limit=15)
    else:
        messages = await client.get_messages(SOURCE_CHANNEL, min_id=last_id, limit=50)

    ordered = list(reversed(list(messages)))
    for group in group_messages(ordered):
        await handle_group(group, state)


# ==================== التشغيل الرئيسي ====================
async def main():
    state = load_state()
    await client.start()

    @client.on(events.NewMessage(chats=SOURCE_CHANNEL))
    async def live_handler(event):
        await on_new_message(event, state)

    await catch_up(state)
    print("[LISTENING] البوت الآن يستمع مباشرة لأي منشور جديد...")

    start = time.time()
    while time.time() - start < MAX_RUNTIME_SECONDS:
        await asyncio.sleep(30)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
