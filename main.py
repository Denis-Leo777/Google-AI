# Обновлённый main.py с:
# ... (все предыдущие фичи)
# + Обработка генерации изображений для модели 'Image Gen'

import logging
import os
import asyncio
import signal
from urllib.parse import urljoin
import base64
import pytesseract
from PIL import Image
import io # Нужен для отправки байтов картинки

import aiohttp.web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile # Добавили InputFile
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

# Логгирование
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Импорты для Tool
try:
    from google.ai.generativelanguage_v1beta.types import Tool, GoogleSearchRetrieval
    logger.info("Успешно импортированы Tool и GoogleSearchRetrieval из google.ai.generativelanguage_v1beta.types")
except ImportError as e:
    logger.critical(f"Не удалось импортировать необходимые классы Tool и GoogleSearchRetrieval! Ошибка: {e}")
    logger.critical("Проверьте версии библиотек google-generativeai, google-ai-generativelanguage, protobuf.")
    exit(1)

# Переменные окружения и их проверка ... (без изменений)
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

# ===== КОНСТАНТА ДЛЯ МОДЕЛИ ГЕНЕРАЦИИ КАРТИНОК =====
IMAGE_GEN_MODEL = 'gemini-2.0-flash-exp-image-generation'
# =====================================================

AVAILABLE_MODELS = {
    'gemini-2.5-pro-exp-03-25': '2.5 Pro (Exp)',
    'gemini-2.0-flash-001': '2.0 Flash',
    IMAGE_GEN_MODEL: 'Image Gen (Exp)' # Используем константу
}
DEFAULT_MODEL = 'gemini-2.5-pro-exp-03-25'

# Поиск не поддерживается только моделью генерации картинок
MODELS_WITHOUT_SEARCH = {IMAGE_GEN_MODEL}

user_selected_model = {}
user_search_enabled = {}
user_temperature = {}

MAX_CONTEXT_CHARS = 95000

# Инструкция системе ... (без изменений)
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


# Команды start, clear_history, set_temperature, enable_search, disable_search, model_command ... (без изменений, кроме текста кнопок/сообщений)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_selected_model[chat_id] = DEFAULT_MODEL
    user_search_enabled[chat_id] = True
    user_temperature[chat_id] = 1.0

    default_model_name = AVAILABLE_MODELS.get(DEFAULT_MODEL, DEFAULT_MODEL)
    start_message = (
        f"Добро пожаловать! По умолчанию используется модель **{default_model_name}**."
        f"\nВы можете пользоваться Google-поиском, улучшенными настройками, чтением изображений и текстовых файлов, а также **генерацией картинок**." # Упомянули генерацию
        "\n/model — выбор модели,"
        "\n/clear — очистить историю."
        "\n/temp <0-2> — установить температуру (креативность)."
        "\n/search_on /search_off — вкл/выкл Google Поиск (не работает для Image Gen)." # Уточнили
        "\nКанал автора: t.me/denisobovsyom"
    )

    await update.message.reply_text(start_message, parse_mode='Markdown')

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['history'] = []
    await update.message.reply_text("🧹 История диалога очищена.")

async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        temp = float(context.args[0])
        if not (0 <= temp <= 2):
            raise ValueError
        user_temperature[chat_id] = temp
        await update.message.reply_text(f"🌡️ Температура установлена на {temp}")
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Укажите температуру от 0 до 2, например: /temp 1.0")

async def enable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_search_enabled[chat_id] = True
    reply_text = "🔍 Google-поиск включён (кроме модели Image Gen)."
    await update.message.reply_text(reply_text)

async def disable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_search_enabled[update.effective_chat.id] = False
    await update.message.reply_text("🔇 Google-поиск отключён.")

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current_model = user_selected_model.get(chat_id, DEFAULT_MODEL)
    keyboard = []
    for m, name in AVAILABLE_MODELS.items():
         button_text = f"{'✅ ' if m == current_model else ''}{name}"
         if m == IMAGE_GEN_MODEL: # Используем константу
             button_text += " (🚫 Поиск)"
         keyboard.append([InlineKeyboardButton(button_text, callback_data=m)])

    await update.message.reply_text("Выберите модель:", reply_markup=InlineKeyboardMarkup(keyboard))


