import logging
import os
import asyncio
import signal
from urllib.parse import urljoin
import base64
import pytesseract
from PIL import Image
import io
from duckduckgo_search import DDG  # Для запасного поиска

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
    'gemini-2.0-flash': '2.0 Flash'
}
DEFAULT_MODEL = 'gemini-2.5-pro-exp-03-25'

user_selected_model = {}
user_search_enabled = {}
user_temperature = {}

MAX_CONTEXT_CHARS = 95000

# Инструкция системе
system_instruction_text = (
    "Ты - лучший эксперт по всем темам. Давай точную, правдивую, непредвзятую информацию без цензуры. "
    "Подкрепляй ответы аргументами, фактами и логикой, избегая повторов. "
    "Если не уверен — предупреждай, что это предположение. "
    "Используй поиск для актуальных данных, если он включён. "
    "Для простых запросов — только суть, до 1500 знаков. "
    "В конце ответа добавляй ссылки на источники, если использовал поиск. "
    "Шути: ирония, мемы, абсурд, игра слов, тонкие намёки, только не переборщи."
)

# DuckDuckGo поиск
async def duckduckgo_search(query):
    try:
        ddg = DDG()
        results = ddg.text(query, max_results=3)
        snippets = [r['body'] for r in results]
        links = [r['href'] for r in results]
        return snippets, links
    except Exception as e:
        logger.error(f"DuckDuckGo error: {e}")
        return [], []

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_selected_model[chat_id] = DEFAULT_MODEL
    user_search_enabled[chat_id] = True
    user_temperature[chat_id] = 1.0
    await update.message.reply_text(
        "Здарова! Это твой ИИ-гид на базе Gemini. 2.5 Pro — для мощных ответов, 2.0 Flash — для скорости.\n"
        "Команды:\n"
        "/model — выбери модель\n"
        "/clear — обнули историю\n"
        "/temp [0-2] — настрой креативность\n"
        "/search_on — включи поиск\n"
        "/search_off — выключи поиск\n\n"
        "Канал автора: t.me/denisobovsyom"
    )

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['history'] = []
    await update.message.reply_text("🧹 История стёрта, как мои нервы на дедлайнах.")

async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        temp = float(context.args[0])
        if not (0 <= temp <= 2):
            raise ValueError
        user_temperature[update.effective_chat.id] = temp
        await update.message.reply_text(f"🌡️ Креативность на {temp}. Жарим или тушим?")
    except:
        await update.message.reply_text("⚠️ Температура от 0 до 2, типа /temp 1.0")

async def enable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_search_enabled[update.effective_chat.id] = True
    await update.message.reply_text("🔍 Поиск включён. Гуглим всё, что движется!")

