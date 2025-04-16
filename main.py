# --- START OF FILE main.txt ---

# Обновлённый main.py:
# - Исправлена ошибка с вкл/выкл поиска: состояние хранится в context.chat_data
# - Добавлен Google Custom Search API как основной поиск
# - DuckDuckGo используется как запасной вариант
# - Исправлен поиск DDG: используется синхронный ddgs.text() в отдельном потоке через asyncio.to_thread()

import logging
import os
import asyncio
import signal
from urllib.parse import urljoin, urlencode
import base64
import pytesseract
from PIL import Image
import io
import pprint
import json
import aiohttp
import aiohttp.web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    PicklePersistence # ===== ИЗМЕНЕНИЕ: Добавляем для сохранения chat_data =====
)
import google.generativeai as genai
from duckduckgo_search import DDGS
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# Инициализируем логгер
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения и их проверка
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')
# ===== ИЗМЕНЕНИЕ: Имя файла для сохранения состояния =====
PERSISTENCE_FILE = "bot_persistence.pickle"
# =======================================================

for var, name in [
    (TELEGRAM_BOT_TOKEN, "TELEGRAM_BOT_TOKEN"),
    (GOOGLE_API_KEY, "GOOGLE_API_KEY"),
    (GOOGLE_CSE_ID, "GOOGLE_CSE_ID"),
    (WEBHOOK_HOST, "WEBHOOK_HOST"),
    (GEMINI_WEBHOOK_PATH, "GEMINI_WEBHOOK_PATH")
]:
    if not var:
        logger.critical(f"Переменная окружения {name} не задана!")
        exit(1)

# Настройка Gemini
genai.configure(api_key=GOOGLE_API_KEY)

# Модели
AVAILABLE_MODELS = {
    'gemini-2.0-flash-thinking-exp-01-21': '2.0 Flash Thinking (Exp)',
    'gemini-2.5-pro-preview-03-25': '2.5 Pro Preview',
    'gemini-2.5-pro-exp-03-25': '2.5 Pro (Exp)',
    'gemini-2.0-flash-001': '2.0 Flash',
}
DEFAULT_MODEL = 'gemini-2.0-flash-thinking-exp-01-21'

# ===== ИЗМЕНЕНИЕ: Удаляем глобальные словари для состояния =====
# user_search_enabled = {} # <-- УДАЛЕНО
# user_selected_model = {} # <-- УДАЛЕНО (перенесено в chat_data)
# user_temperature = {}    # <-- УДАЛЕНО (перенесено в chat_data)
# ===========================================================

# Константы
MAX_CONTEXT_CHARS = 95000
MAX_OUTPUT_TOKENS = 3000
DDG_MAX_RESULTS = 5
GOOGLE_SEARCH_MAX_RESULTS = 5

# Системная инструкция (без изменений)
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

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ===== ИЗМЕНЕНИЕ: Инициализируем значения в chat_data =====
    context.chat_data['selected_model'] = DEFAULT_MODEL
    context.chat_data['search_enabled'] = True
    context.chat_data['temperature'] = 1.0
    # Убедимся, что история существует
    context.chat_data.setdefault('history', [])
    # ========================================================

    default_model_name = AVAILABLE_MODELS.get(DEFAULT_MODEL, DEFAULT_MODEL)
    start_message = (
        f"**{default_model_name}**."
        f"\n + поиск Google (основной) / DuckDuckGo (запасной), чтение изображений (OCR) и текстовых файлов."
        "\n/model — выбор модели"
        "\n/clear — очистить историю"
        "\n/search_on  /search_off — вкл/выкл поиск"
        "\n/temp X.Y — установить температуру (0.0-2.0)"
    )
    await update.message.reply_text(start_message, parse_mode='Markdown')

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['history'] = []
    await update.message.reply_text("🧹 История диалога очищена.")

