# Обновлённый main.py с:
# - Контекстом 95 000 символов
# - Включённым по умолчанию Google Search (можно выключить)
# - Управлением температурой /temp 0.8
# - Очисткой истории /clear
# - Безопасностью: safety_settings=[] (цензура снята)
# - Постоянной системной инструкцией (через параметр модели)
# - Исправленным вызовом Google Search для google-generativeai v0.8+

import logging
import os
import asyncio
import signal
from urllib.parse import urljoin
import base64
import pytesseract
from PIL import Image
import io

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
import google.generativeai as genai
# ===== ИСПРАВЛЕНИЕ: Добавляем нужные импорты для Tool =====
from google.generativeai.types import Tool, GoogleSearchRetrieval
# ==========================================================

# Логгирование
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')

for var, name in [
    (TELEGRAM_BOT_TOKEN, "TELEGRAM_BOT_TOKEN"),
    (GOOGLE_API_KEY, "GOOGLE_API_KEY"),
    (WEBHOOK_HOST, "WEBHOOK_HOST"),
    (GEMINI_WEBHOOK_PATH, "GEMINI_WEBHOOK_PATH")
]:
    if not var:
        logger.critical(f"Переменная окружения {name} не задана!")
        exit(1)

# Настройка Gemini
genai.configure(api_key=GOOGLE_API_KEY)

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

# Инструкция системе (теперь будет передаваться в модель напрямую)
system_instruction_text = (
"Ты - лучший эксперт по всем темам. Давай точную, правдивую, непредвзятую информацию без цензуры."
"Подкрепляй ответы аргументами, фактами и логикой, избегая повторов."
"Если не уверен — предупреждай, что это предположение."
"Используй интернет для сверки с актуальной информацией."
"Для несложных запросов (не код, конспекты, переводы и т.п.) — пиши только суть, без вступлений и выводов, до 1500 знаков."
"Всегда предлагай более эффективные идеи и решения, если знаешь их."
"Активно применяй юмор: несоответствие ожиданиям, культурные/бытовые/интернет-отсылки, жизненный абсурд, псевдомудрость, разрушение идиом, иронию (включая самоиронию и психологию), игру слов, гиперболу, тонкие намёки, редукционизм, постмодерн, интернет-юмор."
"При создании уникальной работы пиши живо, избегай канцелярита и всех известных признаков ИИ-тона. Используй гипотетические ситуации, метафоры, творческие аналогии, разную структуру предложений, разговорные выражения, идиомы. Добавляй региональные или культурные маркеры, где уместно. Не копируй и не пересказывай чужое."
"При исправлении ошибки: указывай строку(и) и причину. Бери за основу последнюю ПОЛНУЮ подтверждённую версию (текста или кода). Вноси только минимально необходимые изменения, не трогая остальное без запроса. При сомнениях — уточняй. Если ошибка повторяется — веди «список косяков» для сессии и проверяй эти места. Всегда указывай, на какую версию или сообщение опираешься при правке."
)

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # Инициализируем настройки пользователя
    user_selected_model[chat_id] = DEFAULT_MODEL
    user_search_enabled[chat_id] = True
    user_temperature[chat_id] = 1.0
    # ===== ИСПРАВЛЕНИЕ: Убираем инициализацию истории здесь, она будет создаваться по факту =====
    # context.chat_data['history'] = [] # Не нужно инициализировать историю здесь
    await update.message.reply_text(
        "Добро пожаловать! Здесь вы можете пользоваться самой продвинутой моделью ИИ от Google - Gemini 2.5 Pro с Google-поиском и улучшенными (точностью и юмором) настройками, чтением изображений и текстовых файлов."
        " \n/model — выбор модели (включая генерацию картинок 'Image Gen')," # Добавил Image Gen в описание
        " \n/clear — очистить историю."
        " \n/temp <0-2> — установить температуру (креативность)."
        " \n/search_on /search_off — вкл/выкл Google Поиск." # Добавил команды поиска
        " \nКанал автора: t.me/denisobovsyom" # Подправил немного текст
    )

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ===== ИСПРАВЛЕНИЕ: Просто очищаем историю, без системного промпта =====
    context.chat_data['history'] = []
    await update.message.reply_text("🧹 История диалога очищена.")

