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
# === НОВЫЕ ИЗМЕНЕНИЯ ===
# - Отправка сообщений с Markdown (с fallback на обычный текст)
# - Улучшенная обработка импорта типов Gemini
# - Обновлены модели, константы, системная инструкция, /start
# - Улучшена команда /temp
# - Улучшено логирование Google Search и обработка ошибок/пустых ответов Gemini
# - Улучшена обработка фото (OCR timeout) и документов (0 байт, chardet, BOM, пустой текст)
# - Скорректированы уровни логирования
# - Аккуратное формирование URL вебхука
# - ИСПРАВЛЕНО: Ошибка TypeError в handle_telegram_webhook (убран create_task/shield)

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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message # Добавил Message
from telegram.constants import ChatAction, ParseMode # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Импорт ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.error import BadRequest # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Импорт ошибки для fallback Markdown
import google.generativeai as genai
# ===== ИСПРАВЛЕНИЕ: Возвращаем импорт DDGS =====
from duckduckgo_search import DDGS # Обычный класс

# ===== ИЗМЕНЕНИЕ: Улучшенная обработка импорта типов Gemini и определения SAFETY_SETTINGS =====
# Строковые представления категорий и порога для запасного варианта
HARM_CATEGORIES_STRINGS = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
]
BLOCK_NONE_STRING = "BLOCK_NONE" # API должен понимать это как строку

# Инициализируем пустым списком
SAFETY_SETTINGS_BLOCK_NONE = []
BlockedPromptException = type('BlockedPromptException', (Exception,), {}) # Заглушка по умолчанию
StopCandidateException = type('StopCandidateException', (Exception,), {}) # Заглушка по умолчанию
# Заглушки для типов, если импорт не удастся или атрибуты пропадут
HarmCategory = type('HarmCategory', (object,), {})
HarmBlockThreshold = type('HarmBlockThreshold', (object,), {})
SafetyRating = type('SafetyRating', (object,), {'category': None, 'probability': None})
BlockReason = type('BlockReason', (object,), {'UNSPECIFIED': 'UNSPECIFIED', 'name': 'UNSPECIFIED'}) # Добавил .name
FinishReason = type('FinishReason', (object,), {'STOP': 'STOP', 'name': 'STOP'}) # Добавил .name

try:
    # Пытаемся импортировать нужные типы
    from google.generativeai.types import (
        HarmCategory as RealHarmCategory, HarmBlockThreshold as RealHarmBlockThreshold,
        BlockedPromptException as RealBlockedPromptException,
        StopCandidateException as RealStopCandidateException,
        SafetyRating as RealSafetyRating, BlockReason as RealBlockReason,
        FinishReason as RealFinishReason
    )
    logger.info("Типы google.generativeai.types успешно импортированы.")

    # Переопределяем заглушки реальными типами
    HarmCategory = RealHarmCategory
    HarmBlockThreshold = RealHarmBlockThreshold
    BlockedPromptException = RealBlockedPromptException
    StopCandidateException = RealStopCandidateException
    SafetyRating = RealSafetyRating
    BlockReason = RealBlockReason
    FinishReason = RealFinishReason

    # Пытаемся создать настройки безопасности используя импортированные Enum типы
    temp_safety_settings = []
    all_enums_found = True
    if hasattr(HarmBlockThreshold, 'BLOCK_NONE'):
        block_none_enum = HarmBlockThreshold.BLOCK_NONE
        for cat_str in HARM_CATEGORIES_STRINGS:
            if hasattr(HarmCategory, cat_str):
                temp_safety_settings.append(
                    {"category": getattr(HarmCategory, cat_str), "threshold": block_none_enum}
                )
            else:
                logger.warning(f"Атрибут категории '{cat_str}' не найден в импортированном HarmCategory.")
                all_enums_found = False
                break # Нет смысла продолжать, если хотя бы одна категория отсутствует
    else:
        logger.warning("Атрибут 'BLOCK_NONE' не найден в импортированном HarmBlockThreshold.")
        all_enums_found = False

    if all_enums_found and temp_safety_settings:
        SAFETY_SETTINGS_BLOCK_NONE = temp_safety_settings
        logger.info("Настройки безопасности BLOCK_NONE успешно установлены с использованием Enum.")
    elif HARM_CATEGORIES_STRINGS: # Если Enum не найдены, но строки категорий есть
        logger.warning("Не удалось создать SAFETY_SETTINGS_BLOCK_NONE с Enum типами. Используем строковые значения.")
        SAFETY_SETTINGS_BLOCK_NONE = [
            {"category": cat_str, "threshold": BLOCK_NONE_STRING}
            for cat_str in HARM_CATEGORIES_STRINGS
        ]
    else:
        logger.warning("Список HARM_CATEGORIES_STRINGS пуст, настройки безопасности не установлены.")
        SAFETY_SETTINGS_BLOCK_NONE = []

except ImportError:
    # Если сам импорт не удался
    logger.warning("Не удалось импортировать типы из google.generativeai.types. Используются строковые значения для настроек безопасности и заглушки для типов.")
    # Устанавливаем настройки безопасности, используя строки, если они есть
    if HARM_CATEGORIES_STRINGS:
        SAFETY_SETTINGS_BLOCK_NONE = [
            {"category": cat_str, "threshold": BLOCK_NONE_STRING}
            for cat_str in HARM_CATEGORIES_STRINGS
        ]
        logger.warning("Настройки безопасности установлены с использованием строковых представлений (BLOCK_NONE).")
    else:
        logger.warning("Список HARM_CATEGORIES_STRINGS пуст, настройки безопасности не установлены.")
        SAFETY_SETTINGS_BLOCK_NONE = []
except Exception as e_import_types:
    logger.error(f"Неожиданная ошибка при импорте/настройке типов Gemini: {e_import_types}", exc_info=True)
    # Пытаемся установить хотя бы строковые значения как fallback
    if HARM_CATEGORIES_STRINGS:
         SAFETY_SETTINGS_BLOCK_NONE = [
             {"category": cat_str, "threshold": BLOCK_NONE_STRING}
             for cat_str in HARM_CATEGORIES_STRINGS
         ]
         logger.warning("Настройки безопасности установлены с использованием строковых представлений (BLOCK_NONE) из-за ошибки.")
    else:
         logger.warning("Список HARM_CATEGORIES_STRINGS пуст, настройки безопасности не установлены из-за ошибки.")
         SAFETY_SETTINGS_BLOCK_NONE = []
# ========================================================================================


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
# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Обновленные модели <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
AVAILABLE_MODELS = {
    'gemini-2.5-flash-preview-04-17': '2.5 Flash Preview',
    'gemini-2.5-pro-exp-03-25': '2.5 Pro exp.',
    'gemini-2.0-flash-thinking-exp-01-21': '2.0 Flash Thinking exp.',
}
# Выбираем модель по умолчанию
DEFAULT_MODEL = 'gemini-2.5-flash-preview-04-17' if 'gemini-2.5-flash-preview-04-17' in AVAILABLE_MODELS else 'gemini-2.5-pro-exp-03-25'
# =========================================================================================

# Переменные состояния пользователя (используем context.user_data)

# Константы
# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Обновленные константы <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
MAX_CONTEXT_CHARS = 100000 # Макс. символов в истории для отправки (примерно)
MAX_OUTPUT_TOKENS = 5000 # Макс. токенов на выходе (можно настроить)
DDG_MAX_RESULTS = 10 # Уменьшил DDG, т.к. это fallback
GOOGLE_SEARCH_MAX_RESULTS = 10 # Уменьшил Google Search для снижения нагрузки и стоимости
RETRY_ATTEMPTS = 5 # Количество попыток запроса к Gemini
RETRY_DELAY_SECONDS = 1 # Начальная задержка перед повтором
# ==========================================================================================

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
# ===================================================

# Настройки безопасности - определены выше

# --- Вспомогательные функции для работы с user_data ---
def get_user_setting(context: ContextTypes.DEFAULT_TYPE, key: str, default_value):
    """Получает настройку пользователя из user_data."""
    return context.user_data.get(key, default_value)

def set_user_setting(context: ContextTypes.DEFAULT_TYPE, key: str, value):
    """Устанавливает настройку пользователя в user_data."""
    context.user_data[key] = value
# -------------------------------------------------------