async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ===== ИЗМЕНЕНИЕ: Используем chat_data =====
    chat_id = update.effective_chat.id # chat_id все еще нужен для логов, но не для хранения
    try:
        temp = float(context.args[0])
        if not (0 <= temp <= 2):
            raise ValueError
        context.chat_data['temperature'] = temp
        await update.message.reply_text(f"🌡️ Температура установлена на {temp}")
        logger.info(f"ChatID: {chat_id} | Температура установлена на {temp}")
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Укажите температуру от 0 до 2, например: /temp 1.0")
    # ==========================================

async def enable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ===== ИЗМЕНЕНИЕ: Используем chat_data =====
    context.chat_data['search_enabled'] = True
    await update.message.reply_text("🔍 Поиск Google/DDG включён.")
    logger.info(f"ChatID: {update.effective_chat.id} | Поиск включен пользователем.")
    # ==========================================

async def disable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ===== ИЗМЕНЕНИЕ: Используем chat_data =====
    context.chat_data['search_enabled'] = False
    await update.message.reply_text("🔇 Поиск Google/DDG отключён.")
    logger.info(f"ChatID: {update.effective_chat.id} | Поиск ОТКЛЮЧЕН пользователем.") # Добавим лог
    # ==========================================

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ===== ИЗМЕНЕНИЕ: Используем chat_data =====
    chat_id = update.effective_chat.id # Нужен для логов
    current_model = context.chat_data.get('selected_model', DEFAULT_MODEL)
    # ==========================================
    keyboard = []
    for m, name in AVAILABLE_MODELS.items():
         button_text = f"{'✅ ' if m == current_model else ''}{name}"
         keyboard.append([InlineKeyboardButton(button_text, callback_data=m)])
    await update.message.reply_text("Выберите модель:", reply_markup=InlineKeyboardMarkup(keyboard))

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id # Нужен для логов
    selected = query.data
    if selected in AVAILABLE_MODELS:
        # ===== ИЗМЕНЕНИЕ: Используем chat_data =====
        context.chat_data['selected_model'] = selected
        # ==========================================
        model_name = AVAILABLE_MODELS[selected]
        reply_text = f"Модель установлена: **{model_name}**"
        await query.edit_message_text(reply_text, parse_mode='Markdown')
        logger.info(f"ChatID: {chat_id} | Модель изменена на {model_name} ({selected})")
    else:
        await query.edit_message_text("❌ Неизвестная модель")


