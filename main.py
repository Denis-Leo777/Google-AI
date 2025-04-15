import os
import logging
import asyncio
import signal
from urllib.parse import urljoin

from aiohttp import web
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import google.generativeai as genai

# Настройки из переменных окружения
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")  # например, https://example.com
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = urljoin(WEBHOOK_HOST, WEBHOOK_PATH)
PORT = int(os.getenv("PORT", 8443))

# Gemini init
genai.configure(api_key=GEMINI_API_KEY)

# Ведение состояния пользователей
user_state = {}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_user_state(user_id):
    if user_id not in user_state:
        user_state[user_id] = {
            "chat_history": [],
            "temperature": 0.7,
            "model_id": "gemini-pro"
        }
    return user_state[user_id]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот с Google Gemini. Напиши что-нибудь.")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_user.id]["chat_history"] = []
    await update.message.reply_text("История очищена.")


async def set_temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        t = float(context.args[0])
        if 0 <= t <= 1:
            user_state[update.effective_user.id]["temperature"] = t
            await update.message.reply_text(f"Температура установлена: {t}")
        else:
            raise ValueError
    except:
        await update.message.reply_text("Пример: /temp 0.7 (от 0 до 1)")


async def choose_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Gemini Pro", callback_data="gemini-pro")],
        [InlineKeyboardButton("Gemini Pro Vision", callback_data="gemini-pro-vision")],
    ]
    await update.message.reply_text("Выбери модель:", reply_markup=InlineKeyboardMarkup(keyboard))


async def model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    model_id = query.data
    user_state[query.from_user.id]["model_id"] = model_id
    await query.edit_message_text(f"✅ Модель выбрана: {model_id}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = get_user_state(uid)
    prompt = update.message.text

    state["chat_history"].append({"role": "user", "parts": [{"text": prompt}]})
    model = genai.GenerativeModel(state["model_id"])

    try:
        response = await model.generate_content_async(
            state["chat_history"],
            generation_config={
                "temperature": state["temperature"],
                "max_output_tokens": 1024,
            },
        )
        reply = response.text.strip()
        state["chat_history"].append({"role": "model", "parts": [{"text": reply}]})
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Ошибка Gemini: {e}")
        await update.message.reply_text("⚠️ Ошибка генерации. Попробуй позже.")


async def main():
    # Telegram Application
    app = Application.builder().token(BOT_TOKEN).build()

    # Хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("temp", set_temp))
    app.add_handler(CommandHandler("model", choose_model))
    app.add_handler(CallbackQueryHandler(model_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Установка вебхука
    await app.bot.set_webhook(WEBHOOK_URL)

    # AIOHTTP веб-сервер
    aio_app = web.Application()
    aio_app.router.add_post(WEBHOOK_PATH, app.webhook_handler())

    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"Бот слушает на порту {PORT}, вебхук: {WEBHOOK_URL}")

    # Завершение по сигналу
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop_event.set)
    await stop_event.wait()


if __name__ == "__main__":
    asyncio.run(main())

async def main():
    # Telegram Application
    app = Application.builder().token(BOT_TOKEN).build()

    # Хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("temp", set_temp))
    app.add_handler(CommandHandler("model", choose_model))
    app.add_handler(CallbackQueryHandler(model_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Установка вебхука
    await app.bot.set_webhook(WEBHOOK_URL)

    # AIOHTTP веб-сервер
    aio_app = web.Application()
    aio_app.router.add_post(WEBHOOK_PATH, app.webhook_handler())

    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"Бот слушает на порту {PORT}, вебхук: {WEBHOOK_URL}")

    # Завершение по сигналу
    stop_event = asyncio.Event()

    def stop():
        logger.info("⛔️ Завершение работы...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)

    await stop_event.wait()

    # Остановка
    await runner.cleanup()
    await app.shutdown()

