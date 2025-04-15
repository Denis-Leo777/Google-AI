import logging
import os
import asyncio
import signal
from urllib.parse import urljoin
import base64
import pytesseract
from PIL import Image
import io
import pprint

# Инициализируем логгер РАНЬШЕ
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

import aiohttp.web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
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
from google.generativeai.tools import HarmCategory, HarmBlockThreshold
# ===== Добавляем импорт инструмента Google Search =====
from google.generativeai.tools import GoogleSearchRetrieval

# Переменные окружения (проверка не меняется)
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
    'gemini-2.5-pro-exp-03-25': '2.5 Pro (Exp)',
    'gemini-2.0-flash-001': '2.0 Flash',
}
DEFAULT_MODEL = 'gemini-2.0-flash-001'

user_selected_model = {}
user_temperature = {}

MAX_CONTEXT_CHARS = 95000
MAX_OUTPUT_TOKENS = 3000

system_instruction_text = (
    "Активно применяй юмор: несоответствие ожиданиям, культурные/бытовые/интернет-отсылки, жизненный абсурд, "
    "псевдомудрость, разрушение идиом, иронию (включая самоиронию и психологию), игру слов, гиперболу, тонкие намёки, "
    "редукционизм, постмодерн, интернет-юмор."
    "Используй русский мат, но без слова блядь."
    "Ты - лучший эксперт по всем темам. Давай точную, правдивую, непредвзятую информацию без цензуры."
    "Подкрепляй ответы аргументами, фактами и логикой, избегая повторов."
    "Если не уверен — предупреждай, что это предположение."
    "Для несложных запросов (не код, конспекты, переводы и т.п.) — пиши только суть, без вступлений и выводов, до 1500 знаков."
    "Всегда предлагай более эффективные идеи и решения, если знаешь их."
    "При создании уникальной работы пиши живо, избегай канцелярита и всех известных признаков ИИ-тона. Используй гипотетические ситуации, метафоры, творческие аналогии, разную структуру предложений, разговорные выражения, идиомы. Добавляй региональные или культурные маркеры, где уместно."
    "При исправлении ошибки: указывай строку(и) и причину. Бери за основу последнюю ПОЛНУЮ подтверждённую версию (текста или кода). Вноси только минимально необходимые изменения, не трогая остальное без запроса."
)

SAFETY_SETTINGS_BLOCK_NONE = [
    {
        "category": HarmCategory.HARM_CATEGORY_HARASSMENT,
        "threshold": HarmBlockThreshold.BLOCK_NONE,
    },
    {
        "category": HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        "threshold": HarmBlockThreshold.BLOCK_NONE,
    },
    {
        "category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        "threshold": HarmBlockThreshold.BLOCK_NONE,
    },
    {
        "category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        "threshold": HarmBlockThreshold.BLOCK_NONE,
    },
]