async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    selected = query.data
    if selected in AVAILABLE_MODELS:
        user_selected_model[chat_id] = selected
        model_name = AVAILABLE_MODELS[selected]
        reply_text = f"Модель установлена: **{model_name}**"
        if selected == IMAGE_GEN_MODEL: # Используем константу
            reply_text += "\n⚠️ Google-поиск для этой модели **всегда отключён**."
        else:
            search_status = "включён" if user_search_enabled.get(chat_id, True) else "выключен"
            reply_text += f"\nℹ️ Google-поиск сейчас **{search_status}** (/search_on /search_off)."

        await query.edit_message_text(reply_text, parse_mode='Markdown')
    else:
        await query.edit_message_text("❌ Неизвестная модель")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = update.message.text.strip()
    if not user_message:
        return

    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    temperature = user_temperature.get(chat_id, 1.0)

    # ===== ИСПРАВЛЕНИЕ: Отправляем typing или uploading_photo в зависимости от модели =====
    if model_id == IMAGE_GEN_MODEL:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
        logger.info(f"ChatID: {chat_id} | Запрос на генерацию изображения. Модель: {model_id}, Темп: {temperature}")
        # Системная инструкция для генерации картинок может быть другой или не нужна
        # Пока используем ту же, но можно переопределить
        current_system_instruction = "Generate an image based on the following description:" # Пример
        current_history = [{"role": "user", "parts": [{"text": user_message}]}] # Не используем историю чата
        use_search = False # Поиск не нужен/не поддерживается
        tools = []
    else:
        # Обычная обработка текста
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        is_search_supported = model_id not in MODELS_WITHOUT_SEARCH
        use_search = is_search_supported and user_search_enabled.get(chat_id, True)
        search_log_reason = f"Модель поддерживает: {is_search_supported}, Настройка пользователя: {user_search_enabled.get(chat_id, True)}"
        logger.info(f"ChatID: {chat_id} | Модель: {model_id}, Темп: {temperature}, Поиск: {'ДА' if use_search else 'НЕТ'} ({search_log_reason})")

        chat_history = context.chat_data.setdefault("history", [])
        chat_history.append({"role": "user", "parts": [{"text": user_message}]})

        total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
        while total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1:
            removed_message = chat_history.pop(0)
            total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
            logger.info(f"ChatID: {chat_id} | История обрезана, удалено сообщение: {removed_message.get('role')}, текущая длина истории: {len(chat_history)}, символов: {total_chars}")
        current_history = chat_history # Используем полную историю для текстовых моделей
        current_system_instruction = system_instruction_text # Используем основную системную инструкцию
        tools = [Tool(google_search=GoogleSearchRetrieval())] if use_search else []
    # ====================================================================================

    reply = None # Инициализируем reply как None

    try:
        model = genai.GenerativeModel(
            model_id,
            tools=tools,
            safety_settings=[],
            generation_config={"temperature": temperature},
            system_instruction=current_system_instruction # Используем актуальную инструкцию
        )
        response = model.generate_content(current_history) # Используем актуальную историю

        # ===== ИСПРАВЛЕНИЕ: Разная обработка ответа =====
        if model_id == IMAGE_GEN_MODEL:
            images_sent = 0
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                        logger.info(f"ChatID: {chat_id} | Получены данные изображения ({part.inline_data.mime_type})")
                        image_bytes = base64.b64decode(part.inline_data.data)
                        image_file = io.BytesIO(image_bytes)
                        # Отправляем как фото
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=image_file,
                            caption=f"🎨 Изображение по запросу: \"{user_message[:100]}{'...' if len(user_message)>100 else ''}\""
                        )
                        images_sent += 1
                        # TODO: Обработать случай нескольких картинок, если API их возвращает

            if images_sent == 0:
                 logger.warning(f"ChatID: {chat_id} | Модель {model_id} не вернула данные изображения.")
                 reply = "❌ Не удалось сгенерировать изображение. Модель не вернула картинку."
            # Если картинка отправлена, текстовый ответ не нужен
            # Можно присвоить reply = None или пустую строку, чтобы ниже не отправился текст

        else: # Обработка для текстовых моделей (как было раньше)
            reply = response.text
            if not reply:
                try:
                    feedback = response.prompt_feedback
                    candidates_info = response.candidates
                    block_reason = feedback.block_reason if feedback else 'N/A'
                    finish_reason = candidates_info[0].finish_reason if candidates_info else 'N/A'
                    safety_ratings = feedback.safety_ratings if feedback else []
                    safety_info = ", ".join([f"{s.category.name}: {s.probability.name}" for s in safety_ratings])
                    logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели. Block: {block_reason}, Finish: {finish_reason}, Safety: [{safety_info}]")
                    reply = f"🤖 Модель не дала ответ. (Причина: {block_reason or finish_reason})"
                except Exception as e_inner:
                    logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели, не удалось извлечь доп. инфо: {e_inner}")
                    reply = "🤖 Нет ответа от модели."

            # Добавляем ответ текстовой модели в историю
            if reply: # Добавляем только если есть текстовый ответ
                 chat_history.append({"role": "model", "parts": [{"text": reply}]})
        # ===============================================

    except Exception as e:
        logger.exception(f"ChatID: {chat_id} | Ошибка при взаимодействии с моделью {model_id}")
        error_message = str(e)
        if "Search Grounding is not supported" in error_message:
            reply = f"❌ Ошибка: Похоже, модель **{AVAILABLE_MODELS.get(model_id, model_id)}** всё-таки не поддерживает Google-поиск. Попробуйте отключить его командой /search_off или выбрать другую модель через /model."
        elif "400" in error_message and "API key not valid" in error_message:
             reply = "❌ Ошибка: Неверный Google API ключ."
        elif "Deadline Exceeded" in error_message:
             reply = "❌ Ошибка: Модель слишком долго отвечала (таймаут)."
        # ===== ИСПРАВЛЕНИЕ: Сообщение об ошибке для Image Gen =====
        elif model_id == IMAGE_GEN_MODEL:
            reply = f"❌ Не удалось сгенерировать изображение из-за ошибки:\n`{error_message}`"
        else:
             reply = f"❌ Ошибка при обращении к модели:\n`{error_message}`"
        # =====================================================

    # Отправляем текстовый ответ ТОЛЬКО если он есть (т.е. не для успешной генерации картинки)
    if reply:
        await update.message.reply_text(reply, parse_mode='Markdown')


