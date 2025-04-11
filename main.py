import logging
import os
import asyncio
import signal
import time
import random
import google.genai as genai
import aiohttp.web
import sys
from typing import Optional, Dict, Union, Any, List
import urllib.parse

# --- КОНФИГУРАЦИЯ ЛОГОВ ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ИМПОРТ ТИПОВ GEMINI ---
try:
    from google.genai import types as genai_types
    logger.info("Импортирован модуль google.genai.types.")
    Tool = genai_types.Tool
    GenerateContentConfig = genai_types.GenerateContentConfig
    GoogleSearch = genai_types.GoogleSearch
    Content = genai_types.Content
    Part = genai_types.Part
    FinishReason = genai_types.FinishReason
    HarmCategory = genai_types.HarmCategory
    HarmProbability = genai_types.HarmProbability
except ImportError as e:
    logger.error(f"Ошибка импорта типов Gemini: {e}")
    exit(1)

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

if not TELEGRAM_BOT_TOKEN or not GOOGLE_API_KEY:
    logger.critical("Не заданы обязательные переменные окружения!")
    exit(1)

# --- ИНИЦИАЛИЗАЦИЯ GEMINI ---
try:
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    logger.info("Клиент Gemini создан.")
except Exception as e:
    logger.exception("Ошибка создания клиента Gemini!")
    exit(1)

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    '⚡ Flash 2.0': 'models/gemini-2.0-flash-001',
    '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25'
}
DEFAULT_MODEL_ALIAS = '✨ Pro 2.5'
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, List[Dict[str, Any]]] = {}

# --- ВЕБХУКИ И СЕРВЕР ---
global_application = None

async def handle_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    global global_application
    try:
        data = await request.json()
        update = Update.de_json(data, global_application.bot)
        await global_application.process_update(update)
        return aiohttp.web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"Ошибка вебхука: {e}")
        return aiohttp.web.Response(status=500)

async def handle_ping(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return aiohttp.web.Response(text="PONG", status=200)

async def run_web_server(app: aiohttp.web.Application, port: int):
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Сервер запущен на порту {port}")

# --- НАСТРОЙКА ПРИЛОЖЕНИЯ ---
async def setup_application() -> Application:
    global global_application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    global_application = application
    
    # Регистрация обработчиков
    handlers = [
        CommandHandler("start", start),
        CommandHandler("model", select_model_command),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
        CallbackQueryHandler(select_model_callback)
    ]
    
    for handler in handlers:
        application.add_handler(handler)

    # Фиксированный URL вебхука
    webhook_url = f"https://google-ai-ugl9.onrender.com/{TELEGRAM_BOT_TOKEN}"
    await application.bot.set_webhook(webhook_url)
    logger.info(f"Вебхук зарегистрирован: {webhook_url}")
    
    return application

# --- ГЛАВНАЯ ФУНКЦИЯ ---
async def main():
    global global_application
    global_application = await setup_application()
    web_app = aiohttp.web.Application()
    
    # Маршруты
    web_app.router.add_post(f"/{TELEGRAM_BOT_TOKEN}", handle_webhook)
    web_app.router.add_get("/", handle_ping)
    
    # Порт из окружения Render
    port = int(os.environ.get("PORT", 8080))
    server_task = asyncio.create_task(run_web_server(web_app, port))
    
    # Обработка сигналов
    stop_event = asyncio.Event()
    
    def signal_handler(sig):
        logger.info(f"Получен сигнал {signal.Signals(sig).name}")
        stop_event.set()
    
    for s in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(s, lambda s=s: signal_handler(s))
    
    # Основной цикл
    await stop_event.wait()
    
    # Завершение работы
    await global_application.stop()
    await global_application.shutdown()
    await server_task
    logger.info("Приложение остановлено")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Работа прервана пользователем")
    except Exception as e:
        logger.exception("Фатальная ошибка:")

