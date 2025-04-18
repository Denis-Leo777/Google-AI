# --- START OF FILE main.py ---

# Обновлённый main.py:
# - Добавлен Google Custom Search API как основной поиск
# - DuckDuckGo используется как запасной вариант
# - Исправлен поиск DDG: используется синхронный ddgs.text() в отдельном потоке через asyncio.to_thread()
# - Скорректирована системная инструкция и формирование промпта с поиском для более естественного ответа.
# - Улучшено формирование промпта для фото и документов для лучшего удержания контекста.
# - История чата сохраняется без поискового контекста.
# - ДОБАВЛЕНА ЛОГИКА ПОВТОРНЫХ ЗАПРОСОВ (RETRY) к Gemini при 500-х ошибках.
# - ИСПРАВЛЕНО: Настройки безопасности BLOCK_NONE устанавливаются даже при ошибке импорта типов.
# - ИСПРАВЛЕНО: Улучшена инструкция и формирование промпта для лучшего удержания контекста диалога.
# - ИСПРАВЛЕНО: Добавлен parse_mode='Markdown' при отправке ответов бота.
# - ИЗМЕНЕНО: Добавлена инструкция для краткости ответов в чате.

import logging
import os
import asyncio # Нужно для asyncio.to_thread
import signal
from urllib.parse import urljoin, urlencode # Добавлен urlencode
import base64
import pytesseract
from PIL import Image
import io
import pprint
import json # Добавлен для парсинга ответа Google
import time # Добавлено для ретраев

# Инициализируем логгер
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO) # Добавил %(name)s для ясности
logger = logging.getLogger(__name__)

# ===== ИЗМЕНЕНИЕ: Импортируем aiohttp для Google Search =====
import aiohttp
# ===========================================================
import aiohttp.web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
# ===== ИЗМЕНЕНИЕ: Импортируем ParseMode =====
from telegram.constants import ChatAction, ParseMode
# ============================================
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
import google.generativeai as genai
# ===== ИСПРАВЛЕНИЕ: Возвращаем импорт DDGS =====
from duckduckgo_search import DDGS # Обычный класс

# ===== ИСПРАВЛЕНИЕ: Обработка импорта и определения SAFETY_SETTINGS =====
# Строковые представления категорий и порога для запасного варианта
HARM_CATEGORIES_STRINGS = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
]
BLOCK_NONE_STRING = "BLOCK_NONE" # API должен понимать это как строку

try:
    # Пытаемся импортировать нужные типы
    from google.generativeai.types import (
        HarmCategory, HarmBlockThreshold, BlockedPromptException,
        StopCandidateException, SafetyRating, BlockReason, FinishReason
    )
    # Определяем настройки безопасности используя импортированные Enum типы
    SAFETY_SETTINGS_BLOCK_NONE = [
        {"category": getattr(HarmCategory, cat_str), "threshold": HarmBlockThreshold.BLOCK_NONE}
        for cat_str in HARM_CATEGORIES_STRINGS
        # Добавляем проверку, что атрибут категории существует в HarmCategory
        if hasattr(HarmCategory, cat_str) and hasattr(HarmBlockThreshold, 'BLOCK_NONE')
    ]
    if not SAFETY_SETTINGS_BLOCK_NONE and HARM_CATEGORIES_STRINGS:
         logger.warning("Не удалось создать SAFETY_SETTINGS_BLOCK_NONE с Enum типами, хотя импорт был успешен? Используем строки.")
         SAFETY_SETTINGS_BLOCK_NONE = [
             {"category": cat_str, "threshold": BLOCK_NONE_STRING}
             for cat_str in HARM_CATEGORIES_STRINGS
         ]
    elif SAFETY_SETTINGS_BLOCK_NONE:
         logger.info("Типы google.generativeai.types успешно импортированы. Настройки безопасности BLOCK_NONE установлены с Enum.")
    else:
        logger.warning("Список HARM_CATEGORIES_STRINGS пуст, настройки безопасности не установлены.")
        SAFETY_SETTINGS_BLOCK_NONE = []

except ImportError:
    # Если импорт не удался, логируем и создаем заглушки для типов ошибок
    logger.warning("Не удалось импортировать типы из google.generativeai.types. Используются строковые значения для настроек безопасности.")
    BlockedPromptException = Exception
    StopCandidateException = Exception
    SAFETY_SETTINGS_BLOCK_NONE = [
        {"category": cat_str, "threshold": BLOCK_NONE_STRING}
        for cat_str in HARM_CATEGORIES_STRINGS
    ]
    logger.warning("Настройки безопасности установлены с использованием строковых представлений (BLOCK_NONE).")
    # Определяем заглушки для типов
    HarmCategory = type('obj', (object,), {})
    HarmBlockThreshold = type('obj', (object,), {})
    SafetyRating = type('obj', (object,), {'category': None, 'probability': None})
    BlockReason = type('obj', (object,), {'UNSPECIFIED': 'UNSPECIFIED'})
    FinishReason = type('obj', (object,), {'STOP': 'STOP'})
# ======================================================================


# Переменные окружения и их проверка
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')

required_env_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "GOOGLE_API_KEY": GOOGLE_API_KEY,
    "GOOGLE_CSE_ID": GOOGLE_CSE_ID,
    "WEBHOOK_HOST": WEBHOOK_HOST,
    "GEMINI_WEBHOOK_PATH": GEMINI_WEBHOOK_PATH
}
missing_vars = [name for name, value in required_env_vars.items() if not value]
if missing_vars:
    logger.critical(f"Отсутствуют критически важные переменные окружения: {', '.join(missing_vars)}")
    exit(1)

# Настройка Gemini
genai.configure(api_key=GOOGLE_API_KEY)

# Модели
AVAILABLE_MODELS = {
    'gemini-2.5-flash-preview-04-17': '2.5 Flash Preview',
    'gemini-2.5-pro-exp-03-25': '2.5 Pro exp.',
    'gemini-2.0-flash-thinking-exp-01-21': '2.0 Flash Thinking exp.',
}
# Выбираем модель по умолчанию - оставлена та, что была в твоем файле
DEFAULT_MODEL = 'gemini-2.5-flash-preview-04-17' if 'gemini-2.5-flash-preview-04-17' in AVAILABLE_MODELS else 'gemini-2.5-pro-exp-03-25'

