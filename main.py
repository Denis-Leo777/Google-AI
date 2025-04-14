import logging
import os
import asyncio
import signal
import time
import json
from urllib.parse import urljoin

import aiohttp.web
import telegram.ext
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

import google.genai as genai  # Используем новый API (без класса GenerativeModel)

# --- КОНФИГУРАЦИЯ ЛОГОВ ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ЗАГРУЗКА КЛЮЧЕЙ И КЛИЕНТА ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
# Переменная для постоянного пути вебхука (должна быть задана в переменных окружения)
geminiwebhook = os.getenv('geminiwebhook')

if not TELEGRAM_BOT_TOKEN:
    logger.critical("Telegram токен не найден!"); exit("Telegram токен не найден")
if not GOOGLE_API_KEY:
    logger.critical("Ключ Google API не найден!"); exit("Google API ключ не найден")
if not WEBHOOK_HOST:
    logger.critical("WEBHOOK_HOST не указан!"); exit("WEBHOOK_HOST не указан")
if not geminiwebhook:
    logger.critical("Переменная geminiwebhook не задана!"); exit("geminiwebhook не задан")

try:
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    logger.info("Клиент google.genai.Client создан.")
except Exception as e:
    logger.exception("Ошибка создания клиента Gemini!")
    exit("Ошибка создания клиента Gemini.")

# Модели и дефолтная модель
AVAILABLE_MODELS = {
    '⚡ Flash 2.0': 'models/gemini-2.0-flash-001',
    '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25'
}
DEFAULT_MODEL_ALIAS = '✨ Pro 2.5'
if DEFAULT_MODEL_ALIAS not in AVAILABLE_MODELS:
    DEFAULT_MODEL_ALIAS = next(iter(AVAILABLE_MODELS))
    logger.warning(f"Дефолтная модель не найдена, установлена: {DEFAULT_MODEL_ALIAS}")

