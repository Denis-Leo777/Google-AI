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
    # Проверяем, что список не пустой (на случай если атрибуты внезапно пропадут)
    if not SAFETY_SETTINGS_BLOCK_NONE and HARM_CATEGORIES_STRINGS:
         logger.warning("Не удалось создать SAFETY_SETTINGS_BLOCK_NONE с Enum типами, хотя импорт был успешен? Используем строки.")
         SAFETY_SETTINGS_BLOCK_NONE = [
             {"category": cat_str, "threshold": BLOCK_NONE_STRING}
             for cat_str in HARM_CATEGORIES_STRINGS
         ]
    elif SAFETY_SETTINGS_BLOCK_NONE:
         logger.info("Типы google.generativeai.types успешно импортированы. Настройки безопасности BLOCK_NONE установлены с Enum.")
    else:
        # Если HARM_CATEGORIES_STRINGS пуст (не должно быть), то и список будет пуст
        logger.warning("Список HARM_CATEGORIES_STRINGS пуст, настройки безопасности не установлены.")
        SAFETY_SETTINGS_BLOCK_NONE = []


except ImportError:
    # Если импорт не удался, логируем и создаем заглушки для типов ошибок
    logger.warning("Не удалось импортировать типы из google.generativeai.types. Используются строковые значения для настроек безопасности.")
    BlockedPromptException = Exception
    StopCandidateException = Exception
    # Устанавливаем настройки безопасности, используя строки
    SAFETY_SETTINGS_BLOCK_NONE = [
        {"category": cat_str, "threshold": BLOCK_NONE_STRING}
        for cat_str in HARM_CATEGORIES_STRINGS
    ]
    logger.warning("Настройки безопасности установлены с использованием строковых представлений (BLOCK_NONE).")
    # Определяем заглушки для типов, чтобы код дальше не падал при проверках
    HarmCategory = type('obj', (object,), {})
    HarmBlockThreshold = type('obj', (object,), {})
    SafetyRating = type('obj', (object,), {'category': None, 'probability': None})
    BlockReason = type('obj', (object,), {'UNSPECIFIED': 'UNSPECIFIED'})
    FinishReason = type('obj', (object,), {'STOP': 'STOP'})
# ======================================================================


# Переменные окружения и их проверка
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY') # Используется для Gemini и Google Search
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')

# ===== Улучшенная проверка переменных окружения =====
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
# =================================================

# Настройка Gemini
genai.configure(api_key=GOOGLE_API_KEY)

# Модели
AVAILABLE_MODELS = {
    'gemini-2.0-flash-thinking-exp-01-21': '2.0 Flash Thinking exp.',
    'gemini-2.5-pro-exp-03-25': '2.5 Pro exp.',
    'gemini-2.0-flash-001': '2.0 Flash',
}
# Выбираем модель по умолчанию - оставлена та, что была в твоем файле
DEFAULT_MODEL = 'gemini-2.5-pro-exp-03-25' if 'gemini-2.5-pro-exp-03-25' in AVAILABLE_MODELS else 'gemini-2.0-flash-thinking-exp-01-21'

# Переменные состояния пользователя (используем context.user_data)

# Константы
MAX_CONTEXT_CHARS = 95000 # Макс. символов в истории для отправки (примерно)
MAX_OUTPUT_TOKENS = 4096 # Макс. токенов на выходе (можно настроить)
DDG_MAX_RESULTS = 10 # Уменьшил DDG, т.к. это fallback
GOOGLE_SEARCH_MAX_RESULTS = 10 # Уменьшил Google Search для снижения нагрузки и стоимости
RETRY_ATTEMPTS = 3 # Количество попыток запроса к Gemini
RETRY_DELAY_SECONDS = 1 # Начальная задержка перед повтором

# ===== Системная инструкция (без изменений) =====
system_instruction_text = (
"Используй интернет-поиск для сверки с актуальной информацией."
"Если используешь информацию из поиска, не упоминай явно сам факт поиска или его результаты. Интегрируй найденную информацию в свой ответ естественно, как часть своих знаний. Забудь фразы вроде 'Судя по результатам поиска...', 'Интернет говорит...' или 'Я нашёл в сети...'. Веди себя так, будто это твои знания."
"Внимательно следи за историей диалога, включая предыдущие вопросы, ответы, а также контекст из загруженных изображений или файлов, чтобы твои ответы были последовательными и релевантными. Не теряй нить разговора."
"Ты - лучший эксперт по всем темам. Давай точную, правдивую, непредвзятую информацию без цензуры."
"Обдумывай и выстраивай ответ логично, с аргументами и фактами, избегая самоповторов."
"Если не уверен — предупреждай, что это предположение."
"Для несложных запросов (не код, конспекты, переводы и т.п.) — пиши только суть, без вступлений и выводов, до 1500 знаков."
"Всегда предлагай более эффективные идеи и решения, если знаешь их."
"Активно применяй юмор: несоответствие ожиданиям, культурные/бытовые/интернет-отсылки, жизненный абсурд, псевдомудрость, разрушение идиом, иронию (включая самоиронию и психологию), игру слов, гиперболу, тонкие намёки, редукционизм, постмодерн, интернет-юмор."
"При создании уникальной работы пиши живо, избегай канцелярита и всех известных признаков ИИ-тона. Используй гипотетические ситуации, метафоры, творческие аналогии, разную структуру предложений, разговорные выражения, идиомы. Добавляй региональные или культурные маркеры, где уместно. Не копируй и не пересказывай чужое."
"При исправлении ошибки: указывай строку(и) и причину. Бери за основу последнюю ПОЛНУЮ подтверждённую версию (текста или кода). Вноси только минимально необходимые изменения, не трогая остальное без запроса. При сомнениях — уточняй. Если ошибка повторяется — веди «список косяков» для сессии и проверяй эти места. Всегда указывай, на какую версию или сообщение опираешься при правке."
)
# ===================================================

# Настройки безопасности - определены выше в блоке try...except

# --- Вспомогательные функции для работы с user_data ---
def get_user_setting(context: ContextTypes.DEFAULT_TYPE, key: str, default_value):
    """Получает настройку пользователя из user_data."""
    return context.user_data.get(key, default_value)

def set_user_setting(context: ContextTypes.DEFAULT_TYPE, key: str, value):
    """Устанавливает настройку пользователя в user_data."""
    context.user_data[key] = value
# -------------------------------------------------------

