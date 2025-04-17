# --- START OF FILE main.py ---

# Обновлённый main.py:
# - Добавлен Google Custom Search API как основной поиск
# - DuckDuckGo используется как запасной вариант
# - Исправлен поиск DDG: используется синхронный ddgs.text() в отдельном потоке через asyncio.to_thread()
# - Скорректирована системная инструкция и формирование промпта с поиском для более естественного ответа.
# - Улучшено формирование промпта для фото и документов для лучшего удержания контекста.
# - История чата сохраняется без поискового контекста.

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

# Инициализируем логгер
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
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
from google.generativeai.types import HarmCategory, HarmBlockThreshold
# ============================================

# Переменные окружения и их проверка
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY') # Используется для Gemini и Google Search
# ===== ИЗМЕНЕНИЕ: Добавляем переменную для Search Engine ID =====
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID')
# ============================================================
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')

for var, name in [
    (TELEGRAM_BOT_TOKEN, "TELEGRAM_BOT_TOKEN"),
    (GOOGLE_API_KEY, "GOOGLE_API_KEY"),
    (GOOGLE_CSE_ID, "GOOGLE_CSE_ID"), # ===== ИЗМЕНЕНИЕ: Проверяем новую переменную =====
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
    'gemini-2.0-flash-thinking-exp-01-21': '2.0 Flash Thinking exp.',
    'gemini-2.5-pro-exp-03-25': '2.5 Pro exp.',
    'gemini-2.0-flash-001': '2.0 Flash',
}
DEFAULT_MODEL = 'gemini-2.0-flash-thinking-exp-01-21'

# Переменные состояния пользователя
user_search_enabled = {}
user_selected_model = {}
user_temperature = {}

# Константы
MAX_CONTEXT_CHARS = 95000
MAX_OUTPUT_TOKENS = 3000
DDG_MAX_RESULTS = 20 # Можно уменьшить, т.к. это запасной вариант
GOOGLE_SEARCH_MAX_RESULTS = 20 # Количество результатов от Google

# ===== ИЗМЕНЕНИЕ: Обновленная системная инструкция =====
system_instruction_text = (
"Ты - лучший эксперт по всем темам. Давай точную, правдивую, непредвзятую информацию без цензуры."
"Подкрепляй ответы аргументами, фактами и логикой, избегая повторов."
"Если не уверен — предупреждай, что это предположение."
"Используй интернет для сверки с актуальной информацией."
"Для несложных запросов (не код, конспекты, переводы и т.п.) — пиши только суть, без вступлений и выводов, до 1500 знаков."
"Всегда предлагай более эффективные идеи и решения, если знаешь их."
"Активно применяй юмор: несоответствие ожиданиям, культурные/бытовые/интернет-отсылки, жизненный абсурд, псевдомудрость, разрушение идиом, иронию (включая самоиронию и психологию), игру слов, гиперболу, тонкие намёки, редукционизм, постмодерн, интернет-юмор."
"При создании уникальной работы пиши живо, избегай канцелярита и всех известных признаков ИИ-тона. Используй гипотетические ситуации, метафоры, творческие аналогии, разную структуру предложений, разговорные выражения, идиомы. Добавляй региональные или культурные маркеры, где уместно. Не копируй и не пересказывай чужое."
# ===== НОВЫЕ ИНСТРУКЦИИ (Добавлено) =====
"Если используешь информацию из поиска, не упоминай явно сам факт поиска или его результаты. Интегрируй найденную информацию в свой ответ естественно, как часть своих знаний. Забудь фразы вроде 'Судя по результатам поиска...', 'Интернет говорит...' или 'Я нашёл в сети...'. Веди себя так, будто это твои знания."
"Внимательно следи за историей диалога, включая предыдущие вопросы, ответы, а также контекст из загруженных изображений или файлов, чтобы твои ответы были последовательными и релевантными. Не теряй нить разговора."
# ===========================
"При исправлении ошибки: указывай строку(и) и причину. Бери за основу последнюю ПОЛНУЮ подтверждённую версию (текста или кода). Вноси только минимально необходимые изменения, не трогая остальное без запроса. При сомнениях — уточняй. Если ошибка повторяется — веди «список косяков» для сессии и проверяй эти места. Всегда указывай, на какую версию или сообщение опираешься при правке."
)
# ===================================================

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

