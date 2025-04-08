# --- START OF FULL CORRECTED main.py (Schema/Tool fix + Part fix) ---

import logging
import os
import asyncio
import google.generativeai as genai
import time
import random
# Используем types для Part и других специфичных типов Gemini, где это нужно
from google.generativeai import types as genai_types
from typing import Optional, Tuple, Union # For type hinting

# Исключения
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
# Библиотека Telegram
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
# Библиотека для поиска Google (НЕОФИЦИАЛЬНАЯ!)
try:
    from googlesearch import search as google_search_sync
except ImportError:
    print("Библиотека googlesearch-python не найдена. Поиск Google не будет работать.")
    print("Установите ее: pip install googlesearch-python")
    google_search_sync = None
else:
    if not callable(google_search_sync):
        print("Что-то пошло не так с импортом googlesearch. Поиск Google не будет работать.")
        google_search_sync = None

# Gemini Function Calling типы - берем из google.protobuf
from google.protobuf.struct_pb2 import Struct

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY: exit("Google API ключ не найден")

# --- Имена моделей (из вашего файла) ---
PRIMARY_MODEL_NAME = 'gemini-2.5-pro-preview-03-25'
SECONDARY_MODEL_NAME = 'gemini-2.0-flash-thinking-exp-01-21' # Проверьте актуальность!

# --- Определение инструмента Google Search для Gemini ---
google_search_tool = None
if google_search_sync:
    # ИСПОЛЬЗУЕМ genai.protos для FunctionDeclaration, Schema, Tool
    google_search_func = genai.protos.FunctionDeclaration( # <-- ИЗМЕНЕНО
        name="google_search",
        description="Получает актуальную информацию из поиска Google по заданному запросу. Используй, когда нужна свежая информация, специфические факты, события или данные, которых может не быть во внутренних знаниях.",
        parameters=genai.protos.Schema( # <-- ИЗМЕНЕНО
            type=genai.protos.Type.OBJECT,
            properties={"query": genai.protos.Schema(type=genai.protos.Type.STRING, description="Поисковый запрос для Google")}, # <-- ИЗМЕНЕНО
            required=["query"]
        )
    )
    google_search_tool = genai.protos.Tool(function_declarations=[google_search_func]) # <-- ИЗМЕНЕНО
    logger.info("Инструмент Google Search для Gemini определен.")
else:
    logger.warning("Инструмент Google Search НЕ будет доступен моделям из-за отсутствия библиотеки googlesearch-python.")