# Константы
MAX_CONTEXT_CHARS = 100000 # Макс. символов в истории для отправки (примерно)
MAX_OUTPUT_TOKENS = 5000 # Макс. токенов на выходе (можно настроить)
DDG_MAX_RESULTS = 10 # Уменьшил DDG, т.к. это fallback
GOOGLE_SEARCH_MAX_RESULTS = 10 # Уменьшил Google Search для снижения нагрузки и стоимости
RETRY_ATTEMPTS = 5 # Количество попыток запроса к Gemini
RETRY_DELAY_SECONDS = 1 # Начальная задержка перед повтором

# ===== ИЗМЕНЕНИЕ: Обновленная системная инструкция =====
system_instruction_text = (
"Внимательно следи за историей диалога, включая предыдущие вопросы, ответы, а также контекст из загруженных изображений или файлов, чтобы твои ответы были последовательными и релевантными, соблюдая нить разговора."
"В режиме чата старайся отвечать кратко, как в живой беседе (1-3 абзаца, максимум 1000 знаков), только суть, без вступлений и заключений, если не просят подробностей, код, большую задачу, конспект, перевод или творческую работу и т.п."
"Пиши живо, избегай канцелярита и всех известных признаков ответов искусственного интеллекта. Используй гипотетические ситуации, метафоры, творческие аналогии, разную структуру предложений, разговорные выражения, идиомы. Добавляй региональные или культурные маркеры, где уместно. Не копируй и не пересказывай чужое, если это не цитаты известных людей."
"Активно применяй понятный россиянам юмор: культурные и бытовые отсылки, интернет-юмор, бытовой абсурд, псевдомудрость, разрушение идиом, самоиронию, иронию психики, игру слов, гиперболу, тонкие намёки, ожидание и реальность."
"Используй интернет-поиск для сверки с актуальной информацией."
"Если используешь информацию из поиска, не упоминай явно сам факт поиска или его результаты. Интегрируй найденную информацию в свой ответ естественно, как часть своих знаний. Забудь фразы вроде 'Судя по результатам поиска...', 'Интернет говорит...' или 'Я нашёл в сети...'. Веди себя так, будто это твои знания."
"Ты - лучший эксперт по всем темам. Давай точную, правдивую, непредвзятую информацию без цензуры."
"Обдумывай и выстраивай ответ логично, с аргументами и фактами, избегая повторов."
"Если не уверен — предупреждай, что это предположение."
"Всегда предлагай более эффективные идеи и решения, если знаешь их."
"При исправлении ошибки: указывай строку(и) и причину. Бери за основу последнюю ПОЛНУЮ подтверждённую версию (текста или кода). Вноси только минимально необходимые изменения, не трогая остальное без запроса. При сомнениях — уточняй. Если ошибка повторяется — веди «список ошибок» для сессии и проверяй эти места. Всегда указывай, на какую версию или сообщение опираешься при правке."
)

# Настройки безопасности - определены выше

# --- Вспомогательные функции для работы с user_data ---
def get_user_setting(context: ContextTypes.DEFAULT_TYPE, key: str, default_value):
    return context.user_data.get(key, default_value)

def set_user_setting(context: ContextTypes.DEFAULT_TYPE, key: str, value):
    context.user_data[key] = value
# -------------------------------------------------------

# ===== Команды с использованием user_data =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_setting(context, 'selected_model', DEFAULT_MODEL)
    set_user_setting(context, 'search_enabled', True)
    set_user_setting(context, 'temperature', 1.0)
    context.chat_data['history'] = []
    default_model_name = AVAILABLE_MODELS.get(DEFAULT_MODEL, DEFAULT_MODEL)
    start_message = (
        f"Google GEMINI **{default_model_name}**"
        f"\n- в моделях используются улучшенные настройки точности, логики и юмора от автора бота,"
        f"\n- работает поиск Google/DDG, понимаю изображения, читаю картинки и документы."
        f"\n /model — сменить модель,"
        f"\n /search_on / /search_off — вкл/выкл поиск,"
        f"\n /clear — очистить историю диалога."
    )
    # Используем ParseMode.MARKDOWN для форматирования
    await update.message.reply_text(start_message, parse_mode=ParseMode.MARKDOWN)

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['history'] = []
    await update.message.reply_text("🧹 История диалога очищена.")

async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            current_temp = get_user_setting(context, 'temperature', 1.0)
            await update.message.reply_text(f"🌡️ Текущая температура: {current_temp:.1f}\nЧтобы изменить, напиши `/temp <значение>` (например, `/temp 0.8`)")
            return
        temp = float(context.args[0])
        if not (0.0 <= temp <= 2.0):
            raise ValueError("Температура должна быть от 0.0 до 2.0")
        set_user_setting(context, 'temperature', temp)
        await update.message.reply_text(f"🌡️ Температура установлена на {temp:.1f}")
    except (ValueError) as e:
        await update.message.reply_text(f"⚠️ Неверный формат. {e}. Пример: `/temp 0.8`")
    except Exception as e:
        logger.error(f"Ошибка в set_temperature: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка при установке температуры.")


async def enable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_setting(context, 'search_enabled', True)
    await update.message.reply_text("🔍 Поиск Google/DDG включён.")