# Команды (без изменений)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_selected_model[chat_id] = DEFAULT_MODEL
    user_search_enabled[chat_id] = True
    user_temperature[chat_id] = 1.0
    default_model_name = AVAILABLE_MODELS.get(DEFAULT_MODEL, DEFAULT_MODEL)
    start_message = (
        f"GEMINI **{default_model_name}**"
        f"\n + поиск Google/DDG, чтение изображений (OCR) и текстовых файлов" # Скорректировано описание
        "\n/model — выбор модели"
        "\n/clear — очистить историю"
        "\n/search_on /search_off — вкл/выкл поиск" # Добавил обратно для удобства
        "\n/temp 1.0 — температура (0-2)" # Добавил обратно для удобства
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
            raise ValueError("Температура должна быть от 0 до 2")
        user_temperature[chat_id] = temp
        await update.message.reply_text(f"🌡️ Температура установлена на {temp}")
    except (IndexError, ValueError) as e:
        error_msg = f"⚠️ Укажите температуру от 0 до 2, например: /temp 1.0 ({e})" if isinstance(e, ValueError) else "⚠️ Укажите температуру от 0 до 2, например: /temp 1.0"
        await update.message.reply_text(error_msg)

async def enable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_search_enabled[update.effective_chat.id] = True
    await update.message.reply_text("🔍 Поиск Google/DDG включён.")

async def disable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_search_enabled[update.effective_chat.id] = False
    await update.message.reply_text("🔇 Поиск Google/DDG отключён.")

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
        # Проверяем, есть ли клавиатура для редактирования
        if query.message.reply_markup:
            try:
                 await query.edit_message_text(reply_text, parse_mode='Markdown')
            except Exception as e:
                 logger.warning(f"Не удалось изменить сообщение с кнопками: {e}. Отправляю новое.")
                 await context.bot.send_message(chat_id, reply_text, parse_mode='Markdown')
        else:
            await context.bot.send_message(chat_id, reply_text, parse_mode='Markdown')

    else:
        # Если модель неизвестна, тоже редактируем сообщение
        if query.message.reply_markup:
            await query.edit_message_text("❌ Неизвестная модель")
        else:
             # Если исходное сообщение уже без кнопок, отправляем новое
             await context.bot.send_message(chat_id, "❌ Неизвестная модель")


# ===== Функция поиска Google (без изменений) =====
async def perform_google_search(query: str, api_key: str, cse_id: str, num_results: int) -> list[str] | None:
    """Выполняет поиск через Google Custom Search API и возвращает список сниппетов."""
    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': api_key,
        'cx': cse_id,
        'q': query,
        'num': num_results,
        'lr': 'lang_ru', # Искать только на русском
        'gl': 'ru'      # Предпочитать результаты из России
    }
    encoded_params = urlencode(params)
    full_url = f"{search_url}?{encoded_params}"
    logger.debug(f"Запрос к Google Search API: {search_url}?key=...&cx=...&q={query}&num={num_results}&lr=lang_ru&gl=ru")

    try:
        # Используем общий ClientSession, если он передан в context, или создаем новый
        session = context.bot_data.get('aiohttp_session')
        if not session:
            session = aiohttp.ClientSession()
            context.bot_data['aiohttp_session'] = session # Сохраняем для переиспользования

        async with session.get(full_url, timeout=10) as response: # Добавил таймаут
            if response.status == 200:
                data = await response.json()
                items = data.get('items', [])
                # Берем сниппет или title, если сниппета нет
                snippets = [item.get('snippet', item.get('title', ''))
                            for item in items if item.get('snippet') or item.get('title')]
                if snippets:
                    logger.info(f"Google Search: Найдено {len(snippets)} результатов для '{query[:50]}...'.")
                    return snippets
                else:
                    logger.info(f"Google Search: Результаты найдены, но не содержат сниппетов/заголовков для '{query[:50]}...'.")
                    return None
            elif response.status == 429:
                logger.warning(f"Google Search: Ошибка 429 - Квота исчерпана для '{query[:50]}...'!")
                return None
            elif response.status == 403:
                 logger.error(f"Google Search: Ошибка 403 - Доступ запрещен для '{query[:50]}...'. Проверьте API ключ и его ограничения, а также включен ли Custom Search API.")
                 return None
            else:
                error_text = await response.text()
                logger.error(f"Google Search: Ошибка API для '{query[:50]}...' - Статус {response.status}, Ответ: {error_text[:200]}...")
                return None
    except aiohttp.ClientConnectorError as e:
        logger.error(f"Google Search: Ошибка сети (соединение) при запросе для '{query[:50]}...' - {e}")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"Google Search: Ошибка сети (aiohttp) при запросе для '{query[:50]}...' - {e}")
        return None
    except asyncio.TimeoutError:
         logger.warning(f"Google Search: Таймаут запроса для '{query[:50]}...'")
         return None
    except json.JSONDecodeError as e:
        # Попытаемся прочитать текст ответа перед ошибкой JSON
        try:
            error_text_json = await response.text()
            logger.error(f"Google Search: Ошибка декодирования JSON ответа для '{query[:50]}...' - {e}. Ответ: {error_text_json[:200]}...")
        except Exception:
             logger.error(f"Google Search: Ошибка декодирования JSON ответа для '{query[:50]}...' - {e}")
        return None
    except Exception as e:
        logger.error(f"Google Search: Непредвиденная ошибка для '{query[:50]}...' - {e}", exc_info=True)
        return None