# ===== Вспомогательная функция для отправки ответа с fallback =====
async def send_reply(target_message: Message, text: str, context: ContextTypes.DEFAULT_TYPE) -> Message | None:
    """Отправляет сообщение с Markdown, если не удается - отправляет как обычный текст."""
    MAX_MESSAGE_LENGTH = 4096
    reply_chunks = [text[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(text), MAX_MESSAGE_LENGTH)]
    sent_message = None
    chat_id = target_message.chat_id

    try:
        for i, chunk in enumerate(reply_chunks):
            if i == 0:
                # Используем reply_text для первого чанка, сохраняем результат
                sent_message = await target_message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            else:
                # Последующие чанки отправляем как новые сообщения, обновляя sent_message
                sent_message = await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(0.1) # Небольшая пауза между чанками
        return sent_message # Возвращаем последнее отправленное сообщение
    except BadRequest as e_md:
        if "Can't parse entities" in str(e_md) or "can't parse" in str(e_md).lower():
            logger.warning(f"ChatID: {chat_id} | Ошибка парсинга Markdown: {e_md}. Попытка отправить как обычный текст.")
            try:
                sent_message = None # Сбрасываем, т.к. первая попытка не удалась
                for i, chunk in enumerate(reply_chunks):
                     if i == 0:
                         sent_message = await target_message.reply_text(chunk) # Без parse_mode
                     else:
                         sent_message = await context.bot.send_message(chat_id=chat_id, text=chunk)
                     await asyncio.sleep(0.1)
                return sent_message
            except Exception as e_plain:
                logger.error(f"ChatID: {chat_id} | Не удалось отправить даже как обычный текст: {e_plain}", exc_info=True)
                # Пытаемся отправить в чат сообщение об ошибке отправки
                try:
                    await context.bot.send_message(chat_id=chat_id, text="❌ Не удалось отправить ответ.")
                except Exception as e_final_send:
                     logger.critical(f"ChatID: {chat_id} | Не удалось отправить даже сообщение об ошибке: {e_final_send}")
        else:
            # Другая ошибка BadRequest или иная ошибка при отправке с Markdown
            logger.error(f"ChatID: {chat_id} | Ошибка при отправке ответа (Markdown): {e_md}", exc_info=True)
            # Пытаемся отправить в чат сообщение об ошибке
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка при отправке ответа: {str(e_md)[:100]}...")
            except Exception as e_error_send:
                logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение об ошибке отправки: {e_error_send}")
    except Exception as e_other:
        # Любая другая непредвиденная ошибка
        logger.error(f"ChatID: {chat_id} | Непредвиденная ошибка при отправке ответа: {e_other}", exc_info=True)
        try:
            await context.bot.send_message(chat_id=chat_id, text="❌ Произошла непредвиденная ошибка при отправке ответа.")
        except Exception as e_unexp_send:
            logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение о непредвиденной ошибке: {e_unexp_send}")

    return None # Возвращаем None, если отправка не удалась
# ==============================================================


# ===== Команды с использованием user_data =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Устанавливаем значения по умолчанию при старте
    set_user_setting(context, 'selected_model', DEFAULT_MODEL)
    set_user_setting(context, 'search_enabled', True)
    set_user_setting(context, 'temperature', 1.0)
    context.chat_data['history'] = [] # Очищаем историю при старте

    default_model_name = AVAILABLE_MODELS.get(DEFAULT_MODEL, DEFAULT_MODEL)
    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Обновленное сообщение /start <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    start_message = (
        f"Google GEMINI **{default_model_name}**"
        f"\n- в моделях используются улучшенные настройки точности, логики и юмора от автора бота,"
        f"\n- работает поиск Google/DDG, понимаю изображения, читаю картинки и документы."
        f"\n `/model` — сменить модель,"
        f"\n `/search_on` / `/search_off` — вкл/выкл поиск,"
        f"\n `/clear` — очистить историю диалога."
        f"\n `/temp` — посмотреть/изменить 'креативность' (0.0-2.0)" # Добавил подсказку про /temp
    )
    # Используем ParseMode.MARKDOWN для форматирования
    await update.message.reply_text(start_message, parse_mode=ParseMode.MARKDOWN)
    # ==================================================================================================

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['history'] = []
    await update.message.reply_text("🧹 История диалога очищена.")

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Улучшенная команда /temp <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        current_temp = get_user_setting(context, 'temperature', 1.0)
        if not context.args:
            await update.message.reply_text(f"🌡️ Текущая температура (креативность): {current_temp:.1f}\nЧтобы изменить, напиши `/temp <значение>` (например, `/temp 0.8`)")
            return

        temp_str = context.args[0].replace(',', '.') # Заменяем запятую на точку
        temp = float(temp_str)
        if not (0.0 <= temp <= 2.0):
            raise ValueError("Температура должна быть от 0.0 до 2.0")
        set_user_setting(context, 'temperature', temp)
        await update.message.reply_text(f"🌡️ Температура установлена на {temp:.1f}")
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"⚠️ Неверный формат. {e}. Укажите число от 0.0 до 2.0. Пример: `/temp 0.8`")
    except Exception as e:
        logger.error(f"Ошибка в set_temperature: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка при установке температуры.")