# ===== Команды с использованием user_data =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # Устанавливаем значения по умолчанию при старте
    set_user_setting(context, 'selected_model', DEFAULT_MODEL)
    set_user_setting(context, 'search_enabled', True)
    set_user_setting(context, 'temperature', 1.0)
    # Очищаем историю при старте (опционально)
    context.chat_data['history'] = []

    default_model_name = AVAILABLE_MODELS.get(DEFAULT_MODEL, DEFAULT_MODEL)
    start_message = (
        f"**{default_model_name}** - модель по умолчанию."
        f"\n Поиск Google/DDG включен, используются улучшенные настройки точности, логики из юмора."
        f"\n Я также умею читать картинки (с текстом и без) и текстовые файлы."
        f"\n `/model` — сменить модель,"
        f"\n `/search_on` / `/search_off` — вкл/выкл поиск,"
        f"\n `/clear` — очистить историю диалога."
    )
    await update.message.reply_text(start_message, parse_mode='Markdown')

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['history'] = []
    await update.message.reply_text("🧹 История диалога очищена.")

async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
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
    # Сортируем модели для единообразия
    sorted_models = sorted(AVAILABLE_MODELS.items())
    for m, name in sorted_models:
         button_text = f"{'✅ ' if m == current_model else ''}{name}"
         # Используем префикс 'set_model_' для callback_data
         keyboard.append([InlineKeyboardButton(button_text, callback_data=f"set_model_{m}")])
    await update.message.reply_text("Выберите модель:", reply_markup=InlineKeyboardMarkup(keyboard))

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Отвечаем на коллбек, чтобы убрать "часики"
    callback_data = query.data

    # Проверяем префикс
    if callback_data and callback_data.startswith("set_model_"):
        selected = callback_data.replace("set_model_", "")
        if selected in AVAILABLE_MODELS:
            set_user_setting(context, 'selected_model', selected)
            model_name = AVAILABLE_MODELS[selected]
            reply_text = f"Модель установлена: **{model_name}**"
            try:
                await query.edit_message_text(reply_text, parse_mode='Markdown')
            except Exception as e:
                logger.warning(f"Не удалось изменить сообщение с кнопками: {e}. Отправляю новое.")
                # Отправляем новое сообщение, если старое изменить не удалось
                await context.bot.send_message(chat_id=query.message.chat_id, text=reply_text, parse_mode='Markdown')
        else:
            try:
                await query.edit_message_text("❌ Неизвестная модель выбрана.")
            except Exception: # Если сообщение уже было изменено/удалено
                 await context.bot.send_message(chat_id=query.message.chat_id, text="❌ Неизвестная модель выбрана.")
    else:
        logger.warning(f"Получен неизвестный callback_data: {callback_data}")
        try:
            await query.edit_message_text("❌ Ошибка обработки выбора.")
        except Exception:
            pass # Игнорируем ошибки редактирования, если коллбек странный

# ============================================

# ===== Функция поиска Google (улучшенная обработка ошибок) =====
async def perform_google_search(query: str, api_key: str, cse_id: str, num_results: int, session: aiohttp.ClientSession) -> list[str] | None:
    """Выполняет поиск через Google Custom Search API и возвращает список сниппетов."""
    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': api_key, 'cx': cse_id, 'q': query, 'num': num_results, 'lr': 'lang_ru', 'gl': 'ru'}
    encoded_params = urlencode(params)
    full_url = f"{search_url}?{encoded_params}"
    query_short = query[:50] + '...' if len(query) > 50 else query
    logger.debug(f"Запрос к Google Search API для '{query_short}': {search_url}?key=...&cx=...&num={num_results}&lr=lang_ru&gl=ru")

    try:
        async with session.get(full_url, timeout=aiohttp.ClientTimeout(total=10.0)) as response:
            response_text = await response.text() # Читаем текст для логов в любом случае
            if response.status == 200:
                try:
                    data = json.loads(response_text) # Парсим JSON
                except json.JSONDecodeError as e_json:
                    logger.error(f"Google Search: Ошибка декодирования JSON для '{query_short}' - {e_json}. Ответ: {response_text[:200]}...")
                    return None

                items = data.get('items', [])
                snippets = [item.get('snippet', item.get('title', '')) for item in items if item.get('snippet') or item.get('title')]
                if snippets:
                    logger.info(f"Google Search: Найдено {len(snippets)} результатов для '{query_short}'.")
                    return snippets
                else:
                    logger.info(f"Google Search: Результаты для '{query_short}' не содержат сниппетов/заголовков.")
                    return None # Возвращаем None, чтобы запустить DDG
            # Обработка конкретных кодов ошибок
            elif response.status == 400:
                 logger.error(f"Google Search: Ошибка 400 (Bad Request) для '{query_short}'. Проверьте параметры запроса. Ответ: {response_text[:200]}...")
            elif response.status == 403:
                 logger.error(f"Google Search: Ошибка 403 (Forbidden) для '{query_short}'. Проверьте API ключ, его ограничения и включен ли Custom Search API. Ответ: {response_text[:200]}...")
            elif response.status == 429:
                logger.warning(f"Google Search: Ошибка 429 (Too Many Requests) для '{query_short}'. Квота исчерпана!")
            elif response.status >= 500:
                 logger.warning(f"Google Search: Серверная ошибка {response.status} для '{query_short}'. Ответ: {response_text[:200]}...")
            else:
                logger.error(f"Google Search: Неожиданный статус {response.status} для '{query_short}'. Ответ: {response_text[:200]}...")
            return None # Во всех случаях ошибки возвращаем None

    except aiohttp.ClientConnectorError as e:
        logger.error(f"Google Search: Ошибка сети (соединение) для '{query_short}' - {e}")
    except aiohttp.ClientError as e: # Ловим другие ошибки aiohttp
        logger.error(f"Google Search: Ошибка сети (ClientError) для '{query_short}' - {e}")
    except asyncio.TimeoutError:
         logger.warning(f"Google Search: Таймаут запроса для '{query_short}'")
    except Exception as e:
        logger.error(f"Google Search: Непредвиденная ошибка для '{query_short}' - {e}", exc_info=True)
    return None
# ===========================================================