# Функция поиска Google (без изменений)
async def perform_google_search(query: str, api_key: str, cse_id: str, num_results: int) -> list[str] | None:
    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': api_key,
        'cx': cse_id,
        'q': query,
        'num': num_results
    }
    encoded_params = urlencode(params)
    full_url = f"{search_url}?{encoded_params}"
    logger.debug(f"Запрос к Google Search API: {search_url}?key=...&cx=...&q={query}&num={num_results}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(full_url) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get('items', [])
                    snippets = [item.get('snippet', '') for item in items if item.get('snippet')]
                    if snippets:
                        logger.info(f"Google Search: Найдено {len(snippets)} результатов.")
                        return snippets
                    else:
                        logger.info("Google Search: Результаты найдены, но не содержат сниппетов.")
                        return None
                elif response.status == 429:
                    logger.warning(f"Google Search: Ошибка 429 - Квота исчерпана!")
                    return None
                elif response.status == 403:
                     logger.error(f"Google Search: Ошибка 403 - Доступ запрещен. Проверьте API ключ и его ограничения, а также включен ли Custom Search API.")
                     return None
                else:
                    error_text = await response.text()
                    logger.error(f"Google Search: Ошибка API - Статус {response.status}, Ответ: {error_text[:200]}...")
                    return None
    except aiohttp.ClientError as e:
        logger.error(f"Google Search: Ошибка сети при запросе - {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Google Search: Ошибка декодирования JSON ответа - {e}")
        return None
    except Exception as e:
        logger.error(f"Google Search: Непредвиденная ошибка - {e}", exc_info=True)
        return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    original_user_message = update.message.text.strip() if update.message.text else ""
    if not original_user_message:
        return

    # ===== ИЗМЕНЕНИЕ: Получаем настройки из chat_data =====
    # Устанавливаем значения по умолчанию, если их нет в chat_data (на всякий случай)
    context.chat_data.setdefault('selected_model', DEFAULT_MODEL)
    context.chat_data.setdefault('search_enabled', True)
    context.chat_data.setdefault('temperature', 1.0)
    context.chat_data.setdefault('history', [])

    model_id = context.chat_data['selected_model']
    temperature = context.chat_data['temperature']
    use_search = context.chat_data['search_enabled'] # Читаем из chat_data
    # ====================================================

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    search_context = ""
    final_user_prompt = original_user_message
    search_provider = None

    # ===== ИЗМЕНЕНИЕ: Добавляем лог о текущем состоянии поиска ПЕРЕД проверкой =====
    logger.info(f"ChatID: {chat_id} | Проверка поиска: use_search = {use_search}")
    # ==========================================================================

    if use_search:
        logger.info(f"ChatID: {chat_id} | Поиск включен. Запрос: '{original_user_message[:50]}...'")
        google_results = await perform_google_search(
            original_user_message, GOOGLE_API_KEY, GOOGLE_CSE_ID, GOOGLE_SEARCH_MAX_RESULTS
        )

        if google_results:
            search_provider = "Google"
            search_snippets = [f"- {snippet}" for snippet in google_results]
            search_context = f"Контекст из поиска {search_provider}:\n" + "\n".join(search_snippets)
            logger.info(f"ChatID: {chat_id} | Найдены и добавлены результаты Google: {len(search_snippets)} сниппетов.")
        else:
            logger.info(f"ChatID: {chat_id} | Поиск Google не дал результатов или произошла ошибка. Пробуем DuckDuckGo...")
            try:
                ddgs = DDGS()
                logger.debug(f"ChatID: {chat_id} | Запрос к DDGS().text('{original_user_message}', region='ru-ru', max_results={DDG_MAX_RESULTS}) через asyncio.to_thread")
                results = await asyncio.to_thread(
                    ddgs.text,
                    original_user_message,
                    region='ru-ru',
                    max_results=DDG_MAX_RESULTS
                )
                logger.debug(f"ChatID: {chat_id} | Результаты DDG:\n{pprint.pformat(results)}")

                if results:
                    search_snippets = [f"- {r.get('body', '')}" for r in results if r.get('body')]
                    if search_snippets:
                        search_provider = "DuckDuckGo (запасной)"
                        search_context = f"Контекст из поиска {search_provider}:\n" + "\n".join(search_snippets)
                        logger.info(f"ChatID: {chat_id} | Найдены и добавлены результаты DDG: {len(search_snippets)} сниппетов.")
                    else:
                        logger.info(f"ChatID: {chat_id} | Результаты DDG найдены, но не содержат текста (body).")
                else:
                    logger.info(f"ChatID: {chat_id} | Результаты DDG не найдены.")
            except Exception as e_ddg:
                logger.error(f"ChatID: {chat_id} | Ошибка при поиске DuckDuckGo: {e_ddg}", exc_info=True)

        if search_context:
            final_user_prompt = (
                f"{search_context}\n\n"
                f"Используя приведенный выше контекст из поиска и свои знания, ответь на следующий вопрос пользователя:\n"
                f"\"{original_user_message}\""
            )
    else:
        # ===== ИЗМЕНЕНИЕ: Эта строка теперь ДОЛЖНА появляться в логах =====
        logger.info(f"ChatID: {chat_id} | Поиск отключен.")
        # =================================================================

    logger.debug(f"ChatID: {chat_id} | Финальный промпт для Gemini:\n{final_user_prompt}")
    search_log_msg = f"Поиск: {'Контекст добавлен (' + search_provider + ')' if search_provider else ('Контекст НЕ добавлен' if use_search else 'Отключен')}"
    logger.info(f"ChatID: {chat_id} | Модель: {model_id}, Темп: {temperature}, {search_log_msg}")

    # ===== ИЗМЕНЕНИЕ: Используем chat_data для истории =====
    chat_history = context.chat_data['history']
    # =====================================================
    # Не добавляем промпт с контекстом поиска в историю, чтобы не засорять её
    chat_history.append({"role": "user", "parts": [{"text": original_user_message}]})

    # Обрезка истории
    total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
    while total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1:
        if len(chat_history) >= 2:
            removed_user = chat_history.pop(0)
            removed_model = chat_history.pop(0)
            logger.info(f"ChatID: {chat_id} | История обрезана, удалена пара сообщений, текущая длина истории: {len(chat_history)}")
        else:
            removed_message = chat_history.pop(0)
            logger.info(f"ChatID: {chat_id} | История обрезана, удалено сообщение: {removed_message.get('role')}, история пуста.")
        total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
        logger.info(f"ChatID: {chat_id} | ... новая общая длина символов: {total_chars}")


    history_for_model = list(chat_history[:-1])
    history_for_model.append({"role": "user", "parts": [{"text": final_user_prompt}]})

    current_system_instruction = system_instruction_text
    tools = []
    reply = None

    try:
        generation_config=genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=MAX_OUTPUT_TOKENS
        )
        model = genai.GenerativeModel(
            model_id,
            tools=tools,
            safety_settings=SAFETY_SETTINGS_BLOCK_NONE,
            generation_config=generation_config,
            system_instruction=current_system_instruction
        )
        response = model.generate_content(history_for_model)

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
            except AttributeError:
                 logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели, не удалось извлечь доп. инфо (AttributeError).")
                 reply = "🤖 Нет ответа от модели."
            except Exception as e_inner:
                logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели, не удалось извлечь доп. инфо: {e_inner}")
                reply = "🤖 Нет ответа от модели."

        if reply:
             # Добавляем ответ модели в ОСНОВНУЮ историю в chat_data
             chat_history.append({"role": "model", "parts": [{"text": reply}]})

    except Exception as e:
        logger.exception(f"ChatID: {chat_id} | Ошибка при взаимодействии с моделью {model_id}")
        error_message = str(e)
        try:
            if isinstance(e, genai.types.BlockedPromptException):
                 reply = f"❌ Запрос заблокирован моделью. Причина: {e}"
            elif isinstance(e, genai.types.StopCandidateException):
                 reply = f"❌ Генерация остановлена моделью. Причина: {e}"
            elif "429" in error_message and ("quota" in error_message or "Resource has been exhausted" in error_message):
                 reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (ошибка 429). Попробуйте позже."
            elif "400" in error_message and "API key not valid" in error_message:
                 reply = "❌ Ошибка: Неверный Google API ключ."
            elif "Deadline Exceeded" in error_message or "504" in error_message or "500" in error_message: # Добавил 500
                 reply = f"❌ Ошибка: Проблема на стороне Google ({error_message.splitlines()[0]}). Попробуйте позже." # Более общее сообщение для 5xx
            else:
                 reply = f"❌ Ошибка при обращении к модели: {error_message}"
        except AttributeError:
             logger.warning("genai.types не содержит BlockedPromptException/StopCandidateException, используем общую обработку.")
             if "429" in error_message and ("quota" in error_message or "Resource has been exhausted" in error_message):
                  reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (ошибка 429). Попробуйте позже."
             elif "400" in error_message and "API key not valid" in error_message:
                  reply = "❌ Ошибка: Неверный Google API ключ."
             elif "Deadline Exceeded" in error_message or "504" in error_message or "500" in error_message:
                  reply = f"❌ Ошибка: Проблема на стороне Google ({error_message.splitlines()[0]}). Попробуйте позже."
             else:
                  reply = f"❌ Ошибка при обращении к модели: {error_message}"

    if reply:
        MAX_MESSAGE_LENGTH = 4096
        for i in range(0, len(reply), MAX_MESSAGE_LENGTH):
            await update.message.reply_text(reply[i:i + MAX_MESSAGE_LENGTH])