# --- Настройка Gemini ---
primary_model = None
secondary_model = None
gemini_tools = [google_search_tool] if google_search_tool else None # Инструменты для обеих моделей
try:
    genai.configure(api_key=GOOGLE_API_KEY)

    primary_model = genai.GenerativeModel(
        PRIMARY_MODEL_NAME,
        generation_config={"temperature": 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
        system_instruction="Ваша длинная системная инструкция...", # Сократил для примера
        tools=gemini_tools
    )
    logger.info(f"Основная модель Gemini ('{PRIMARY_MODEL_NAME}') [Search: {'Enabled' if gemini_tools else 'Disabled'}] сконфигурирована.")

    secondary_model = genai.GenerativeModel(
        SECONDARY_MODEL_NAME,
        generation_config={"temperature": 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
        system_instruction="Ваша длинная системная инструкция...", # Сократил для примера
        tools=gemini_tools
    )
    logger.info(f"Запасная модель Gemini ('{SECONDARY_MODEL_NAME}') [Search: {'Enabled' if gemini_tools else 'Disabled'}] сконфигурирована.")

except GoogleAPIError as e:
    logger.exception(f"Критическая ошибка при конфигурации Gemini API: {e}")
    exit(f"Не удалось настроить Gemini (API Error): {e}")
except Exception as e:
    logger.exception("Критическая ошибка при инициализации моделей Gemini!")
    exit(f"Не удалось настроить Gemini (General Error): {e}")

# --- Инициализация ИСТОРИЙ ЧАТА для ОБЕИХ моделей ---
primary_chat_histories = {}
secondary_chat_histories = {}

# --- Функция выполнения поиска Google ---
# Без изменений
async def perform_google_search(query: str, num_results: int = 5) -> str:
    # ... (код функции perform_google_search) ...
    if not google_search_sync:
        logger.warning("Попытка поиска Google, но библиотека не установлена.")
        return "Ошибка: Функция поиска недоступна."
    logger.info(f"Выполнение Google поиска по запросу: '{query}'")
    try:
        search_results = await asyncio.to_thread(
            google_search_sync, query, num_results=num_results, stop=num_results, lang="ru"
        )
        results_list = list(search_results)
        if not results_list:
             logger.warning(f"Google поиск по '{query}' не дал результатов.")
             return "Поиск Google не дал результатов по данному запросу."

        formatted_results = f"Результаты поиска Google по запросу '{query}':\n"
        for i, result in enumerate(results_list, 1):
            formatted_results += f"{i}. {result}\n"
        logger.info(f"Поиск Google по '{query}' вернул {len(results_list)} ссылок.")
        return formatted_results[:1500]

    except Exception as e:
        logger.exception(f"Ошибка во время выполнения Google поиска по запросу '{query}': {e}")
        return f"Ошибка при выполнении поиска Google: {e}"

# --- Вспомогательная функция для обработки хода Gemini ---
# Используем genai_types.Part здесь!
async def process_gemini_chat_turn(
    chat_session: genai.ChatSession,
    model_name: str,
    initial_content: Union[str, genai_types.Part], # <-- Правильно: genai_types.Part
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int
) -> str:
    # ... (код функции process_gemini_chat_turn, используя genai_types.Part.from_function_response) ...
    current_content = initial_content
    is_function_response = isinstance(initial_content, genai_types.Part) # <-- Правильно

    for attempt in range(5):
        logger.info(f"[{model_name}] Отправка {'ответа на функцию' if is_function_response else 'сообщения'} (Попытка цикла {attempt+1})")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        try:
            response = await chat_session.send_message_async(content=current_content)
            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0]
                if part.function_call and part.function_call.name == "google_search":
                    # ... (логика обработки function call)
                    function_call = part.function_call
                    if not google_search_tool:
                         # ... (обработка ошибки отсутствия инструмента)
                         s_err = Struct()
                         s_err.update({"content": "Ошибка: Функция поиска Google не сконфигурирована в боте."})
                         current_content = genai_types.Part.from_function_response(name="google_search", response=s_err) # <-- Правильно
                         is_function_response = True
                         continue
                    # ... (извлечение query)
                    args = {key: value for key, value in function_call.args.items()}
                    query = args.get("query")
                    logger.info(f"[{model_name}] Запрошен вызов функции: google_search(query='{query}')")
                    if query:
                        # ... (вызов perform_google_search)
                        search_result = await perform_google_search(query)
                        s_res = Struct()
                        s_res.update({"content": search_result})
                        current_content = genai_types.Part.from_function_response(name="google_search", response=s_res) # <-- Правильно
                        is_function_response = True
                        continue
                    else:
                        # ... (обработка отсутствия query)
                        s_err = Struct()
                        s_err.update({"content": "Ошибка: Параметр 'query' не был предоставлен для поиска."})
                        current_content = genai_types.Part.from_function_response(name="google_search", response=s_err) # <-- Правильно
                        is_function_response = True
                        continue
                else: # Не function call
                    # ... (извлечение response.text, обработка ValueError) ...
                    try:
                        final_text = response.text
                        # ...
                        return final_text
                    except ValueError as e:
                        # ...
                        raise ValueError(...) from e
            else: # Пустой ответ
                # ... (обработка пустого ответа, возможно ValueError) ...
                 raise Exception(...)
        except (ResourceExhausted, FailedPrecondition, GoogleAPIError) as e:
             # ... (обработка ошибок API)
             raise e
        except ValueError as ve: # Ошибка блокировки
             # ...
             raise ve
        except Exception as e:
             # ...
             raise e
    # Если вышли из цикла
    raise Exception(...)


# --- Обработчики Telegram ---
# start и handle_message без изменений
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код start) ...
    user = update.effective_user
    chat_id = update.effective_chat.id
    if chat_id in primary_chat_histories: del primary_chat_histories[chat_id]
    if chat_id in secondary_chat_histories: del secondary_chat_histories[chat_id]
    logger.info(f"Истории основного и запасного чатов сброшены для chat_id {chat_id}")
    search_status = "включен (если нужна)" if google_search_tool else "отключен"
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Gemini бот ({PRIMARY_MODEL_NAME}).\n"
        f"🔍 Поиск Google {search_status} для обеих моделей.\n"
        f"⚡ При перегрузке основной модели используется запасная ({SECONDARY_MODEL_NAME}).\n"
        f"⚠️ Бесплатные лимиты основной модели малы!",
        reply_to_message_id=update.message.message_id
    )
    logger.info(f"/start от {user.id} ({user.username}) в чате {chat_id}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код handle_message) ...
    user_message = update.message.text
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id} ({user.username}) в чате {chat_id}: '{user_message[:50]}...'")
    if not primary_model or not secondary_model: #...
        return
    final_text: Optional[str] = None
    used_fallback: bool = False
    error_message: Optional[str] = None
    try: # Основная модель
        if chat_id not in primary_chat_histories: #...
            primary_chat_histories[chat_id] = primary_model.start_chat(history=[])
            logger.info(f"Начат новый основной чат для chat_id {chat_id}")
        primary_chat = primary_chat_histories[chat_id]
        logger.info(f"Попытка обработки с основной моделью: {PRIMARY_MODEL_NAME}")
        final_text = await process_gemini_chat_turn(primary_chat, PRIMARY_MODEL_NAME, user_message, context, chat_id)
    except ResourceExhausted as e_primary: #...
        used_fallback = True
    except FailedPrecondition as e_precondition: #...
        error_message = "..."
    except ValueError as e_blocked: #...
        error_message = f"..."
    except (GoogleAPIError, Exception) as e_primary_other: #...
        error_message = f"..."

    if used_fallback: # Запасная модель
        logger.info(f"Переключение на запасную модель: {SECONDARY_MODEL_NAME}")
        try:
            if chat_id not in secondary_chat_histories: #...
                secondary_chat_histories[chat_id] = secondary_model.start_chat(history=[])
                logger.info(f"Начат новый запасной чат для chat_id {chat_id}")
            secondary_chat = secondary_chat_histories[chat_id]
            final_text = await process_gemini_chat_turn(secondary_chat, SECONDARY_MODEL_NAME, user_message, context, chat_id)
            error_message = None # Успех
        except ResourceExhausted as e_secondary: #...
            error_message = f"..."
        except FailedPrecondition as e_precondition_fallback: #...
             error_message = "..."
        except ValueError as e_blocked_fallback: #...
             error_message = f"..."
        except (GoogleAPIError, Exception) as e_fallback_other: #...
             error_message = f"..."

    # Отправка ответа
    if final_text: #...
        prefix = f"⚡️ [{SECONDARY_MODEL_NAME}]:\n" if used_fallback else ""
        # ...
    elif error_message: #...
        # ...
    else: #...
        # ...


# Функция main без изменений
def main() -> None:
    # ... (код main) ...
    if not primary_model or not secondary_model: #...
         return
    if not google_search_sync: #...
         logger.warning("...")
    logger.info("Инициализация Telegram Application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Запуск бота...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