# ===============================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    original_user_message = update.message.text.strip() if update.message.text else ""
    # Проверяем, не пустой ли 'фейковый' апдейт из фото/документа
    if not original_user_message and hasattr(update, 'message') and hasattr(update.message, 'text') and update.message.text:
        original_user_message = update.message.text.strip()

    if not original_user_message:
        logger.warning(f"ChatID: {chat_id} | Получено пустое сообщение.")
        return

    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    temperature = user_temperature.get(chat_id, 1.0)
    use_search = user_search_enabled.get(chat_id, True)

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # ===== ИЗМЕНЕНИЕ: Логика поиска и формирования промпта =====
    search_context_snippets = [] # Список для хранения найденных сниппетов
    final_user_prompt = original_user_message # По умолчанию промпт = оригинальное сообщение
    search_provider = None # Отслеживаем, какой поиск сработал
    search_log_msg = "Поиск отключен" # Инициализация лога

    if use_search:
        search_log_msg = "Поиск Google/DDG включен"
        logger.info(f"ChatID: {chat_id} | {search_log_msg}. Запрос: '{original_user_message[:50]}...'")

        # --- Попытка Google Search ---
        google_results = await perform_google_search(
            original_user_message, GOOGLE_API_KEY, GOOGLE_CSE_ID, GOOGLE_SEARCH_MAX_RESULTS
        )

        if google_results:
            search_provider = "Google"
            search_context_snippets = google_results # Просто сохраняем список строк
            search_log_msg += f" (Google: {len(search_context_snippets)} рез.)"
            logger.info(f"ChatID: {chat_id} | Использованы результаты Google: {len(search_context_snippets)} сниппетов.")
        else:
            search_log_msg += " (Google: нет рез.)"
            logger.info(f"ChatID: {chat_id} | Поиск Google не дал результатов или ошибка. Пробуем DuckDuckGo...")
            # --- Попытка DuckDuckGo (если Google не сработал) ---
            try:
                ddgs = DDGS()
                logger.debug(f"ChatID: {chat_id} | Запрос к DDGS().text('{original_user_message}', region='ru-ru', max_results={DDG_MAX_RESULTS}) через asyncio.to_thread")
                results_ddg = await asyncio.to_thread(
                    ddgs.text,
                    original_user_message,
                    region='ru-ru',
                    max_results=DDG_MAX_RESULTS,
                    timeout=10 # Добавим таймаут для DDG
                )
                logger.debug(f"ChatID: {chat_id} | Результаты DDG:\n{pprint.pformat(results_ddg)}")

                if results_ddg:
                    # Берем только body, если оно есть
                    ddg_snippets = [r.get('body', '') for r in results_ddg if r.get('body')]
                    if ddg_snippets:
                        search_provider = "DuckDuckGo"
                        search_context_snippets = ddg_snippets
                        search_log_msg += f" (DDG: {len(search_context_snippets)} рез.)"
                        logger.info(f"ChatID: {chat_id} | Использованы результаты DDG: {len(search_context_snippets)} сниппетов.")
                    else:
                        search_log_msg += " (DDG: рез. без текста)"
                        logger.info(f"ChatID: {chat_id} | Результаты DDG найдены, но не содержат текста (body).")
                else:
                    search_log_msg += " (DDG: нет рез.)"
                    logger.info(f"ChatID: {chat_id} | Результаты DDG не найдены.")
            except TimeoutError:
                 logger.warning(f"ChatID: {chat_id} | Таймаут при поиске DuckDuckGo.")
                 search_log_msg += " (DDG: таймаут)"
            except Exception as e_ddg:
                logger.error(f"ChatID: {chat_id} | Ошибка при поиске DuckDuckGo: {e_ddg}", exc_info=True)
                search_log_msg += " (DDG: ошибка)"
        # --- Конец блока поиска ---

        # Обновляем финальный промпт, если есть сниппеты из ЛЮБОГО поиска
        if search_context_snippets:
            # Собираем контекст без явного заголовка
            search_context = "\n".join([f"- {s}" for s in search_context_snippets]) # Добавим маркеры для лучшего восприятия моделью
            # Формируем промпт хитрее: сначала доп. инфа, потом сам вопрос
            final_user_prompt = (
                f"Дополнительная информация по теме (используй её по необходимости, не ссылаясь):\n{search_context}\n\n"
                f"Вопрос пользователя: \"{original_user_message}\""
            )
        # Если сниппетов нет, final_user_prompt остается original_user_message

    # ===== Обновленное логирование =====
    logger.info(f"ChatID: {chat_id} | Модель: {model_id}, Темп: {temperature}, {search_log_msg}")
    logger.debug(f"ChatID: {chat_id} | Финальный промпт для Gemini (может включать доп. инфо):\n{final_user_prompt}")
    # ==================================

    chat_history = context.chat_data.setdefault("history", [])
    # Добавляем в историю ТОЛЬКО оригинальное сообщение пользователя
    chat_history.append({"role": "user", "parts": [{"text": original_user_message}]})

    # Обрезка истории (без изменений в логике, но уточнено логирование)
    total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
    removed_count = 0
    while total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1:
        # Удаляем пару USER+MODEL, начиная с самых старых
        if len(chat_history) >= 2:
            chat_history.pop(0) # user
            chat_history.pop(0) # model
            removed_count += 2
        else: # Если осталось только одно сообщение (user), удаляем его
            chat_history.pop(0)
            removed_count += 1
        # Пересчитываем символы после удаления
        total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history if p.get("parts") and p["parts"][0].get("text"))
    if removed_count > 0:
        logger.info(f"ChatID: {chat_id} | История обрезана, удалено {removed_count} сообщений. Текущая длина: {len(chat_history)}, символов: {total_chars}")

    # Создаем копию истории для отправки модели, заменяя последний запрос пользователя на модифицированный
    history_for_model = list(chat_history[:-1]) # Берем все, кроме последнего (оригинального) запроса пользователя
    history_for_model.append({"role": "user", "parts": [{"text": final_user_prompt}]}) # Добавляем модифицированный запрос (с контекстом или без)

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
        # Используем history_for_model для запроса
        response = model.generate_content(history_for_model)

        reply = response.text
        if not reply:
            # Обработка пустого ответа (используем .name для enum)
            try:
                feedback = response.prompt_feedback
                candidates_info = response.candidates
                block_reason_enum = feedback.block_reason if feedback else None
                block_reason = block_reason_enum.name if block_reason_enum else 'N/A'

                finish_reason_enum = candidates_info[0].finish_reason if candidates_info else None
                finish_reason_val = finish_reason_enum.name if finish_reason_enum else 'N/A'

                safety_ratings = feedback.safety_ratings if feedback else []
                safety_info = ", ".join([f"{s.category.name}: {s.probability.name}" for s in safety_ratings])

                logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели. Block: {block_reason}, Finish: {finish_reason_val}, Safety: [{safety_info}]")
                if block_reason_enum and block_reason_enum != genai.types.BlockReason.UNSPECIFIED:
                     reply = f"🤖 Модель не дала ответ. (Причина блокировки: {block_reason})"
                elif finish_reason_enum and finish_reason_enum != genai.types.FinishReason.STOP: # Если остановилась не штатно
                    reply = f"🤖 Модель не дала ответ. (Причина: {finish_reason_val})"
                else: # Если причина неясна или штатная остановка без текста
                    reply = f"🤖 Модель дала пустой ответ."
            except AttributeError as e_attr:
                 logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели, не удалось извлечь доп. инфо (AttributeError: {e_attr}).")
                 reply = "🤖 Нет ответа от модели."
            except Exception as e_inner:
                logger.warning(f"ChatID: {chat_id} | Пустой ответ от модели, ошибка извлечения доп. инфо: {e_inner}", exc_info=True)
                reply = "🤖 Нет ответа от модели."

        # Добавляем ответ модели в ОСНОВНУЮ историю (chat_history)
        if reply:
             chat_history.append({"role": "model", "parts": [{"text": reply}]})

    except Exception as e:
        # Обработка ошибок (без изменений)
        logger.exception(f"ChatID: {chat_id} | Ошибка при взаимодействии с моделью {model_id}")
        error_message = str(e)
        reply = f"❌ Ошибка при обращении к модели." # Default message
        try:
            if hasattr(genai, 'types'):
                if isinstance(e, genai.types.BlockedPromptException):
                     reply = f"❌ Запрос заблокирован моделью. Причина: {e}"
                elif isinstance(e, genai.types.StopCandidateException):
                     reply = f"❌ Генерация остановлена моделью. Причина: {e}"
                elif "429" in error_message and ("quota" in error_message or "Resource has been exhausted" in error_message):
                     reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (ошибка 429). Попробуйте позже."
                elif "400" in error_message and "API key not valid" in error_message:
                     reply = "❌ Ошибка: Неверный Google API ключ."
                elif "Deadline Exceeded" in error_message or "504" in error_message:
                     reply = "❌ Ошибка: Модель слишком долго отвечала (таймаут)."
                elif "User location is not supported for accessing this model" in error_message:
                     reply = f"❌ Ошибка: Ваш регион не поддерживается для модели {model_id}."
                else:
                     reply = f"❌ Ошибка при обращении к модели: {error_message}"
            else:
                 # Fallback if genai.types is not available
                 logger.warning("genai.types не найден, используем общую обработку ошибок.")
                 if "429" in error_message and ("quota" in error_message or "Resource has been exhausted" in error_message):
                      reply = f"❌ Ошибка: Достигнут лимит запросов к API Google (ошибка 429). Попробуйте позже."
                 # ... (other checks)
                 else:
                      reply = f"❌ Ошибка при обращении к модели: {error_message}"

        except AttributeError:
             # Fallback for unexpected AttributeErrors during error handling
             logger.warning("AttributeError при обработке ошибки genai.")
             reply = f"❌ Ошибка при обращении к модели: {error_message}"

    if reply:
        # Разбиваем длинные сообщения (без изменений)
        MAX_MESSAGE_LENGTH = 4096
        reply_chunks = [reply[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(reply), MAX_MESSAGE_LENGTH)]
        # Отвечаем на исходное сообщение пользователя
        message_to_reply = update.message
        for chunk in reply_chunks:
            try:
                # Пытаемся ответить на сообщение
                message_to_reply = await message_to_reply.reply_text(chunk)
            except Exception as e_reply:
                logger.error(f"ChatID: {chat_id} | Ошибка при отправке ответа: {e_reply}. Попытка отправить в чат.")
                try:
                     # Если ответить не удалось (например, сообщение удалено), просто шлем в чат
                     message_to_reply = await context.bot.send_message(chat_id=chat_id, text=chunk)
                except Exception as e_send:
                     logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение в чат: {e_send}")
                     # Прерываем отправку остальных частей, если даже в чат не шлется
                     break