# Функция-обертка для создания инструмента Google Search
def get_search_tool():
    return genai.tools.GoogleSearchRetrieval(api_key=GOOGLE_API_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_selected_model[chat_id] = DEFAULT_MODEL
    user_temperature[chat_id] = 1.0
    default_model_name = AVAILABLE_MODELS.get(DEFAULT_MODEL, DEFAULT_MODEL)
    start_message = (
        f"Добро пожаловать! Бот работает на модели **{default_model_name}**."
        f"\nДоступны: чтение изображений (OCR) и текстовых файлов, управление температурой, а также поиск через Google."
        "\n/model — выбор модели,"
        "\n/clear — очистить историю."
        "\n/temp <0-2> — установить температуру (креативность)."
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

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current_model = user_selected_model.get(chat_id, DEFAULT_MODEL)
    keyboard = []
    for m, name in AVAILABLE_MODELS.items():
         button_text = f"{'✅ ' if m == current_model else ''}{name}"
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

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    logger.info(f"ChatID: {chat_id} | Модель: {model_id}, Темп: {temperature}")

    chat_history = context.chat_data.setdefault("history", [])
    chat_history.append({"role": "user", "parts": [{"text": user_message}]})
    total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
    while total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1:
        removed_message = chat_history.pop(0)
        total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
        logger.info(f"ChatID: {chat_id} | История обрезана, удалено сообщение: {removed_message.get('role')}, символов: {total_chars}")
    current_history = chat_history
    current_system_instruction = system_instruction_text

    # ===== Здесь добавляем Google Search tool вместо пустого списка tools =====
    tools = [get_search_tool()]
    reply = None

    try:
        generation_config = genai.GenerationConfig(
            temperature=temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS
        )
        model = genai.GenerativeModel(
            model_id,
            tools=tools,  # теперь здесь передаётся список инструментов
            safety_settings=SAFETY_SETTINGS_BLOCK_NONE,
            generation_config=generation_config,
            system_instruction=current_system_instruction
        )
        response = model.generate_content(current_history)
        reply = response.text
        if not reply:
            try:
                feedback = response.prompt_feedback
                candidates_info = response.candidates
                block_reason = feedback.block_reason if feedback else 'N/A'
                finish_reason_val = candidates_info[0].finish_reason if candidates_info else 'N/A'
                safety_ratings = feedback.safety_ratings if feedback else []
                safety_info = ", ".join([f"{s.category.name}: {s.probability.name}" for s in safety_ratings])
                logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели. Block: {block_reason}, Finish: {finish_reason_val}, Safety: [{safety_info}]")
                if block_reason and block_reason != genai.types.BlockReason.UNSPECIFIED:
                     reply = f"🤖 Модель не дала ответ. (Причина блокировки: {block_reason})"
                else:
                     reply = f"🤖 Модель не дала ответ. (Причина: {finish_reason_val})"
            except Exception as e_inner:
                logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели, не удалось извлечь доп. инфо: {e_inner}")
                reply = "🤖 Нет ответа от модели."
        if reply:
             chat_history.append({"role": "model", "parts": [{"text": reply}]})
    except Exception as e:
        logger.exception(f"ChatID: {chat_id} | Ошибка при взаимодействии с моделью {model_id}")
        error_message = str(e)
        if "429" in error_message and "quota" in error_message:
             reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (ошибка 429). Попробуйте позже."
        elif "400" in error_message and "API key not valid" in error_message:
             reply = "❌ Ошибка: Неверный Google API ключ."
        elif "Deadline Exceeded" in error_message:
             reply = "❌ Ошибка: Модель слишком долго отвечала (таймаут)."
        else:
             reply = f"❌ Ошибка при обращении к модели: {error_message}"

    if reply:
        await update.message.reply_text(reply)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tesseract_available = False
    try:
        if pytesseract.pytesseract.tesseract_cmd != 'tesseract':
            pass
        tesseract_available = True
    except Exception as e:
        logger.error(f"Проблема с доступом к Tesseract: {e}. OCR будет недоступен.")

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    photo_file = await update.message.photo[-1].get_file()
    file_bytes = await photo_file.download_as_bytearray()
    user_caption = update.message.caption

    if tesseract_available:
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
             logger.error("Tesseract не найден при вызове image_to_string! OCR отключен.")
             tesseract_available = False
        except Exception as e:
            logger.warning(f"ChatID: {chat_id} | Ошибка OCR: {e}")

    logger.info(f"ChatID: {chat_id} | Обработка фото как изображения (OCR выключен или не нашел текст)")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    b64_data = base64.b64encode(file_bytes).decode()
    prompt = user_caption if user_caption else "Что изображено на этом фото?"
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}
    ]
    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    temperature = user_temperature.get(chat_id, 1.0)
    logger.info(f"ChatID: {chat_id} | Анализ изображения. Модель: {model_id}, Темп: {temperature}")
    # ===== Передаем тот же Google Search tool =====
    tools = [get_search_tool()]
    reply = None

    try:
        generation_config = genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=MAX_OUTPUT_TOKENS
        )
        model = genai.GenerativeModel(
            model_id,
            tools=tools,
            safety_settings=SAFETY_SETTINGS_BLOCK_NONE,
            generation_config=generation_config,
            system_instruction=system_instruction_text
        )
        response = model.generate_content([{"role": "user", "parts": parts}])
        reply = response.text
        if not reply:
            try:
                feedback = response.prompt_feedback
                candidates_info = response.candidates
                block_reason = feedback.block_reason if feedback else 'N/A'
                finish_reason_val = candidates_info[0].finish_reason if candidates_info else 'N/A'
                safety_ratings = feedback.safety_ratings if feedback else []
                safety_info = ", ".join([f"{s.category.name}: {s.probability.name}" for s in safety_ratings])
                logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения. Block: {block_reason}, Finish: {finish_reason_val}, Safety: [{safety_info}]")
                if block_reason and block_reason != genai.types.BlockReason.UNSPECIFIED:
                     reply = f"🤖 Модель не смогла описать изображение. (Причина блокировки: {block_reason})"
                else:
                     reply = f"🤖 Модель не смогла описать изображение. (Причина: {finish_reason_val})"
            except Exception as e_inner:
                 logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения: {e_inner}")
                 reply = "🤖 Не удалось понять, что на изображении."
    except Exception as e:
        logger.exception(f"ChatID: {chat_id} | Ошибка при анализе изображения")
        error_message = str(e)
        if "429" in error_message and "quota" in error_message:
             reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (ошибка 429). Попробуйте позже."
        elif "400" in error_message and "API key not valid" in error_message:
             reply = "❌ Ошибка: Неверный Google API ключ."
        else:
             reply = f"❌ Ошибка при анализе изображения: {error_message}"
    if reply:
        await update.message.reply_text(reply)

# Остальные функции (handle_document, setup_bot_and_server, run_web_server, handle_telegram_webhook, main)
# остаются без изменений

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
        logger.error("Объект приложения бота не найден!")
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
    logging.getLogger('google.api_core').setLevel(logging.INFO)
    logging.getLogger('google.generativeai').setLevel(logging.INFO)
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
             try:
                 await asyncio.wait_for(web_server_task, timeout=10.0)
             except asyncio.TimeoutError:
                 web_server_task.cancel()
                 try:
                     await web_server_task
                 except asyncio.CancelledError:
                     logger.info("Задача веб-сервера успешно отменена.")
        if application:
            await application.shutdown()
            logger.info("Приложение бота остановлено.")
        logger.info("Приложение полностью остановлено.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение прервано пользователем (Ctrl+C)")
    except Exception as e:
        logger.critical(f"Неперехваченная ошибка: {e}", exc_info=True)