user_selected_model = {}  # chat_id -> alias
chat_histories = {}       # chat_id -> список сообщений

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

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_selected_model.pop(chat_id, None)
    chat_histories.pop(chat_id, None)
    search_status = "включен (если поддерживается)"
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Бот Gemini v70 (Webhook).\n\nМодель: <b>{DEFAULT_MODEL_ALIAS}</b>\n"
        f"🔍 Поиск: <b>{search_status}</b>.\n\nИспользуй /model для смены, /start для сброса.\n\nСпрашивай!"
    )

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    keyboard = [[InlineKeyboardButton(f"✅ {alias}" if alias == current_alias else alias, callback_data=alias)]
                for alias in AVAILABLE_MODELS.keys()]
    if not keyboard:
        await update.message.reply_text("Нет моделей.")
        return
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    selected_alias = query.data
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias not in AVAILABLE_MODELS:
        logger.error(f"Пользователь {user_id} выбрал неверный alias: {selected_alias}")
        await query.edit_message_text("❌ Ошибка: Неизвестный выбор модели.")
        return
    if selected_alias == current_alias:
        logger.info(f"{user_id} перевыбрал модель: {selected_alias}")
        await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        return
    user_selected_model[chat_id] = selected_alias
    chat_histories.pop(chat_id, None)
    logger.info(f"{user_id} сменил модель: {selected_alias}")
    keyboard = [[InlineKeyboardButton(f"✅ {alias}" if alias == selected_alias else alias, callback_data=alias)]
                for alias in AVAILABLE_MODELS.keys()]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(text=f"✅ Модель: *{selected_alias}*! \nИстория сброшена.\n\nНачните чат:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Не удалось изменить сообщение: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"Модель: *{selected_alias}*!\nИстория сброшена.", parse_mode=ParseMode.MARKDOWN)

# --- ФУНКЦИЯ ОБРАБОТКИ СООБЩЕНИЙ ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not update.message or not update.message.text:
            logger.warning("Пустое сообщение."); return
        user_message = update.message.text.strip()
        chat_id = update.effective_chat.id
        message_id = update.message.message_id
        logger.info(f"Сообщение из чата {chat_id}: '{user_message[:80]}'")
        selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
        model_id = AVAILABLE_MODELS.get(selected_alias)
        if not model_id:
            logger.error(f"Критическая ошибка: Не найден ID для '{selected_alias}'")
            await update.message.reply_text("Ошибка конфига.", reply_to_message_id=message_id)
            return

        # Подготовка истории чата
        current_history = chat_histories.get(chat_id, [])
        try:
            user_part = {'text': user_message}
            api_contents = current_history + [{'role': 'user', 'parts': [user_part]}]
        except Exception as e:
            logger.error(f"Ошибка формирования содержания: {e}")
            api_contents = current_history + [{'role': 'user', 'parts': [{'text': user_message}]}]

        logger.info(f"Запрос к модели '{model_id}', история: {len(current_history)} сообщений.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        tools_list = None  # Если требуются инструменты (например, поиск) – добавить сюда список
        generation_config_for_api = {}  # Дополнительные настройки генерации

        system_instruction_content = {'parts': [{'text': system_instruction_text}]} if system_instruction_text else None

        # Генерация ответа через новый API
        response = gemini_client.models.generate_content(
            model=model_id,
            contents=api_contents,
            generation_config=(generation_config_for_api if generation_config_for_api else None),
            tools=tools_list,
            system_instruction=system_instruction_content
        )

        logger.info(f"Ответ от модели '{model_id}' получен.")
        final_text = response.text  # Используем быстрый accessor нового ответа

        if final_text and not final_text.startswith("⚠️"):
            try:
                history_to_update = chat_histories.get(chat_id, []).copy()
                history_to_update.append({'role': 'user', 'parts': [{'text': user_message}]})
                history_to_update.append({'role': 'model', 'parts': [{'text': final_text}]})
                chat_histories[chat_id] = history_to_update
                logger.info(f"История чата {chat_id} обновлена, сообщений: {len(chat_histories[chat_id])}.")
            except Exception as e:
                logger.error(f"Ошибка обновления истории: {e}")
        elif final_text and final_text.startswith("⚠️"):
            logger.warning("Ответ содержит ошибку, история не обновлена.")
            final_text = None

        reply_markup = None
        if final_text:
            max_length = 4096
            bot_response = final_text if len(final_text) <= max_length else final_text[:max_length - 3] + "..."
            try:
                await update.message.reply_text(bot_response, reply_to_message_id=message_id, reply_markup=reply_markup)
                logger.info(f"Ответ отправлен ({len(bot_response)} симв.).")
            except Exception as e:
                logger.exception(f"Ошибка отправки ответа Telegram: {e}")
        elif not final_text:
            await update.message.reply_text("Модель вернула пустой ответ без ошибок. 🤷", reply_to_message_id=message_id)
    except Exception as e:
        logger.exception(f"Критическая ошибка в handle_message: {e}")
        try:
            await update.message.reply_text("Произошла внутренняя ошибка при обработке вашего сообщения. 🤯", reply_to_message_id=message_id)
        except Exception as e_reply:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e_reply}")

# --- ВЕБ-СЕРВЕР И ЗАПУСК ---
async def handle_ping(request: aiohttp.web.Request) -> aiohttp.web.Response:
    logger.info(f"Получен HTTP пинг от {request.remote}")
    return aiohttp.web.Response(text="OK", status=200)

async def handle_telegram_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    application = request.app.get('bot_app')
    if not application:
        logger.error("Объект Application не найден!")
        return aiohttp.web.Response(status=500, text="Internal Server Error")
    if request.method != "POST":
        return aiohttp.web.Response(status=405, text="Method Not Allowed")
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
        return aiohttp.web.Response(status=200, text="OK")
    except Exception as e:
        logger.exception(f"Ошибка обработки вебхука: {e}")
        return aiohttp.web.Response(status=500, text="Internal Server Error")

async def run_web_server(port: int, stop_event: asyncio.Event, application: Application):
    app = aiohttp.web.Application()
    app['bot_app'] = application
    app.router.add_get('/', handle_ping)
    # Используем фиксированный путь из переменной geminiwebhook
    webhook_path = f"/{geminiwebhook}"
    app.router.add_post(webhook_path, handle_telegram_webhook)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Веб-сервер запущен на http://0.0.0.0:{port}, путь вебхука: {webhook_path}")
    await stop_event.wait()
    await runner.cleanup()

async def setup_bot_and_server(stop_event: asyncio.Event):
    application = Application.builder().token(TELEGRAM_BOT_TOKEN)\
        .connect_timeout(40).read_timeout(40).pool_timeout(60).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    port = int(os.environ.get("PORT", 8080))
    await application.initialize()
    # Используем фиксированный путь вебхука из переменной geminiwebhook
    webhook_path = f"/{geminiwebhook}"
    webhook_url = urljoin(WEBHOOK_HOST, webhook_path)
    try:
        await application.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        logger.info(f"Вебхук установлен на {webhook_url}")
    except Exception as e:
        logger.exception(f"Ошибка установки вебхука: {e}")
        raise
    web_server_coro = run_web_server(port, stop_event, application)
    return application, web_server_coro

if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event = asyncio.Event()
    try:
        application, web_server_coro = loop.run_until_complete(setup_bot_and_server(stop_event))
        web_server_task = loop.create_task(web_server_coro)
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, lambda s=s: loop.create_task(stop_event.set()))
        logger.info("Запуск основного цикла...")
        loop.run_forever()
    except Exception as e:
        logger.exception("Критическая ошибка в главном потоке!")
    finally:
        loop.run_until_complete(application.shutdown())
        loop.close()
        logger.info("Процесс завершён.")