async def disable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_setting(context, 'search_enabled', False)
    await update.message.reply_text("🔇 Поиск Google/DDG отключён.")

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_model = get_user_setting(context, 'selected_model', DEFAULT_MODEL)
    keyboard = []
    sorted_models = sorted(AVAILABLE_MODELS.items())
    for m, name in sorted_models:
         button_text = f"{'✅ ' if m == current_model else ''}{name}"
         keyboard.append([InlineKeyboardButton(button_text, callback_data=f"set_model_{m}")])
    await update.message.reply_text("Выберите модель:", reply_markup=InlineKeyboardMarkup(keyboard))

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    callback_data = query.data
    if callback_data and callback_data.startswith("set_model_"):
        selected = callback_data.replace("set_model_", "")
        if selected in AVAILABLE_MODELS:
            set_user_setting(context, 'selected_model', selected)
            model_name = AVAILABLE_MODELS[selected]
            reply_text = f"Модель установлена: **{model_name}**"
            try:
                # Используем ParseMode.MARKDOWN для форматирования
                await query.edit_message_text(reply_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.warning(f"Не удалось изменить сообщение с кнопками: {e}. Отправляю новое.")
                await context.bot.send_message(chat_id=query.message.chat_id, text=reply_text, parse_mode=ParseMode.MARKDOWN)
        else:
            try:
                await query.edit_message_text("❌ Неизвестная модель выбрана.")
            except Exception:
                 await context.bot.send_message(chat_id=query.message.chat_id, text="❌ Неизвестная модель выбрана.")
    else:
        logger.warning(f"Получен неизвестный callback_data: {callback_data}")
        try:
            await query.edit_message_text("❌ Ошибка обработки выбора.")
        except Exception: pass
# ============================================

# ===== Функция поиска Google (без изменений) =====
async def perform_google_search(query: str, api_key: str, cse_id: str, num_results: int, session: aiohttp.ClientSession) -> list[str] | None:
    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': api_key, 'cx': cse_id, 'q': query, 'num': num_results, 'lr': 'lang_ru', 'gl': 'ru'}
    encoded_params = urlencode(params)
    full_url = f"{search_url}?{encoded_params}"
    query_short = query[:50] + '...' if len(query) > 50 else query
    logger.debug(f"Запрос к Google Search API для '{query_short}'...")
    try:
        async with session.get(full_url, timeout=aiohttp.ClientTimeout(total=10.0)) as response:
            response_text = await response.text()
            if response.status == 200:
                try: data = json.loads(response_text)
                except json.JSONDecodeError as e_json:
                    logger.error(f"Google Search: Ошибка JSON для '{query_short}' - {e_json}. Ответ: {response_text[:200]}...")
                    return None
                items = data.get('items', [])
                snippets = [item.get('snippet', item.get('title', '')) for item in items if item.get('snippet') or item.get('title')]
                if snippets:
                    logger.info(f"Google Search: Найдено {len(snippets)} рез. для '{query_short}'.")
                    return snippets
                else:
                    logger.info(f"Google Search: 0 сниппетов/заголовков для '{query_short}'.")
                    return None
            elif response.status == 400: logger.error(f"Google Search: Ошибка 400 для '{query_short}'. Ответ: {response_text[:200]}...")
            elif response.status == 403: logger.error(f"Google Search: Ошибка 403 для '{query_short}'. Проверьте ключ/API. Ответ: {response_text[:200]}...")
            elif response.status == 429: logger.warning(f"Google Search: Ошибка 429 для '{query_short}'. Квота?")
            elif response.status >= 500: logger.warning(f"Google Search: Ошибка {response.status} для '{query_short}'. Ответ: {response_text[:200]}...")
            else: logger.error(f"Google Search: Статус {response.status} для '{query_short}'. Ответ: {response_text[:200]}...")
            return None
    except aiohttp.ClientConnectorError as e: logger.error(f"Google Search: Ошибка сети (conn) для '{query_short}' - {e}")
    except aiohttp.ClientError as e: logger.error(f"Google Search: Ошибка сети (client) для '{query_short}' - {e}")
    except asyncio.TimeoutError: logger.warning(f"Google Search: Таймаут для '{query_short}'")
    except Exception as e: logger.error(f"Google Search: Неожиданная ошибка для '{query_short}' - {e}", exc_info=True)
    return None
# ===========================================================

# ===== Основной обработчик сообщений с РЕТРАЯМИ =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    original_user_message = ""
    if update.message and update.message.text:
         original_user_message = update.message.text.strip()
    elif hasattr(update, 'message') and hasattr(update.message, 'text') and update.message.text:
         original_user_message = update.message.text.strip()

    if not original_user_message:
        logger.warning(f"ChatID: {chat_id} | Пустое сообщение в handle_message.")
        return

    model_id = get_user_setting(context, 'selected_model', DEFAULT_MODEL)
    temperature = get_user_setting(context, 'temperature', 1.0)
    use_search = get_user_setting(context, 'search_enabled', True)

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # --- Блок поиска ---
    search_context_snippets = []
    search_provider = None
    search_log_msg = "Поиск отключен"

    if use_search:
        query_short = original_user_message[:50] + '...' if len(original_user_message) > 50 else original_user_message
        search_log_msg = f"Поиск Google/DDG для '{query_short}'"
        logger.info(f"ChatID: {chat_id} | {search_log_msg}...")
        session = context.bot_data.get('aiohttp_session')
        if not session or session.closed:
            logger.info("Создание новой сессии aiohttp для поиска.")
            timeout = aiohttp.ClientTimeout(total=60.0, connect=10.0, sock_connect=10.0, sock_read=30.0)
            session = aiohttp.ClientSession(timeout=timeout)
            context.bot_data['aiohttp_session'] = session
        google_results = await perform_google_search(original_user_message, GOOGLE_API_KEY, GOOGLE_CSE_ID, GOOGLE_SEARCH_MAX_RESULTS, session)
        if google_results:
            search_provider = "Google"
            search_context_snippets = google_results
            search_log_msg += f" (Google: {len(search_context_snippets)} рез.)"
        else:
            search_log_msg += " (Google: 0 рез./ошибка)"
            logger.info(f"ChatID: {chat_id} | Google fail. Пробуем DuckDuckGo...")
            try:
                ddgs = DDGS()
                results_ddg = await asyncio.to_thread(ddgs.text, original_user_message, region='ru-ru', max_results=DDG_MAX_RESULTS, timeout=10)
                if results_ddg:
                    ddg_snippets = [r.get('body', '') for r in results_ddg if r.get('body')]
                    if ddg_snippets:
                        search_provider = "DuckDuckGo"
                        search_context_snippets = ddg_snippets
                        search_log_msg += f" (DDG: {len(search_context_snippets)} рез.)"
                    else: search_log_msg += " (DDG: 0 текст. рез.)"
                else: search_log_msg += " (DDG: 0 рез.)"
            except TimeoutError:
                 logger.warning(f"ChatID: {chat_id} | Таймаут поиска DDG.")
                 search_log_msg += " (DDG: таймаут)"
            except Exception as e_ddg:
                logger.error(f"ChatID: {chat_id} | Ошибка поиска DDG: {e_ddg}", exc_info=True)
                search_log_msg += " (DDG: ошибка)"
    # --- Конец блока поиска ---

    # Формируем финальный промпт для модели
    final_user_prompt = original_user_message # По умолчанию
    if search_context_snippets:
        search_context_lines = [f"- {s.strip()}" for s in search_context_snippets if s.strip()]
        if search_context_lines:
            search_context = "\n".join(search_context_lines)
            # ===== ИЗМЕНЕНИЕ: Структура промпта с поиском =====
            final_user_prompt = (
                f"Вопрос пользователя: \"{original_user_message}\"\n\n"
                f"(Возможно релевантная доп. информация из поиска, используй с осторожностью, если подходит к вопросу, иначе игнорируй):\n{search_context}"
            )
            # =================================================
            logger.info(f"ChatID: {chat_id} | Добавлен контекст из {search_provider} ({len(search_context_lines)} непустых сниппетов).")
        else:
             logger.info(f"ChatID: {chat_id} | Сниппеты из {search_provider} пустые, контекст не добавлен.")
             search_log_msg += " (пустые сниппеты)"

    logger.info(f"ChatID: {chat_id} | {search_log_msg}")
    logger.debug(f"ChatID: {chat_id} | Финальный промпт для Gemini (длина {len(final_user_prompt)}):\n{final_user_prompt[:500]}...")

    # --- История и ее обрезка ---
    chat_history = context.chat_data.setdefault("history", [])
    chat_history.append({"role": "user", "parts": [{"text": original_user_message}]})
    current_total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
    removed_count = 0
    while current_total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1:
        if len(chat_history) >= 2:
            chat_history.pop(0); chat_history.pop(0); removed_count += 2
        else: chat_history.pop(0); removed_count += 1
        current_total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
    if removed_count > 0: logger.info(f"ChatID: {chat_id} | История обрезана, удалено {removed_count} сообщ. Текущая: {len(chat_history)} сообщ., ~{current_total_chars} симв.")
    history_for_model = list(chat_history[:-1])
    history_for_model.append({"role": "user", "parts": [{"text": final_user_prompt}]})
    # --- Конец подготовки истории ---

    # --- Вызов модели с РЕТРАЯМИ ---
    reply = None; response = None; last_exception = None; generation_successful = False
    for attempt in range(RETRY_ATTEMPTS):
        try:
            logger.info(f"ChatID: {chat_id} | Попытка {attempt + 1}/{RETRY_ATTEMPTS} запроса к модели {model_id}...")
            generation_config=genai.GenerationConfig(temperature=temperature, max_output_tokens=MAX_OUTPUT_TOKENS)
            model = genai.GenerativeModel(model_id, safety_settings=SAFETY_SETTINGS_BLOCK_NONE, generation_config=generation_config, system_instruction=system_instruction_text)
            response = await asyncio.to_thread(model.generate_content, history_for_model)

            if hasattr(response, 'text'): reply = response.text
            else: reply = None; logger.warning(f"ChatID: {chat_id} | Ответ модели не содержит 'text' (попытка {attempt + 1}).")

            if not reply:
                 try:
                     feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else None
                     candidates_info = response.candidates if hasattr(response, 'candidates') else []
                     block_reason_enum = feedback.block_reason if feedback and hasattr(feedback, 'block_reason') else None
                     block_reason = block_reason_enum.name if block_reason_enum and hasattr(block_reason_enum, 'name') else str(block_reason_enum or 'N/A')
                     finish_reason_enum = candidates_info[0].finish_reason if candidates_info and hasattr(candidates_info[0], 'finish_reason') else None
                     finish_reason_val = finish_reason_enum.name if finish_reason_enum and hasattr(finish_reason_enum, 'name') else str(finish_reason_enum or 'N/A')
                     safety_ratings = feedback.safety_ratings if feedback and hasattr(feedback, 'safety_ratings') else []
                     safety_info = ", ".join([f"{s.category.name}: {s.probability.name}" for s in safety_ratings if hasattr(s, 'category') and hasattr(s, 'probability')])
                     logger.warning(f"ChatID: {chat_id} | Пустой ответ или нет текста (попытка {attempt + 1}). Block: {block_reason}, Finish: {finish_reason_val}, Safety: [{safety_info}]")
                     if block_reason != 'UNSPECIFIED' and block_reason != 'N/A': reply = f"🤖 Модель не дала ответ. (Причина блокировки: {block_reason})"
                     elif finish_reason_val != 'STOP' and finish_reason_val != 'N/A': reply = f"🤖 Модель завершила работу без ответа. (Причина: {finish_reason_val})"
                     else: reply = "🤖 Модель дала пустой ответ."; generation_successful = True
                 except AttributeError as e_attr: logger.warning(f"ChatID: {chat_id} | Пустой ответ, ошибка атрибута: {e_attr}. Попытка {attempt + 1}"); reply = "🤖 Получен пустой ответ от модели (ошибка атрибута)."
                 except Exception as e_inner: logger.warning(f"ChatID: {chat_id} | Пустой ответ, ошибка извлечения инфо: {e_inner}. Попытка {attempt + 1}", exc_info=True); reply = "🤖 Получен пустой ответ от модели (внутренняя ошибка)."

            if reply and reply != "🤖 Модель дала пустой ответ.": generation_successful = True
            if generation_successful: logger.info(f"ChatID: {chat_id} | Успешная генерация на попытке {attempt + 1}."); break

        except BlockedPromptException as e: logger.warning(f"ChatID: {chat_id} | Запрос заблокирован (поп. {attempt + 1}): {e}"); reply = f"❌ Запрос заблокирован моделью."; last_exception = e; break
        except StopCandidateException as e: logger.warning(f"ChatID: {chat_id} | Генерация остановлена (поп. {attempt + 1}): {e}"); reply = f"❌ Генерация остановлена моделью."; last_exception = e; break
        except Exception as e:
            last_exception = e; error_message = str(e); logger.warning(f"ChatID: {chat_id} | Ошибка генерации (поп. {attempt + 1}): {error_message[:200]}...")
            is_retryable = "500" in error_message or "503" in error_message
            is_rate_limit = "429" in error_message and ("quota" in error_message or "Resource has been exhausted" in error_message)
            if is_rate_limit: logger.error(f"ChatID: {chat_id} | Лимит запросов (429). Прекращаем."); reply = f"❌ Ошибка: Лимит запросов к API Google (429)."; break
            if is_retryable and attempt < RETRY_ATTEMPTS - 1:
                wait_time = RETRY_DELAY_SECONDS * (2 ** attempt); logger.info(f"ChatID: {chat_id} | Ожидание {wait_time:.1f} сек перед поп. {attempt + 2}..."); await asyncio.sleep(wait_time); continue
            else:
                logger.error(f"ChatID: {chat_id} | Не удалось сгенерировать ответ после {attempt + 1} попыток. Ошибка: {e}", exc_info=True if not is_retryable else False)
                if "400" in error_message and "API key not valid" in error_message: reply = "❌ Ошибка: Неверный Google API ключ."
                elif "User location is not supported" in error_message: reply = f"❌ Ошибка: Ваш регион не поддерживается для модели {model_id}."
                elif "400" in error_message and ("image input" in error_message or " richiesto" in error_message): reply = f"❌ Ошибка: Неверный формат запроса ({error_message[:100]}...)."
                else: reply = f"❌ Ошибка после {attempt + 1} попыток. ({error_message[:100]}...)"
                break
    # --- Конец блока вызова модели ---

    # Добавляем ответ в историю
    if reply:
        if chat_history and chat_history[-1]["role"] == "user": chat_history.append({"role": "model", "parts": [{"text": reply}]})
        else: chat_history.append({"role": "model", "parts": [{"text": reply}]}); logger.warning(f"ChatID: {chat_id} | Ответ модели добавлен, но история была нарушена?")

    # Отправка ответа пользователю
    if reply:
        MAX_MESSAGE_LENGTH = 4096
        reply_chunks = [reply[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(reply), MAX_MESSAGE_LENGTH)]
        message_to_reply = update.message
        try:
            for i, chunk in enumerate(reply_chunks):
                send_method = message_to_reply.reply_text if i == 0 else context.bot.send_message
                kwargs = {'text': chunk, 'parse_mode': ParseMode.MARKDOWN} # ===== ИЗМЕНЕНИЕ: Добавлен parse_mode =====
                if i > 0: kwargs['chat_id'] = chat_id
                message_to_reply = await send_method(**kwargs)
                await asyncio.sleep(0.1)
        except Exception as e_reply:
            logger.error(f"ChatID: {chat_id} | Ошибка отправки ({type(e_reply).__name__}): {e_reply}. Попытка без Markdown.", exc_info=False) # Убрал exc_info для краткости
            try:
                # Попытка отправить без форматирования
                kwargs['parse_mode'] = None
                if i == 0: await message_to_reply.reply_text(**kwargs)
                else: await context.bot.send_message(**kwargs)
            except Exception as e_send_no_md:
                 logger.error(f"ChatID: {chat_id} | Не удалось отправить даже без Markdown: {e_send_no_md}", exc_info=True)
    else:
         logger.error(f"ChatID: {chat_id} | Нет ответа для отправки пользователю.")
         try: await update.message.reply_text("🤖 К сожалению, не удалось получить ответ от модели.")
         except Exception as e_final_fail: logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение об ошибке: {e_final_fail}")

# =============================================================

# ===== Обработчики фото и документов =====

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tesseract_available = False
    try: pytesseract.pytesseract.get_tesseract_version(); tesseract_available = True
    except Exception as e: logger.debug(f"Tesseract не найден: {e}.")

    if not update.message or not update.message.photo: logger.warning(f"ChatID: {chat_id} | Нет фото в handle_photo."); return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    try:
        photo_file = await update.message.photo[-1].get_file()
        file_bytes = await photo_file.download_as_bytearray()
    except Exception as e: logger.error(f"ChatID: {chat_id} | Не удалось скачать фото: {e}", exc_info=True); await update.message.reply_text("❌ Не удалось загрузить."); return

    user_caption = update.message.caption or ""

    # --- OCR ---
    if tesseract_available:
        try:
            image = Image.open(io.BytesIO(file_bytes))
            extracted_text = pytesseract.image_to_string(image, lang='rus+eng', timeout=15)
            if extracted_text and extracted_text.strip():
                logger.info(f"ChatID: {chat_id} | OCR нашел текст.")
                ocr_context = f"На изображении обнаружен текст:\n```\n{extracted_text.strip()}\n```" # Упростил
                user_prompt = f"Пользователь загрузил фото{' с подписью: \"'+user_caption+'\"' if user_caption else ''}. {ocr_context}\nЧто можешь сказать об этом фото и тексте?"
                fake_message = type('obj', (object,), {'text': user_prompt, 'reply_text': update.message.reply_text, 'chat_id': chat_id})
                fake_update = type('obj', (object,), {'effective_chat': update.effective_chat, 'message': fake_message})
                await handle_message(fake_update, context); return
            else: logger.info(f"ChatID: {chat_id} | OCR не нашел текст.")
        except pytesseract.TesseractNotFoundError: logger.error("Tesseract не найден! OCR отключен."); tesseract_available = False
        except RuntimeError as timeout_error: logger.warning(f"ChatID: {chat_id} | OCR таймаут: {timeout_error}")
        except Exception as e: logger.warning(f"ChatID: {chat_id} | Ошибка OCR: {e}", exc_info=True)
    # --- Конец OCR ---

    # --- Обработка как изображение ---
    logger.info(f"ChatID: {chat_id} | Обработка фото как изображения.")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    MAX_IMAGE_BYTES = 4 * 1024 * 1024
    if len(file_bytes) > MAX_IMAGE_BYTES: logger.warning(f"ChatID: {chat_id} | Изображение ({len(file_bytes)} байт) велико.")

    try: b64_data = base64.b64encode(file_bytes).decode()
    except Exception as e: logger.error(f"ChatID: {chat_id} | Ошибка Base64: {e}", exc_info=True); await update.message.reply_text("❌ Ошибка обработки."); return

    if user_caption: prompt_text = f"Пользователь прислал фото с подписью: \"{user_caption}\". Опиши изображение и связь с подписью."
    else: prompt_text = "Пользователь прислал фото. Опиши изображение."
    parts = [{"text": prompt_text}, {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}]

    model_id = get_user_setting(context, 'selected_model', DEFAULT_MODEL)
    temperature = get_user_setting(context, 'temperature', 1.0)

    vision_capable_keywords = ['flash', 'pro', 'vision', 'ultra']
    is_vision_model = any(keyword in model_id for keyword in vision_capable_keywords)
    if not is_vision_model:
         vision_models_ids = [m_id for m_id in AVAILABLE_MODELS if any(keyword in m_id for keyword in vision_capable_keywords)]
         if vision_models_ids:
             original_model_name = AVAILABLE_MODELS.get(model_id, model_id)
             model_id = vision_models_ids[0]
             new_model_name = AVAILABLE_MODELS.get(model_id, model_id)
             logger.warning(f"ChatID: {chat_id} | Модель {original_model_name} не vision. Временно -> {new_model_name}.")
         else: logger.error(f"ChatID: {chat_id} | Нет vision моделей."); await update.message.reply_text("❌ Нет моделей для анализа изображений."); return

    logger.info(f"ChatID: {chat_id} | Анализ изображения. Модель: {model_id}, Темп: {temperature}")
    reply = None; last_exception = None

    # --- Вызов Vision модели с РЕТРАЯМИ ---
    for attempt in range(RETRY_ATTEMPTS):
        try:
            logger.info(f"ChatID: {chat_id} | Попытка {attempt + 1}/{RETRY_ATTEMPTS} анализа фото...")
            generation_config=genai.GenerationConfig(temperature=temperature, max_output_tokens=MAX_OUTPUT_TOKENS)
            model = genai.GenerativeModel(model_id, safety_settings=SAFETY_SETTINGS_BLOCK_NONE, generation_config=generation_config, system_instruction=system_instruction_text)
            response = await asyncio.to_thread(model.generate_content, [{"role": "user", "parts": parts}])

            if hasattr(response, 'text'): reply = response.text
            else: reply = None; logger.warning(f"ChatID: {chat_id} | Ответ vision не содержит 'text' (поп. {attempt + 1}).")

            if not reply:
                 try:
                    feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else None
                    candidates_info = response.candidates if hasattr(response, 'candidates') else []
                    block_reason_enum = feedback.block_reason if feedback and hasattr(feedback, 'block_reason') else None
                    block_reason = block_reason_enum.name if block_reason_enum and hasattr(block_reason_enum, 'name') else str(block_reason_enum or 'N/A')
                    finish_reason_enum = candidates_info[0].finish_reason if candidates_info and hasattr(candidates_info[0], 'finish_reason') else None
                    finish_reason_val = finish_reason_enum.name if finish_reason_enum and hasattr(finish_reason_enum, 'name') else str(finish_reason_enum or 'N/A')
                    logger.warning(f"ChatID: {chat_id} | Пустой ответ (фото, поп. {attempt + 1}). Block: {block_reason}, Finish: {finish_reason_val}")
                    if block_reason != 'UNSPECIFIED' and block_reason != 'N/A': reply = f"🤖 Не удалось описать фото (блок: {block_reason})."
                    elif finish_reason_val != 'STOP' and finish_reason_val != 'N/A': reply = f"🤖 Не удалось описать фото (причина: {finish_reason_val})."
                    else: reply = "🤖 Не удалось понять, что на фото (пусто)."
                    break # Выходим, даже если пустой ответ (чтобы не повторять)
                 except Exception as e_inner: logger.warning(f"ChatID: {chat_id} | Ошибка инфо (фото): {e_inner}", exc_info=True); reply = "🤖 Ошибка обработки ответа (фото)."

            if reply and "Не удалось понять" not in reply and "Не удалось описать" not in reply:
                 logger.info(f"ChatID: {chat_id} | Успешный анализ фото (поп. {attempt + 1})."); break

        except BlockedPromptException as e: logger.warning(f"ChatID: {chat_id} | Анализ фото заблокирован (поп. {attempt + 1}): {e}"); reply = f"❌ Анализ фото заблокирован."; last_exception = e; break
        except StopCandidateException as e: logger.warning(f"ChatID: {chat_id} | Анализ фото остановлен (поп. {attempt + 1}): {e}"); reply = f"❌ Анализ фото остановлен."; last_exception = e; break
        except Exception as e:
            last_exception = e; error_message = str(e); logger.warning(f"ChatID: {chat_id} | Ошибка анализа фото (поп. {attempt + 1}): {error_message[:200]}...")
            is_retryable = "500" in error_message or "503" in error_message
            is_input_error = "400" in error_message and ("image" in error_message.lower() or "input" in error_message.lower() or "payload size" in error_message.lower())
            is_key_error = "400" in error_message and "API key not valid" in error_message
            is_location_error = "User location is not supported" in error_message
            is_rate_limit = "429" in error_message

            if is_input_error: reply = f"❌ Ошибка: Проблема с фото или моделью ({error_message[:100]}...)."; break
            elif is_key_error: reply = "❌ Ошибка: Неверный Google API ключ."; break
            elif is_location_error: reply = f"❌ Ошибка: Регион не поддерживается ({model_id})."; break
            elif is_rate_limit: reply = f"❌ Ошибка: Лимит запросов (429)."; break
            elif is_retryable and attempt < RETRY_ATTEMPTS - 1:
                wait_time = RETRY_DELAY_SECONDS * (2 ** attempt); logger.info(f"ChatID: {chat_id} | Ожидание {wait_time:.1f} сек перед ретраем фото..."); await asyncio.sleep(wait_time); continue
            else: logger.error(f"ChatID: {chat_id} | Не удалось анализ фото после {attempt + 1} попыток. Ошибка: {e}", exc_info=True if not is_retryable else False); reply = f"❌ Ошибка анализа фото ({error_message[:100]}...)"; break
    # --- Конец блока ретраев ---

    if reply:
        MAX_MESSAGE_LENGTH = 4096
        reply_chunks = [reply[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(reply), MAX_MESSAGE_LENGTH)]
        message_to_reply = update.message
        try:
             for i, chunk in enumerate(reply_chunks):
                 send_method = message_to_reply.reply_text if i == 0 else context.bot.send_message
                 # ===== ИЗМЕНЕНИЕ: Добавлен parse_mode =====
                 kwargs = {'text': chunk, 'parse_mode': ParseMode.MARKDOWN}
                 if i > 0: kwargs['chat_id'] = chat_id
                 message_to_reply = await send_method(**kwargs)
                 await asyncio.sleep(0.1)
        except Exception as e_reply:
            logger.error(f"ChatID: {chat_id} | Ошибка отправки ответа (фото) ({type(e_reply).__name__}): {e_reply}. Пробую без Markdown.", exc_info=False)
            try:
                kwargs['parse_mode'] = None # Убираем Markdown
                if i == 0: await message_to_reply.reply_text(**kwargs)
                else: await context.bot.send_message(**kwargs)
            except Exception as e_send_no_md: logger.error(f"ChatID: {chat_id} | Не удалось отправить ответ (фото) даже без Markdown: {e_send_no_md}", exc_info=True)
    else:
         logger.error(f"ChatID: {chat_id} | Нет ответа (фото) для отправки.")
         try: await update.message.reply_text("🤖 К сожалению, не удалось проанализировать изображение.")
         except Exception as e_final_fail: logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение об ошибке (фото): {e_final_fail}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not update.message or not update.message.document: logger.warning(f"ChatID: {chat_id} | Нет документа."); return
    doc = update.message.document
    allowed_mime_prefixes = ('text/', 'application/json', 'application/xml', 'application/csv', 'application/x-python', 'application/x-sh', 'application/javascript', 'application/x-yaml', 'application/x-tex', 'application/rtf', 'application/sql')
    allowed_mime_types = ('application/octet-stream',)
    mime_type = doc.mime_type or "application/octet-stream"
    is_allowed_prefix = any(mime_type.startswith(prefix) for prefix in allowed_mime_prefixes)
    is_allowed_type = mime_type in allowed_mime_types
    if not (is_allowed_prefix or is_allowed_type):
        await update.message.reply_text(f"⚠️ Пока читаю только текст (.txt, .py, .json и т.п.). Ваш тип: `{mime_type}`"); logger.warning(f"ChatID: {chat_id} | Неподдерживаемый файл: {doc.file_name} (MIME: {mime_type})"); return

    MAX_FILE_SIZE_MB = 15; file_size_bytes = doc.file_size or 0
    if file_size_bytes == 0: logger.warning(f"ChatID: {chat_id} | Файл '{doc.file_name}' пустой."); # await update.message.reply_text(f"⚠️ Файл '{doc.file_name}' пустой."); return
    if file_size_bytes > MAX_FILE_SIZE_MB * 1024 * 1024: await update.message.reply_text(f"❌ Файл '{doc.file_name}' > {MAX_FILE_SIZE_MB} MB."); logger.warning(f"ChatID: {chat_id} | Слишком большой файл: {doc.file_name} ({file_size_bytes / (1024*1024):.2f} MB)"); return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    try: doc_file = await doc.get_file(); file_bytes = await doc_file.download_as_bytearray()
    except Exception as e: logger.error(f"ChatID: {chat_id} | Не удалось скачать '{doc.file_name}': {e}", exc_info=True); await update.message.reply_text("❌ Не удалось загрузить."); return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    text = None; detected_encoding = None; encodings_to_try = ['utf-8', 'cp1251', 'latin-1', 'cp866', 'iso-8859-5']; chardet_available = False
    try: import chardet; chardet_available = True
    except ImportError: logger.info("chardet не найден, пропускаем автоопределение.")
    if chardet_available:
        try:
            chardet_limit = min(len(file_bytes), 50 * 1024)
            if chardet_limit > 0:
                 detected = chardet.detect(file_bytes[:chardet_limit])
                 if detected and detected['encoding'] and detected['confidence'] > 0.6:
                      _enc = detected['encoding'].lower()
                      logger.info(f"ChatID: {chat_id} | Chardet: {_enc} (conf: {detected['confidence']:.2f}) для '{doc.file_name}'")
                      if _enc not in encodings_to_try: encodings_to_try.insert(0, _enc)
                      if _enc == 'utf-8' and file_bytes.startswith(b'\xef\xbb\xbf'):
                          if 'utf-8-sig' not in encodings_to_try: encodings_to_try.insert(0, 'utf-8-sig'); logger.info("-> Используем utf-8-sig")
            else: logger.warning(f"ChatID: {chat_id} | Файл '{doc.file_name}' пуст для chardet.")
        except Exception as e_chardet: logger.warning(f"Ошибка chardet для '{doc.file_name}': {e_chardet}")

    for encoding in list(dict.fromkeys(encodings_to_try)):
        try:
            if not file_bytes and file_size_bytes == 0: text = ""; logger.info(f"Файл '{doc.file_name}' пуст."); break # Обработка пустого файла
            text = file_bytes.decode(encoding); detected_encoding = encoding; logger.info(f"ChatID: {chat_id} | Файл '{doc.file_name}' декодирован как {encoding}."); break
        except (UnicodeDecodeError, LookupError): logger.debug(f"ChatID: {chat_id} | Файл '{doc.file_name}' не в {encoding}.")
        except Exception as e: logger.error(f"ChatID: {chat_id} | Ошибка декодирования '{doc.file_name}' как {encoding}: {e}", exc_info=True)

    if text is None: logger.error(f"ChatID: {chat_id} | Не удалось декодировать '{doc.file_name}' ({list(dict.fromkeys(encodings_to_try))})"); await update.message.reply_text(f"❌ Не удалось прочитать '{doc.file_name}'. Попробуйте UTF-8."); return
    if text == "" and file_size_bytes > 0: logger.warning(f"ChatID: {chat_id} | Текст пуст после декодирования '{doc.file_name}'."); await update.message.reply_text(f"⚠️ Не удалось извлечь текст из '{doc.file_name}'."); return
    if text == "" and file_size_bytes == 0: await update.message.reply_text(f"ℹ️ Файл '{doc.file_name}' пустой."); return # Уведомляем о пустом файле

    approx_max_tokens = (MAX_OUTPUT_TOKENS * 2) if MAX_OUTPUT_TOKENS < 4000 else 8000; MAX_FILE_CHARS = min(MAX_CONTEXT_CHARS // 2, approx_max_tokens * 3); truncated = text; warning_msg = ""
    if len(text) > MAX_FILE_CHARS:
        truncated = text[:MAX_FILE_CHARS]; last_newline = truncated.rfind('\n')
        if last_newline > MAX_FILE_CHARS * 0.8: truncated = truncated[:last_newline]
        warning_msg = f"\n\n(⚠️ Текст файла обрезан до ~{len(truncated) // 1000}k симв.)"; logger.warning(f"ChatID: {chat_id} | Текст '{doc.file_name}' обрезан до {len(truncated)} симв.")

    user_caption = update.message.caption or ""; file_name = doc.file_name or "файл"; encoding_info = f"(кодировка: {detected_encoding})" if detected_encoding else ""
    file_context = f"Содержимое файла '{file_name}' {encoding_info}:\n```\n{truncated}\n```{warning_msg}"
    if user_caption: user_prompt = f"Загружен файл '{file_name}' с комментарием: \"{user_caption}\". {file_context}\nПроанализируй."
    else: user_prompt = f"Загружен файл '{file_name}'. {file_context}\nЧто скажешь об этом тексте?"

    fake_message = type('obj', (object,), {'text': user_prompt, 'reply_text': update.message.reply_text, 'chat_id': chat_id})
    fake_update = type('obj', (object,), {'effective_chat': update.effective_chat, 'message': fake_message})
    await handle_message(fake_update, context)
# ======================================

# --- Функции веб-сервера и запуска ---
async def setup_bot_and_server(stop_event: asyncio.Event):
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    timeout = aiohttp.ClientTimeout(total=60.0, connect=10.0, sock_connect=10.0, sock_read=30.0)
    aiohttp_session = aiohttp.ClientSession(timeout=timeout)
    application.bot_data['aiohttp_session'] = aiohttp_session
    logger.info("Сессия aiohttp создана.")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("temp", set_temperature))
    application.add_handler(CommandHandler("search_on", enable_search))
    application.add_handler(CommandHandler("search_off", disable_search))
    application.add_handler(CallbackQueryHandler(select_model_callback, pattern="^set_model_"))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    try:
        await application.initialize()
        webhook_path_segment = GEMINI_WEBHOOK_PATH.strip('/')
        webhook_url = f"{WEBHOOK_HOST.rstrip('/')}/{webhook_path_segment}"
        logger.info(f"Установка вебхука: {webhook_url}")
        await application.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, secret_token=os.getenv('WEBHOOK_SECRET_TOKEN'))
        logger.info("Вебхук установлен.")
        return application, run_web_server(application, stop_event)
    except Exception as e:
        logger.critical(f"Ошибка инициализации/вебхука: {e}", exc_info=True)
        if 'aiohttp_session' in application.bot_data and not application.bot_data['aiohttp_session'].closed: await application.bot_data['aiohttp_session'].close()
        raise