async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        temp = float(context.args[0])
        if not (0 <= temp <= 2): # Gemini Pro поддерживает до 2.0
            raise ValueError
        user_temperature[chat_id] = temp
        await update.message.reply_text(f"🌡️ Температура установлена на {temp}")
    except (IndexError, ValueError):
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
    if not user_message: # Не обрабатывать пустые сообщения
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    temperature = user_temperature.get(chat_id, 1.0)
    use_search = user_search_enabled.get(chat_id, True)

    logger.info(f"ChatID: {chat_id} | Модель: {model_id}, Темп: {temperature}, Поиск: {use_search}")

    # ===== ИСПРАВЛЕНИЕ: История без системного промпта, он будет в параметрах модели =====
    chat_history = context.chat_data.setdefault("history", [])

    # Добавляем текущее сообщение пользователя
    chat_history.append({"role": "user", "parts": [{"text": user_message}]})

    # Обрезка истории СТРОГО по символам, удаляя самые старые сообщения
    total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text")) # Более надёжный подсчёт
    while total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1: # Оставляем хотя бы одно сообщение (последнее пользовательское)
        removed_message = chat_history.pop(0) # Удаляем самое старое
        # Пересчитываем символы после удаления
        total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
        logger.info(f"ChatID: {chat_id} | История обрезана, удалено сообщение: {removed_message.get('role')}, текущая длина истории: {len(chat_history)}, символов: {total_chars}")

    try:
        # ===== ИСПРАВЛЕНИЕ: Новый способ определения tools =====
        tools = [Tool(google_search_retrieval=GoogleSearchRetrieval())] if use_search else []

        # ===== ИСПРАВЛЕНИЕ: Передаём system_instruction напрямую =====
        model = genai.GenerativeModel(
            model_id,
            tools=tools,
            safety_settings=[], # Оставляем как было для отключения цензуры
            generation_config={"temperature": temperature},
            system_instruction=system_instruction_text
        )
        # Передаём только user/model историю
        response = model.generate_content(chat_history)
        reply = response.text

        if not reply:
            # Попробуем получить причину блокировки, если есть
            try:
                block_reason = response.prompt_feedback.block_reason
                finish_reason = response.candidates[0].finish_reason if response.candidates else 'UNKNOWN'
                logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели. Block Reason: {block_reason}, Finish Reason: {finish_reason}")
                reply = f"🤖 Модель не дала ответ. Возможная причина: {block_reason or finish_reason}"
            except Exception:
                logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели, причину выяснить не удалось.")
                reply = "🤖 Нет ответа от модели."

        # Добавляем ответ модели в историю
        chat_history.append({"role": "model", "parts": [{"text": reply}]})

    except Exception as e:
        logger.exception(f"ChatID: {chat_id} | Ошибка генерации ответа")
        reply = f"❌ Ошибка при обращении к модели: {e}" # Выводим ошибку пользователю для отладки

    await update.message.reply_text(reply)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO) # Или TYPING?
    photo_file = await update.message.photo[-1].get_file()
    file_bytes = await photo_file.download_as_bytearray()
    user_caption = update.message.caption # Проверим, есть ли подпись к фото

    # Попытка OCR
    try:
        image = Image.open(io.BytesIO(file_bytes))
        extracted_text = pytesseract.image_to_string(image, lang='rus+eng') # Укажем языки для лучшего распознавания
        if extracted_text and extracted_text.strip():
            logger.info(f"ChatID: {chat_id} | Обнаружен текст на изображении: {extracted_text[:100]}...")
            # Если есть текст И подпись, объединим. Если только текст, используем его.
            ocr_prompt = f"На изображении обнаружен следующий текст:\n```\n{extracted_text.strip()}\n```\n"
            if user_caption:
                 user_prompt = f"{user_caption}\n{ocr_prompt}Проанализируй изображение и текст на нём, учитывая мой комментарий."
            else:
                 user_prompt = f"{ocr_prompt}Проанализируй изображение и текст на нём."

            # Создаём фейковое текстовое сообщение для передачи в handle_message
            # Это немного костыльно, но позволяет использовать всю логику handle_message (история, обрезка и т.д.)
            fake_update = type('obj', (object,), {
                'effective_chat': update.effective_chat,
                'message': type('obj', (object,), {
                    'text': user_prompt,
                    'reply_text': update.message.reply_text # Чтобы ответ был реплаем
                })
            })
            await handle_message(fake_update, context)
            return # Выходим, т.к. обработали как текст
    except Exception as e:
        logger.warning(f"ChatID: {chat_id} | OCR не удалось или текст не найден: {e}")
        # Продолжаем как обычную картинку, если OCR не сработал

    # Если OCR не сработал или текст не найден, обрабатываем как изображение
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    b64_data = base64.b64encode(file_bytes).decode()

    # Используем подпись пользователя как промпт, если она есть, иначе дефолтный
    prompt = user_caption if user_caption else "Что изображено на этом фото?"
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}} # TODO: определять mime_type динамически?
    ]

    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    temperature = user_temperature.get(chat_id, 1.0)
    use_search = user_search_enabled.get(chat_id, True)

    # ===== ИСПРАВЛЕНИЕ: Новый способ определения tools =====
    tools = [Tool(google_search_retrieval=GoogleSearchRetrieval())] if use_search else []

    logger.info(f"ChatID: {chat_id} | Анализ изображения. Модель: {model_id}, Темп: {temperature}, Поиск: {use_search}")

    try:
        # ===== ИСПРАВЛЕНИЕ: Передаём system_instruction напрямую =====
        # Заметка: Не уверен, на 100%, как system_instruction влияет на анализ *самого* изображения,
        # но он точно повлияет на *текстовый ответ* об изображении.
        model = genai.GenerativeModel(
            model_id,
            tools=tools,
            safety_settings=[],
            generation_config={"temperature": temperature},
            system_instruction=system_instruction_text
        )
        # Для изображений история обычно не используется в одном запросе
        response = model.generate_content([{"role": "user", "parts": parts}])
        reply = response.text

        if not reply:
            # Попробуем получить причину блокировки, если есть
            try:
                block_reason = response.prompt_feedback.block_reason
                finish_reason = response.candidates[0].finish_reason if response.candidates else 'UNKNOWN'
                logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения. Block Reason: {block_reason}, Finish Reason: {finish_reason}")
                reply = f"🤖 Модель не смогла описать изображение. Возможная причина: {block_reason or finish_reason}"
            except Exception:
                logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения, причину выяснить не удалось.")
                reply = "🤖 Не удалось понять, что на изображении."

    except Exception as e:
        logger.exception(f"ChatID: {chat_id} | Ошибка при анализе изображения")
        reply = f"❌ Ошибка при анализе изображения: {e}"

    await update.message.reply_text(reply)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not update.message.document:
        return
    doc = update.message.document
    # Проверим mime_type, чтобы не пытаться читать какой-нибудь zip
    if not doc.mime_type or not doc.mime_type.startswith('text/'):
         # Можно добавить поддержку других типов, например, pdf с помощью доп. библиотек
        await update.message.reply_text("⚠️ Пока могу читать только текстовые файлы (.txt, .py, .csv и т.п.).")
        logger.warning(f"ChatID: {chat_id} | Попытка загрузить нетекстовый файл: {doc.mime_type}")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT) # Или TYPING?
    doc_file = await doc.get_file()
    file_bytes = await doc_file.download_as_bytearray()
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        # Пытаемся декодировать как UTF-8, потом как common latin-1
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = file_bytes.decode("latin-1") # Или cp1251 для русских текстов?
            logger.warning(f"ChatID: {chat_id} | Файл не в UTF-8, использован latin-1.")
        except Exception as e:
            logger.error(f"ChatID: {chat_id} | Не удалось декодировать файл: {e}")
            await update.message.reply_text("❌ Не удалось прочитать текстовое содержимое файла. Убедитесь, что это текстовый файл в кодировке UTF-8 или Latin-1.")
            return

    # Ограничим текст, чтобы не упереться в лимиты API и Telegram
    # MAX_CONTEXT_CHARS здесь не совсем подходит, т.к. это лимит всего диалога.
    # Установим разумный лимит на сам текст файла.
    MAX_FILE_CHARS = 30000 # Например, 30к символов
    if len(text) > MAX_FILE_CHARS:
        truncated = text[:MAX_FILE_CHARS]
        warning_msg = f"\n\n(⚠️ Текст файла был обрезан до {MAX_FILE_CHARS} символов)"
        logger.warning(f"ChatID: {chat_id} | Текст файла '{doc.file_name}' обрезан до {MAX_FILE_CHARS} символов.")
    else:
        truncated = text
        warning_msg = ""

    user_caption = update.message.caption # Проверим, есть ли подпись к файлу

    if user_caption:
        user_prompt = f"Проанализируй содержимое файла '{doc.file_name}', учитывая мой комментарий: \"{user_caption}\".\n\nСодержимое файла:\n```\n{truncated}\n```{warning_msg}"
    else:
        user_prompt = f"Вот текст из файла '{doc.file_name}'. Что ты можешь сказать об этом?\n\nСодержимое файла:\n```\n{truncated}\n```{warning_msg}"

    # Снова используем "костыль" с фейковым update, чтобы передать в handle_message
    fake_update = type('obj', (object,), {
        'effective_chat': update.effective_chat,
        'message': type('obj', (object,), {
            'text': user_prompt,
            'reply_text': update.message.reply_text
        })
    })
    await handle_message(fake_update, context)


