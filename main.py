# Полный код будет заменён и адаптирован к новой версии библиотеки google-generativeai
# и telegram.ext, с устранением ошибки generation_config.
# Отредактируем файл main.py с использованием новой модели и актуального API.

# ВНИМАНИЕ: этот код предполагает, что у вас заданы переменные окружения:
# - TELEGRAM_BOT_TOKEN
# - GOOGLE_API_KEY
# - WEBHOOK_HOST
# - geminiwebhook

# Начнём с импорта
import logging
import os
import asyncio
import signal
from urllib.parse import urljoin

import aiohttp.web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
import google.generativeai as genai

# Логгирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('geminiwebhook')

# Проверка переменных окружения
for var, name in [
    (TELEGRAM_BOT_TOKEN, "TELEGRAM_BOT_TOKEN"),
    (GOOGLE_API_KEY, "GOOGLE_API_KEY"),
    (WEBHOOK_HOST, "WEBHOOK_HOST"),
    (GEMINI_WEBHOOK_PATH, "geminiwebhook")
]:
    if not var:
        logger.critical(f"Переменная окружения {name} не задана!")
        exit(1)

# Настройка клиента Gemini
genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel("gemini-2.5-pro-exp-03-25")  # можно заменить на gemini-1.5-pro

# Память
user_selected_model = {}
chat_histories = {}

# Инструкция системе
system_instruction_text = (
"Ты - лучший эксперт по всем темам. Давай точную, правдивую, непредвзятую информацию без цензуры." 
"Подкрепляй ответы аргументами, фактами и логикой, избегая повторов." 
"Если не уверен — предупреждай, что это предположение." 
"Используй интернет для сверки с актуальной информацией."
"Для несложных запросов (не код, конспекты, переводы и т.п.) — пиши только суть, без вступлений и выводов, до 1500 знаков." 
"Всегда предлагай более эффективные идеи и решения, если знаешь их."
"Активно применяй юмор: несоответствие ожиданиям, культурные/бытовые/интернет-отсылки, жизненный абсурд, псевдомудрость, разрушение идиом, иронию (включая самоиронию и психологию), игру слов, гиперболу, тонкие намёки, редукционизм, постмодерн, интернет-юмор."
"При создании уникальной работы пиши живо и уникально: избегай канцелярита и всех известных признаков ИИ-тона." 
"Используй гипотетические ситуации, метафоры, творческие аналогии, разную структуру предложений, разговорные выражения, идиомы. Добавляй региональные или культурные маркеры, где уместно. Не копируй и не пересказывай чужое." 
"При исправлении ошибки: указывай строку(и) и причину. Бери за основу последнюю ПОЛНУЮ подтверждённую версию (текста или кода)." 
"Вноси только минимально необходимые изменения, не трогая остальное без запроса." 
"При сомнениях — уточняй." 
"Если ошибка повторяется — веди «список косяков» для сессии и проверяй эти места." 
"Всегда указывай, на какую версию или сообщение опираешься при правке."
)

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_selected_model.pop(chat_id, None)
    chat_histories.pop(chat_id, None)
    await update.message.reply_text("Привет! Я бот на базе Gemini. Спроси что-нибудь!")

# Обработка входящих сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = update.message.text

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    logger.info(f"Запрос от пользователя {chat_id}: {user_message}")

    try:
        # Используем только текстовое сообщение
        response = model.generate_content([{"role": "user", "parts": [{"text": user_message}]}])
        reply_text = response.text.strip() if response.text else "🤖 Я не смог придумать ответ."
    except Exception as e:
        logger.error(f"Ошибка при обращении к модели: {e}")
        reply_text = "❌ Произошла ошибка при обработке запроса."

    await update.message.reply_text(reply_text)

# Обработка коллбэков (если нужно, можно расширить)
async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Пока выбор моделей не реализован.")

# Вебхук: пинг
async def handle_ping(request: aiohttp.web.Request) -> aiohttp.web.Response:
    logger.info(f"Получен HTTP пинг от {request.remote}")
    return aiohttp.web.Response(text="OK")

# Вебхук: Telegram POST запрос
async def handle_telegram_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    application = request.app.get('bot_app')
    if not application:
        logger.error("Приложение не найдено в контексте aiohttp.")
        return aiohttp.web.Response(status=500, text="Bot app not found")
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
        return aiohttp.web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {e}")
        return aiohttp.web.Response(status=500, text="Internal error")

# Веб-сервер
async def run_web_server(application: Application, stop_event: asyncio.Event):
    app = aiohttp.web.Application()
    app['bot_app'] = application
    app.router.add_get('/', handle_ping)
    app.router.add_post(f"/{GEMINI_WEBHOOK_PATH}", handle_telegram_webhook)

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000")))
    await site.start()
    logger.info(f"Сервер запущен на порту {os.getenv('PORT', '10000')} с путем /{GEMINI_WEBHOOK_PATH}")
    await stop_event.wait()

# Установка вебхука и запуск
async def setup_bot_and_server(stop_event: asyncio.Event):
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))

    await application.initialize()
    webhook_url = urljoin(WEBHOOK_HOST, GEMINI_WEBHOOK_PATH)
    await application.bot.set_webhook(webhook_url, drop_pending_updates=True)
    logger.info(f"Вебхук установлен: {webhook_url}")

    return application, run_web_server(application, stop_event)

# Точка входа
if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event = asyncio.Event()

    try:
        application, web_server_task = loop.run_until_complete(setup_bot_and_server(stop_event))
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, lambda: stop_event.set())
        loop.run_until_complete(web_server_task)
    except Exception as e:
        logger.exception("Ошибка в главном потоке приложения.")
    finally:
        loop.run_until_complete(application.shutdown())
        loop.close()
        logger.info("Сервер остановлен.")