# Функции handle_photo, handle_document, setup_bot_and_server, run_web_server, handle_telegram_webhook, main
# остаются БЕЗ ИЗМЕНЕНИЙ по сравнению с предыдущей версией.
# ... (код этих функций как в предыдущем ответе) ...


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    photo_file = await update.message.photo[-1].get_file()
    file_bytes = await photo_file.download_as_bytearray()
    user_caption = update.message.caption

    # Попытка OCR
    try:
        image = Image.open(io.BytesIO(file_bytes))
        extracted_text = pytesseract.image_to_string(image, lang='rus+eng')
        if extracted_text and extracted_text.strip():
            logger.info(f"ChatID: {chat_id} | Обнаружен текст на изображении (OCR)")
            ocr_prompt = f"На изображении обнаружен следующий текст:\n```\n{extracted_text.strip()}\n```\n"
            if user_caption:
                 user_prompt = f"{user_caption}\n{ocr_prompt}Проанализируй изображение и текст на нём, учитывая мой комментарий."
            else:
                 user_prompt = f"{ocr_prompt}Проанализируй изображение и текст на нём."

            fake_update = type('obj', (object,), {
                'effective_chat': update.effective_chat,
                'message': type('obj', (object,), {
                    'text': user_prompt,
                    'reply_text': update.message.reply_text
                })
            })
            await handle_message(fake_update, context)
            return
    except pytesseract.TesseractNotFoundError:
         logger.error("Tesseract не найден! Убедитесь, что он установлен и доступен в PATH (включая языки tesseract-ocr-eng, tesseract-ocr-rus в render.yaml). OCR отключен.")
    except Exception as e:
        logger.warning(f"ChatID: {chat_id} | Ошибка OCR (кроме TesseractNotFoundError): {e}")

    # Обработка как изображение
    logger.info(f"ChatID: {chat_id} | Обработка фото как изображения (без OCR или текст не найден)")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    b64_data = base64.b64encode(file_bytes).decode()
    prompt = user_caption if user_caption else "Что изображено на этом фото?"
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}
    ]

    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    temperature = user_temperature.get(chat_id, 1.0)

    is_search_supported = model_id not in MODELS_WITHOUT_SEARCH
    use_search = is_search_supported and user_search_enabled.get(chat_id, True)

    search_log_reason = f"Модель поддерживает: {is_search_supported}, Настройка пользователя: {user_search_enabled.get(chat_id, True)}"
    logger.info(f"ChatID: {chat_id} | Анализ изображения. Модель: {model_id}, Темп: {temperature}, Поиск: {'ДА' if use_search else 'НЕТ'} ({search_log_reason})")

    try:
        tools = [Tool(google_search=GoogleSearchRetrieval())] if use_search else []

        model = genai.GenerativeModel(
            model_id,
            tools=tools,
            safety_settings=[],
            generation_config={"temperature": temperature},
            system_instruction=system_instruction_text # Для анализа фото системная инструкция может быть полезна
        )
        # Для анализа фото передаем parts напрямую
        response = model.generate_content([{"role": "user", "parts": parts}])
        reply = response.text

        if not reply:
            try:
                feedback = response.prompt_feedback
                candidates_info = response.candidates
                block_reason = feedback.block_reason if feedback else 'N/A'
                finish_reason = candidates_info[0].finish_reason if candidates_info else 'N/A'
                safety_ratings = feedback.safety_ratings if feedback else []
                safety_info = ", ".join([f"{s.category.name}: {s.probability.name}" for s in safety_ratings])
                logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения. Block: {block_reason}, Finish: {finish_reason}, Safety: [{safety_info}]")
                reply = f"🤖 Модель не смогла описать изображение. (Причина: {block_reason or finish_reason})"
            except Exception as e_inner:
                 logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения, не удалось извлечь доп. инфо: {e_inner}")
                 reply = "🤖 Не удалось понять, что на изображении."

    except Exception as e:
        logger.exception(f"ChatID: {chat_id} | Ошибка при анализе изображения")
        error_message = str(e)
        if "Search Grounding is not supported" in error_message:
            reply = f"❌ Ошибка: Похоже, модель **{AVAILABLE_MODELS.get(model_id, model_id)}** всё-таки не поддерживает Google-поиск при анализе изображений. Попробуйте отключить его командой /search_off или выбрать другую модель через /model."
        elif "400" in error_message and "API key not valid" in error_message:
             reply = "❌ Ошибка: Неверный Google API ключ."
        else:
            reply = f"❌ Ошибка при анализе изображения:\n`{error_message}`"

    # Отправляем текстовый ответ (описание картинки)
    await update.message.reply_text(reply, parse_mode='Markdown')


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not update.message.document:
        return
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith('text/'):
        await update.message.reply_text("⚠️ Пока могу читать только текстовые файлы (.txt, .py, .csv и т.п.).")
        logger.warning(f"ChatID: {chat_id} | Попытка загрузить нетекстовый файл: {doc.mime_type}")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    doc_file = await doc.get_file()
    file_bytes = await doc_file.download_as_bytearray()
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = file_bytes.decode("latin-1")
            logger.warning(f"ChatID: {chat_id} | Файл не в UTF-8, использован latin-1.")
        except Exception as e:
            logger.error(f"ChatID: {chat_id} | Не удалось декодировать файл: {e}")
            await update.message.reply_text("❌ Не удалось прочитать текстовое содержимое файла. Убедитесь, что это текстовый файл в кодировке UTF-8 или Latin-1.")
            return

    MAX_FILE_CHARS = 30000
    if len(text) > MAX_FILE_CHARS:
        truncated = text[:MAX_FILE_CHARS]
        warning_msg = f"\n\n(⚠️ Текст файла был обрезан до {MAX_FILE_CHARS} символов)"
        logger.warning(f"ChatID: {chat_id} | Текст файла '{doc.file_name}' обрезан до {MAX_FILE_CHARS} символов.")
    else:
        truncated = text
        warning_msg = ""

    user_caption = update.message.caption

    if user_caption:
        user_prompt = f"Проанализируй содержимое файла '{doc.file_name}', учитывая мой комментарий: \"{user_caption}\".\n\nСодержимое файла:\n```\n{truncated}\n```{warning_msg}"
    else:
        user_prompt = f"Вот текст из файла '{doc.file_name}'. Что ты можешь сказать об этом?\n\nСодержимое файла:\n```\n{truncated}\n```{warning_msg}"

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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("temp", set_temperature))
    application.add_handler(CommandHandler("search_on", enable_search))
    application.add_handler(CommandHandler("search_off", disable_search))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.TEXT, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await application.initialize()
    webhook_url = urljoin(WEBHOOK_HOST, f"/{GEMINI_WEBHOOK_PATH}")
    logger.info(f"Устанавливаю вебхук: {webhook_url}")
    await application.bot.set_webhook(webhook_url, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    return application, run_web_server(application, stop_event)


async def run_web_server(application: Application, stop_event: asyncio.Event):
    app = aiohttp.web.Application()
    async def health_check(request):
        return aiohttp.web.Response(text="OK")
    app.router.add_get('/', health_check)

    app['bot_app'] = application
    webhook_path = f"/{GEMINI_WEBHOOK_PATH}"
    app.router.add_post(webhook_path, handle_telegram_webhook)
    logger.info(f"Вебхук слушает на пути: {webhook_path}")

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", port)
    try:
        await site.start()
        logger.info(f"Сервер запущен на http://0.0.0.0:{port}")
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
        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
        return aiohttp.web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"Ошибка обработки вебхук-запроса: {e}", exc_info=True)
        return aiohttp.web.Response(text="OK", status=200)


async def main():
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    application = None
    web_server_task = None
    try:
        logger.info("Запускаю настройку бота и сервера...")
        application, web_server_coro = await setup_bot_and_server(stop_event)
        web_server_task = asyncio.create_task(web_server_coro)
        logger.info("Настройка завершена, жду сигналов остановки...")
        await stop_event.wait()

    except Exception as e:
        logger.exception("Критическая ошибка в главном потоке приложения.")
    finally:
        logger.info("Начинаю процесс остановки...")
        if web_server_task and not web_server_task.done():
             logger.info("Ожидаю завершения веб-сервера...")
             try:
                 await asyncio.wait_for(web_server_task, timeout=10.0)
             except asyncio.TimeoutError:
                 logger.warning("Веб-сервер не завершился за 10 секунд, отменяю задачу...")
                 web_server_task.cancel()
                 try:
                     await web_server_task
                 except asyncio.CancelledError:
                     logger.info("Задача веб-сервера успешно отменена.")
                 except Exception as e:
                     logger.error(f"Ошибка при ожидании отмены задачи веб-сервера: {e}")
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение прервано пользователем (Ctrl+C)")
    except Exception as e:
        logger.critical(f"Неперехваченная ошибка на верхнем уровне: {e}", exc_info=True)