# ===== ИЗМЕНЕНИЕ: Обработчик фото =====
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tesseract_available = False
    try:
        pytesseract.pytesseract.get_tesseract_version()
        tesseract_available = True
        logger.info(f"Tesseract доступен. Путь: {pytesseract.pytesseract.tesseract_cmd}")
    except Exception as e:
        logger.error(f"Проблема с доступом к Tesseract: {e}. OCR будет недоступен.")
        # Можно попробовать найти tesseract автоматически, но пока оставим так

    if not update.message.photo:
        logger.warning(f"ChatID: {chat_id} | В handle_photo не найдено фото.")
        return

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
                # --- ИЗМЕНЕНИЕ: Диалоговый промпт для OCR ---
                ocr_context = f"На изображении обнаружен следующий текст:\n```\n{extracted_text.strip()}\n```"
                if user_caption:
                    user_prompt = f"Пользователь загрузил фото с подписью: \"{user_caption}\". {ocr_context}\nЧто можешь сказать об этом фото и тексте на нём?"
                else:
                    user_prompt = f"Пользователь загрузил фото. {ocr_context}\nЧто можешь сказать об этом фото и тексте на нём?"
                # -----------------------------------------

                # Создаем "фейковый" update для handle_message
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
                return # Выходим, чтобы не обрабатывать как простое изображение
            else:
                 logger.info(f"ChatID: {chat_id} | OCR не нашел текст на изображении.")

        except pytesseract.TesseractNotFoundError:
             logger.error("Tesseract не найден! Проверьте путь. OCR отключен.")
             tesseract_available = False
        except Exception as e:
            logger.warning(f"ChatID: {chat_id} | Ошибка OCR: {e}", exc_info=True)

    # Обработка как изображение (если OCR выключен/не сработал)
    logger.info(f"ChatID: {chat_id} | Обработка фото как изображения (без/после OCR)")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    MAX_IMAGE_BYTES = 4 * 1024 * 1024 # Примерный лимит Gemini
    if len(file_bytes) > MAX_IMAGE_BYTES:
        logger.warning(f"ChatID: {chat_id} | Изображение ({len(file_bytes)} байт) может быть слишком большим для API.")
        # Можно добавить сжатие или просто предупредить пользователя/отклонить

    b64_data = base64.b64encode(file_bytes).decode()
    # --- ИЗМЕНЕНИЕ: Промпт для изображения - передаем описание как часть "диалога" ---
    # Вместо простого "Что на фото?", делаем более контекстный запрос
    if user_caption:
         prompt_text = f"Пользователь прислал фото с подписью: \"{user_caption}\". Опиши, что видишь на изображении и как это соотносится с подписью (если применимо)."
    else:
         prompt_text = "Пользователь прислал фото без подписи. Опиши, что видишь на изображении."
    parts = [
        {"text": prompt_text},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}
    ]
    # -------------------------------------------------------------------------------

    model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
    # Проверка и возможное переключение на vision-модель (логика без изменений)
    if 'flash' not in model_id and 'pro' not in model_id and 'vision' not in model_id:
         vision_models = [m for m in AVAILABLE_MODELS if 'flash' in m or 'pro' in m or 'vision' in m]
         if vision_models:
             original_model_name = AVAILABLE_MODELS.get(model_id, model_id)
             model_id = vision_models[0]
             new_model_name = AVAILABLE_MODELS.get(model_id, model_id)
             logger.warning(f"ChatID: {chat_id} | Модель {original_model_name} не vision. Временно использую {new_model_name}.")
             # Уведомлять пользователя необязательно, чтобы не спамить
             # await context.bot.send_message(chat_id=chat_id, text=f"ℹ️ Ваша модель не видит картинки, временно использую {new_model_name}.")
         else:
             logger.error(f"ChatID: {chat_id} | Нет доступных vision моделей.")
             await update.message.reply_text("❌ Нет доступных моделей для анализа изображений.")
             return

    temperature = user_temperature.get(chat_id, 1.0)
    logger.info(f"ChatID: {chat_id} | Анализ изображения. Модель: {model_id}, Темп: {temperature}")
    tools = []
    reply = None

    try:
        generation_config=genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=MAX_OUTPUT_TOKENS
        )
        model = genai.GenerativeModel(
            model_id, # Используем актуальный model_id (мог измениться)
            tools=tools,
            safety_settings=SAFETY_SETTINGS_BLOCK_NONE,
            generation_config=generation_config,
            system_instruction=system_instruction_text
        )
        # Передаем список словарей для vision
        response = model.generate_content([{"role": "user", "parts": parts}])
        reply = response.text

        if not reply:
            # Обработка пустого ответа (без изменений)
            try:
                feedback = response.prompt_feedback
                candidates_info = response.candidates
                block_reason_enum = feedback.block_reason if feedback else None
                block_reason = block_reason_enum.name if block_reason_enum else 'N/A'
                finish_reason_enum = candidates_info[0].finish_reason if candidates_info else None
                finish_reason_val = finish_reason_enum.name if finish_reason_enum else 'N/A'
                safety_ratings = feedback.safety_ratings if feedback else []
                safety_info = ", ".join([f"{s.category.name}: {s.probability.name}" for s in safety_ratings])

                logger.warning(f"ChatID: {chat_id} | Пустой ответ при анализе изображения. Block: {block_reason}, Finish: {finish_reason_val}, Safety: [{safety_info}]")
                if block_reason_enum and block_reason_enum != genai.types.BlockReason.UNSPECIFIED:
                     reply = f"🤖 Модель не смогла описать изображение. (Причина блокировки: {block_reason})"
                elif finish_reason_enum and finish_reason_enum != genai.types.FinishReason.STOP:
                    reply = f"🤖 Модель не смогла описать изображение. (Причина: {finish_reason_val})"
                else:
                    reply = f"🤖 Не удалось понять, что на изображении (пустой ответ)."

            except AttributeError as e_attr:
                 logger.warning(f"ChatID: {chat_id} | Пустой ответ (фото), ошибка атрибута: {e_attr}.")
                 reply = "🤖 Не удалось понять, что на изображении."
            except Exception as e_inner:
                 logger.warning(f"ChatID: {chat_id} | Пустой ответ (фото), ошибка извлечения инфо: {e_inner}", exc_info=True)
                 reply = "🤖 Не удалось понять, что на изображении."

    except Exception as e:
        # Обработка ошибок (без изменений)
        logger.exception(f"ChatID: {chat_id} | Ошибка при анализе изображения")
        error_message = str(e)
        reply = f"❌ Ошибка при анализе изображения." # Default
        try:
            if hasattr(genai, 'types'):
                 # ... (обработка BlockedPromptException, StopCandidateException, 429, 400) ...
                if isinstance(e, genai.types.BlockedPromptException):
                     reply = f"❌ Анализ изображения заблокирован. Причина: {e}"
                elif isinstance(e, genai.types.StopCandidateException):
                     reply = f"❌ Анализ изображения остановлен. Причина: {e}"
                elif "429" in error_message and ("quota" in error_message or "Resource has been exhausted" in error_message):
                     reply = f"❌ Ошибка: Лимит запросов к API Google (429). Попробуйте позже."
                elif "400" in error_message and "API key not valid" in error_message:
                     reply = "❌ Ошибка: Неверный Google API ключ."
                elif "400" in error_message and ("image" in error_message.lower() or "input" in error_message.lower()):
                     # Более общая проверка на проблемы с изображением/вводом
                     reply = f"❌ Ошибка: Проблема с изображением или модель не поддерживает такой ввод ({error_message[:100]}...)."
                elif "Deadline Exceeded" in error_message or "504" in error_message:
                     reply = "❌ Ошибка: Модель долго отвечала (таймаут)."
                elif "User location is not supported" in error_message:
                     reply = f"❌ Ошибка: Ваш регион не поддерживается для модели {model_id}."
                else:
                     reply = f"❌ Ошибка при анализе изображения: {error_message}"
            else:
                 # Fallback
                 # ... (обработка 429, 400) ...
                 reply = f"❌ Ошибка при анализе изображения: {error_message}"
        except AttributeError:
            logger.warning("AttributeError при обработке ошибки genai (фото).")
            reply = f"❌ Ошибка при анализе изображения: {error_message}"

    if reply:
        # Разбиваем длинные сообщения (без изменений)
        MAX_MESSAGE_LENGTH = 4096
        reply_chunks = [reply[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(reply), MAX_MESSAGE_LENGTH)]
        message_to_reply = update.message
        for chunk in reply_chunks:
            try:
                message_to_reply = await message_to_reply.reply_text(chunk)
            except Exception as e_reply:
                logger.error(f"ChatID: {chat_id} | Ошибка при ответе на фото: {e_reply}. Попытка отправить в чат.")
                try:
                     message_to_reply = await context.bot.send_message(chat_id=chat_id, text=chunk)
                except Exception as e_send:
                     logger.error(f"ChatID: {chat_id} | Не удалось отправить сообщение (фото) в чат: {e_send}")
                     break