async def run_web_server(application: Application, stop_event: asyncio.Event):
    app = aiohttp.web.Application()
    async def health_check(request):
        try:
            bot_info = await application.bot.get_me()
            if bot_info: return aiohttp.web.Response(text=f"OK: Bot {bot_info.username} active.")
            else: return aiohttp.web.Response(text="Error: Bot info unavailable", status=503)
        except Exception as e: logger.error(f"Health check fail: {e}", exc_info=True); return aiohttp.web.Response(text=f"Error: HC fail ({type(e).__name__})", status=503)

    app.router.add_get('/', health_check)
    app['bot_app'] = application
    webhook_path = GEMINI_WEBHOOK_PATH.strip('/')
    if not webhook_path.startswith('/'): webhook_path = '/' + webhook_path
    app.router.add_post(webhook_path, handle_telegram_webhook)
    logger.info(f"Вебхук слушает на: {webhook_path}")

    runner = aiohttp.web.AppRunner(app); await runner.setup()
    port = int(os.getenv("PORT", "10000")); host = "0.0.0.0"
    site = aiohttp.web.TCPSite(runner, host, port)
    try: await site.start(); logger.info(f"Веб-сервер запущен: http://{host}:{port}"); await stop_event.wait()
    except asyncio.CancelledError: logger.info("Веб-сервер отменен.")
    except Exception as e: logger.error(f"Ошибка веб-сервера: {e}", exc_info=True)
    finally: logger.info("Остановка веб-сервера..."); await runner.cleanup(); logger.info("Веб-сервер остановлен.")