# ==============================================================================================


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

    if callback_data and callback_data.startswith("set_model_"):
        selected = callback_data.replace("set_model_", "")
        if selected in AVAILABLE_MODELS:
            set_user_setting(context, 'selected_model', selected)
            model_name = AVAILABLE_MODELS[selected]
            reply_text = f"Модель установлена: **{model_name}**"
            try:
                # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Используем Markdown <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
                await query.edit_message_text(reply_text, parse_mode=ParseMode.MARKDOWN)
            except BadRequest as e_md:
                 if "Message is not modified" in str(e_md):
                     logger.info(f"Пользователь выбрал ту же модель: {model_name}")
                     # Можно не отправлять сообщение, если оно не изменилось
                 else:
                     logger.warning(f"Не удалось изменить сообщение с кнопками (Markdown): {e_md}. Отправляю новое.")
                     try: # Попытка отправить как обычный текст
                         await query.edit_message_text(reply_text.replace('**', '')) # Убираем Markdown
                     except Exception as e_edit_plain:
                          logger.error(f"Не удалось изменить сообщение даже как простой текст: {e_edit_plain}. Отправляю новое.")
                          await context.bot.send_message(chat_id=query.message.chat_id, text=reply_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.warning(f"Не удалось изменить сообщение с кнопками (другая ошибка): {e}. Отправляю новое.")
                await context.bot.send_message(chat_id=query.message.chat_id, text=reply_text, parse_mode=ParseMode.MARKDOWN)
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
            status = response.status

            # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Логирование ошибок JSON и кодов ответа <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
            if status == 200:
                try:
                    data = json.loads(response_text) # Парсим JSON
                except json.JSONDecodeError as e_json:
                    logger.error(f"Google Search: Ошибка декодирования JSON для '{query_short}' (статус {status}) - {e_json}. Ответ: {response_text[:200]}...")
                    return None # Считаем ошибкой

                items = data.get('items', [])
                snippets = [item.get('snippet', item.get('title', '')) for item in items if item.get('snippet') or item.get('title')]
                if snippets:
                    logger.info(f"Google Search: Найдено {len(snippets)} результатов для '{query_short}'.")
                    return snippets
                else:
                    logger.info(f"Google Search: Результаты для '{query_short}' не содержат сниппетов/заголовков (статус {status}).")
                    # Не считаем это ошибкой, просто нет результатов - пусть DDG попробует
                    return None # Возвращаем None, чтобы запустить DDG
            # Обработка конкретных кодов ошибок
            elif status == 400:
                 logger.error(f"Google Search: Ошибка 400 (Bad Request) для '{query_short}'. Проверьте параметры запроса. Ответ: {response_text[:200]}...")
            elif status == 403:
                 logger.error(f"Google Search: Ошибка 403 (Forbidden) для '{query_short}'. Проверьте API ключ, его ограничения и включен ли Custom Search API. Ответ: {response_text[:200]}...")
            elif status == 429:
                logger.warning(f"Google Search: Ошибка 429 (Too Many Requests) для '{query_short}'. Квота исчерпана! Ответ: {response_text[:200]}...")
            elif status >= 500:
                 logger.warning(f"Google Search: Серверная ошибка {status} для '{query_short}'. Ответ: {response_text[:200]}...")
            else:
                logger.error(f"Google Search: Неожиданный статус {status} для '{query_short}'. Ответ: {response_text[:200]}...")
            return None # Во всех случаях ошибки возвращаем None
            # ==========================================================================================================

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

    # ===== Формируем финальный промпт для модели =====
    if search_context_snippets:
        # Убираем пустые и пробельные строки из сниппетов
        search_context_lines = [f"- {s.strip()}" for s in search_context_snippets if s.strip()]
        if search_context_lines: # Убедимся, что контекст не пустой после очистки
            search_context = "\n".join(search_context_lines)
            # Ставим вопрос пользователя ПЕРЕД поисковым контекстом
            final_user_prompt = (
                f"Вопрос пользователя: \"{original_user_message}\"\n\n"
                f"(Возможно релевантная доп. информация из поиска, используй с осторожностью, если подходит к вопросу, иначе игнорируй):\n{search_context}"
            )
            logger.info(f"ChatID: {chat_id} | Добавлен контекст из {search_provider} ({len(search_context_lines)} непустых сниппетов).")
        else:
             final_user_prompt = original_user_message
             logger.info(f"ChatID: {chat_id} | Сниппеты из {search_provider} оказались пустыми после очистки, контекст не добавлен.")
             search_log_msg += " (пустые сниппеты)"
    else:
        final_user_prompt = original_user_message
    # ==========================================================

    # Обновляем лог поиска
    logger.info(f"ChatID: {chat_id} | {search_log_msg}")
    logger.debug(f"ChatID: {chat_id} | Финальный промпт для Gemini (длина {len(final_user_prompt)}):\n{final_user_prompt[:500]}...") # Логируем начало

    # --- История и ее обрезка ---
    chat_history = context.chat_data.setdefault("history", [])
    # Добавляем только оригинальное сообщение пользователя в основную историю
    chat_history.append({"role": "user", "parts": [{"text": original_user_message}]})

    # Обрезка истории
    current_total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and isinstance(p["parts"], list) and len(p["parts"]) > 0 and p["parts"][0].get("text"))
    removed_count = 0
    while current_total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1:
        removed_entry = chat_history.pop(0) # user
        if removed_entry.get("parts") and isinstance(removed_entry["parts"], list) and len(removed_entry["parts"]) > 0 and removed_entry["parts"][0].get("text"):
             current_total_chars -= len(removed_entry["parts"][0]["text"])
        removed_count += 1
        if chat_history: # Если есть еще что удалять (model)
            removed_entry = chat_history.pop(0) # model
            if removed_entry.get("parts") and isinstance(removed_entry["parts"], list) and len(removed_entry["parts"]) > 0 and removed_entry["parts"][0].get("text"):
                 current_total_chars -= len(removed_entry["parts"][0]["text"])
            removed_count += 1
        # Обновляем current_total_chars на всякий случай
        # current_total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and isinstance(p["parts"], list) and len(p["parts"]) > 0 and p["parts"][0].get("text"))


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
            if hasattr(response, 'text'):
                reply = response.text
            else:
                reply = None
                logger.warning(f"ChatID: {chat_id} | Ответ модели не содержит атрибута 'text' (попытка {attempt + 1}).")

            # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Улучшенный анализ пустого ответа Gemini <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
            # Обработка пустого ответа или отсутствия текста
            if not reply:
                 block_reason_str = 'N/A'
                 finish_reason_str = 'N/A'
                 safety_info_str = 'N/A'
                 try:
                     # Пытаемся извлечь причину блокировки
                     if hasattr(response, 'prompt_feedback') and response.prompt_feedback and hasattr(response.prompt_feedback, 'block_reason'):
                         block_reason_enum = response.prompt_feedback.block_reason
                         block_reason_str = block_reason_enum.name if hasattr(block_reason_enum, 'name') else str(block_reason_enum)

                     # Пытаемся извлечь причину завершения из кандидатов
                     if hasattr(response, 'candidates') and response.candidates:
                         # Проверяем, что response.candidates это список/кортеж и он не пуст
                         if isinstance(response.candidates, (list, tuple)) and len(response.candidates) > 0:
                             first_candidate = response.candidates[0]
                             if hasattr(first_candidate, 'finish_reason'):
                                 finish_reason_enum = first_candidate.finish_reason
                                 finish_reason_str = finish_reason_enum.name if hasattr(finish_reason_enum, 'name') else str(finish_reason_enum)
                         else:
                             logger.warning(f"ChatID: {chat_id} | Атрибут 'candidates' не является списком/кортежем или пуст: {type(response.candidates)}")

                     # Пытаемся извлечь safety ratings
                     if hasattr(response, 'prompt_feedback') and response.prompt_feedback and hasattr(response.prompt_feedback, 'safety_ratings') and response.prompt_feedback.safety_ratings:
                         safety_ratings = response.prompt_feedback.safety_ratings
                         # Проверяем, что safety_ratings это список/кортеж
                         if isinstance(safety_ratings, (list, tuple)):
                              ratings_list = []
                              for s in safety_ratings:
                                   cat_name = s.category.name if hasattr(s, 'category') and hasattr(s.category, 'name') else str(getattr(s, 'category', '?'))
                                   prob_name = s.probability.name if hasattr(s, 'probability') and hasattr(s.probability, 'name') else str(getattr(s, 'probability', '?'))
                                   ratings_list.append(f"{cat_name}: {prob_name}")
                              if ratings_list:
                                   safety_info_str = ", ".join(ratings_list)
                         else:
                              logger.warning(f"ChatID: {chat_id} | Атрибут 'safety_ratings' не является списком/кортежем: {type(safety_ratings)}")


                     logger.warning(f"ChatID: {chat_id} | Пустой ответ или нет текста (попытка {attempt + 1}). Block: {block_reason_str}, Finish: {finish_reason_str}, Safety: [{safety_info_str}]")

                     # Формируем сообщение для пользователя
                     if block_reason_str not in ['UNSPECIFIED', 'N/A', 'BLOCK_REASON_UNSPECIFIED']: # Учитываем и Enum и строки
                         reply = f"🤖 Модель не дала ответ. (Причина блокировки: {block_reason_str})"
                     elif finish_reason_str not in ['STOP', 'N/A', 'FINISH_REASON_STOP']:
                         reply = f"🤖 Модель завершила работу без ответа. (Причина: {finish_reason_str})"
                     else:
                         reply = "🤖 Модель дала пустой ответ."
                         generation_successful = True # Успех, хоть и пустой

                 except AttributeError as e_attr:
                     logger.warning(f"ChatID: {chat_id} | Пустой ответ, не удалось извлечь доп. инфо (AttributeError: {e_attr}). Попытка {attempt + 1}")
                     reply = "🤖 Получен пустой ответ от модели (ошибка атрибута при разборе)."
                 except Exception as e_inner:
                     logger.warning(f"ChatID: {chat_id} | Пустой ответ, ошибка извлечения доп. инфо: {e_inner}. Попытка {attempt + 1}", exc_info=True)
                     reply = "🤖 Получен пустой ответ от модели (внутренняя ошибка при разборе)."
            # ========================================================================================================

            # Если ответ не пустой и не служебный пустой ответ, считаем генерацию успешной
            if reply and reply != "🤖 Модель дала пустой ответ.":
                 generation_successful = True

            # Если генерация успешна, выходим из цикла ретраев
            if generation_successful:
                 logger.info(f"ChatID: {chat_id} | Успешная генерация на попытке {attempt + 1}.")
                 break # Выход из цикла for

        # Обработка исключений от API Gemini
        except BlockedPromptException as e:
            # Используем hasattr для безопасного доступа к response, если он есть
            reason_str = "неизвестна"
            if hasattr(e, 'response') and hasattr(e.response, 'prompt_feedback') and hasattr(e.response.prompt_feedback, 'block_reason'):
                 reason_enum = e.response.prompt_feedback.block_reason
                 reason_str = reason_enum.name if hasattr(reason_enum, 'name') else str(reason_enum)
            logger.warning(f"ChatID: {chat_id} | Запрос заблокирован моделью (BlockedPromptException) на попытке {attempt + 1}. Причина: {reason_str}. Ошибка: {e}")
            reply = f"❌ Запрос заблокирован моделью. (Причина: {reason_str})"
            last_exception = e
            break
        except StopCandidateException as e:
             # Используем hasattr для безопасного доступа к response
             reason_str = "неизвестна"
             if hasattr(e, 'response') and hasattr(e.response, 'candidates') and e.response.candidates:
                 if isinstance(e.response.candidates, (list, tuple)) and len(e.response.candidates) > 0:
                     first_candidate = e.response.candidates[0]
                     if hasattr(first_candidate, 'finish_reason'):
                          reason_enum = first_candidate.finish_reason
                          reason_str = reason_enum.name if hasattr(reason_enum, 'name') else str(reason_enum)

             logger.warning(f"ChatID: {chat_id} | Генерация остановлена моделью (StopCandidateException) на попытке {attempt + 1}. Причина: {reason_str}. Ошибка: {e}")
             reply = f"❌ Генерация остановлена моделью. (Причина: {reason_str})"
             last_exception = e
             break
        # Обработка других исключений
        except Exception as e:
            last_exception = e
            error_message = str(e)
            logger.warning(f"ChatID: {chat_id} | Ошибка генерации на попытке {attempt + 1}: {error_message[:200]}...")

            # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Улучшенная обработка ошибок Gemini (коды, сообщения) <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
            is_retryable = False
            if "500" in error_message or "503" in error_message or "internal error" in error_message.lower():
                is_retryable = True
                logger.info(f"ChatID: {chat_id} | Обнаружена ошибка 5xx или внутренняя, попытка повтора...")
            elif "429" in error_message and ("quota" in error_message.lower() or "resource has been exhausted" in error_message.lower()):
                 logger.error(f"ChatID: {chat_id} | Достигнут лимит запросов (429). Прекращаем попытки. Ошибка: {error_message}")
                 reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (429). Попробуйте позже."
                 break # Прекращаем попытки при 429
            elif "400" in error_message and "api key not valid" in error_message.lower():
                 logger.error(f"ChatID: {chat_id} | Неверный Google API ключ (400). Прекращаем попытки. Ошибка: {error_message}")
                 reply = "❌ Ошибка: Неверный Google API ключ (400)."
                 break
            elif "user location is not supported" in error_message.lower():
                 logger.error(f"ChatID: {chat_id} | Регион не поддерживается (400). Прекращаем попытки. Ошибка: {error_message}")
                 reply = f"❌ Ошибка: Ваш регион не поддерживается для модели {model_id} (400)."
                 break
            elif "400" in error_message and ("image input" in error_message.lower() or " richiesto" in error_message.lower() or "invalid value" in error_message.lower()):
                 # Эта ошибка вероятнее при работе с фото/документами, но на всякий случай
                 logger.error(f"ChatID: {chat_id} | Неверный формат запроса (400). Прекращаем попытки. Ошибка: {error_message}")
                 reply = f"❌ Ошибка: Неверный формат запроса к модели (400). Возможно, проблема с входными данными. ({error_message[:100]}...)"
                 break

            # Логика ретраев
            if is_retryable and attempt < RETRY_ATTEMPTS - 1:
                wait_time = RETRY_DELAY_SECONDS * (2 ** attempt)
                logger.info(f"ChatID: {chat_id} | Ожидание {wait_time:.1f} сек перед попыткой {attempt + 2}...")
                await asyncio.sleep(wait_time)
                continue # Переходим к следующей попытке
            else:
                # Если ошибка неretryable или попытки исчерпаны
                log_level = logging.ERROR if not is_retryable else logging.WARNING
                logger.log(log_level, f"ChatID: {chat_id} | Не удалось выполнить генерацию после {attempt + 1} попыток. Последняя ошибка: {e}", exc_info=True if not is_retryable else False)
                # Формируем финальное сообщение об ошибке
                if reply is None: # Если не было специфичной ошибки 4xx/5xx выше
                     reply = f"❌ Ошибка при обращении к модели после {attempt + 1} попыток. ({error_message[:100]}...)"
                break # Выходим из цикла ретраев
            # ========================================================================================================

    # --- Конец блока вызова модели ---

    # Добавляем финальный ответ (или сообщение об ошибке) в ОСНОВНУЮ историю
    if reply:
        # Убедимся, что последнее сообщение - user, и только потом добавляем model
        if chat_history and chat_history[-1].get("role") == "user":
             chat_history.append({"role": "model", "parts": [{"text": reply}]})
        else:
             # Если история пуста или последнее сообщение не user, логируем и все равно добавляем
             logger.warning(f"ChatID: {chat_id} | Ответ модели добавлен, но последнее сообщение в истории было не 'user' или история пуста.")
             chat_history.append({"role": "model", "parts": [{"text": reply}]})

    # Отправка ответа пользователю с помощью новой функции
    if reply:
        if update.message: # Убедимся, что есть оригинальное сообщение для ответа
             await send_reply(update.message, reply, context)
        else:
             # Если это "фейковый" апдейт без message (теоретически не должно быть)
             logger.error(f"ChatID: {chat_id} | Не найдено сообщение для ответа в update.")
             try: # Пытаемся отправить просто в чат
                  await context.bot.send_message(chat_id=chat_id, text=reply)
             except Exception as e_send_direct:
                  logger.error(f"ChatID: {chat_id} | Не удалось отправить ответ напрямую в чат: {e_send_direct}")
    else:
         logger.error(f"ChatID: {chat_id} | Нет ответа для отправки пользователю после всех попыток.")
         try:
              await update.message.reply_text("🤖 К сожалению, не удалось получить ответ от модели после нескольких попыток.")
         except Exception as e_final_fail:
              logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение о финальной ошибке: {e_final_fail}")