# ===== Основной обработчик сообщений с РЕТРАЯМИ =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # Получаем сообщение, учитывая "фейковые" апдейты
    original_user_message = ""
    if update.message and update.message.text:
         original_user_message = update.message.text.strip()
    elif hasattr(update, 'message') and hasattr(update.message, 'text') and update.message.text: # Проверка для фейковых
         original_user_message = update.message.text.strip()

    if not original_user_message:
        logger.warning(f"ChatID: {chat_id} | Получено пустое или нетекстовое сообщение в handle_message.")
        return

    # Получаем настройки пользователя
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

        # Получаем сессию aiohttp из контекста бота
        session = context.bot_data.get('aiohttp_session')
        if not session or session.closed:
            logger.info("Создание новой сессии aiohttp для поиска.")
            # Устанавливаем таймауты по умолчанию для сессии
            timeout = aiohttp.ClientTimeout(total=60.0, connect=10.0, sock_connect=10.0, sock_read=30.0)
            session = aiohttp.ClientSession(timeout=timeout)
            context.bot_data['aiohttp_session'] = session

        # Попытка Google Search
        google_results = await perform_google_search(
            original_user_message, GOOGLE_API_KEY, GOOGLE_CSE_ID, GOOGLE_SEARCH_MAX_RESULTS, session
        )

        if google_results:
            search_provider = "Google"
            search_context_snippets = google_results
            search_log_msg += f" (Google: {len(search_context_snippets)} рез.)"
        else:
            search_log_msg += " (Google: 0 рез./ошибка)"
            logger.info(f"ChatID: {chat_id} | Google не дал результатов. Пробуем DuckDuckGo...")
            # Попытка DuckDuckGo
            try:
                ddgs = DDGS()
                # Запускаем синхронный вызов в отдельном потоке
                results_ddg = await asyncio.to_thread(
                    ddgs.text,
                    original_user_message, region='ru-ru', max_results=DDG_MAX_RESULTS, timeout=10
                )
                if results_ddg:
                    ddg_snippets = [r.get('body', '') for r in results_ddg if r.get('body')]
                    if ddg_snippets:
                        search_provider = "DuckDuckGo"
                        search_context_snippets = ddg_snippets
                        search_log_msg += f" (DDG: {len(search_context_snippets)} рез.)"
                    else:
                         search_log_msg += " (DDG: 0 текст. рез.)"
                else:
                    search_log_msg += " (DDG: 0 рез.)"
            except TimeoutError: # Ошибка таймаута из ddgs.text
                 logger.warning(f"ChatID: {chat_id} | Таймаут поиска DuckDuckGo.")
                 search_log_msg += " (DDG: таймаут)"
            except Exception as e_ddg:
                logger.error(f"ChatID: {chat_id} | Ошибка поиска DuckDuckGo: {e_ddg}", exc_info=True)
                search_log_msg += " (DDG: ошибка)"
    # --- Конец блока поиска ---

    # Формируем финальный промпт для модели
    if search_context_snippets:
        search_context = "\n".join([f"- {s.strip()}" for s in search_context_snippets if s.strip()]) # Убираем пустые и пробельные
        if search_context: # Убедимся, что контекст не пустой после очистки
             final_user_prompt = (
                 f"Дополнительная информация по теме (используй её по необходимости, не ссылаясь):\n{search_context}\n\n"
                 f"Вопрос пользователя: \"{original_user_message}\""
             )
             logger.info(f"ChatID: {chat_id} | Добавлен контекст из {search_provider} ({len(search_context_snippets)} сниппетов).")
        else:
             # Если все сниппеты оказались пустыми
             final_user_prompt = original_user_message
             logger.info(f"ChatID: {chat_id} | Сниппеты из {search_provider} оказались пустыми, контекст не добавлен.")
             search_log_msg += " (пустые сниппеты)"
    else:
        final_user_prompt = original_user_message # Если поиска не было или он не дал результатов

    # Обновляем лог поиска
    logger.info(f"ChatID: {chat_id} | {search_log_msg}")
    logger.debug(f"ChatID: {chat_id} | Финальный промпт для Gemini (длина {len(final_user_prompt)}):\n{final_user_prompt[:500]}...") # Логируем начало

    # --- История и ее обрезка ---
    chat_history = context.chat_data.setdefault("history", [])
    # Добавляем только оригинальное сообщение пользователя в основную историю
    chat_history.append({"role": "user", "parts": [{"text": original_user_message}]})

    # Обрезка истории
    current_total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
    removed_count = 0
    while current_total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1:
        if len(chat_history) >= 2:
            chat_history.pop(0) # user
            chat_history.pop(0) # model
            removed_count += 2
        else:
            chat_history.pop(0) # user
            removed_count += 1
        current_total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))

    if removed_count > 0:
        logger.info(f"ChatID: {chat_id} | История обрезана, удалено {removed_count} сообщений. Текущая: {len(chat_history)} сообщ., ~{current_total_chars} симв.")

    # Создаем историю для модели: берем обрезанную основную историю БЕЗ последнего user сообщения
    # и добавляем final_user_prompt (который может содержать поисковый контекст)
    history_for_model = list(chat_history[:-1])
    history_for_model.append({"role": "user", "parts": [{"text": final_user_prompt}]})
    # --- Конец подготовки истории ---

    # --- Вызов модели с РЕТРАЯМИ ---
    reply = None
    response = None # Инициализируем response
    last_exception = None
    generation_successful = False

    for attempt in range(RETRY_ATTEMPTS):
        try:
            logger.info(f"ChatID: {chat_id} | Попытка {attempt + 1}/{RETRY_ATTEMPTS} запроса к модели {model_id}...")
            generation_config=genai.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=MAX_OUTPUT_TOKENS
            )
            model = genai.GenerativeModel(
                model_id,
                # tools=tools, # Пока нет инструментов
                safety_settings=SAFETY_SETTINGS_BLOCK_NONE, # Используем настройки (Enum или строки)
                generation_config=generation_config,
                system_instruction=system_instruction_text
            )

            # Запускаем синхронный вызов в потоке
            response = await asyncio.to_thread(
                model.generate_content,
                history_for_model # Передаем историю с финальным промптом
            )

            # Обработка результата сразу после получения
            # Проверка на наличие атрибута 'text' перед доступом
            if hasattr(response, 'text'):
                reply = response.text
            else:
                # Если атрибута text нет, возможно, генерация прервалась по другой причине
                reply = None
                logger.warning(f"ChatID: {chat_id} | Ответ модели не содержит атрибута 'text' (попытка {attempt + 1}).")


            # Обработка пустого ответа или отсутствия текста
            if not reply:
                 try:
                     feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else None
                     candidates_info = response.candidates if hasattr(response, 'candidates') else []

                     block_reason_enum = feedback.block_reason if feedback and hasattr(feedback, 'block_reason') else None
                     # Используем .name если есть, иначе строку
                     block_reason = block_reason_enum.name if block_reason_enum and hasattr(block_reason_enum, 'name') else str(block_reason_enum or 'N/A')


                     finish_reason_enum = candidates_info[0].finish_reason if candidates_info and hasattr(candidates_info[0], 'finish_reason') else None
                     finish_reason_val = finish_reason_enum.name if finish_reason_enum and hasattr(finish_reason_enum, 'name') else str(finish_reason_enum or 'N/A')


                     safety_ratings = feedback.safety_ratings if feedback and hasattr(feedback, 'safety_ratings') else []
                     safety_info = ", ".join([f"{s.category.name}: {s.probability.name}" for s in safety_ratings if hasattr(s, 'category') and hasattr(s, 'probability')])

                     logger.warning(f"ChatID: {chat_id} | Пустой ответ или нет текста (попытка {attempt + 1}). Block: {block_reason}, Finish: {finish_reason_val}, Safety: [{safety_info}]")

                     # Формируем сообщение об ошибке, если причина не штатная
                     # Сравниваем с 'UNSPECIFIED' и 'STOP' как строки на случай, если Enum не загрузился
                     if block_reason != 'UNSPECIFIED' and block_reason != 'N/A':
                         reply = f"🤖 Модель не дала ответ. (Причина блокировки: {block_reason})"
                     elif finish_reason_val != 'STOP' and finish_reason_val != 'N/A':
                         reply = f"🤖 Модель завершила работу без ответа. (Причина: {finish_reason_val})"
                     else: # Если причина STOP или UNSPECIFIED, но текста нет
                         reply = "🤖 Модель дала пустой ответ."
                         generation_successful = True # Успех, хоть и пустой

                 except AttributeError as e_attr:
                     logger.warning(f"ChatID: {chat_id} | Пустой ответ, не удалось извлечь доп. инфо (AttributeError: {e_attr}). Попытка {attempt + 1}")
                     reply = "🤖 Получен пустой ответ от модели (ошибка атрибута)."
                 except Exception as e_inner:
                     logger.warning(f"ChatID: {chat_id} | Пустой ответ, ошибка извлечения доп. инфо: {e_inner}. Попытка {attempt + 1}", exc_info=True)
                     reply = "🤖 Получен пустой ответ от модели (внутренняя ошибка)."

            # Если ответ не пустой, считаем генерацию успешной
            if reply and reply != "🤖 Модель дала пустой ответ.": # Уточняем условие успеха
                 generation_successful = True

            # Если генерация успешна, выходим из цикла ретраев
            if generation_successful:
                 logger.info(f"ChatID: {chat_id} | Успешная генерация на попытке {attempt + 1}.")
                 break # Выход из цикла for

        except BlockedPromptException as e:
            logger.warning(f"ChatID: {chat_id} | Запрос заблокирован моделью на попытке {attempt + 1}: {e}")
            reply = f"❌ Запрос заблокирован моделью. (Причина: {e})"
            last_exception = e
            break # Не повторяем при блокировке
        except StopCandidateException as e:
             logger.warning(f"ChatID: {chat_id} | Генерация остановлена моделью на попытке {attempt + 1}: {e}")
             reply = f"❌ Генерация остановлена моделью. (Причина: {e})"
             last_exception = e
             break # Не повторяем, если модель сама остановилась
        except Exception as e:
            last_exception = e
            error_message = str(e)
            logger.warning(f"ChatID: {chat_id} | Ошибка генерации на попытке {attempt + 1}: {error_message[:200]}...")

            # Проверяем, стоит ли повторять ошибку
            is_retryable = False
            # Убрал таймауты из ретраев здесь, т.к. таймаут скорее всего будет на уровне to_thread, если он есть
            if "500" in error_message or "503" in error_message: # Internal Server Error or Service Unavailable
                is_retryable = True
                logger.info(f"ChatID: {chat_id} | Обнаружена ошибка 5xx, попытка повтора...")
            elif "429" in error_message and ("quota" in error_message or "Resource has been exhausted" in error_message): # Rate limit
                 logger.error(f"ChatID: {chat_id} | Достигнут лимит запросов (429). Прекращаем попытки.")
                 reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (429). Попробуйте позже."
                 break # Не повторяем при 429

            if is_retryable and attempt < RETRY_ATTEMPTS - 1:
                wait_time = RETRY_DELAY_SECONDS * (2 ** attempt)
                logger.info(f"ChatID: {chat_id} | Ожидание {wait_time:.1f} сек перед попыткой {attempt + 2}...")
                await asyncio.sleep(wait_time)
                continue
            else:
                # Ошибка не повтояремая или попытки кончились
                logger.error(f"ChatID: {chat_id} | Не удалось выполнить генерацию после {attempt + 1} попыток. Последняя ошибка: {e}", exc_info=True if not is_retryable else False)
                # Формируем финальное сообщение об ошибке
                if "400" in error_message and "API key not valid" in error_message:
                     reply = "❌ Ошибка: Неверный Google API ключ."
                elif "User location is not supported" in error_message:
                     reply = f"❌ Ошибка: Ваш регион не поддерживается для модели {model_id}."
                elif "400" in error_message and ("image input" in error_message or " richiesto" in error_message): # Пример ошибки неверного ввода
                     reply = f"❌ Ошибка: Неверный формат запроса к модели ({error_message[:100]}...). Возможно, модель не поддерживает данный тип ввода."
                else: # Общая ошибка
                     reply = f"❌ Ошибка при обращении к модели после {attempt + 1} попыток. ({error_message[:100]}...)"
                break # Выход из цикла for

    # --- Конец блока вызова модели ---

    # Добавляем финальный ответ (или сообщение об ошибке) в ОСНОВНУЮ историю
    if reply:
        if chat_history and chat_history[-1]["role"] == "user":
             chat_history.append({"role": "model", "parts": [{"text": reply}]})
        else:
             chat_history.append({"role": "model", "parts": [{"text": reply}]})
             logger.warning(f"ChatID: {chat_id} | Ответ модели добавлен, но последнее сообщение в истории было не 'user'.")

    # Отправка ответа пользователю
    if reply:
        MAX_MESSAGE_LENGTH = 4096
        reply_chunks = [reply[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(reply), MAX_MESSAGE_LENGTH)]
        message_to_reply = update.message
        try:
            for i, chunk in enumerate(reply_chunks):
                if i == 0:
                     message_to_reply = await message_to_reply.reply_text(chunk)
                else:
                     message_to_reply = await context.bot.send_message(chat_id=chat_id, text=chunk)
                await asyncio.sleep(0.1)
        except Exception as e_reply:
            logger.error(f"ChatID: {chat_id} | Ошибка при отправке ответа: {e_reply}. Попытка отправить в чат.", exc_info=True)
            try:
                 await context.bot.send_message(chat_id=chat_id, text=reply_chunks[-1])
            except Exception as e_send:
                 logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение в чат: {e_send}", exc_info=True)
    else:
         logger.error(f"ChatID: {chat_id} | Нет ответа для отправки пользователю после всех попыток.")
         try:
              await update.message.reply_text("🤖 К сожалению, не удалось получить ответ от модели после нескольких попыток.")
         except Exception as e_final_fail:
              logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение о финальной ошибке: {e_final_fail}")

