import logging
import os
import asyncio
import signal
from urllib.parse import urljoin
import base64

import aiohttp.web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
import google.genai as genai

# Логгирование
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('geminiwebhook')

for var, name in [
    (TELEGRAM_BOT_TOKEN, "TELEGRAM_BOT_TOKEN"),
    (GOOGLE_API_KEY, "GOOGLE_API_KEY"),
    (WEBHOOK_HOST, "WEBHOOK_HOST"),
    (GEMINI_WEBHOOK_PATH, "geminiwebhook")
]:
    if not var:
        logger.critical(f"Переменная окружения {name} не задана!")
        exit(1)

# Строка удалена: genai.configure(api_key=GOOGLE_API_KEY)

AVAILABLE_MODELS = {
    'gemini-2.5-pro-exp-03-25': '2.5 Pro',
    'gemini-2.0-flash': '2.0 Flash',
    'gemini-2.0-flash-exp-image-generation': 'Image Gen'
}
DEFAULT_MODEL = 'gemini-2.5-pro-exp-03-25'

user_selected_model = {}
user_search_enabled = {}
user_temperature = {}
MAX_CONTEXT_CHARS = 95000

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
    user_selected_model[chat_id] = DEFAULT_MODEL
    user_search_enabled[chat_id] = True
    user_temperature[chat_id] = 1.0
    await update.message.reply_text("🎉 Здесь вы можете пользоваться самой продвинутой моделью ИИ от Google - Gemini 2.5 Pro с Google-поиском и улучшенными (точностью и юмором) настройками, чтением изображений и текстовых файлов. /model - выбор модели создания изображений 'Image Gen', /clear - очистить историю. Канал автора: t.me/denisobovsyom")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['history'] = [{"role": "system", "parts": [{"text": system_instruction_text}]}]
    await update.message.reply_text("🧹 История диалога очищена.")

async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        temp = float(context.args[0])
        if not (0 <= temp <= 2):
            raise ValueError
        user_temperature[update.effective_chat.id] = temp
        await update.message.reply_text(f"🌡️ Температура установлена на {temp}")
    except:
        await update.message.reply_text("⚠️ Укажите температуру от 0 до 2, например: /temp 1.0")

async def enable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_search_enabled[update.effective_chat.id] = True
    await update.message.reply_text("🔍 Google-поиск включён")

async def disable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_search_enabled[update.effective_chat.id] = False
    await update.message.reply_text("🔇 Google-поиск отключён")

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current_model = user_selected_model.get(chat_id, DEFAULT_MODEL)
    keyboard = [
        [InlineKeyboardButton(f"{'✅ ' if m == current_model else ''}{name}", callback_data=m)]
        for m, name in AVAILABLE_MODELS.items()
    ]
    await update.message.reply_text("Выберите модель:", reply_markup=InlineKeyboardMarkup(keyboard))

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    selected = query.data
    if selected in AVAILABLE_MODELS:
        user_selected_model[chat_id] = selected
        await query.edit_message_text(f"Модель установлена: {AVAILABLE_MODELS[selected]}")
    else:
        await query.edit_message_text("❌ Неизвестная модель")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = update.message.text.strip()
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    temperature = user_temperature.get(chat_id, 1.0)
    use_search = user_search_enabled.get(chat_id, True)

    logger.info(f"Модель: {model_id}, Темп: {temperature}, Поиск: {use_search}")

    chat_history = context.chat_data.setdefault("history", [])
    if not any(msg.get("role") == "system" for msg in chat_history):
        chat_history.insert(0, {"role": "system", "parts": [{"text": system_instruction_text}]})
    chat_history.append({"role": "user", "parts": [{"text": user_message}]})

    total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history)
    while total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1:
        if chat_history[1].get("role") == "system":
            chat_history.pop(2)
        else:
            chat_history.pop(1)
        total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history)

    try:
        tools = [genai.tool_spec.google_search] if use_search else []
        model = genai.GenerativeModel(
            model_id,
            api_key=GOOGLE_API_KEY,  # <-- Добавлено
            tools=tools,
            safety_settings=[],
            generation_config={"temperature": temperature}
        )
        response = model.generate_content(chat_history)
        reply = response.text or "🤖 Нет ответа от модели."
        chat_history.append({"role": "model", "parts": [{"text": reply}]})
    except Exception as e:
        logger.exception("Ошибка генерации ответа")
        reply = "❌ Ошибка при обращении к модели."

    await update.message.reply_text(reply)