# ===== ИЗМЕНЕНИЕ: Обработчик документов =====
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not update.message.document:
        logger.warning(f"ChatID: {chat_id} | В handle_document нет документа.")
        return

    doc = update.message.document
    # Проверка MIME типа (без изменений)
    allowed_mime_prefixes = ('text/', 'application/json', 'application/xml', 'application/csv', 'application/x-python', 'application/x-sh', 'application/javascript')
    # application/octet-stream может быть чем угодно, но часто это текст без явного типа
    allowed_mime_types = ('application/octet-stream',)

    is_allowed_prefix = any(doc.mime_type.startswith(prefix) for prefix in allowed_mime_prefixes) if doc.mime_type else False
    is_allowed_type = doc.mime_type in allowed_mime_types if doc.mime_type else False

    if not (is_allowed_prefix or is_allowed_type):
        mime_type_str = f"`{doc.mime_type}`" if doc.mime_type else "неизвестный"
        await update.message.reply_text(f"⚠️ Пока могу читать только текстовые файлы (типа .txt, .py, .json, .csv и т.п.). Ваш тип: {mime_type_str}")
        logger.warning(f"ChatID: {chat_id} | Попытка загрузить неподдерживаемый файл: {doc.file_name} (MIME: {doc.mime_type})")
        return

    # Ограничение размера файла (без изменений)
    MAX_FILE_SIZE_MB = 10
    if doc.file_size and doc.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ Файл '{doc.file_name}' слишком большой (> {MAX_FILE_SIZE_MB} MB).")
        logger.warning(f"ChatID: {chat_id} | Слишком большой файл: {doc.file_name} ({doc.file_size / (1024*1024):.2f} MB)")
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

    # Определение кодировки и декодирование (без изменений)
    text = None
    detected_encoding = None
    encodings_to_try = ['utf-8', 'latin-1', 'cp1251']
    for encoding in encodings_to_try:
        try:
            text = file_bytes.decode(encoding)
            detected_encoding = encoding
            logger.info(f"ChatID: {chat_id} | Файл '{doc.file_name}' декодирован как {encoding}.")
            break
        except UnicodeDecodeError:
            logger.debug(f"ChatID: {chat_id} | Файл '{doc.file_name}' не в {encoding}.")
        except Exception as e:
            logger.error(f"ChatID: {chat_id} | Ошибка декодирования '{doc.file_name}' как {encoding}: {e}")

    if text is None:
        logger.error(f"ChatID: {chat_id} | Не удалось декодировать '{doc.file_name}' ни одной из: {encodings_to_try}")
        await update.message.reply_text(f"❌ Не удалось прочитать текстовое содержимое файла '{doc.file_name}'. Попробуйте кодировку UTF-8.")
        return

    # Обрезка текста (без изменений)
    MAX_FILE_CHARS = MAX_CONTEXT_CHARS // 3 # Оставляем запас
    truncated = text
    warning_msg = ""
    if len(text) > MAX_FILE_CHARS:
        truncated = text[:MAX_FILE_CHARS]
        warning_msg = f"\n\n(⚠️ Текст файла был обрезан до {MAX_FILE_CHARS} символов)"
        logger.warning(f"ChatID: {chat_id} | Текст файла '{doc.file_name}' обрезан до {MAX_FILE_CHARS} симв.")

    user_caption = update.message.caption if update.message.caption else ""
    file_name = doc.file_name or "файл"
    encoding_info = f"(предположительно {detected_encoding})" if detected_encoding else ""

    # --- ИЗМЕНЕНИЕ: Диалоговый промпт для документа ---
    file_context = f"Содержимое файла '{file_name}' {encoding_info}:\n```\n{truncated}\n```{warning_msg}"
    if user_caption:
        user_prompt = f"Пользователь загрузил файл '{file_name}' с комментарием: \"{user_caption}\". {file_context}\nПроанализируй, пожалуйста."
    else:
        user_prompt = f"Пользователь загрузил файл '{file_name}'. {file_context}\nЧто можешь сказать об этом тексте?"
    # -----------------------------------------------

    # Создаем фейковый апдейт для handle_message (без изменений)
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
# ======================================