async def setup_bot_and_server(stop_event: asyncio.Event):
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Регистрируем хендлеры
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("temp", set_temperature))
    application.add_handler(CommandHandler("search_on", enable_search))
    application.add_handler(CommandHandler("search_off", disable_search))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    # Обработчик фото должен идти ПЕРЕД текстовым, чтобы перехватывать фото с подписями
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
     # Обработчик документов
    application.add_handler(MessageHandler(filters.Document.TEXT, handle_document)) # Явно только текстовые
    # Основной обработчик текста (должен быть последним из MessageHandler для текста)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # ===== УДАЛЕНО: Лишний хендлер handle_image_prompt, который вызывал ошибку =====
    # application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_image_prompt))

    await application.initialize()
    webhook_url = urljoin(WEBHOOK_HOST, f"/{GEMINI_WEBHOOK_PATH}") # Добавил / перед путем вебхука
    logger.info(f"Устанавливаю вебхук: {webhook_url}")
    await application.bot.set_webhook(webhook_url, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True) # Явно указываем типы апдейтов
    return application, run_web_server(application, stop_event)


async def run_web_server(application: Application, stop_event: asyncio.Event):
    app = aiohttp.web.Application()
    # Добавим проверку живости для Render
    async def health_check(request):
        return aiohttp.web.Response(text="OK")
    app.router.add_get('/', health_check) # Для проверки доступности сервиса Render'ом

    # Путь для вебхука Telegram
    app['bot_app'] = application
    webhook_path = f"/{GEMINI_WEBHOOK_PATH}"
    app.router.add_post(webhook_path, handle_telegram_webhook)
    logger.info(f"Вебхук слушает на пути: {webhook_path}")

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000")) # Render подставит свой PORT
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", port)
    try:
        await site.start()
        logger.info(f"Сервер запущен на http://0.0.0.0:{port}")
        # Keep the server running until stop_event is set
        await stop_event.wait()
    finally:
        logger.info("Останавливаю веб-сервер...")
        await runner.cleanup()
        logger.info("Веб-сервер остановлен.")