# =============================================================

# ===== Обработчики фото и документов (добавлен fallback Markdown и улучшен анализ ответа) =====

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tesseract_available = False
    try:
        pytesseract.pytesseract.get_tesseract_version()
        tesseract_available = True
    except Exception as e:
        logger.debug(f"Tesseract не найден стандартным способом: {e}. OCR отключен.")

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
            # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Таймаут OCR проверен (уже был) <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
            extracted_text = pytesseract.image_to_string(image, lang='rus+eng', timeout=15) # Таймаут 15 секунд
            if extracted_text and extracted_text.strip():
                logger.info(f"ChatID: {chat_id} | Обнаружен текст на изображении (OCR).")
                # Используем Markdown для выделения текста
                ocr_context = f"На изображении обнаружен следующий текст:\n```\n{extracted_text.strip()}\n```"
                if user_caption:
                    user_prompt = f"Пользователь загрузил фото с подписью: \"{user_caption}\". {ocr_context}\nЧто можешь сказать об этом фото и тексте на нём?"
                else:
                    user_prompt = f"Пользователь загрузил фото. {ocr_context}\nЧто можешь сказать об этом фото и тексте на нём?"

                # Создаем фейковый update для передачи в handle_message
                # Убедимся, что reply_text передается корректно
                if hasattr(update.message, 'reply_text') and callable(update.message.reply_text):
                     fake_message = type('obj', (object,), {'text': user_prompt, 'reply_text': update.message.reply_text, 'chat_id': chat_id})
                     fake_update = type('obj', (object,), {'effective_chat': update.effective_chat, 'message': fake_message})
                     await handle_message(fake_update, context) # Передаем в основной обработчик
                     return
                else:
                     logger.error(f"ChatID: {chat_id} | Не удалось передать reply_text для OCR-запроса.")
                     # Можно попробовать отправить сообщение об ошибке или просто не обрабатывать
                     await update.message.reply_text("❌ Ошибка: не удалось обработать запрос с текстом из фото.")
                     return

            else:
                 logger.info(f"ChatID: {chat_id} | OCR не нашел текст на изображении.")
        except pytesseract.TesseractNotFoundError:
             logger.error("Tesseract не найден! Проверьте путь. OCR отключен.")
             tesseract_available = False # Отключаем для этой сессии, если не найден
        except RuntimeError as timeout_error: # Ловим ошибку таймаута tesseract
             logger.warning(f"ChatID: {chat_id} | OCR таймаут: {timeout_error}")
             # Сообщаем пользователю о таймауте OCR и продолжаем как обычное фото
             await update.message.reply_text("⏳ Не удалось распознать текст на фото за отведенное время. Анализирую как обычное изображение...")
        except Exception as e:
            logger.warning(f"ChatID: {chat_id} | Ошибка OCR: {e}", exc_info=True)
            # Сообщаем пользователю и продолжаем как обычное фото
            await update.message.reply_text("⚠️ Произошла ошибка при распознавании текста. Анализирую как обычное изображение...")
    # --- Конец OCR ---

    # --- Обработка как изображение ---
    logger.info(f"ChatID: {chat_id} | Обработка фото как изображения (без/после OCR).")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    MAX_IMAGE_BYTES = 4 * 1024 * 1024
    if len(file_bytes) > MAX_IMAGE_BYTES:
        logger.warning(f"ChatID: {chat_id} | Изображение ({len(file_bytes)} байт) может быть слишком большим для API (> {MAX_IMAGE_BYTES / (1024*1024):.1f} MB).")
        # Пока не отклоняем, но предупреждаем

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
    # Важно: передаем список словарей для multi-modal input
    parts = [{"text": prompt_text}, {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}]
    # Передаем контент как список словарей
    content_for_vision = [{"role": "user", "parts": parts}]


    # Получаем настройки пользователя
    model_id = get_user_setting(context, 'selected_model', DEFAULT_MODEL)
    temperature = get_user_setting(context, 'temperature', 1.0)

    # Проверка на vision модель и возможное переключение
    vision_capable_keywords = ['flash', 'pro', 'vision', 'ultra'] # 'pro' может быть и не vision, но 'pro-vision' - да
    # Точная проверка, есть ли 'vision' или это 'flash'/'pro' версии >= 1.5 (предположительно)
    is_vision_model = any(keyword in model_id for keyword in ['vision', 'flash', 'pro']) # Упрощенная проверка, т.к. Gemini Pro и Flash обычно vision

    if not is_vision_model:
         # Ищем модель, которая точно vision (по названию) или новее
         vision_models = [m_id for m_id in AVAILABLE_MODELS if any(keyword in m_id for keyword in vision_capable_keywords)]
         if vision_models:
             original_model_name = AVAILABLE_MODELS.get(model_id, model_id)
             # Предпочитаем 'flash' или 'pro' если есть, иначе берем первую
             fallback_model_id = next((m for m in vision_models if 'flash' in m or 'pro' in m), vision_models[0])
             model_id = fallback_model_id
             new_model_name = AVAILABLE_MODELS.get(model_id, model_id)
             logger.warning(f"ChatID: {chat_id} | Модель {original_model_name} не vision. Временно использую {new_model_name}.")
             await update.message.reply_text(f"ℹ️ Ваша модель ({original_model_name}) не подходит для фото. Временно использую {new_model_name}.", parse_mode=ParseMode.MARKDOWN)
         else:
             logger.error(f"ChatID: {chat_id} | Нет доступных vision моделей в AVAILABLE_MODELS.")
             await update.message.reply_text("❌ Нет доступных моделей для анализа изображений.")
             return

    logger.info(f"ChatID: {chat_id} | Анализ изображения. Модель: {model_id}, Темп: {temperature}")
    reply = None
    last_exception = None
    response_vision = None # Отдельная переменная для ответа vision

    # --- Вызов Vision модели с РЕТРАЯМИ ---
    for attempt in range(RETRY_ATTEMPTS):
        try:
            logger.info(f"ChatID: {chat_id} | Попытка {attempt + 1}/{RETRY_ATTEMPTS} анализа изображения...")
            generation_config=genai.GenerationConfig(temperature=temperature, max_output_tokens=MAX_OUTPUT_TOKENS)
            model = genai.GenerativeModel(model_id, safety_settings=SAFETY_SETTINGS_BLOCK_NONE, generation_config=generation_config, system_instruction=system_instruction_text)

            # Запускаем generate_content с правильным форматом контента
            response_vision = await asyncio.to_thread(
                 model.generate_content,
                 content_for_vision # Передаем список словарей
            )

            if hasattr(response_vision, 'text'):
                 reply = response_vision.text
            else:
                 reply = None
                 logger.warning(f"ChatID: {chat_id} | Ответ vision модели не содержит 'text' (попытка {attempt + 1}).")

            # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Улучшенный анализ пустого ответа (фото) <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
            if not reply:
                 block_reason_str = 'N/A'
                 finish_reason_str = 'N/A'
                 try:
                     # Используем response_vision для анализа
                     if hasattr(response_vision, 'prompt_feedback') and response_vision.prompt_feedback and hasattr(response_vision.prompt_feedback, 'block_reason'):
                         block_reason_enum = response_vision.prompt_feedback.block_reason
                         block_reason_str = block_reason_enum.name if hasattr(block_reason_enum, 'name') else str(block_reason_enum)

                     if hasattr(response_vision, 'candidates') and response_vision.candidates:
                          if isinstance(response_vision.candidates, (list, tuple)) and len(response_vision.candidates) > 0:
                              first_candidate = response_vision.candidates[0]
                              if hasattr(first_candidate, 'finish_reason'):
                                   finish_reason_enum = first_candidate.finish_reason
                                   finish_reason_str = finish_reason_enum.name if hasattr(finish_reason_enum, 'name') else str(finish_reason_enum)
                          else:
                               logger.warning(f"ChatID: {chat_id} | (Фото) 'candidates' не список/кортеж или пуст: {type(response_vision.candidates)}")


                     logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения (попытка {attempt + 1}). Block: {block_reason_str}, Finish: {finish_reason_str}")

                     if block_reason_str not in ['UNSPECIFIED', 'N/A', 'BLOCK_REASON_UNSPECIFIED']:
                         reply = f"🤖 Модель не смогла описать изображение. (Причина блокировки: {block_reason_str})"
                     elif finish_reason_str not in ['STOP', 'N/A', 'FINISH_REASON_STOP']:
                          reply = f"🤖 Модель не смогла описать изображение. (Причина: {finish_reason_str})"
                     else:
                          reply = "🤖 Не удалось понять, что на изображении (пустой ответ)."
                          break # Выходим из ретраев, если просто пустой ответ без ошибки

                 except AttributeError as e_attr:
                      logger.warning(f"ChatID: {chat_id} | Ошибка извлечения инфо из пустого ответа (фото, AttributeError: {e_attr}). Попытка {attempt + 1}")
                      reply = "🤖 Не удалось понять, что на изображении (ошибка атрибута при разборе ответа)."
                 except Exception as e_inner:
                      logger.warning(f"ChatID: {chat_id} | Ошибка извлечения инфо из пустого ответа (фото): {e_inner}", exc_info=True)
                      reply = "🤖 Не удалось понять, что на изображении (ошибка обработки ответа)."
            # ========================================================================================================

            # Если ответ не пустой и не содержит сообщение об ошибке, выходим
            if reply and "Не удалось понять" not in reply and "не смогла описать" not in reply:
                 logger.info(f"ChatID: {chat_id} | Успешный анализ изображения на попытке {attempt + 1}.")
                 break

        # Обработка исключений API
        except BlockedPromptException as e:
             reason_str = "неизвестна"
             if hasattr(e, 'response') and hasattr(e.response, 'prompt_feedback') and hasattr(e.response.prompt_feedback, 'block_reason'):
                  reason_enum = e.response.prompt_feedback.block_reason
                  reason_str = reason_enum.name if hasattr(reason_enum, 'name') else str(reason_enum)
             logger.warning(f"ChatID: {chat_id} | Анализ изображения заблокирован (BlockedPromptException) на попытке {attempt + 1}. Причина: {reason_str}. Ошибка: {e}")
             reply = f"❌ Анализ изображения заблокирован моделью. (Причина: {reason_str})"
             last_exception = e
             break
        except StopCandidateException as e:
             reason_str = "неизвестна"
             if hasattr(e, 'response') and hasattr(e.response, 'candidates') and e.response.candidates:
                 if isinstance(e.response.candidates, (list, tuple)) and len(e.response.candidates) > 0:
                      first_candidate = e.response.candidates[0]
                      if hasattr(first_candidate, 'finish_reason'):
                           reason_enum = first_candidate.finish_reason
                           reason_str = reason_enum.name if hasattr(reason_enum, 'name') else str(reason_enum)
             logger.warning(f"ChatID: {chat_id} | Анализ изображения остановлен (StopCandidateException) на попытке {attempt + 1}. Причина: {reason_str}. Ошибка: {e}")
             reply = f"❌ Анализ изображения остановлен моделью. (Причина: {reason_str})"
             last_exception = e
             break
        # Обработка других исключений
        except Exception as e:
            last_exception = e
            error_message = str(e)
            logger.warning(f"ChatID: {chat_id} | Ошибка анализа изображения на попытке {attempt + 1}: {error_message[:200]}...")

            # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Улучшенная обработка ошибок Gemini (фото) <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
            is_retryable = "500" in error_message or "503" in error_message or "internal error" in error_message.lower()
            is_input_error = "400" in error_message and ("image" in error_message.lower() or "input" in error_message.lower() or "payload size" in error_message.lower() or "invalid value" in error_message.lower() or "failed to decode" in error_message.lower())
            is_key_error = "400" in error_message and "api key not valid" in error_message.lower()
            is_location_error = "user location is not supported" in error_message.lower()
            is_quota_error = "429" in error_message and ("quota" in error_message.lower() or "resource has been exhausted" in error_message.lower())

            if is_input_error:
                 logger.error(f"ChatID: {chat_id} | Ошибка входных данных фото (400). Прекращаем попытки. Ошибка: {error_message}")
                 reply = f"❌ Ошибка: Проблема с форматом изображения или запроса к модели (400). ({error_message[:100]}...)."
                 break
            elif is_key_error:
                 logger.error(f"ChatID: {chat_id} | Неверный API ключ (400, фото). Прекращаем попытки.")
                 reply = "❌ Ошибка: Неверный Google API ключ (400)."
                 break
            elif is_location_error:
                  logger.error(f"ChatID: {chat_id} | Регион не поддерживается (400, фото). Прекращаем попытки.")
                  reply = f"❌ Ошибка: Ваш регион не поддерживается для модели {model_id} (400)."
                  break
            elif is_quota_error:
                 logger.error(f"ChatID: {chat_id} | Достигнут лимит запросов (429, фото). Прекращаем попытки.")
                 reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (429). Попробуйте позже."
                 break
            # Логика ретраев для 5xx
            elif is_retryable and attempt < RETRY_ATTEMPTS - 1:
                wait_time = RETRY_DELAY_SECONDS * (2 ** attempt)
                logger.info(f"ChatID: {chat_id} | Ожидание {wait_time:.1f} сек перед повторным анализом (попытка {attempt + 2})...")
                await asyncio.sleep(wait_time)
                continue
            else:
                # Если ошибка неretryable или попытки исчерпаны
                log_level = logging.ERROR if not is_retryable else logging.WARNING
                logger.log(log_level, f"ChatID: {chat_id} | Не удалось выполнить анализ изображения после {attempt + 1} попыток. Ошибка: {e}", exc_info=True if not is_retryable else False)
                if reply is None: # Если не было специфичной ошибки выше
                     reply = f"❌ Ошибка при анализе изображения после {attempt + 1} попыток. ({error_message[:100]}...)"
                break
            # ========================================================================================================
    # --- Конец блока ретраев ---

    # Отправка ответа с помощью новой функции
    if reply:
        if update.message:
             await send_reply(update.message, reply, context)
        else:
             logger.error(f"ChatID: {chat_id} | (Фото) Не найдено сообщение для ответа.")
             try: await context.bot.send_message(chat_id=chat_id, text=reply)
             except Exception as e_send_direct: logger.error(f"ChatID: {chat_id} | (Фото) Не удалось отправить ответ напрямую: {e_send_direct}")
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
    # 'application/octet-stream' может быть чем угодно, но разрешим для текстовых файлов без явного типа
    allowed_mime_types = ('application/octet-stream',)

    mime_type = doc.mime_type or "application/octet-stream"
    is_allowed_prefix = any(mime_type.startswith(prefix) for prefix in allowed_mime_prefixes)
    is_allowed_type = mime_type in allowed_mime_types

    if not (is_allowed_prefix or is_allowed_type):
        # Используем Markdown для `mime_type`
        await update.message.reply_text(f"⚠️ Пока могу читать только текстовые файлы (типа .txt, .py, .json, .csv, .xml, .sh, .yaml, .sql, .rtf и т.п.). Ваш тип: `{mime_type}`", parse_mode=ParseMode.MARKDOWN)
        logger.warning(f"ChatID: {chat_id} | Попытка загрузить неподдерживаемый файл: {doc.file_name} (MIME: {mime_type})")
        return

    # Ограничение размера файла
    MAX_FILE_SIZE_MB = 15
    file_size_bytes = doc.file_size or 0

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Улучшенная обработка 0-байтных файлов <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    if file_size_bytes == 0:
         logger.info(f"ChatID: {chat_id} | Загружен пустой файл '{doc.file_name}'.")
         # Уведомляем пользователя
         await update.message.reply_text(f"ℹ️ Файл '{doc.file_name}' пустой.")
         return # Не обрабатываем дальше
    # ==========================================================================================================

    if file_size_bytes > MAX_FILE_SIZE_MB * 1024 * 1024:
        # Используем Markdown для имени файла
        await update.message.reply_text(f"❌ Файл `{doc.file_name}` слишком большой (> {MAX_FILE_SIZE_MB} MB).", parse_mode=ParseMode.MARKDOWN)
        logger.warning(f"ChatID: {chat_id} | Слишком большой файл: {doc.file_name} ({file_size_bytes / (1024*1024):.2f} MB)")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    try:
        doc_file = await doc.get_file()
        file_bytes = await doc_file.download_as_bytearray()
        # Перепроверяем размер после скачивания, если вдруг file_size был неточным
        if not file_bytes:
             logger.warning(f"ChatID: {chat_id} | Файл '{doc.file_name}' скачан, но оказался пустым (0 байт).")
             await update.message.reply_text(f"ℹ️ Файл '{doc.file_name}' пустой.")
             return
    except Exception as e:
        logger.error(f"ChatID: {chat_id} | Не удалось скачать документ '{doc.file_name}': {e}", exc_info=True)
        await update.message.reply_text("❌ Не удалось загрузить файл.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Определение кодировки и декодирование
    text = None
    detected_encoding = None
    # Добавим 'utf-8-sig' в начало на всякий случай
    encodings_to_try = ['utf-8-sig', 'utf-8', 'cp1251', 'latin-1', 'cp866', 'iso-8859-5']
    chardet_available = False
    try:
        import chardet
        chardet_available = True
    except ImportError:
        logger.info("Библиотека chardet не найдена, пропускаем автоопределение кодировки.")

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Улучшенная работа с chardet и BOM <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    if chardet_available:
        try:
            # Анализируем только начало файла для скорости
            chardet_limit = min(len(file_bytes), 50 * 1024) # Не более 50 KB
            # Убедимся, что есть что анализировать
            if chardet_limit > 0:
                 detected = chardet.detect(file_bytes[:chardet_limit])
                 if detected and detected['encoding'] and detected['confidence'] > 0.7: # Увеличим порог уверенности
                      potential_encoding = detected['encoding'].lower()
                      logger.info(f"ChatID: {chat_id} | Chardet определил: {potential_encoding} (уверенность: {detected['confidence']:.2f}) для '{doc.file_name}'")

                      # Явная проверка на UTF-8 BOM
                      if potential_encoding == 'utf-8' and file_bytes.startswith(b'\xef\xbb\xbf'):
                           logger.info(f"ChatID: {chat_id} | Обнаружен UTF-8 BOM, используем 'utf-8-sig'.")
                           detected_encoding = 'utf-8-sig'
                           # Перемещаем 'utf-8-sig' в начало списка, если его там еще нет
                           if 'utf-8-sig' in encodings_to_try: encodings_to_try.remove('utf-8-sig')
                           encodings_to_try.insert(0, 'utf-8-sig')
                      else:
                           detected_encoding = potential_encoding
                           # Добавляем определенную кодировку в начало списка попыток
                           if detected_encoding in encodings_to_try: encodings_to_try.remove(detected_encoding)
                           encodings_to_try.insert(0, detected_encoding)
                 else:
                      logger.info(f"ChatID: {chat_id} | Chardet не смог определить кодировку для '{doc.file_name}' с достаточной уверенностью (уверенность: {detected.get('confidence')}).")
            # else: # Логирование пустого файла уже было выше
            #      logger.warning(f"ChatID: {chat_id} | Файл '{doc.file_name}' пустой, chardet не используется.")
        except Exception as e_chardet:
            logger.warning(f"Ошибка при использовании chardet для '{doc.file_name}': {e_chardet}")
            detected_encoding = None # Сбрасываем, если была ошибка

    # Убираем дубликаты кодировок, сохраняя порядок
    unique_encodings = list(dict.fromkeys(encodings_to_try))
    logger.debug(f"ChatID: {chat_id} | Порядок попыток декодирования для '{doc.file_name}': {unique_encodings}")

    for encoding in unique_encodings:
        try:
            # Пустые файлы уже отсеяны выше
            text = file_bytes.decode(encoding)
            detected_encoding = encoding # Сохраняем успешную кодировку
            logger.info(f"ChatID: {chat_id} | Файл '{doc.file_name}' успешно декодирован как {encoding}.")
            break # Выходим из цикла при успехе
        except UnicodeDecodeError:
            logger.debug(f"ChatID: {chat_id} | Файл '{doc.file_name}' не в {encoding}.")
        except LookupError: # Если кодировка неизвестна системе
            logger.warning(f"ChatID: {chat_id} | Кодировка {encoding} не поддерживается для '{doc.file_name}'.")
        except Exception as e_decode:
            logger.error(f"ChatID: {chat_id} | Неожиданная ошибка декодирования '{doc.file_name}' как {encoding}: {e_decode}", exc_info=True)
            # Не прерываем цикл, пробуем другие кодировки

    # Проверка после цикла
    if text is None: # Если ни одна кодировка не подошла
        logger.error(f"ChatID: {chat_id} | Не удалось декодировать '{doc.file_name}' ни одной из: {unique_encodings}")
        await update.message.reply_text(f"❌ Не удалось прочитать текстовое содержимое файла `{doc.file_name}`. Попробуйте сохранить его в кодировке UTF-8.", parse_mode=ParseMode.MARKDOWN)
        return

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Проверка на пустой текст после декодирования <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    if not text.strip() and len(file_bytes) > 0:
         logger.warning(f"ChatID: {chat_id} | Файл '{doc.file_name}' ({len(file_bytes)} байт, кодировка {detected_encoding}) после декодирования дал пустой текст.")
         # Уведомляем пользователя
         await update.message.reply_text(f"⚠️ Не удалось извлечь содержательный текст из файла `{doc.file_name}` (возможно, он содержит только пробелы или нечитаемые символы).", parse_mode=ParseMode.MARKDOWN)
         return
    # ==============================================================================================================

    # Обрезка текста
    # Увеличим лимит символов для файлов, т.к. MAX_CONTEXT_CHARS тоже увеличен
    approx_max_tokens = MAX_OUTPUT_TOKENS * 2 # Примерно, зависит от языка
    # Оставляем не более половины общего контекста под файл, но не более чем нужно для токенов
    MAX_FILE_CHARS = min(MAX_CONTEXT_CHARS // 2, approx_max_tokens * 4) # 4 символа на токен - с запасом

    truncated = text
    warning_msg = ""
    if len(text) > MAX_FILE_CHARS:
        truncated = text[:MAX_FILE_CHARS]
        # Обрезаем по последнему переносу строки, чтобы не рвать слова/строки кода
        last_newline = truncated.rfind('\n')
        # Обрезаем, только если перенос строки не слишком близко к началу
        if last_newline > MAX_FILE_CHARS * 0.8:
            truncated = truncated[:last_newline]
        # Используем Markdown для предупреждения
        warning_msg = f"\n\n**(⚠️ Текст файла был обрезан до ~{len(truncated) // 1000}k символов)**"
        logger.warning(f"ChatID: {chat_id} | Текст файла '{doc.file_name}' обрезан до {len(truncated)} символов (лимит {MAX_FILE_CHARS}).")


    user_caption = update.message.caption if update.message.caption else ""
    file_name = doc.file_name or "файл"
    encoding_info = f"(предположительно {detected_encoding})" if detected_encoding else "(кодировка неизвестна)"

    # Формируем диалоговый промпт с Markdown
    # Используем ` для имен файлов и кодировок
    file_context = f"Содержимое файла `{file_name}` {encoding_info}:\n```\n{truncated}\n```{warning_msg}"
    if user_caption:
        # Экранируем кавычки в подписи пользователя на всякий случай
        safe_caption = user_caption.replace('"', '\\"')
        user_prompt = f"Пользователь загрузил файл `{file_name}` с комментарием: \"{safe_caption}\". {file_context}\nПроанализируй, пожалуйста."
    else:
        user_prompt = f"Пользователь загрузил файл `{file_name}`. {file_context}\nЧто можешь сказать об этом тексте?"

    # Создаем фейковый апдейт для передачи в handle_message
    if hasattr(update.message, 'reply_text') and callable(update.message.reply_text):
        fake_message = type('obj', (object,), {'text': user_prompt, 'reply_text': update.message.reply_text, 'chat_id': chat_id})
        fake_update = type('obj', (object,), {'effective_chat': update.effective_chat, 'message': fake_message})
        await handle_message(fake_update, context)
    else:
        logger.error(f"ChatID: {chat_id} | Не удалось передать reply_text для запроса с документом.")
        await update.message.reply_text("❌ Ошибка: не удалось обработать запрос с файлом.")


# ======================================

# --- Функции веб-сервера и запуска ---
async def setup_bot_and_server(stop_event: asyncio.Event):
    """Настраивает приложение бота, вебхук и возвращает приложение и корутину веб-сервера."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Настройка HTTP-сессии для бота (используется для поиска)
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
        webhook_path_segment = GEMINI_WEBHOOK_PATH.strip('/')
        # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Аккуратное формирование URL <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
        # Формируем URL без urljoin, чтобы избежать двойных слэшей если WEBHOOK_HOST заканчивается на /
        webhook_url = f"{WEBHOOK_HOST.rstrip('/')}/{webhook_path_segment}"
        # ============================================================================================
        logger.info(f"Установка вебхука: {webhook_url}")
        await application.bot.set_webhook(
            url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True, # Сбрасываем ожидающие апдейты при рестарте
            secret_token=os.getenv('WEBHOOK_SECRET_TOKEN') # Добавлена поддержка секретного токена
        )
        logger.info("Вебхук успешно установлен.")
        return application, run_web_server(application, stop_event)
    except Exception as e:
        logger.critical(f"Ошибка при инициализации бота или установке вебхука: {e}", exc_info=True)
        # Убедимся, что сессия закрыта при ошибке инициализации
        if 'aiohttp_session' in application.bot_data and application.bot_data['aiohttp_session'] and not application.bot_data['aiohttp_session'].closed:
             await application.bot_data['aiohttp_session'].close()
             logger.info("Сессия aiohttp закрыта из-за ошибки инициализации.")
        raise


async def run_web_server(application: Application, stop_event: asyncio.Event):
    """Запускает веб-сервер aiohttp для приема вебхуков."""
    app = aiohttp.web.Application()

    async def health_check(request):
        """Проверяет доступность бота."""
        try:
            # Простая проверка - получаем информацию о боте
            bot_info = await application.bot.get_me()
            if bot_info:
                 # Можно добавить проверку соединения с Gemini, но это может замедлять health check
                 # try:
                 #    model = genai.GenerativeModel(DEFAULT_MODEL)
                 #    await asyncio.to_thread(model.count_tokens, "health check")
                 #    gemini_status = "OK"
                 # except Exception as gemini_e:
                 #    gemini_status = f"Error ({type(gemini_e).__name__})"
                 #    logger.warning(f"Health check: Gemini connection test failed: {gemini_e}")

                 return aiohttp.web.Response(text=f"OK: Bot {bot_info.username} is running.") # Gemini status: {gemini_status}
            else:
                 logger.warning("Health check: Bot info not available from get_me()")
                 return aiohttp.web.Response(text="Error: Bot info not available", status=503)
        except Exception as e:
            logger.error(f"Health check failed: {e}", exc_info=True) # Логируем ошибку
            return aiohttp.web.Response(text=f"Error: Health check failed ({type(e).__name__})", status=503)

    app.router.add_get('/', health_check) # Health check на корневом пути
    app['bot_app'] = application # Сохраняем приложение бота в контексте aiohttp

    # Убедимся, что путь вебхука начинается со слэша
    webhook_path = GEMINI_WEBHOOK_PATH.strip('/')
    if not webhook_path.startswith('/'):
         webhook_path = '/' + webhook_path
    app.router.add_post(webhook_path, handle_telegram_webhook) # Основной путь для вебхуков
    logger.info(f"Вебхук будет слушать на пути: {webhook_path}")

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000")) # Используем переменную окружения PORT или 10000
    host = os.getenv("HOST", "0.0.0.0") # Слушаем на всех интерфейсах по умолчанию
    site = aiohttp.web.TCPSite(runner, host, port)
    try:
        await site.start()
        logger.info(f"Веб-сервер запущен на http://{host}:{port}")
        # Ожидаем события остановки
        await stop_event.wait()
    except asyncio.CancelledError:
         logger.info("Задача веб-сервера отменена.")
    except Exception as e:
        # Логируем ошибки при старте сервера (например, порт занят)
        logger.error(f"Ошибка при запуске/работе веб-сервера на {host}:{port}: {e}", exc_info=True)
    finally:
        logger.info("Остановка веб-сервера...")
        await runner.cleanup() # Корректно освобождаем ресурсы
        logger.info("Веб-сервер остановлен.")


async def handle_telegram_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """Обрабатывает входящие запросы от Telegram."""
    application = request.app.get('bot_app')
    if not application:
        logger.critical("Объект приложения бота не найден в контексте aiohttp!")
        return aiohttp.web.Response(status=500, text="Internal Server Error: Bot application not found")

    # Проверка секретного токена, если он установлен
    secret_token = os.getenv('WEBHOOK_SECRET_TOKEN')
    if secret_token:
         header_token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
         if header_token != secret_token:
             logger.warning("Получен запрос с неверным секретным токеном.")
             # Возвращаем 403 Forbidden
             return aiohttp.web.Response(status=403, text="Forbidden: Invalid secret token")

    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИСПРАВЛЕНИЕ: Убрали create_task и shield <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
        # Просто ждем завершения обработки апдейта
        await application.process_update(update)
        # ============================================================================================
        # Отвечаем Telegram, что все ОК
        return aiohttp.web.Response(text="OK", status=200)
    except json.JSONDecodeError as e:
         # Если Telegram прислал некорректный JSON
         body = await request.text() # Читаем тело запроса для лога
         logger.error(f"Ошибка декодирования JSON от Telegram: {e}. Тело: {body[:500]}...")
         return aiohttp.web.Response(text="Bad Request", status=400)
    except Exception as e:
        # Ловим другие возможные ошибки при десериализации Update или во время process_update
        logger.error(f"Критическая ошибка обработки вебхук-запроса: {e}", exc_info=True)
        # Возвращаем 500, чтобы Telegram попробовал снова (если это временная ошибка)
        return aiohttp.web.Response(text="Internal Server Error", status=500)


async def main():
    """Основная функция запуска приложения."""
    # Настройка уровней логирования
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    # Устанавливаем базовую конфигурацию - уровень INFO по умолчанию
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO, # Начинаем с INFO
        # Можно добавить хендлеры для вывода в файл и т.д.
        # handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")]
    )

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ИЗМЕНЕНИЕ: Корректировка уровней логов <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    # Понижаем уровень для слишком "болтливых" библиотек
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('google.api_core').setLevel(logging.WARNING)
    # Оставляем INFO для Gemini и DDG для отладки поиска и генерации
    logging.getLogger('google.generativeai').setLevel(logging.INFO)
    logging.getLogger('duckduckgo_search').setLevel(logging.INFO)
    # PIL и Tesseract могут быть шумными, ставим INFO или WARNING
    logging.getLogger('PIL').setLevel(logging.INFO)
    logging.getLogger('pytesseract').setLevel(logging.INFO)
    # Логи доступа aiohttp можно сделать WARNING в проде
    logging.getLogger('aiohttp.access').setLevel(logging.WARNING)
    # Уровень для PTB можно оставить INFO для отладки или WARNING в продакшене
    logging.getLogger('telegram.ext').setLevel(logging.INFO)
    logging.getLogger('telegram.bot').setLevel(logging.INFO) # Логи взаимодействия с API Telegram
    # Устанавливаем уровень для нашего логгера, который мы задали через переменную окружения
    logger.setLevel(log_level)
    logger.info(f"--- Установлен уровень логгирования: {log_level_str} ({log_level}) ---")
    # ============================================================================================


    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event() # Событие для координации остановки

    # Обработчик сигналов SIGINT (Ctrl+C) и SIGTERM (от Docker/systemd)
    def signal_handler():
        if not stop_event.is_set():
             logger.info("Получен сигнал SIGINT/SIGTERM, инициирую штатную остановку...")
             stop_event.set() # Устанавливаем событие, чтобы главный цикл и веб-сервер начали останавливаться
        else:
             logger.warning("Повторный сигнал остановки получен. Завершение уже идет.")

    # Добавляем обработчики сигналов в цикл событий
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            # Предпочтительный способ для asyncio
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
             # Fallback для систем, где add_signal_handler не работает (например, Windows в некоторых случаях)
             logger.warning(f"Не удалось установить обработчик для {sig} через loop.add_signal_handler. Использую signal.signal().")
             try:
                 # Этот способ может быть не идеальным в asyncio, но лучше, чем ничего
                 signal.signal(sig, lambda s, f: signal_handler())
             except Exception as e_signal:
                 logger.error(f"Не удалось установить обработчик {sig} через signal.signal: {e_signal}")


    application = None
    web_server_task = None
    aiohttp_session_main = None # Храним сессию здесь для корректного закрытия

    try:
        logger.info(f"--- Запуск приложения Gemini Telegram Bot ---")
        application, web_server_coro = await setup_bot_and_server(stop_event)
        # Запускаем веб-сервер как задачу asyncio
        web_server_task = asyncio.create_task(web_server_coro)
        # Получаем сессию из bot_data для последующего закрытия
        aiohttp_session_main = application.bot_data.get('aiohttp_session')

        logger.info("Приложение настроено и веб-сервер запущен. Ожидание сигнала остановки (Ctrl+C)...")
        # Главный цикл ожидает события остановки
        await stop_event.wait()

    except asyncio.CancelledError:
        # Если главная задача была отменена извне (маловероятно)
        logger.info("Главная задача main() была отменена.")
    except Exception as e:
        # Ловим критические ошибки на этапе запуска
        logger.critical("Критическая ошибка в главном потоке до или во время ожидания.", exc_info=True)
    finally:
        logger.info("--- Начало процесса штатной остановки приложения ---")

        # Убедимся, что событие установлено, даже если остановка инициирована иначе
        if not stop_event.is_set(): stop_event.set()

        # 1. Останавливаем веб-сервер, чтобы он перестал принимать новые запросы
        if web_server_task and not web_server_task.done():
             logger.info("Остановка веб-сервера (graceful shutdown)...")
             # Веб-сервер должен сам завершиться при установке stop_event, но дадим ему время
             try:
                 # Ждем завершения задачи веб-сервера с таймаутом
                 await asyncio.wait_for(web_server_task, timeout=15.0)
                 logger.info("Веб-сервер успешно завершен.")
             except asyncio.TimeoutError:
                 logger.warning("Веб-сервер не завершился за 15 секунд, принудительная отмена задачи...")
                 web_server_task.cancel()
                 try: await web_server_task # Ждем завершения отмены
                 except asyncio.CancelledError: logger.info("Задача веб-сервера принудительно отменена.")
                 except Exception as e_cancel: logger.error(f"Ошибка при ожидании отмены веб-сервера: {e_cancel}", exc_info=True)
             except asyncio.CancelledError:
                  # Если само ожидание было отменено
                  logger.info("Ожидание завершения веб-сервера было отменено.")
             except Exception as e_wait:
                  # Другая ошибка при ожидании завершения
                  logger.error(f"Ошибка при ожидании штатного завершения веб-сервера: {e_wait}", exc_info=True)

        # 2. Останавливаем приложение Telegram бота (закрывает соединения, и т.д.)
        if application:
            logger.info("Остановка приложения Telegram бота (application.shutdown)...")
            try:
                 await application.shutdown()
                 logger.info("Приложение Telegram бота остановлено.")
            except Exception as e_shutdown:
                 logger.error(f"Ошибка при application.shutdown(): {e_shutdown}", exc_info=True)

        # 3. Закрываем HTTP сессию, созданную в main/setup
        if aiohttp_session_main and not aiohttp_session_main.closed:
             logger.info("Закрытие основной сессии aiohttp...")
             await aiohttp_session_main.close()
             # Небольшая пауза для завершения фоновых задач закрытия соединений
             await asyncio.sleep(0.5)
             logger.info("Основная сессия aiohttp закрыта.")

        # 4. Отменяем все еще работающие задачи (например, process_update)
        # Получаем все задачи, кроме текущей (main)
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if tasks:
            logger.info(f"Отмена {len(tasks)} оставшихся фоновых задач...")
            # Отправляем сигнал отмены всем задачам
            [task.cancel() for task in tasks]
            # Собираем результаты отмены (ждем завершения)
            # return_exceptions=True, чтобы gather не упал при первой же CancelledError
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Логируем результаты/ошибки отмененных задач для отладки
            cancelled_count = 0
            error_count = 0
            for i, res in enumerate(results):
                 if isinstance(res, asyncio.CancelledError):
                      cancelled_count += 1
                 elif isinstance(res, Exception):
                      error_count += 1
                      # Логируем ошибку из отмененной задачи
                      logger.warning(f"Ошибка в отмененной задаче {tasks[i].get_name()}: {res}", exc_info=(isinstance(res, Exception))) # Покажем traceback для исключений
                 # else: # Задача завершилась успешно до или во время отмены
                 #    logger.debug(f"Задача {tasks[i].get_name()} успешно завершилась во время остановки.")
            logger.info(f"Оставшиеся задачи ({len(tasks)}) завершены (отменено: {cancelled_count}, с ошибкой: {error_count}).")

        logger.info("--- Приложение полностью остановлено ---")

if __name__ == '__main__':
    # # Для отладки асинхронных проблем можно раскомментировать:
    # os.environ['PYTHONASYNCIODEBUG'] = '1'
    # logging.getLogger('asyncio').setLevel(logging.DEBUG)

    try:
        # Запускаем главную асинхронную функцию
        asyncio.run(main())
    except KeyboardInterrupt:
        # Пользователь нажал Ctrl+C во время работы asyncio.run (маловероятно при правильной обработке сигналов)
        logger.info("Приложение прервано пользователем (KeyboardInterrupt в asyncio.run).")
    except Exception as e_top:
        # Ловим любые другие неперехваченные ошибки на самом верхнем уровне
        logger.critical("Неперехваченная ошибка в asyncio.run(main).", exc_info=True)

# --- END OF FILE main.py ---