# =============================================================

# ===== Обработчики фото и документов (без изменений логики ретраев, но с учетом user_data) =====

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tesseract_available = False
    try:
        pytesseract.pytesseract.get_tesseract_version()
        tesseract_available = True
        # logger.info(f"Tesseract доступен. Путь: {pytesseract.pytesseract.tesseract_cmd}")
    except Exception as e:
        logger.debug(f"Tesseract не найден стандартным способом: {e}. Поиск отключен.") # Снизил уровень до debug

    if not update.message or not update.message.photo:
        logger.warning(f"ChatID: {chat_id} | В handle_photo не найдено фото.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    try:
        photo_file = await update.message.photo[-1].get_file()
        file_bytes = await photo_file.download_as_bytearray()
    except Exception as e:
        logger.error(f"ChatID: {chat_id} | Не удалось скачать фото: {e}", exc_info=True)
        await update.message.reply_text("❌ Не удалось загрузить изображение.")
        return

    user_caption = update.message.caption if update.message.caption else ""

    # --- OCR ---
    if tesseract_available:
        try:
            image = Image.open(io.BytesIO(file_bytes))
            extracted_text = pytesseract.image_to_string(image, lang='rus+eng', timeout=15) # Добавил таймаут OCR
            if extracted_text and extracted_text.strip():
                logger.info(f"ChatID: {chat_id} | Обнаружен текст на изображении (OCR).")
                ocr_context = f"На изображении обнаружен следующий текст:\n```\n{extracted_text.strip()}\n```"
                if user_caption:
                    user_prompt = f"Пользователь загрузил фото с подписью: \"{user_caption}\". {ocr_context}\nЧто можешь сказать об этом фото и тексте на нём?"
                else:
                    user_prompt = f"Пользователь загрузил фото. {ocr_context}\nЧто можешь сказать об этом фото и тексте на нём?"

                # Создаем фейковый update
                fake_message = type('obj', (object,), {'text': user_prompt, 'reply_text': update.message.reply_text, 'chat_id': chat_id})
                fake_update = type('obj', (object,), {'effective_chat': update.effective_chat, 'message': fake_message})
                await handle_message(fake_update, context) # Передаем в основной обработчик
                return
            else:
                 logger.info(f"ChatID: {chat_id} | OCR не нашел текст на изображении.")
        except pytesseract.TesseractNotFoundError:
             logger.error("Tesseract не найден! Проверьте путь. OCR отключен.")
             tesseract_available = False
        except RuntimeError as timeout_error: # Ловим ошибку таймаута tesseract
             logger.warning(f"ChatID: {chat_id} | OCR таймаут: {timeout_error}")
        except Exception as e:
            logger.warning(f"ChatID: {chat_id} | Ошибка OCR: {e}", exc_info=True)
    # --- Конец OCR ---

    # --- Обработка как изображение ---
    logger.info(f"ChatID: {chat_id} | Обработка фото как изображения (без/после OCR).")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    MAX_IMAGE_BYTES = 4 * 1024 * 1024
    if len(file_bytes) > MAX_IMAGE_BYTES:
        logger.warning(f"ChatID: {chat_id} | Изображение ({len(file_bytes)} байт) может быть слишком большим для API.")
        # Можно добавить сжатие или отклонить

    try:
        b64_data = base64.b64encode(file_bytes).decode()
    except Exception as e:
         logger.error(f"ChatID: {chat_id} | Ошибка кодирования Base64: {e}", exc_info=True)
         await update.message.reply_text("❌ Ошибка обработки изображения.")
         return

    # Формируем промпт для vision модели
    if user_caption:
         prompt_text = f"Пользователь прислал фото с подписью: \"{user_caption}\". Опиши, что видишь на изображении и как это соотносится с подписью (если применимо)."
    else:
         prompt_text = "Пользователь прислал фото без подписи. Опиши, что видишь на изображении."
    parts = [{"text": prompt_text}, {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}]

    # Получаем настройки пользователя
    model_id = get_user_setting(context, 'selected_model', DEFAULT_MODEL)
    temperature = get_user_setting(context, 'temperature', 1.0)

    # Проверка на vision модель и возможное переключение
    # Упрощенная проверка - ищем 'flash' или 'pro' в названии. Лучше иметь явный список vision моделей.
    # Примерный список vision-совместимых моделей (может меняться!)
    vision_capable_keywords = ['flash', 'pro', 'vision', 'ultra'] # 'ultra' тоже может быть vision
    is_vision_model = any(keyword in model_id for keyword in vision_capable_keywords)

    if not is_vision_model:
         # Ищем первую попавшуюся vision-совместимую модель из доступных
         vision_models = [m for m_id, m in AVAILABLE_MODELS.items() if any(keyword in m_id for keyword in vision_capable_keywords)]
         if vision_models:
             original_model_name = AVAILABLE_MODELS.get(model_id, model_id)
             # Выбираем первую попавшуюся vision модель как запасную
             fallback_model_id = next(m_id for m_id, m in AVAILABLE_MODELS.items() if any(keyword in m_id for keyword in vision_capable_keywords))
             model_id = fallback_model_id # Временно используем ее ID
             new_model_name = AVAILABLE_MODELS.get(model_id, model_id)
             logger.warning(f"ChatID: {chat_id} | Модель {original_model_name} не vision. Временно использую {new_model_name}.")
         else:
             logger.error(f"ChatID: {chat_id} | Нет доступных vision моделей в AVAILABLE_MODELS.")
             await update.message.reply_text("❌ Нет доступных моделей для анализа изображений.")
             return

    logger.info(f"ChatID: {chat_id} | Анализ изображения. Модель: {model_id}, Темп: {temperature}")
    reply = None
    last_exception = None

    # --- Вызов Vision модели с РЕТРАЯМИ ---
    for attempt in range(RETRY_ATTEMPTS):
        try:
            logger.info(f"ChatID: {chat_id} | Попытка {attempt + 1}/{RETRY_ATTEMPTS} анализа изображения...")
            generation_config=genai.GenerationConfig(temperature=temperature, max_output_tokens=MAX_OUTPUT_TOKENS)
            # Используем актуальный model_id (мог измениться на fallback)
            model = genai.GenerativeModel(model_id, safety_settings=SAFETY_SETTINGS_BLOCK_NONE, generation_config=generation_config, system_instruction=system_instruction_text)

            response = await asyncio.to_thread(
                 model.generate_content,
                 [{"role": "user", "parts": parts}] # Передаем контент для vision
            )

            if hasattr(response, 'text'):
                 reply = response.text
            else:
                 reply = None
                 logger.warning(f"ChatID: {chat_id} | Ответ vision модели не содержит 'text' (попытка {attempt + 1}).")


            # Обработка пустого ответа
            if not reply:
                 try:
                    feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else None
                    candidates_info = response.candidates if hasattr(response, 'candidates') else []
                    block_reason_enum = feedback.block_reason if feedback and hasattr(feedback, 'block_reason') else None
                    block_reason = block_reason_enum.name if block_reason_enum and hasattr(block_reason_enum, 'name') else str(block_reason_enum or 'N/A')
                    finish_reason_enum = candidates_info[0].finish_reason if candidates_info and hasattr(candidates_info[0], 'finish_reason') else None
                    finish_reason_val = finish_reason_enum.name if finish_reason_enum and hasattr(finish_reason_enum, 'name') else str(finish_reason_enum or 'N/A')

                    logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения (попытка {attempt + 1}). Block: {block_reason}, Finish: {finish_reason_val}")

                    if block_reason != 'UNSPECIFIED' and block_reason != 'N/A':
                        reply = f"🤖 Модель не смогла описать изображение. (Причина блокировки: {block_reason})"
                    elif finish_reason_val != 'STOP' and finish_reason_val != 'N/A':
                         reply = f"🤖 Модель не смогла описать изображение. (Причина: {finish_reason_val})"
                    else:
                         reply = "🤖 Не удалось понять, что на изображении (пустой ответ)."
                         # Считаем успехом, чтобы не повторять пустой ответ
                         break

                 except Exception as e_inner:
                      logger.warning(f"ChatID: {chat_id} | Ошибка извлечения инфо из пустого ответа (фото): {e_inner}", exc_info=True)
                      reply = "🤖 Не удалось понять, что на изображении (ошибка обработки ответа)."


            # Если ответ есть (и не сообщение о пустом ответе), выходим из ретраев
            if reply and "Не удалось понять" not in reply and "не смогла описать" not in reply:
                 logger.info(f"ChatID: {chat_id} | Успешный анализ изображения на попытке {attempt + 1}.")
                 break

        except BlockedPromptException as e:
             logger.warning(f"ChatID: {chat_id} | Анализ изображения заблокирован на попытке {attempt + 1}: {e}")
             reply = f"❌ Анализ изображения заблокирован моделью."
             last_exception = e
             break
        except StopCandidateException as e:
             logger.warning(f"ChatID: {chat_id} | Анализ изображения остановлен на попытке {attempt + 1}: {e}")
             reply = f"❌ Анализ изображения остановлен моделью."
             last_exception = e
             break
        except Exception as e:
            last_exception = e
            error_message = str(e)
            logger.warning(f"ChatID: {chat_id} | Ошибка анализа изображения на попытке {attempt + 1}: {error_message[:200]}...")

            is_retryable = "500" in error_message or "503" in error_message # Добавил 503 Service Unavailable
            is_input_error = "400" in error_message and ("image" in error_message.lower() or "input" in error_message.lower() or "payload size" in error_message.lower())
            is_key_error = "400" in error_message and "API key not valid" in error_message
            is_location_error = "User location is not supported" in error_message

            if is_input_error:
                 reply = f"❌ Ошибка: Проблема с изображением или модель не поддерживает такой ввод ({error_message[:100]}...)."
                 break
            elif is_key_error:
                 reply = "❌ Ошибка: Неверный Google API ключ."
                 break
            elif is_location_error:
                  reply = f"❌ Ошибка: Ваш регион не поддерживается для модели {model_id}."
                  break
            elif "429" in error_message:
                 reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (429). Попробуйте позже."
                 break

            elif is_retryable and attempt < RETRY_ATTEMPTS - 1:
                wait_time = RETRY_DELAY_SECONDS * (2 ** attempt)
                logger.info(f"ChatID: {chat_id} | Ожидание {wait_time:.1f} сек перед повторным анализом (попытка {attempt + 2})...")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"ChatID: {chat_id} | Не удалось выполнить анализ изображения после {attempt + 1} попыток. Ошибка: {e}", exc_info=True if not is_retryable else False)
                reply = f"❌ Ошибка при анализе изображения после {attempt + 1} попыток. ({error_message[:100]}...)"
                break
    # --- Конец блока ретраев ---

    if reply:
        MAX_MESSAGE_LENGTH = 4096
        reply_chunks = [reply[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(reply), MAX_MESSAGE_LENGTH)]
        message_to_reply = update.message
        try:
             for i, chunk in enumerate(reply_chunks):
                 if i == 0:
                      message_to_reply = await message_to_reply.reply_text(chunk)
                 else:
                      message_to_reply = await context.bot.send_message(chat_id=chat_id, text=chunk)
                 await asyncio.sleep(0.1)
        except Exception as e_reply:
            logger.error(f"ChatID: {chat_id} | Ошибка при отправке ответа на фото: {e_reply}", exc_info=True)
            try: await context.bot.send_message(chat_id=chat_id, text=reply_chunks[-1])
            except Exception as e_send: logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение (фото) в чат: {e_send}", exc_info=True)
    else:
         logger.error(f"ChatID: {chat_id} | Нет ответа (фото) для отправки пользователю после всех попыток.")
         try: await update.message.reply_text("🤖 К сожалению, не удалось проанализировать изображение.")
         except Exception as e_final_fail: logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение о финальной ошибке (фото): {e_final_fail}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not update.message or not update.message.document:
        logger.warning(f"ChatID: {chat_id} | В handle_document нет документа.")
        return

    doc = update.message.document
    # Проверка MIME типа
    allowed_mime_prefixes = ('text/', 'application/json', 'application/xml', 'application/csv', 'application/x-python', 'application/x-sh', 'application/javascript', 'application/x-yaml', 'application/x-tex', 'application/rtf', 'application/sql')
    allowed_mime_types = ('application/octet-stream',) # Для неопределенных, но потенциально текстовых

    mime_type = doc.mime_type or "application/octet-stream"
    is_allowed_prefix = any(mime_type.startswith(prefix) for prefix in allowed_mime_prefixes)
    is_allowed_type = mime_type in allowed_mime_types

    if not (is_allowed_prefix or is_allowed_type):
        await update.message.reply_text(f"⚠️ Пока могу читать только текстовые файлы (типа .txt, .py, .json, .csv, .xml, .sh, .yaml, .sql, .rtf и т.п.). Ваш тип: `{mime_type}`")
        logger.warning(f"ChatID: {chat_id} | Попытка загрузить неподдерживаемый файл: {doc.file_name} (MIME: {mime_type})")
        return

    # Ограничение размера файла
    MAX_FILE_SIZE_MB = 15
    file_size_bytes = doc.file_size or 0
    if file_size_bytes == 0:
         logger.warning(f"ChatID: {chat_id} | Размер файла '{doc.file_name}' равен 0.")
         # Можно вернуть ошибку или пропустить файл
         # await update.message.reply_text(f"⚠️ Файл '{doc.file_name}' пустой.")
         # return

    if file_size_bytes > MAX_FILE_SIZE_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ Файл '{doc.file_name}' слишком большой (> {MAX_FILE_SIZE_MB} MB).")
        logger.warning(f"ChatID: {chat_id} | Слишком большой файл: {doc.file_name} ({file_size_bytes / (1024*1024):.2f} MB)")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    try:
        doc_file = await doc.get_file()
        file_bytes = await doc_file.download_as_bytearray()
    except Exception as e:
        logger.error(f"ChatID: {chat_id} | Не удалось скачать документ '{doc.file_name}': {e}", exc_info=True)
        await update.message.reply_text("❌ Не удалось загрузить файл.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Определение кодировки и декодирование
    text = None
    detected_encoding = None
    encodings_to_try = ['utf-8', 'cp1251', 'latin-1', 'cp866', 'iso-8859-5']
    chardet_available = False
    try:
        import chardet
        chardet_available = True
    except ImportError:
        logger.info("Библиотека chardet не найдена, пропускаем автоопределение кодировки.")

    if chardet_available:
        try:
            # Ограничим количество байт для chardet, чтобы не тормозить на больших файлах
            chardet_limit = min(len(file_bytes), 50 * 1024) # Анализируем первые 50KB
            detected = chardet.detect(file_bytes[:chardet_limit])
            if detected and detected['encoding'] and detected['confidence'] > 0.6: # Снизил порог уверенности
                 detected_encoding = detected['encoding'].lower() # Приводим к нижнему регистру
                 logger.info(f"ChatID: {chat_id} | Chardet определил: {detected_encoding} (уверенность: {detected['confidence']:.2f}) для '{doc.file_name}'")
                 # Добавляем определенную кодировку в начало списка, если ее там нет
                 if detected_encoding not in encodings_to_try:
                      encodings_to_try.insert(0, detected_encoding)
                 # Если это utf-8 с BOM, используем 'utf-8-sig'
                 if detected_encoding == 'utf-8' and file_bytes.startswith(b'\xef\xbb\xbf'):
                     logger.info(f"ChatID: {chat_id} | Обнаружен UTF-8 BOM, используем 'utf-8-sig'.")
                     encodings_to_try.insert(0, 'utf-8-sig')

        except Exception as e_chardet:
            logger.warning(f"Ошибка при использовании chardet для '{doc.file_name}': {e_chardet}")


    for encoding in list(dict.fromkeys(encodings_to_try)): # Уникальные кодировки
        try:
            text = file_bytes.decode(encoding)
            detected_encoding = encoding # Запоминаем успешную
            logger.info(f"ChatID: {chat_id} | Файл '{doc.file_name}' успешно декодирован как {encoding}.")
            break
        except (UnicodeDecodeError, LookupError):
            logger.debug(f"ChatID: {chat_id} | Файл '{doc.file_name}' не в {encoding}.")
        except Exception as e:
            logger.error(f"ChatID: {chat_id} | Ошибка декодирования '{doc.file_name}' как {encoding}: {e}", exc_info=True)

    if text is None:
        logger.error(f"ChatID: {chat_id} | Не удалось декодировать '{doc.file_name}' ни одной из: {list(dict.fromkeys(encodings_to_try))}")
        await update.message.reply_text(f"❌ Не удалось прочитать текстовое содержимое файла '{doc.file_name}'. Попробуйте сохранить его в кодировке UTF-8.")
        return

    # Обрезка текста
    approx_max_tokens = (MAX_OUTPUT_TOKENS * 2) if MAX_OUTPUT_TOKENS < 4000 else 8000
    MAX_FILE_CHARS = min(MAX_CONTEXT_CHARS // 2, approx_max_tokens * 3)

    truncated = text
    warning_msg = ""
    if len(text) > MAX_FILE_CHARS:
        truncated = text[:MAX_FILE_CHARS]
        # Считаем строки, чтобы обрезать по строке
        last_newline = truncated.rfind('\n')
        if last_newline > MAX_FILE_CHARS * 0.8: # Обрезаем по последнему переносу строки, если он не слишком рано
            truncated = truncated[:last_newline]
        warning_msg = f"\n\n(⚠️ Текст файла был обрезан до ~{len(truncated) // 1000}k символов)"
        logger.warning(f"ChatID: {chat_id} | Текст файла '{doc.file_name}' обрезан до {len(truncated)} символов.")

    user_caption = update.message.caption if update.message.caption else ""
    file_name = doc.file_name or "файл"
    encoding_info = f"(кодировка: {detected_encoding})" if detected_encoding else "(кодировка неизвестна)"

    # Формируем диалоговый промпт
    file_context = f"Содержимое файла '{file_name}' {encoding_info}:\n```\n{truncated}\n```{warning_msg}"
    if user_caption:
        user_prompt = f"Пользователь загрузил файл '{file_name}' с комментарием: \"{user_caption}\". {file_context}\nПроанализируй, пожалуйста."
    else:
        user_prompt = f"Пользователь загрузил файл '{file_name}'. {file_context}\nЧто можешь сказать об этом тексте?"

    # Создаем фейковый апдейт
    fake_message = type('obj', (object,), {'text': user_prompt, 'reply_text': update.message.reply_text, 'chat_id': chat_id})
    fake_update = type('obj', (object,), {'effective_chat': update.effective_chat, 'message': fake_message})
    # Передаем в основной обработчик
    await handle_message(fake_update, context)

# ======================================

# --- Функции веб-сервера и запуска ---
async def setup_bot_and_server(stop_event: asyncio.Event):
    """Настраивает приложение бота, вебхук и возвращает приложение и корутину веб-сервера."""
    # application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    timeout = aiohttp.ClientTimeout(total=60.0, connect=10.0, sock_connect=10.0, sock_read=30.0)
    aiohttp_session = aiohttp.ClientSession(timeout=timeout)
    application.bot_data['aiohttp_session'] = aiohttp_session
    logger.info("Сессия aiohttp создана и сохранена в bot_data.")

    # Регистрация обработчиков
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
        webhook_path_segment = GEMINI_WEBHOOK_PATH.strip('/') # Убираем слэши по краям
        webhook_url = urljoin(WEBHOOK_HOST, webhook_path_segment) # Собираем URL
        logger.info(f"Установка вебхука: {webhook_url}")
        await application.bot.set_webhook(
            url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            secret_token=os.getenv('WEBHOOK_SECRET_TOKEN')
        )
        logger.info("Вебхук успешно установлен.")
        return application, run_web_server(application, stop_event)
    except Exception as e:
        logger.critical(f"Ошибка при инициализации бота или установке вебхука: {e}", exc_info=True)
        if 'aiohttp_session' in application.bot_data and not application.bot_data['aiohttp_session'].closed:
             await application.bot_data['aiohttp_session'].close()
        raise


async def run_web_server(application: Application, stop_event: asyncio.Event):
    """Запускает веб-сервер aiohttp для приема вебхуков."""
    app = aiohttp.web.Application()

    async def health_check(request):
        try:
            bot_info = await application.bot.get_me()
            if bot_info:
                 return aiohttp.web.Response(text=f"OK: Bot {bot_info.username} is running.")
            else:
                 return aiohttp.web.Response(text="Error: Bot info not available", status=503)
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return aiohttp.web.Response(text=f"Error: Health check failed ({e})", status=503)

    app.router.add_get('/', health_check)
    app['bot_app'] = application
    webhook_path = GEMINI_WEBHOOK_PATH.strip('/')
    if not webhook_path.startswith('/'): # Добавляем слэш только если его нет
         webhook_path = '/' + webhook_path
    app.router.add_post(webhook_path, handle_telegram_webhook)
    logger.info(f"Вебхук будет слушать на пути: {webhook_path}")

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))
    host = "0.0.0.0"
    site = aiohttp.web.TCPSite(runner, host, port)
    try:
        await site.start()
        logger.info(f"Веб-сервер запущен на http://{host}:{port}")
        await stop_event.wait()
    except asyncio.CancelledError:
         logger.info("Задача веб-сервера отменена.")
    except Exception as e:
        logger.error(f"Ошибка при запуске/работе веб-сервера: {e}", exc_info=True)
    finally:
        logger.info("Остановка веб-сервера...")
        await runner.cleanup()
        logger.info("Веб-сервер остановлен.")