async def handle_image_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    if model_id != 'gemini-2.0-flash-exp-image-generation':
        await handle_message(update, context)
        return

    user_message = update.message.text.strip()
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        model = genai.GenerativeModel(
            model_id,
            api_key=GOOGLE_API_KEY  # <-- Добавлено
        )
        response = model.generate_content([{"role": "user", "parts": [{"text": user_message}]}])
        image_data = response.candidates[0].content.parts[0].inline_data.data
        image_bytes = base64.b64decode(image_data)
        await update.message.reply_photo(photo=image_bytes, caption="🖼️ Сгенерировано изображение")
    except Exception as e:
        logger.exception("Ошибка генерации изображения")
        await update.message.reply_text("❌ Не удалось сгенерировать изображение.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    photo_file = await update.message.photo[-1].get_file()
    file_bytes = await photo_file.download_as_bytearray()
    b64_data = base64.b64encode(file_bytes).decode()

    parts = [
        {"text": "Что изображено на этом фото?"},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}
    ]
    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    temperature = user_temperature.get(chat_id, 1.0)
    tools = [genai.tool_spec.google_search] if user_search_enabled.get(chat_id, True) else []

    try:
        model = genai.GenerativeModel(
            model_id,
            api_key=GOOGLE_API_KEY,  # <-- Добавлено
            tools=tools,
            safety_settings=[],
            generation_config={"temperature": temperature}
        )
        response = model.generate_content([{"role": "user", "parts": parts}])
        reply = response.text or "🤖 Не удалось понять изображение."
    except Exception as e:
        logger.exception("Ошибка при анализе изображения")
        reply = "❌ Ошибка при анализе изображения."

    await update.message.reply_text(reply)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    doc = await update.message.document.get_file()
    file_bytes = await doc.download_as_bytearray()

    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1", errors="ignore")

    truncated = text[:15000]
    # Создаём новое "сообщение" чтобы передать текст документа в handle_message
    # Это не самое элегантное решение, но работает в рамках текущей структуры
    fake_message = update.message.copy()
    fake_message.text = f"Вот текст из файла '{update.message.document.file_name}':\n\n{truncated}"
    await handle_message(Update(update.update_id, fake_message), context) # Передаем копию Update с измененным текстом

async def handle_ping(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return aiohttp.web.Response(text="OK")

async def handle_telegram_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    application = request.app.get('bot_app')
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
        return aiohttp.web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"Ошибка webhook: {e}")
        return aiohttp.web.Response(status=500, text="Internal error")

async def run_web_server(application: Application, stop_event: asyncio.Event):
    app = aiohttp.web.Application()
    app['bot_app'] = application
    app.router.add_get('/', handle_ping)
    app.router.add_post(f"/{GEMINI_WEBHOOK_PATH}", handle_telegram_webhook)

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000")))
    await site.start()
    logger.info(f"Сервер запущен на порту {os.getenv('PORT', '10000')}...")
    await stop_event.wait()
    logger.info("Остановка сервера...")
    await runner.cleanup()

async def setup_bot_and_server(stop_event: asyncio.Event):
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("temp", set_temperature))
    application.add_handler(CommandHandler("search_on", enable_search))
    application.add_handler(CommandHandler("search_off", disable_search))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    # Важно: Обработчик текста должен идти *после* команд, чтобы не перехватывать их
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_image_prompt)) # Используем handle_image_prompt, т.к. он сам вызывает handle_message если модель не image gen
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    await application.initialize()
    webhook_url = urljoin(WEBHOOK_HOST, f"/{GEMINI_WEBHOOK_PATH}")
    logger.info(f"Установка webhook на {webhook_url}")
    try:
        await application.bot.set_webhook(webhook_url, drop_pending_updates=True, secret_token=GEMINI_WEBHOOK_PATH[:32]) # Можно добавить секретный токен для безопасности
        logger.info("Webhook успешно установлен.")
    except Exception as e:
        logger.exception(f"Не удалось установить webhook: {e}")
        raise # Перевыбрасываем исключение, чтобы главный поток знал об ошибке

    return application, asyncio.create_task(run_web_server(application, stop_event)) # Возвращаем задачу для ожидания

# Определяем application в глобальной области видимости
application: Application | None = None

async def main(stop_event: asyncio.Event):
    global application # Используем global для изменения глобальной переменной
    application, web_server_task = await setup_bot_and_server(stop_event)
    await web_server_task # Ждем завершения веб-сервера

if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event = asyncio.Event()

    try:
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, lambda: stop_event.set())
        # Запускаем main внутри run_until_complete
        loop.run_until_complete(main(stop_event))
    except Exception as e:
        logger.exception("Критическая ошибка в главном потоке.")
    finally:
        logger.info("Начинаем процедуру остановки...")
        # Проверяем глобальную application
        if application:
             logger.info("Остановка приложения Telegram...")
             # Убедимся, что цикл событий доступен для shutdown
             if not loop.is_closed():
                 loop.run_until_complete(application.shutdown())
                 logger.info("Приложение Telegram остановлено.")
             else:
                 logger.warning("Цикл событий уже закрыт, не могу остановить приложение Telegram.")

        # Даем немного времени на завершение задач и закрываем цикл
        if not loop.is_closed():
            try:
                # Проверим, есть ли еще запущенные задачи
                tasks = asyncio.all_tasks(loop)
                if tasks:
                    logger.info(f"Ожидание завершения {len(tasks)} задач...")
                    # Дадим им шанс завершиться, но не бесконечно
                    _, pending = loop.run_until_complete(asyncio.wait(tasks, timeout=2.0))
                    if pending:
                        logger.warning(f"{len(pending)} задач не завершились и будут отменены.")
                        for task in pending:
                            task.cancel()
                        # Дать время на обработку отмены
                        loop.run_until_complete(asyncio.sleep(0.1))

                loop.run_until_complete(asyncio.sleep(0.5)) # Небольшая пауза перед закрытием
            except RuntimeError as e:
                 logger.warning(f"Ошибка при ожидании перед закрытием цикла: {e}")
            finally:
                 loop.close()
                 logger.info("Цикл событий закрыт. Сервер полностью остановлен.")
        else:
            logger.info("Цикл событий уже был закрыт.")