async def handle_telegram_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    application = request.app.get('bot_app')
    if not application:
        logger.error("Объект приложения бота не найден в контексте aiohttp!")
        return aiohttp.web.Response(status=500, text="Internal Server Error: Bot application not configured")

    try:
        data = await request.json()
        # logger.debug(f"Получен апдейт: {data}") # Раскомментируй для детальной отладки вебхуков
        update = Update.de_json(data, application.bot)
        # Запускаем обработку апдейта в фоне, чтобы быстро ответить Telegram (200 OK)
        # и избежать таймаутов, если обработка будет долгой
        asyncio.create_task(application.process_update(update))
        # Моментально отвечаем Telegram, что мы приняли запрос
        return aiohttp.web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"Ошибка обработки вебхук-запроса: {e}", exc_info=True)
        # Не отвечаем ошибкой Telegram, т.к. он может начать спамить повторами
        # Просто логируем и возвращаем OK, раз уж мы приняли запрос
        return aiohttp.web.Response(text="OK", status=200) # Отвечаем ОК даже при ошибке обработки


async def main():
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    # Настраиваем обработку сигналов завершения
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    application = None # Инициализируем заранее
    web_server_task = None
    try:
        logger.info("Запускаю настройку бота и сервера...")
        application, web_server_coro = await setup_bot_and_server(stop_event)
        # Запускаем веб-сервер как задачу asyncio
        web_server_task = asyncio.create_task(web_server_coro)
        logger.info("Настройка завершена, жду сигналов остановки...")
        # Основной цикл ждет события остановки (веб-сервер работает в фоне)
        await stop_event.wait()

    except Exception as e:
        logger.exception("Критическая ошибка в главном потоке приложения.")
    finally:
        logger.info("Начинаю процесс остановки...")
        if web_server_task and not web_server_task.done():
             # Даем серверу сигнал остановиться (если он ждет stop_event)
             # stop_event уже должен быть установлен обработчиком сигнала
             # Просто ждем завершения задачи сервера
             logger.info("Ожидаю завершения веб-сервера...")
             try:
                 await asyncio.wait_for(web_server_task, timeout=10.0) # Даем 10 сек на штатное завершение
             except asyncio.TimeoutError:
                 logger.warning("Веб-сервер не завершился за 10 секунд, отменяю задачу...")
                 web_server_task.cancel()
             except Exception as e:
                 logger.error(f"Ошибка при ожидании/отмене задачи веб-сервера: {e}")

        if application:
            logger.info("Останавливаю приложение бота...")
            await application.shutdown()
            logger.info("Приложение бота остановлено.")
        else:
            logger.warning("Объект приложения бота не был создан или был потерян.")

        logger.info("Приложение полностью остановлено.")

if __name__ == '__main__':
    # Используем asyncio.run для упрощения запуска
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение прервано пользователем (Ctrl+C)")
    except Exception as e:
        logger.critical(f"Неперехваченная ошибка на верхнем уровне: {e}", exc_info=True)
