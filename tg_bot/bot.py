import asyncio
import logging
import re
import os
import requests
import tempfile
from telegram import Update, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from scraper import scrape_lot

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8901098603:AAFS2Kynv7oFLJKYy58eAThQ5XFrISDGbXI")
COPART_PATTERN = re.compile(r"copart\.com", re.IGNORECASE)
IAAI_PATTERN = re.compile(r"iaai\.com", re.IGNORECASE)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Переклади значень полів
TRANSLATIONS = {
    # Пошкодження
    "mechanical": "Механічні",
    "undercarriage": "Ходова частина",
    "front end": "Передня частина",
    "rear end": "Задня частина",
    "rollover": "Перекидання",
    "water/flood": "Затоплення",
    "flood": "Затоплення",
    "fire": "Пожежа",
    "hail": "Град",
    "vandalism": "Вандалізм",
    "theft recovered": "Після крадіжки",
    "collision": "Зіткнення",
    "side": "Бокове",
    "left side": "Ліва сторона",
    "right side": "Права сторона",
    "all over": "По всьому кузову",
    "minor dents/scratches": "Дрібні вм'ятини/подряпини",
    "primary paint": "Фарбування",
    "normal wear": "Нормальний знос",
    "normal wear & tear": "Нормальний знос",
    "none": "Відсутні",
    # Стан (Engine start)
    "run and drive": "Заводиться та їде 🟢",
    "run & drive": "Заводиться та їде 🟢",
    "starts": "Заводиться 🟡",
    "starts and drives": "Заводиться та їде 🟢",
    "starts & drives": "Заводиться та їде 🟢",
    "stationary": "Не заводиться 🔴",
    "does not start": "Не заводиться 🔴",
    "enhanced vehicles": "Заводиться та їде 🟢",
    # Паливо
    "gas": "Бензин",
    "gasoline": "Бензин",
    "diesel": "Дизель",
    "electric": "Електро",
    "hybrid": "Гібрид",
    "flex fuel": "Гнучке паливо",
    # Трансмісія
    "automatic": "Автомат",
    "automatic transmission": "Автомат",
    "manual": "Механіка",
    "cvt": "CVT (варіатор)",
    # Привід
    "fwd": "Передній привід (FWD)",
    "rwd": "Задній привід (RWD)",
    "awd": "Повний привід (AWD)",
    "4wd": "Повний привід (4WD)",
    "4x4": "Повний привід (4x4)",
    "all wheel drive": "Повний привід (AWD)",
    "all wheel": "Повний привід (AWD)",
    "front wheel drive": "Передній привід (FWD)",
    "rear wheel drive": "Задній привід (RWD)",
    "2wd": "Задній привід (2WD)",
    # Тип кузова
    "sedan 4dr": "Седан",
    "sedan": "Седан",
    "suv": "Позашляховик",
    "sport utility": "Позашляховик",
    "coupe": "Купе",
    "convertible": "Кабріолет",
    "wagon": "Універсал",
    "hatchback": "Хетчбек",
    "pickup": "Пікап",
    "van": "Мінівен",
    "truck": "Вантажівка",
    # Ключі
    "yes": "Так ✅",
    "no": "Ні ❌",
}


def translate(val: str) -> str:
    if not val:
        return val
    low = val.strip().lower()
    if low in TRANSLATIONS:
        return TRANSLATIONS[low]
    # Partial match for damage types
    for k, v in TRANSLATIONS.items():
        if low == k:
            return v
    return val


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Надішли посилання на лот з *Copart* або *IAAI*",
        parse_mode="Markdown"
    )


def download_photo(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        if len(r.content) > 5000:
            return r.content
    except Exception as e:
        logger.warning(f"Cannot download {url}: {e}")
    return None


def clean(val) -> str | None:
    if not val:
        return None
    s = str(val).strip()
    bad = ["Check now", "Pure sale", "Bid now", "Current bid", "Haven't bid",
           "Location:", "code:", "type", "Engine"]
    if any(b.lower() in s.lower() for b in bad):
        return None
    if len(s) > 120:
        return None
    if s in ("—", "N/A", "", "— / —", "null", "—  —", "— —", "0 mi", "0"):
        return None
    # Remove pipe-separated garbage like "| Jun 25, 2026 | NC - RALEIGH"
    if s.startswith("|") or ("|" in s and len(s) > 30):
        return None
    return s


def format_car_info(data: dict, source: str) -> str:
    source_label = "🟦 Copart" if source == "copart" else "🟧 IAAI"
    lines = [f"{source_label} — *{data.get('title', 'Авто')}*\n"]

    fields = [
        ("💰 Поточна ставка", "current_bid", False),
        ("📊 Основне пошкодження", "primary_damage", True),
        ("📊 Вторинне пошкодження", "secondary_damage", True),
        ("🔧 Об'єм двигуна", "engine", False),
        ("🔩 Циліндри", "cylinders", False),
        ("⚙️ Коробка передач", "transmission", True),
        ("🚙 Привід", "drive", True),
        ("⛽ Паливо", "fuel", True),
        ("🎨 Колір", "color", False),
        ("🚗 Тип кузова", "body_style", True),
        ("🛣 Пробіг", "odometer", False),
        ("🔑 Є ключ", "keys", True),
        ("🚦 Стан авто", "condition", True),
        ("📆 Дата продажу", "sale_date", False),
    ]

    for label, key, do_translate in fields:
        val = clean(data.get(key))
        if val:
            if do_translate:
                val = translate(val)
            if clean(val):
                lines.append(f"{label}: *{val}*")

    url = data.get("url", "")
    if url:
        lines.append(f"\n🔗 [Відкрити лот]({url})")

    return "\n".join(lines)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    is_copart = bool(COPART_PATTERN.search(text))
    is_iaai = bool(IAAI_PATTERN.search(text))

    if not (is_copart or is_iaai):
        await update.message.reply_text("❌ Надішли посилання з *copart.com* або *iaai.com*", parse_mode="Markdown")
        return

    source = "copart" if is_copart else "iaai"
    status_msg = await update.message.reply_text("⏳ Збираю інформацію...")

    try:
        lot_data = await asyncio.get_event_loop().run_in_executor(None, scrape_lot, text, source)

        if not lot_data:
            await status_msg.edit_text("❌ Не вдалося отримати дані.")
            return

        await status_msg.edit_text("📸 Завантажую фото...")

        images = lot_data.get("images", [])
        photo_bytes = []
        for img_url in images[:10]:
            data = await asyncio.get_event_loop().run_in_executor(None, download_photo, img_url)
            if data:
                photo_bytes.append(data)

        caption = format_car_info(lot_data, source)

        if photo_bytes:
            tmp_files = []
            media_group = []
            for i, pb in enumerate(photo_bytes):
                tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                tmp.write(pb)
                tmp.close()
                tmp_files.append(tmp.name)
                if i == 0:
                    media_group.append(InputMediaPhoto(
                        media=open(tmp.name, "rb"),
                        caption=caption[:1024],
                        parse_mode="Markdown"
                    ))
                else:
                    media_group.append(InputMediaPhoto(media=open(tmp.name, "rb")))

            await update.message.reply_media_group(media=media_group)

            if len(caption) > 1024:
                await update.message.reply_text(caption[1024:], parse_mode="Markdown")

            for f in tmp_files:
                try:
                    os.remove(f)
                except Exception:
                    pass
        else:
            await update.message.reply_text(caption, parse_mode="Markdown")

        await status_msg.delete()

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Помилка: {str(e)}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started!")
    app.run_polling()


if __name__ == "__main__":
    main()