# Обработчик фото (без изменений в логике хранения состояния)
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tesseract_available = False
    try:
        pytesseract.pytesseract.get_tesseract_version()
        tesseract_available = True
        logger.info("Tesseract доступен.")
    except Exception as e:
        common_paths = ['tesseract', '/usr/bin/tesseract', '/usr/local/bin/tesseract']
        found = False
        for path in common_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                pytesseract.pytesseract.tesseract_cmd = path
                try:
                    pytesseract.pytesseract.get_tesseract_version()
                    tesseract_available = True
                    logger.info(f"Tesseract найден по пути: {path}")
                    found = True
                    break
                except Exception:
                    continue
        if not found:
            logger.error(f"Tesseract не найден или не исполняемый. Ошибка: {e}. OCR будет недоступен.")

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    try:
        photo_file = await update.message.photo[-1].get_file()
        file_bytes = await photo_file.download_as_bytearray()
    except Exception as e:
        logger.error(f"ChatID: {chat_id} | Не удалось скачать фото: {e}")
        await update.message.reply_text("❌ Не удалось загрузить изображение.")
        return

    user_caption = update.message.caption if update.message.caption else ""

    if tesseract_available:
        try:
            image = Image.open(io.BytesIO(file_bytes))
            extracted_text = pytesseract.image_to_string(image, lang='rus+eng')
            if extracted_text and extracted_text.strip():
                logger.info(f"ChatID: {chat_id} | Обнаружен текст на изображении (OCR)")
                ocr_prompt = f"На изображении обнаружен следующий текст:\n```\n{extracted_text.strip()}\n```\n"
                if user_caption:
                     user_prompt = f"{user_caption}\n{ocr_prompt}\nПроанализируй изображение и текст на нём, учитывая мой комментарий."
                else:
                     user_prompt = f"{ocr_prompt}\nПроанализируй изображение и текст на нём."

                fake_message = type('obj', (object,), {
                    'text': user_prompt,
                    'reply_text': update.message.reply_text,
                    'chat_id': chat_id
                 })
                fake_update = type('obj', (object,), {
                    'effective_chat': update.effective_chat,
                    'message': fake_message
                })

                await handle_message(fake_update, context)
                return
            else:
                 logger.info(f"ChatID: {chat_id} | OCR не нашел текст на изображении.")

        except pytesseract.TesseractNotFoundError:
             logger.error("Tesseract не найден при вызове image_to_string! Убедитесь, что путь к tesseract указан верно (pytesseract.pytesseract.tesseract_cmd). OCR отключен.")
             tesseract_available = False
        except Exception as e:
            logger.warning(f"ChatID: {chat_id} | Ошибка OCR: {e}", exc_info=True)


    logger.info(f"ChatID: {chat_id} | Обработка фото как изображения (без OCR)")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    MAX_IMAGE_BYTES = 4 * 1024 * 1024
    if len(file_bytes) > MAX_IMAGE_BYTES:
        logger.warning(f"ChatID: {chat_id} | Изображение слишком большое ({len(file_bytes)} байт), может не обработаться.")
        if len(file_bytes) > MAX_IMAGE_BYTES * 1.5:
             await update.message.reply_text("❌ Изображение слишком большое даже после попытки сжатия.")
             return

    b64_data = base64.b64encode(file_bytes).decode()
    prompt = user_caption if user_caption else "Что изображено на этом фото?"
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}
    ]

    # ===== ИЗМЕНЕНИЕ: Получаем модель и температуру из chat_data =====
    model_id = context.chat_data.get('selected_model', DEFAULT_MODEL)
    temperature = context.chat_data.get('temperature', 1.0)
    # ================================================================

    if 'flash' not in model_id and 'pro' not in model_id and 'vision' not in model_id:
         vision_models = [m for m in AVAILABLE_MODELS if 'flash' in m or 'pro' in m or 'vision' in m]
         if vision_models:
             original_model_name = AVAILABLE_MODELS.get(model_id, model_id)
             model_id = vision_models[0]
             new_model_name = AVAILABLE_MODELS.get(model_id, model_id)
             logger.warning(f"ChatID: {chat_id} | Модель {original_model_name} не поддерживает изображения. Временно переключено на {new_model_name}.")
             # ===== ИЗМЕНЕНИЕ: Обновляем модель и в chat_data, чтобы следующее сообщение не сбросило =====
             # context.chat_data['selected_model'] = model_id # Решаем не менять настройку пользователя
             # ===========================================================================================
             await context.bot.send_message(chat_id=chat_id, text=f"ℹ️ Ваша текущая модель не видит картинки, временно использую {new_model_name}.")
         else:
             logger.error(f"ChatID: {chat_id} | Нет доступных моделей для анализа изображений.")
             await update.message.reply_text("❌ Ни одна из доступных моделей не может анализировать изображения.")
             return

    logger.info(f"ChatID: {chat_id} | Анализ изображения. Модель: {model_id}, Темп: {temperature}")
    tools = []
    reply = None

    try:
        generation_config=genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=MAX_OUTPUT_TOKENS
        )
        model = genai.GenerativeModel(
            model_id, # Используем model_id (возможно, временный)
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
            except AttributeError:
                 logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения, не удалось извлечь доп. инфо (AttributeError).")
                 reply = "🤖 Не удалось понять, что на изображении."
            except Exception as e_inner:
                 logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения, не удалось извлечь доп. инфо: {e_inner}")
                 reply = "🤖 Не удалось понять, что на изображении."

    except Exception as e:
        logger.exception(f"ChatID: {chat_id} | Ошибка при анализе изображения")
        error_message = str(e)
        try:
            if "400 The model requires bild input" in error_message or "400 Request payload size exceeds the limit" in error_message:
                 reply = "❌ Ошибка: Либо модель не поддерживает изображения, либо картинка слишком большая."
            elif isinstance(e, genai.types.BlockedPromptException):
                 reply = f"❌ Запрос на анализ изображения заблокирован моделью. Причина: {e}"
            elif isinstance(e, genai.types.StopCandidateException):
                 reply = f"❌ Анализ изображения остановлен моделью. Причина: {e}"
            elif "429" in error_message and ("quota" in error_message or "Resource has been exhausted" in error_message):
                 reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (ошибка 429). Попробуйте позже."
            elif "400" in error_message and "API key not valid" in error_message:
                 reply = "❌ Ошибка: Неверный Google API ключ."
            elif "Deadline Exceeded" in error_message or "504" in error_message or "500" in error_message:
                 reply = f"❌ Ошибка: Проблема на стороне Google ({error_message.splitlines()[0]}). Попробуйте позже."
            else:
                reply = f"❌ Ошибка при анализе изображения: {error_message}"
        except AttributeError:
             logger.warning("genai.types не содержит BlockedPromptException/StopCandidateException, используем общую обработку.")
             if "400 The model requires bild input" in error_message or "400 Request payload size exceeds the limit" in error_message:
                  reply = "❌ Ошибка: Либо модель не поддерживает изображения, либо картинка слишком большая."
             elif "429" in error_message and ("quota" in error_message or "Resource has been exhausted" in error_message):
                  reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (ошибка 429). Попробуйте позже."
             elif "400" in error_message and "API key not valid" in error_message:
                  reply = "❌ Ошибка: Неверный Google API ключ."
             elif "Deadline Exceeded" in error_message or "504" in error_message or "500" in error_message:
                  reply = f"❌ Ошибка: Проблема на стороне Google ({error_message.splitlines()[0]}). Попробуйте позже."
             else:
                 reply = f"❌ Ошибка при анализе изображения: {error_message}"

    if reply:
        MAX_MESSAGE_LENGTH = 4096
        for i in range(0, len(reply), MAX_MESSAGE_LENGTH):
            await update.message.reply_text(reply[i:i + MAX_MESSAGE_LENGTH])


# Обработчик документов (без изменений в логике хранения состояния)
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not update.message.document:
        return
    doc = update.message.document
    allowed_mime_prefixes = ('text/', 'application/json', 'application/xml', 'application/csv', 'application/x-python', 'application/x-sh')
    allowed_mime_types = ('application/octet-stream',)

    if not doc.mime_type or (not any(doc.mime_type.startswith(prefix) for prefix in allowed_mime_prefixes) and doc.mime_type not in allowed_mime_types):
        await update.message.reply_text(f"⚠️ Пока могу читать только текстовые файлы (например, .txt, .py, .js, .csv, .json, .xml, .log и т.п.). Ваш тип: `{doc.mime_type}`")
        logger.warning(f"ChatID: {chat_id} | Попытка загрузить файл неподдерживаемого типа: {doc.mime_type}, Имя: {doc.file_name}")
        return

    MAX_FILE_SIZE_MB = 10
    if doc.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ Файл '{doc.file_name}' слишком большой (> {MAX_FILE_SIZE_MB} MB).")
        logger.warning(f"ChatID: {chat_id} | Попытка загрузить слишком большой файл: {doc.file_name} ({doc.file_size} байт)")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    try:
        doc_file = await doc.get_file()
        file_bytes = await doc_file.download_as_bytearray()
    except Exception as e:
        logger.error(f"ChatID: {chat_id} | Не удалось скачать документ '{doc.file_name}': {e}")
        await update.message.reply_text("❌ Не удалось загрузить файл.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    text = None
    detected_encoding = None
    encodings_to_try = ['utf-8', 'latin-1', 'cp1251']

    for encoding in encodings_to_try:
        try:
            text = file_bytes.decode(encoding)
            detected_encoding = encoding
            logger.info(f"ChatID: {chat_id} | Файл '{doc.file_name}' успешно декодирован как {encoding}.")
            break
        except UnicodeDecodeError:
            logger.debug(f"ChatID: {chat_id} | Файл '{doc.file_name}' не в кодировке {encoding}.")
            continue
        except Exception as e:
            logger.error(f"ChatID: {chat_id} | Непредвиденная ошибка при декодировании файла '{doc.file_name}' как {encoding}: {e}")

    if text is None:
        logger.error(f"ChatID: {chat_id} | Не удалось декодировать файл '{doc.file_name}' ни одной из кодировок: {encodings_to_try}")
        await update.message.reply_text(f"❌ Не удалось прочитать текстовое содержимое файла '{doc.file_name}'. Попробуйте сохранить его в кодировке UTF-8.")
        return

    MAX_FILE_CHARS = MAX_CONTEXT_CHARS // 3
    if len(text) > MAX_FILE_CHARS:
        truncated = text[:MAX_FILE_CHARS]
        warning_msg = f"\n\n(⚠️ Текст файла был обрезан до {MAX_FILE_CHARS} символов)"
        logger.warning(f"ChatID: {chat_id} | Текст файла '{doc.file_name}' обрезан до {MAX_FILE_CHARS} символов.")
    else:
        truncated = text
        warning_msg = ""

    user_caption = update.message.caption if update.message.caption else ""
    file_name = doc.file_name or "файл"

    if user_caption:
        user_prompt = f"Проанализируй содержимое файла '{file_name}' (кодировка: {detected_encoding}), учитывая мой комментарий: \"{user_caption}\".\n\nСодержимое файла:\n```\n{truncated}\n```{warning_msg}"
    else:
        user_prompt = f"Вот текст из файла '{file_name}' (кодировка: {detected_encoding}). Что ты можешь сказать об этом?\n\nСодержимое файла:\n```\n{truncated}\n```{warning_msg}"

    fake_message = type('obj', (object,), {
        'text': user_prompt,
        'reply_text': update.message.reply_text,
        'chat_id': chat_id
     })
    fake_update = type('obj', (object,), {
        'effective_chat': update.effective_chat,
        'message': fake_message
    })
    await handle_message(fake_update, context)


async def setup_bot_and_server(stop_event: asyncio.Event):
    # ===== ИЗМЕНЕНИЕ: Создаем и передаем объект персистентности =====
    persistence = PicklePersistence(filepath=PERSISTENCE_FILE)
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    # ==============================================================

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("temp", set_temperature))
    application.add_handler(CommandHandler("search_on", enable_search))
    application.add_handler(CommandHandler("search_off", disable_search))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await application.initialize()
    webhook_url = urljoin(WEBHOOK_HOST, f"/{GEMINI_WEBHOOK_PATH}")
    logger.info(f"Устанавливаю вебхук: {webhook_url}")
    # Важно: drop_pending_updates=True может приводить к потере сообщений при перезапуске
    # Если это проблема, установите в False, но тогда бот может обработать старые сообщения
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
        # Передаем управление обработчику PTB
        asyncio.create_task(application.process_update(update))
        return aiohttp.web.Response(text="OK", status=200)
    except json.JSONDecodeError as e:
         logger.error(f"Ошибка декодирования JSON от Telegram: {e}")
         raw_body = await request.text()
         logger.debug(f"Сырое тело запроса: {raw_body[:500]}...")
         return aiohttp.web.Response(text="Bad Request", status=400)
    except Exception as e:
        logger.error(f"Ошибка обработки вебхук-запроса: {e}", exc_info=True)
        return aiohttp.web.Response(text="OK", status=200)


async def main():
    logging.getLogger('google.api_core').setLevel(logging.WARNING)
    logging.getLogger('google.generativeai').setLevel(logging.WARNING)
    logging.getLogger('duckduckgo_search').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.INFO)
    # ===== ИЗМЕНЕНИЕ: Добавляем логгер для персистентности =====
    logging.getLogger('telegram.ext.persistence').setLevel(logging.INFO)
    # ========================================================

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Получен сигнал остановки, инициирую завершение...")
        if not stop_event.is_set():
            stop_event.set()
        else:
            logger.warning("Повторный сигнал остановки, принудительное завершение может потребоваться.")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            logger.warning(f"Не удалось установить обработчик для {sig}. Остановка может работать некорректно на Windows.")


    application = None
    web_server_task = None
    try:
        logger.info("Запускаю настройку бота и сервера...")
        application, web_server_coro = await setup_bot_and_server(stop_event)
        web_server_task = asyncio.create_task(web_server_coro)
        logger.info("Настройка завершена, жду сигналов остановки...")
        await stop_event.wait()

    except asyncio.CancelledError:
         logger.info("Основная задача была отменена.")
    except Exception as e:
        logger.exception("Критическая ошибка в главном потоке приложения.")
    finally:
        logger.info("Начинаю процесс штатной остановки...")

        if web_server_task and not web_server_task.done():
             logger.info("Останавливаю веб-сервер...")
             # Даем веб-серверу знать, что пора заканчивать (он ждет stop_event)
             if not stop_event.is_set(): stop_event.set() # На всякий случай
             try:
                 await asyncio.wait_for(web_server_task, timeout=15.0)
                 logger.info("Веб-сервер успешно завершился.")
             except asyncio.TimeoutError:
                 logger.warning("Веб-сервер не завершился за 15 секунд, отменяю задачу...")
                 web_server_task.cancel()
                 try:
                     await web_server_task
                 except asyncio.CancelledError:
                     logger.info("Задача веб-сервера успешно отменена.")
                 except Exception as e_cancel:
                     logger.error(f"Ошибка при ожидании отмены задачи веб-сервера: {e_cancel}", exc_info=True)
             except asyncio.CancelledError:
                 logger.info("Задача веб-сервера была отменена во время ожидания.")
             except Exception as e_wait:
                 logger.error(f"Ошибка при ожидании задачи веб-сервера: {e_wait}", exc_info=True)

        if application:
            logger.info("Останавливаю приложение бота (включая сохранение состояния)...")
            try:
                 # ===== ИЗМЕНЕНИЕ: Shutdown сохранит данные, если используется персистентность =====
                 await application.shutdown()
                 logger.info("Приложение бота остановлено.")
            except Exception as e_shutdown:
                 logger.error(f"Ошибка при остановке приложения бота: {e_shutdown}", exc_info=True)
        else:
            logger.warning("Объект приложения бота не был создан или был потерян.")

        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if tasks:
            logger.info(f"Отменяю {len(tasks)} оставшихся задач...")
            [task.cancel() for task in tasks]
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("Оставшиеся задачи завершены/отменены.")
            except Exception as e_gather:
                logger.error(f"Ошибка при ожидании отмены оставшихся задач: {e_gather}", exc_info=True)


        logger.info("Приложение полностью остановлено.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение прервано пользователем (Ctrl+C)")
    except Exception as e:
        logger.critical(f"Неперехваченная ошибка на верхнем уровне: {e}", exc_info=True)

# --- END OF FILE main.txt ---