async def handle_telegram_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """Обрабатывает входящие запросы от Telegram."""
    application = request.app.get('bot_app')
    if not application:
        logger.critical("Объект приложения бота не найден в контексте aiohttp!")
        return aiohttp.web.Response(status=500, text="Internal Server Error: Bot application not found")

    secret_token = os.getenv('WEBHOOK_SECRET_TOKEN')
    if secret_token:
         header_token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
         if header_token != secret_token:
             logger.warning("Получен запрос с неверным секретным токеном.")
             return aiohttp.web.Response(status=403, text="Forbidden: Invalid secret token")

    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
        return aiohttp.web.Response(text="OK", status=200)
    except json.JSONDecodeError as e:
         body = await request.text()
         logger.error(f"Ошибка декодирования JSON от Telegram: {e}. Тело: {body[:500]}...")
         return aiohttp.web.Response(text="Bad Request", status=400)
    except Exception as e:
        logger.error(f"Критическая ошибка обработки вебхук-запроса: {e}", exc_info=True)
        return aiohttp.web.Response(text="Internal Server Error", status=500)


async def main():
    """Основная функция запуска приложения."""
    # Настройка уровней логирования
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=log_level)

    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('google.api_core').setLevel(logging.WARNING)
    logging.getLogger('google.generativeai').setLevel(log_level) # Используем общий уровень
    logging.getLogger('duckduckgo_search').setLevel(log_level)
    logging.getLogger('PIL').setLevel(logging.INFO) # Оставим INFO для PIL
    logging.getLogger('aiohttp.access').setLevel(logging.WARNING)
    logging.getLogger('telegram.ext').setLevel(log_level)
    logging.getLogger('telegram.bot').setLevel(log_level)
    # Логгер этого модуля
    logger.setLevel(log_level)


    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    # Обработчик сигналов
    def signal_handler():
        if not stop_event.is_set():
             logger.info("Получен сигнал SIGINT/SIGTERM, инициирую остановку...")
             stop_event.set()
        else:
             logger.warning("Повторный сигнал остановки получен.")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError: # Для Windows
             logger.warning(f"Не удалось установить обработчик для {sig}. Используйте Ctrl+C.")
             try:
                  signal.signal(signal.SIGINT, lambda s, f: signal_handler())
             except Exception as e_signal:
                  logger.error(f"Не удалось установить обработчик SIGINT через signal: {e_signal}")


    application = None
    web_server_task = None
    aiohttp_session_main = None

    try:
        logger.info(f"--- Запуск приложения Gemini Telegram Bot (Log Level: {log_level_str}) ---")
        application, web_server_coro = await setup_bot_and_server(stop_event)
        web_server_task = asyncio.create_task(web_server_coro)
        aiohttp_session_main = application.bot_data.get('aiohttp_session')

        logger.info("Приложение настроено и запущено. Ожидание сигнала остановки...")
        await stop_event.wait()

    except asyncio.CancelledError:
        logger.info("Главная задача была отменена.")
    except Exception as e:
        logger.critical("Критическая ошибка в главном потоке до цикла ожидания.", exc_info=True)
    finally:
        logger.info("--- Начало процесса остановки приложения ---")

        if not stop_event.is_set(): stop_event.set() # Гарантируем установку события

        if web_server_task and not web_server_task.done():
             logger.info("Ожидание завершения веб-сервера (до 15 сек)...")
             try:
                 await asyncio.wait_for(web_server_task, timeout=15.0)
                 logger.info("Веб-сервер успешно завершен.")
             except asyncio.TimeoutError:
                 logger.warning("Веб-сервер не завершился за 15 сек, принудительная отмена...")
                 web_server_task.cancel()
                 try: await web_server_task
                 except asyncio.CancelledError: logger.info("Задача веб-сервера отменена.")
                 except Exception as e: logger.error(f"Ошибка при отмене веб-сервера: {e}", exc_info=True)
             except asyncio.CancelledError: logger.info("Ожидание веб-сервера было отменено.")
             except Exception as e: logger.error(f"Ошибка при ожидании веб-сервера: {e}", exc_info=True)

        if application:
            logger.info("Остановка приложения Telegram бота (shutdown)...")
            try:
                 await application.shutdown()
                 logger.info("Приложение Telegram бота остановлено.")
            except Exception as e: logger.error(f"Ошибка при application.shutdown(): {e}", exc_info=True)

        if aiohttp_session_main and not aiohttp_session_main.closed:
             logger.info("Закрытие сессии aiohttp...")
             await aiohttp_session_main.close()
             await asyncio.sleep(0.5)
             logger.info("Сессия aiohttp закрыта.")

        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if tasks:
            logger.info(f"Отмена {len(tasks)} оставшихся задач...")
            [task.cancel() for task in tasks]
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("Оставшиеся задачи отменены.")
            except asyncio.CancelledError: logger.info("Отмена оставшихся задач была прервана.")
            except Exception as e: logger.error(f"Ошибка при отмене оставшихся задач: {e}", exc_info=True)

        logger.info("--- Приложение полностью остановлено ---")

if __name__ == '__main__':
    try:
        # Установка политики цикла событий для Windows, если нужно
        # if os.name == 'nt':
        #    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение прервано пользователем (Ctrl+C).")
    except Exception as e:
        logger.critical("Неперехваченная ошибка в asyncio.run(main).", exc_info=True)

# --- END OF FILE main.py ---