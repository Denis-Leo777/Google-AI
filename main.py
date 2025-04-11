# --- START OF FULL WEBHOOK-READY CODE ---

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

# --- СИСТЕМНЫЙ ПРОМПТ ---
system_instruction_text = (
    "Ты - лучший эксперт в любых вопросах. Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку."
    "Подкрепляй аргументами и доказательствами, но без самоповторов. Если не знаешь ответ - всегда предупреждай, что пишешь предположение."
    "Обязательно используй поиск в интернете для сверки с новой информацией по теме."
    "Если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, написание кода, или другая, требующая объема работа, то отвечай в пределах 2000 знаков."
    "Активно применяй юмор: несоответствие ожиданиям, культурные и бытовые отсылки, шутки об актуальных в интернете темах, жизненный абсурд и абсурдные решения проблем, псевдомудрость, разрушение идиом, безобидная ирония и самоирония, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, тонкие и интимные намёки, редукционизм, пост-модерн и интернет-юмор."
    "При создании уникальной работы не допускай признаков ИИ, избегай копирования или близкого пересказа существующих текстов, включай гипотетические ситуации для иллюстрации понятий, применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи, варьируй структуру предложений, естественно включай разговорные выражения, идиомы и фигуры речи, используй живые стилистические решения, свойственные людям, вставляй региональные выражения или культурно специфичные ссылки, где это уместно, добавляй остроумие."
    "При исправлении ошибки, указанной пользователем, идентифицируй конкретную строку(и) и конкретную причину ошибки. Бери за основу последнюю ПОЛНУЮ версию, предоставленную пользователем или сгенерированную тобой и подтвержденную как шаг вперед (даже если причиной была другая ошибка). Внеси только минимально необходимые изменения для исправления указанной ошибки. НЕ переписывай смежные части, НЕ удаляй ничего, НЕ меняй форматирование в других частях без явного запроса."
    "При возникновении сомнений, уточни у пользователя, какую версию использовать как базу. Если в ходе диалога выявляется повторяющаяся ошибка, добавь это в 'красный список' для данной сессии. Перед отправкой любого ответа, содержащего подобные конструкции, выполни целенаправленную проверку именно этих 'болевых точек'."
    "Если пользователь предоставляет свой полный текст (или код) как основу, используй именно этот текст (код). Не пытайся 'улучшить' или 'переформатировать' его части, не относящиеся к запросу на исправление, если только пользователь явно об этом не попросил."
    "В диалогах, связанных с разработкой или итеративным исправлением, всегда явно ссылайся на номер версии или предыдущее сообщение, которое берется за основу. Поддерживай четкое понимание, какая версия кода является 'последней рабочей' или 'последней предоставленной'."
)

# --- ИМПОРТЫ TELEGRAM ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
    TypeHandler
)

# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    if chat_id in user_selected_model:
        del user_selected_model[chat_id]
    if chat_id in chat_histories:
        del chat_histories[chat_id]
    
    search_status = "включен" if GoogleSearch else "ОТКЛЮЧЕН"
    await update.message.reply_html(
        rf"Привет, {user.mention_html()}! Бот Gemini (Webhook) v1.0"
        f"\n\nМодель по умолчанию: <b>{DEFAULT_MODEL_ALIAS}</b>"
        f"\n🔍 Поиск Google: <b>{search_status}</b>"
        f"\n\nИспользуй /model для смены модели",
        reply_to_message_id=update.message.message_id
    )

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    keyboard = [[InlineKeyboardButton(f"✅ {alias}" if alias == current_alias else alias, callback_data=alias)] 
               for alias in AVAILABLE_MODELS]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Текущая модель: *{current_alias}*\nВыберите новую:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    selected_alias = query.data
    chat_id = query.message.chat_id
    
    if selected_alias not in AVAILABLE_MODELS:
        await query.edit_message_text(text="❌ Неизвестная модель")
        return
    
    user_selected_model[chat_id] = selected_alias
    if chat_id in chat_histories:
        del chat_histories[chat_id]
    
    await query.edit_message_text(
        text=f"✅ Модель изменена на *{selected_alias}*\nИстория чата сброшена!",
        parse_mode=ParseMode.MARKDOWN
    )

def extract_response_text(response) -> Optional[str]:
    try:
        return response.text
    except AttributeError:
        try:
            if response.candidates and response.candidates[0].content.parts:
                return "".join(p.text for p in response.candidates[0].content.parts)
        except Exception as e:
            logger.error(f"Ошибка извлечения текста: {e}")
            return "⚠️ Ошибка обработки ответа"
    return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    
    user_message = update.message.text
    chat_id = update.effective_chat.id
    message_id = update.message.message_id
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    model_id = AVAILABLE_MODELS.get(selected_alias)
    
    if not model_id:
        await update.message.reply_text("❌ Ошибка конфигурации модели")
        return

    try:
        response = gemini_client.generate_content(
        model=model_id,
        contents=[{"role": "user", "parts": [{"text": user_message}]}],  # Закрыл `contents` правильно
        system_instruction={"parts": [{"text": system_instruction_text}]}
        )

        reply_text = extract_response_text(response)
        
        if not reply_text:
            raise ValueError("Пустой ответ от модели")
            
        await update.message.reply_text(
            reply_text,
            reply_to_message_id=message_id,
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"Ошибка Gemini API: {e}")
        await update.message.reply_text(
            "⚠️ Произошла ошибка при обработке запроса",
            reply_to_message_id=message_id
        )

# --- ВЕБХУКИ И СЕРВЕР ---
async def handle_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
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
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Регистрация обработчиков
    handlers = [
        CommandHandler("start", start),
        CommandHandler("model", select_model_command),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
        CallbackQueryHandler(select_model_callback),
        TypeHandler(Update, lambda update, ctx: logger.debug(f"Получено обновление: {update}"))
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
    application = await setup_application()
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
    await application.stop()
    await application.shutdown()
    await server_task
    logger.info("Приложение остановлено")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Работа прервана пользователем")
    except Exception as e:
        logger.exception("Фатальная ошибка:")

# --- END OF FULL WEBHOOK-READY CODE ---