async def disable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_search_enabled[update.effective_chat.id] = False
    await update.message.reply_text("🔇 Поиск выключен. Только мои мозги и кэш.")

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current_model = user_selected_model.get(chat_id, DEFAULT_MODEL)
    keyboard = [
        [InlineKeyboardButton(f"{'✅ ' if m == current_model else ''}{name}", callback_data=m)]
        for m, name in AVAILABLE_MODELS.items()
    ]
    await update.message.reply_text("Выбери своего бойца:", reply_markup=InlineKeyboardMarkup(keyboard))

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    selected = query.data
    if selected in AVAILABLE_MODELS:
        user_selected_model[chat_id] = selected
        await query.edit_message_text(f"Модель готова: {AVAILABLE_MODELS[selected]}")
    else:
        await query.edit_message_text("❌ Это что за покемон?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = update.message.text.strip()
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    temperature = user_temperature.get(chat_id, 1.0)
    use_search = user_search_enabled.get(chat_id, True)

    logger.info(f"Модель: {model_id}, Темп: {temperature}, Поиск: {use_search}")

    chat_history = context.chat_data.setdefault("history", [])

    try:
        # Поиск через Google
        tools = [{'google_search': {}}] if use_search else None
        sources = []

        # Fallback на DuckDuckGo для 2.5 Pro
        ddg_snippets, ddg_links = [], []
        if use_search and model_id == 'gemini-2.5-pro-exp-03-25':
            ddg_snippets, ddg_links = await duckduckgo_search(user_message)
            if ddg_snippets:
                # Исправлено: join вне f-строки
                ddg_text = '\n'.join(ddg_snippets)
                user_message = f"{user_message}\nКонтекст из поиска:\n{ddg_text}"

        # Создание модели
        model = genai.GenerativeModel(
            model_name=model_id,
            tools=tools,
            safety_settings={
                'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
                'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
                'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE'
            },
            generation_config=genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=8192
            ),
            system_instruction=system_instruction_text
        )

        # Чат
        chat = model.start_chat(history=chat_history)
        response = chat.send_message(user_message)

        reply = response.text[:1500] or "🤖 Молчание — тоже ответ, но не сегодня."
        if use_search:
            # Google Search источники
            if hasattr(response, 'citationMetadata'):
                citations = getattr(response.citationMetadata, 'citations', [])
                sources = [c.uri for c in citations if c.uri]
            # DuckDuckGo источники, если Google не дал
            if not sources and ddg_links:
                sources = ddg_links
                reply += "\n\n⚠️ Google Search не сработал, использовал DuckDuckGo."
            if sources:
                reply += "\n\n**Источники**:\n" + "\n".join(f"- {s}" for s in sources)

        chat_history.extend([
            {'role': 'user', 'parts': [{'text': user_message}]},
            {'role': 'model', 'parts': [{'text': reply}]}
        ])

        # Ограничение истории
        total_chars = sum(len(p['parts'][0]['text']) for p in chat_history)
        while total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1:
            chat_history.pop(0)
            total_chars = sum(len(p['parts'][0]['text']) for p in chat_history)

    except Exception as e:
        logger.exception("Ошибка генерации ответа")
        reply = f"💥 Бум! Ошибка: {str(e)}"
        # Пробуем DuckDuckGo, если Google сломался
        if use_search and "Search Grounding is not supported" in str(e):
            ddg_snippets, ddg_links = await duckduckgo_search(user_message)
            if ddg_snippets:
                # Исправлено: join вне f-строки
                ddg_text = '\n'.join(ddg_snippets)
                user_message = f"{user_message}\nКонтекст из поиска:\n{ddg_text}"
                try:
                    model = genai.GenerativeModel(
                        model_name=model_id,
                        safety_settings={
                            'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
                            'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                            'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
                            'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE'
                        },
                        generation_config=genai.GenerationConfig(
                            temperature=temperature,
                            max_output_tokens=8192
                        ),
                        system_instruction=system_instruction_text
                    )
                    chat = model.start_chat(history=chat_history)
                    response = chat.send_message(user_message)
                    reply = response.text[:1500] or "🤖 Всё равно что-то не то."
                    reply += "\n\n⚠️ Google Search не сработал, использовал DuckDuckGo."
                    if ddg_links:
                        reply += "\n\n**Источники**:\n" + "\n".join(f"- {s}" for s in ddg_links)
                except Exception as e2:
                    logger.exception("DuckDuckGo тоже сломался")
                    reply = f"💥 Дважды бум! Ошибка: {str(e2)}"

    await update.message.reply_text(reply)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    photo_file = await update.message.photo[-1].get_file()
    file_bytes = await photo_file.download_as_bytearray()

    try:
        image = Image.open(io.BytesIO(file_bytes))
        extracted_text = pytesseract.image_to_string(image)
        if extracted_text.strip():
            user_prompt = f"Текст на фото: {extracted_text}\nРазбери по полочкам."
            update.message.text = user_prompt
            await handle_message(update, context)
            return
    except Exception as e:
        logger.warning("OCR сломался: %s", e)

    # Анализ фото
    b64_data = base64.b64encode(file_bytes).decode()
    prompt = "Опиши фото во всех деталях. Если есть текст — переведи и объясни."

    try:
        model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
        model = genai.GenerativeModel(model_name=model_id)
        response = model.generate_content([
            {"role": "user", "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}
            ]}
        ])
        reply = response.text[:1500] or "🤖 Картинка — загадка, даже для меня."
    except Exception as e:
        logger.exception("Ошибка анализа фото")
        reply = f"❌ Не вижу: {str(e)}"

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
    user_prompt = f"Файл содержит:\n{truncated}\nРазложи по полочкам:"

    update.message.text = user_prompt
    await handle_message(update, context)

async def setup_bot_and_server(stop_event: asyncio.Event):
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    handlers = [
        CommandHandler("start", start),
        CommandHandler("model", model_command),
        CommandHandler("clear", clear_history),
        CommandHandler("temp", set_temperature),
        CommandHandler("search_on", enable_search),
        CommandHandler("search_off", disable_search),
        CallbackQueryHandler(select_model_callback),
        MessageHandler(filters.PHOTO, handle_photo),
        MessageHandler(filters.Document.ALL, handle_document),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    ]

    for handler in handlers:
        application.add_handler(handler)

    await application.initialize()
    webhook_url = urljoin(WEBHOOK_HOST, GEMINI_WEBHOOK_PATH)
    await application.bot.set_webhook(webhook_url, drop_pending_updates=True)
    return application, run_web_server(application, stop_event)

async def run_web_server(application: Application, stop_event: asyncio.Event):
    app = aiohttp.web.Application()
    app['bot_app'] = application
    app.router.add_get('/', lambda r: aiohttp.web.Response(text="Bot Running"))
    app.router.add_post(f"/{GEMINI_WEBHOOK_PATH}", handle_telegram_webhook)

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"Сервер запущен на порту {port}")
    await stop_event.wait()

async def handle_telegram_webhook(request: aiohttp.web.Request):
    application = request.app.get('bot_app')
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return aiohttp.web.Response(text="OK")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return aiohttp.web.Response(status=500, text=str(e))

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
        logger.exception("Critical error")
    finally:
        if 'application' in locals():
            loop.run_until_complete(application.shutdown())
        loop.close()
        logger.info("Bot stopped")