async def handle_telegram_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    application = request.app.get('bot_app')
    if not application: logger.critical("Bot application не найден!"); return aiohttp.web.Response(status=500, text="ISE: Bot not found")
    secret_token = os.getenv('WEBHOOK_SECRET_TOKEN')
    if secret_token:
         header_token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
         if header_token != secret_token: logger.warning("Неверный секретный токен."); return aiohttp.web.Response(status=403, text="Forbidden")
    try:
        data = await request.json(); update = Update.de_json(data, application.bot)
        # Защищаем process_update от отмены во время выполнения
        asyncio.create_task(asyncio.shield(application.process_update(update)))
        return aiohttp.web.Response(text="OK", status=200)
    except json.JSONDecodeError as e: body = await request.text(); logger.error(f"Ошибка JSON от TG: {e}. Тело: {body[:500]}..."); return aiohttp.web.Response(text="Bad Request", status=400)
    except Exception as e: logger.error(f"Критическая ошибка вебхука: {e}", exc_info=True); return aiohttp.web.Response(text="Internal Server Error", status=500)

async def main():
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper(); log_level = getattr(logging, log_level_str, logging.INFO)
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=log_level)
    logging.getLogger('httpx').setLevel(logging.WARNING); logging.getLogger('httpcore').setLevel(logging.WARNING); logging.getLogger('google.api_core').setLevel(logging.WARNING)
    logging.getLogger('google.generativeai').setLevel(logging.INFO); logging.getLogger('duckduckgo_search').setLevel(logging.INFO); logging.getLogger('PIL').setLevel(logging.INFO)
    logging.getLogger('aiohttp.access').setLevel(logging.WARNING); logging.getLogger('telegram.ext').setLevel(logging.INFO); logging.getLogger('telegram.bot').setLevel(logging.INFO)
    logger.setLevel(log_level)

    loop = asyncio.get_running_loop(); stop_event = asyncio.Event()
    def signal_handler():
        if not stop_event.is_set(): logger.info("Сигнал остановки, завершаю..."); stop_event.set()
        else: logger.warning("Повторный сигнал.")
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError: logger.warning(f"Нет обработчика {sig}."); try: signal.signal(signal.SIGINT, lambda s, f: signal_handler()) except Exception as e: logger.error(f"Ошибка signal.signal: {e}")

    application = None; web_server_task = None; aiohttp_session_main = None
    try:
        logger.info(f"--- Запуск бота (Log Level: {log_level_str}) ---")
        application, web_server_coro = await setup_bot_and_server(stop_event)
        web_server_task = asyncio.create_task(web_server_coro)
        aiohttp_session_main = application.bot_data.get('aiohttp_session')
        logger.info("Приложение запущено. Ctrl+C для остановки."); await stop_event.wait()
    except asyncio.CancelledError: logger.info("Главная задача отменена.")
    except Exception as e: logger.critical("Критическая ошибка до цикла ожидания.", exc_info=True)
    finally:
        logger.info("--- Остановка приложения ---")
        if not stop_event.is_set(): stop_event.set()
        if web_server_task and not web_server_task.done():
             logger.info("Остановка веб-сервера..."); try: await asyncio.wait_for(web_server_task, timeout=15.0); logger.info("Веб-сервер остановлен.")
             except asyncio.TimeoutError: logger.warning("Таймаут остановки веб-сервера, отмена..."); web_server_task.cancel(); try: await web_server_task except: pass
             except Exception as e: logger.error(f"Ошибка остановки веб-сервера: {e}", exc_info=True)
        if application: logger.info("Остановка Telegram App..."); try: await application.shutdown(); logger.info("Telegram App остановлено.") except Exception as e: logger.error(f"Ошибка application.shutdown(): {e}", exc_info=True)
        if aiohttp_session_main and not aiohttp_session_main.closed: logger.info("Закрытие HTTP сессии..."); await aiohttp_session_main.close(); await asyncio.sleep(0.5); logger.info("HTTP сессия закрыта.")
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()];
        if tasks: logger.info(f"Отмена {len(tasks)} задач..."); [task.cancel() for task in tasks]; results = await asyncio.gather(*tasks, return_exceptions=True); logger.info("Задачи отменены.")
        logger.info("--- Приложение остановлено ---")

if __name__ == '__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: logger.info("Прервано пользователем (Ctrl+C).")
    except Exception as e: logger.critical("Неперехваченная ошибка в asyncio.run(main).", exc_info=True)

# --- END OF FILE main.py ---
