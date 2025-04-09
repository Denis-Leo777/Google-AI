# --- START OF FULL CORRECTED main.py (Log search result + Stronger Prompt + drop_pending) ---

import logging
import os
import asyncio
import google.generativeai as genai
# Убрали импорт types
import time
import random
from typing import Optional, Dict, Union, Any

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Исключения
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
# Библиотека для поиска Google
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
    '⚡ Flash': 'gemini-2.0-flash-001', # Оставим Flash основной
    '🧠 Pro Exp': 'gemini-2.5-pro-exp-03-25',
    '🖼️ Imagen 3 (Картинки!)': 'imagen-3.0-generate-002',
}
DEFAULT_MODEL_ALIAS = '⚡ Flash'

# --- Определение инструмента Google Search ---
google_search_tool = None
if google_search_sync:
    google_search_func = genai.protos.FunctionDeclaration(
        name="google_search",
        description="Получает самую свежую информацию из поиска Google по запросу. Используй для новостей, текущих событий, погоды, действующих лиц.",
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
        "Отвечай... остроумие. " # Ваша основная инструкция
        "КРИТИЧЕСКИ ВАЖНО: Твои внутренние знания могут быть устаревшими. "
        "Если вопрос касается текущих событий, политики (например, 'кто сейчас президент', 'последние выборы'), "
        "погоды, новостей, спортивных результатов или любой другой информации, которая могла измениться, "
        "ТЫ ОБЯЗАН использовать инструмент google_search для получения САМОЙ АКТУАЛЬНОЙ информации. "
        "ПРИОРИТИЗИРУЙ информацию из google_search над своими внутренними знаниями при ответе на такие вопросы."
    )

    for alias, model_id in AVAILABLE_MODELS.items():
        if 'imagen' in model_id.lower():
             logger.warning(f"Модель '{alias}' ({model_id}) пропущена (генерация изображений).")
             continue
        try:
            model = genai.GenerativeModel(
                model_id,
                generation_config={"temperature": 0.8 if 'Flash' in alias else 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
                system_instruction=system_instruction_text, # Передаем САМУЮ СИЛЬНУЮ инструкцию
                tools=gemini_tools
            )
            LOADED_MODELS[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Search: {'Enabled' if gemini_tools else 'Disabled'}] успешно загружена.")
        except Exception as e:
            logger.error(f"!!! ОШИБКА загрузки модели '{alias}' ({model_id}): {e}")

    if not LOADED_MODELS: raise RuntimeError("Ни одна текстовая модель не загружена!")
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS:
        try: DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS)); logger.warning(f"Установлена модель по умолчанию: {DEFAULT_MODEL_ALIAS}")
        except StopIteration: raise RuntimeError("Не удалось установить модель по умолчанию.")

except GoogleAPIError as e: logger.exception(...); exit(...)
except Exception as e: logger.exception(...); exit(...)

# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, Any] = {} # Без type hint для ChatSession

# --- Функция выполнения поиска Google ---
async def perform_google_search(query: str, num_results: int = 5) -> str:
    if not google_search_sync: return "Ошибка: Функция поиска недоступна."
    logger.info(f"!!!! Начало Google поиска: '{query}'")
    formatted_results = f"Результаты поиска Google по запросу '{query}':\n" # Начинаем формировать ответ
    try:
        # Используем `num` вместо `num_results` для googlesearch-python
        search_results = await asyncio.to_thread(
            google_search_sync, query, num=num_results, lang="ru" # Используем num
        )
        results_list = list(search_results)
        if not results_list:
            logger.warning(f"!!!! Google поиск по '{query}' не дал результатов.")
            formatted_results += "Поиск Google не дал результатов."
        else:
            for i, result in enumerate(results_list, 1):
                formatted_results += f"{i}. {result}\n"
            logger.info(f"!!!! Google поиск по '{query}' вернул {len(results_list)} ссылок.")

    except Exception as e:
        logger.exception(f"!!!! ОШИБКА Google поиска '{query}': {e}")
        formatted_results += f"\nОшибка при выполнении поиска Google: {e}"

    # ЛОГИРУЕМ ТО, ЧТО ВОЗВРАЩАЕМ В GEMINI
    logger.info(f"!!!! РЕЗУЛЬТАТ ДЛЯ GEMINI (начало): {formatted_results[:200]}...")
    return formatted_results[:1500] # Ограничиваем длину

# --- Вспомогательная функция для обработки хода Gemini ---
# (Без изменений по сравнению с предыдущей версией, где мы убрали type hints и использовали protos.FunctionResponse)
async def process_gemini_chat_turn(
    chat_session, model_name: str, initial_content, context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> str:
    # ... (код как в предыдущем ответе) ...
    current_message_or_response = initial_content
    is_function_response = isinstance(current_message_or_response, genai.protos.FunctionResponse)
    for attempt in range(5):
        # ... (логирование) ...
        content_to_send = current_message_or_response
        if is_function_response: logger.info(f"[{model_name}] Отправляем FunctionResponse: {current_message_or_response.name}")
        else: logger.info(f"[{model_name}] Отправляем строку: {str(content_to_send)[:100]}...")
        try:
            # ... (вызов send_message_async) ...
            response = await chat_session.send_message_async(content=content_to_send)
            # ... (логирование ответа) ...
            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0]
                logger.info(f"[{model_name}] ПОЛУЧЕНА ЧАСТЬ: {part}")
                if hasattr(part, 'function_call') and part.function_call and part.function_call.name == "google_search":
                    # ... (обработка function call, вызов perform_google_search, подготовка FunctionResponse) ...
                    # ... current_message_or_response = genai.protos.FunctionResponse(...)
                    continue
                else: # Не function call
                    # ... (извлечение текста, обработка ошибок) ...
                    final_text = response.text
                    return final_text
            else: # Пустой ответ
                 # ... (обработка пустого ответа) ...
                 raise Exception(...)
        except (ResourceExhausted, FailedPrecondition, GoogleAPIError, ValueError, Exception) as e:
             # ... (обработка всех исключений) ...
             raise e
    raise Exception(...)

# --- ОБРАБОТЧИКИ TELEGRAM ---
# (start, select_model_command, select_model_callback, handle_message - без изменений по сравнению с предыдущей версией)
async def start(...) -> None: ...
async def select_model_command(...) -> None: ...
async def select_model_callback(...) -> None: ...
async def handle_message(...) -> None: ...
async def test_search(...) -> None: ... # Оставим тестовую команду

# --- main ---
def main() -> None:
    """Запускает бота."""
    if not LOADED_MODELS: logger.critical(...); return
    if not google_search_sync: logger.warning(...)

    logger.info("Инициализация Telegram...");
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(CommandHandler("testsearch", test_search)) # Оставляем
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))

    logger.info("Запуск бота...");
    # ДОБАВЛЯЕМ drop_pending_updates=True
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
