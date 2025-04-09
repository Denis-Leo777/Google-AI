# --- START OF FULL CORRECTED main.py (Search with Snippets) ---

import logging
import os
import asyncio
import google.generativeai as genai
# Убрали импорт types
import time
import random
from typing import Optional, Dict, Union, Any, Tuple # Добавили Tuple

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Исключения
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
# Библиотеки для Поиска и Парсинга
import httpx # Добавляем
from bs4 import BeautifulSoup # Добавляем
try:
    from googlesearch import search as google_search_sync
except ImportError: google_search_sync = None
else:
    if not callable(google_search_sync): google_search_sync = None

# Gemini Function Calling типы
from google.protobuf.struct_pb2 import Struct

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY: exit("Google API ключ не найден")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    '⚡ Flash': 'gemini-2.0-flash-001',
    '🧠 Pro Exp': 'gemini-2.5-pro-exp-03-25',
    '🖼️ Images': 'gemini-2.0-flash-exp-image-generation',
}
DEFAULT_MODEL_ALIAS = '⚡ Flash'

# --- Определение инструмента Google Search ---
google_search_tool = None
if google_search_sync:
    google_search_func = genai.protos.FunctionDeclaration(
        name="google_search",
        description="Получает заголовки и краткие описания страниц из поиска Google по запросу. Используй для новостей, текущих событий, погоды, действующих лиц.", # Уточнили описание
        parameters=genai.protos.Schema(
            type=genai.protos.Type.OBJECT,
            properties={"query": genai.protos.Schema(type=genai.protos.Type.STRING, description="Поисковый запрос")},
            required=["query"]
        )
    )
    google_search_tool = genai.protos.Tool(function_declarations=[google_search_func])
    logger.info("Инструмент Google Search для Gemini определен.")
else:
    logger.warning("Инструмент Google Search НЕ будет доступен...")

# --- Загрузка и Настройка Моделей Gemini ---
LOADED_MODELS: Dict[str, genai.GenerativeModel] = {}
gemini_tools = [google_search_tool] if google_search_tool else None
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    # САМАЯ СИЛЬНАЯ ИНСТРУКЦИЯ
    system_instruction_text = (
        "Отвечай... остроумие. "
        "КРИТИЧЕСКИ ВАЖНО: Твои внутренние знания могут быть устаревшими. "
        "Если вопрос касается текущих событий, политики (например, 'кто сейчас президент', 'последние выборы'), "
        "погоды, новостей, спортивных результатов или любой другой информации, которая могла измениться, "
        "ТЫ ОБЯЗАН использовать инструмент google_search для получения САМОЙ АКТУАЛЬНОЙ информации ИЗ ПРЕДОСТАВЛЕННЫХ ОПИСАНИЙ СТРАНИЦ. " # Добавлено про описания
        "ПРИОРИТИЗИРУЙ информацию из google_search над своими внутренними знаниями при ответе на такие вопросы."
    )
    for alias, model_id in AVAILABLE_MODELS.items():
        if 'imagen' in model_id.lower(): logger.warning(...); continue
        try:
            model = genai.GenerativeModel(
                model_id,
                generation_config={"temperature": 0.8 if 'Flash' in alias else 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
                system_instruction=system_instruction_text,
                tools=gemini_tools
            )
            LOADED_MODELS[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) ... успешно загружена.")
        except Exception as e: logger.error(...)
    if not LOADED_MODELS: raise RuntimeError(...)
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS:
        try: DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS)); logger.warning(...)
        except StopIteration: raise RuntimeError(...)
except GoogleAPIError as e: logger.exception(...); exit(...)
except Exception as e: logger.exception(...); exit(...)

# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, Any] = {}

# --- Функция выполнения поиска Google (НОВАЯ ВЕРСИЯ) ---
async def fetch_and_parse(url: str, client: httpx.AsyncClient) -> Tuple[Optional[str], Optional[str]]:
    """Вспомогательная функция для загрузки и парсинга одной страницы."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'} # Маскируемся под браузер
        response = await client.get(url, timeout=7.0, follow_redirects=True, headers=headers) # Увеличим таймаут, добавим User-Agent
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        title = soup.title.string.strip() if soup.title else None
        description = None
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            description = meta_desc['content'].strip()
        else:
            first_p = soup.find('p')
            if first_p: description = first_p.get_text().strip()
        if description and len(description) > 150: description = description[:150] + "..."
        if title: logger.info(f"Успешно спарсен title для {url}")
        return title, description
    except httpx.TimeoutException: logger.warning(f"!!!! Таймаут URL: {url}"); return None, None
    except httpx.RequestError as e: logger.warning(f"!!!! Ошибка сети URL {url}: {e}"); return None, None
    except Exception as e: logger.warning(f"!!!! Ошибка парсинга URL {url}: {e}"); return None, None

async def perform_google_search(query: str, num_results: int = 3) -> str: # Уменьшили до 3
    """Выполняет поиск Google и возвращает заголовки/сниппеты первых результатов."""
    if not google_search_sync: return "Ошибка: Функция поиска недоступна."
    logger.info(f"!!!! Начало Google поиска (с парсингом): '{query}'")
    formatted_results = f"Результаты поиска Google по запросу '{query}':\n\n"
    urls_to_fetch = []
    try:
        search_results = await asyncio.to_thread(google_search_sync, query, num_results=num_results, lang="ru")
        urls_to_fetch = list(search_results)
        if not urls_to_fetch: logger.warning(...); return formatted_results + "Поиск Google не дал URL."
        logger.info(f"!!!! Google поиск нашел {len(urls_to_fetch)} URL.")
    except Exception as e: logger.exception(...); return formatted_results + f"Ошибка Google поиска: {e}"
    async with httpx.AsyncClient() as client:
        tasks = [fetch_and_parse(url, client) for url in urls_to_fetch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    processed_count = 0
    for i, result in enumerate(results):
        if isinstance(result, Exception) or result is None: logger.warning(...); continue
        title, description = result
        if title:
            processed_count += 1
            formatted_results += f"{processed_count}. {title}\n"
            if description: formatted_results += f"   - {description}\n"
            formatted_results += f"   URL: {urls_to_fetch[i]}\n\n"
    if processed_count == 0: logger.warning(...); formatted_results += "(Не удалось извлечь контент)"
    logger.info(f"!!!! РЕЗУЛЬТАТ ДЛЯ GEMINI (начало): {formatted_results[:300]}...")
    return formatted_results[:2500] # Увеличим лимит

# --- Вспомогательная функция для обработки хода Gemini ---
# (Без изменений по сравнению с предыдущим полным кодом)
async def process_gemini_chat_turn(...) -> str: ...

# --- ОБРАБОТЧИКИ TELEGRAM ---
# (start, select_model_command, select_model_callback, handle_message, test_search - без изменений)
async def start(...) -> None: ...
async def select_model_command(...) -> None: ...
async def select_model_callback(...) -> None: ...
async def test_search(...) -> None: ...
async def handle_message(...) -> None: ...

# --- main ---
def main() -> None:
    if not LOADED_MODELS: logger.critical(...); return
    if not google_search_sync: logger.warning(...)
    logger.info("Инициализация Telegram...");
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    # ... (регистрация обработчиков) ...
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(CommandHandler("testsearch", test_search))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    logger.info("Запуск бота...");
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