# --- Функции веб-сервера и запуска (без существенных изменений, но с улучшениями из прошлого ответа) ---
async def setup_bot_and_server(stop_event: asyncio.Event):
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Создаем и сохраняем сессию aiohttp для переиспользования
    aiohttp_session = aiohttp.ClientSession()
    application.bot_data['aiohttp_session'] = aiohttp_session

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("temp", set_temperature))
    application.add_handler(CommandHandler("search_on", enable_search))
    application.add_handler(CommandHandler("search_off", disable_search))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # Используем кастомный фильтр для документов, проверка внутри handle_document
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await application.initialize()
    webhook_url = urljoin(WEBHOOK_HOST, f"/{GEMINI_WEBHOOK_PATH}")
    logger.info(f"Устанавливаю вебхук: {webhook_url}")
    await application.bot.set_webhook(webhook_url, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    return application, run_web_server(application, stop_event)


async def run_web_server(application: Application, stop_event: asyncio.Event):
    app = aiohttp.web.Application()
    async def health_check(request):
        # Проверяем наличие бота в приложении для более полной проверки
        if 'bot_app' in request.app and request.app['bot_app']:
            return aiohttp.web.Response(text="OK")
        else:
             return aiohttp.web.Response(text="Error: Bot not configured", status=503)

    app.router.add_get('/', health_check)

    app['bot_app'] = application
    webhook_path = f"/{GEMINI_WEBHOOK_PATH}"
    app.router.add_post(webhook_path, handle_telegram_webhook)
    logger.info(f"Вебхук слушает на пути: {webhook_path}")

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))
    # Убедимся, что хост 0.0.0.0 используется для Docker/внешних подключений
    host = "0.0.0.0"
    site = aiohttp.web.TCPSite(runner, host, port)
    try:
        await site.start()
        logger.info(f"Сервер запущен на http://{host}:{port}")
        # Ждем события остановки
        await stop_event.wait()
        logger.info("Событие остановки получено веб-сервером.")
    except asyncio.CancelledError:
        logger.info("Задача веб-сервера отменена.")
    finally:
        logger.info("Останавливаю веб-сервер (cleanup)...")
        await runner.cleanup()
        logger.info("Веб-сервер остановлен.")


async def handle_telegram_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    application = request.app.get('bot_app')
    if not application:
        logger.error("Объект приложения бота не найден в контексте aiohttp!")
        return aiohttp.web.Response(status=500, text="Internal Server Error: Bot application not configured")

    try:
        data = await request.json()
        # logger.debug(f"Получен вебхук: {data}") # Осторожно, может быть много данных
        update = Update.de_json(data, application.bot)
        # Обрабатываем апдейт в фоне, чтобы быстро ответить телеграму
        asyncio.create_task(application.process_update(update))
        return aiohttp.web.Response(text="OK", status=200)
    except json.JSONDecodeError as e:
         body = await request.text()
         logger.error(f"Ошибка декодирования JSON от Telegram: {e}. Тело запроса: {body[:500]}...")
         return aiohttp.web.Response(text="Bad Request", status=400)
    except Exception as e:
        logger.error(f"Критическая ошибка обработки вебхук-запроса: {e}", exc_info=True)
        # Отвечаем 200 OK, чтобы Telegram не долбил повторами
        return aiohttp.web.Response(text="OK", status=200)


async def main():
    # Настройка уровней логирования
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('google.api_core').setLevel(logging.WARNING)
    logging.getLogger('google.generativeai').setLevel(logging.INFO) # Оставляем INFO для Gemini
    logging.getLogger('duckduckgo_search').setLevel(logging.INFO)
    logging.getLogger('PIL').setLevel(logging.INFO)
    logging.getLogger('aiohttp.access').setLevel(logging.WARNING) # Уменьшаем логи доступа aiohttp

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    # Обработчик сигналов
    def signal_handler():
        logger.info("Получен сигнал SIGINT/SIGTERM, начинаю остановку...")
        if not stop_event.is_set():
            stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
             logger.warning(f"Не удалось установить обработчик для {sig} (возможно, Windows).")

    application = None
    web_server_task = None
    aiohttp_session_local = None # Локальная переменная для сессии

    try:
        logger.info("Запускаю настройку бота и сервера...")
        application, web_server_coro = await setup_bot_and_server(stop_event)
        web_server_task = asyncio.create_task(web_server_coro)
        # Сохраняем ссылку на сессию для закрытия
        if 'aiohttp_session' in application.bot_data:
            aiohttp_session_local = application.bot_data['aiohttp_session']

        logger.info("Настройка завершена, приложение запущено.")
        await stop_event.wait() # Ждем сигнала остановки

    except asyncio.CancelledError:
        logger.info("Главная задача main была отменена.")
    except Exception as e:
        logger.critical("Критическая ошибка в главном потоке приложения.", exc_info=True)
    finally:
        logger.info("Начинаю процесс штатной остановки...")

        # 1. Устанавливаем событие остановки (на случай, если сигнал не установил)
        if not stop_event.is_set():
            stop_event.set()

        # 2. Останавливаем веб-сервер
        if web_server_task and not web_server_task.done():
             logger.info("Ожидаю завершения веб-сервера...")
             try:
                 # Даем время на завершение текущих запросов
                 await asyncio.wait_for(web_server_task, timeout=15.0)
                 logger.info("Веб-сервер успешно завершен.")
             except asyncio.TimeoutError:
                 logger.warning("Веб-сервер не завершился за 15 сек, отменяю...")
                 web_server_task.cancel()
                 try: await web_server_task
                 except asyncio.CancelledError: logger.info("Задача веб-сервера отменена.")
                 except Exception as e: logger.error(f"Ошибка при отмене веб-сервера: {e}", exc_info=True)
             except asyncio.CancelledError:
                  logger.info("Ожидание веб-сервера было отменено.")
             except Exception as e:
                 logger.error(f"Ошибка при ожидании веб-сервера: {e}", exc_info=True)

        # 3. Останавливаем приложение бота
        if application:
            logger.info("Останавливаю приложение бота (shutdown)...")
            try:
                 await application.shutdown()
                 logger.info("Приложение бота остановлено.")
            except Exception as e:
                 logger.error(f"Ошибка при application.shutdown(): {e}", exc_info=True)
        else:
            logger.warning("Объект application не найден для остановки.")

        # 4. Закрываем сессию aiohttp
        if aiohttp_session_local and not aiohttp_session_local.closed:
             logger.info("Закрываю сессию aiohttp...")
             await aiohttp_session_local.close()
             logger.info("Сессия aiohttp закрыта.")

        # 5. Отменяем оставшиеся задачи (если есть)
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if tasks:
            logger.info(f"Отменяю {len(tasks)} оставшихся задач...")
            [task.cancel() for task in tasks]
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("Оставшиеся задачи отменены.")
            except asyncio.CancelledError:
                logger.info("Отмена задач была прервана.")
            except Exception as e:
                logger.error(f"Ошибка при отмене оставшихся задач: {e}", exc_info=True)

        logger.info("Приложение полностью остановлено.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение прервано пользователем (Ctrl+C)")
    except Exception as e:
        logger.critical(f"Неперехваченная ошибка на верхнем уровне.", exc_info=True)

# --- END OF FILE main.py ---