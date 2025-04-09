# --- START OF FULL CORRECTED main.py (Detailed Logging for Function Call) ---

import logging
import os
import asyncio
import google.generativeai as genai
import time
import random
from typing import Optional, Tuple, Union

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Исключения
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
# Библиотека Telegram
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
# Библиотека для поиска Google
try:
    from googlesearch import search as google_search_sync
except ImportError:
    print("Библиотека googlesearch-python не найдена...")
    google_search_sync = None
else:
    if not callable(google_search_sync):
        print("Проблема с импортом googlesearch...")
        google_search_sync = None

# Gemini Function Calling типы
from google.protobuf.struct_pb2 import Struct

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY: exit("Google API ключ не найден")

# --- Имена моделей ---
# ВАЖНО: СДЕЛАЕМ FLASH ОСНОВНОЙ ДЛЯ ТЕСТА СТАБИЛЬНОСТИ FUNCTION CALLING
PRIMARY_MODEL_NAME = 'gemini-2.0-flash-001' # <-- Используем Flash как основную!
SECONDARY_MODEL_NAME = 'gemini-2.5-pro-exp-03-25' # <-- Pro как запасную

# --- Определение инструмента Google Search для Gemini ---
google_search_tool = None
if google_search_sync:
    google_search_func = genai.protos.FunctionDeclaration(
        name="google_search",
        description="Получает актуальную информацию из поиска Google по заданному запросу. Используй, когда нужна свежая информация, специфические факты, события или данные, которых может не быть во внутренних знаниях.",
        parameters=genai.protos.Schema(
            type=genai.protos.Type.OBJECT,
            properties={"query": genai.protos.Schema(type=genai.protos.Type.STRING, description="Поисковый запрос для Google")},
            required=["query"]
        )
    )
    google_search_tool = genai.protos.Tool(function_declarations=[google_search_func])
    logger.info("Инструмент Google Search для Gemini определен.")
else:
    logger.warning("Инструмент Google Search НЕ будет доступен...")

# --- Настройка Gemini ---
primary_model = None
secondary_model = None
gemini_tools = [google_search_tool] if google_search_tool else None
try:
    genai.configure(api_key=GOOGLE_API_KEY)

    system_instruction_text = (
        "Отвечай в пределах 2000 знаков... " # Ваша инструкция
        "ВАЖНО: Если вопрос касается текущих событий, актуальных фактов, действующих лиц (например, 'кто сейчас президент', 'какая погода', 'последние новости', 'результаты матча'), "
        "обязательно используй инструмент google_search для получения самой свежей информации перед тем, как дать ответ."
    )

    primary_model = genai.GenerativeModel(
        PRIMARY_MODEL_NAME, # Теперь Flash
        generation_config={"temperature": 0.8, "top_p": 1, "top_k": 40, "max_output_tokens": 2048}, # Чуть другие параметры для Flash
        system_instruction=system_instruction_text,
        tools=gemini_tools
    )
    logger.info(f"Основная модель Gemini ('{PRIMARY_MODEL_NAME}') [Search: {'Enabled' if gemini_tools else 'Disabled'}] сконфигурирована.")

    secondary_model = genai.GenerativeModel(
        SECONDARY_MODEL_NAME, # Теперь Pro
        generation_config={"temperature": 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
        system_instruction=system_instruction_text,
        tools=gemini_tools
    )
    logger.info(f"Запасная модель Gemini ('{SECONDARY_MODEL_NAME}') [Search: {'Enabled' if gemini_tools else 'Disabled'}] сконфигурирована.")

except GoogleAPIError as e:
    logger.exception(f"Критическая ошибка при конфигурации Gemini API: {e}")
    exit(f"Не удалось настроить Gemini (API Error): {e}")
except Exception as e:
    logger.exception("Критическая ошибка при инициализации моделей Gemini!")
    exit(f"Не удалось настроить Gemini (General Error): {e}")

# --- Инициализация ИСТОРИЙ ЧАТА ---
primary_chat_histories = {}
secondary_chat_histories = {}

# --- Функция выполнения поиска Google ---
async def perform_google_search(query: str, num_results: int = 5) -> str:
    if not google_search_sync:
        logger.warning("!!!! Попытка поиска Google, но библиотека не установлена.")
        return "Ошибка: Функция поиска недоступна."
    logger.info(f"!!!! Начало выполнения Google поиска по запросу: '{query}'")
    try:
        # Выполняем синхронную функцию в отдельном потоке
        search_results = await asyncio.to_thread(
            google_search_sync, query, num_results=num_results, stop=num_results, lang="ru"
        )
        results_list = list(search_results) # Преобразуем генератор
        if not results_list:
            logger.warning(f"!!!! Google поиск по '{query}' не дал результатов.")
            return "Поиск Google не дал результатов по данному запросу."

        formatted_results = f"Результаты поиска Google по запросу '{query}':\n" + "".join(f"{i}. {r}\n" for i, r in enumerate(results_list, 1))
        logger.info(f"!!!! Поиск Google по '{query}' успешно вернул {len(results_list)} ссылок.")
        return formatted_results[:1500] # Ограничиваем длину

    except Exception as e:
        # Логируем именно ошибку поиска
        logger.exception(f"!!!! ОШИБКА во время выполнения Google поиска по запросу '{query}': {e}")
        return f"Ошибка при выполнении поиска Google: {e}"

# --- Вспомогательная функция для обработки хода Gemini ---
async def process_gemini_chat_turn(
    chat_session,
    model_name: str,
    initial_content,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int
) -> str:
    """Обрабатывает один ход диалога с Gemini, включая Function Calling (для v0.7.1)."""
    current_message_or_response = initial_content
    is_function_response = False # Определим внутри цикла

    for attempt in range(5):
        # Определяем, отправляем мы ответ на функцию или исходное сообщение
        # Используем genai.protos.FunctionResponse, т.к. current_message_or_response будет им в цикле
        is_function_response = isinstance(current_message_or_response, genai.protos.FunctionResponse)
        logger.info(f"[{model_name}] Итерация {attempt+1}. Отправка {'ОТВЕТА НА ФУНКЦИЮ' if is_function_response else 'СООБЩЕНИЯ'}.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # Готовим контент для отправки
        content_to_send = None
        if is_function_response:
            try:
                # Оборачиваем FunctionResponse в Part
                content_to_send = genai.protos.Part(function_response=current_message_or_response)
                logger.info(f"[{model_name}] Упаковываем FunctionResponse в Part: {content_to_send}")
            except Exception as e:
                logger.exception(f"[{model_name}] !!! Ошибка упаковки FunctionResponse в Part: {e}")
                raise RuntimeError("Ошибка упаковки ответа функции") from e
        else:
            content_to_send = current_message_or_response
            logger.info(f"[{model_name}] Отправляем как есть (строка): {str(content_to_send)[:100]}...") # Логируем начало строки

        if content_to_send is None:
             raise ValueError("Не удалось подготовить контент для отправки")

        try:
            logger.info(f"[{model_name}] !!! НАЧАЛО вызова send_message_async...")
            response = await chat_session.send_message_async(content=content_to_send)
            logger.info(f"[{model_name}] !!! ЗАВЕРШЕНИЕ вызова send_message_async.")

            # Проверка ответа
            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0]
                logger.info(f"[{model_name}] ПОЛУЧЕНА ЧАСТЬ ОТВЕТА: {part}")

                if hasattr(part, 'function_call') and part.function_call and part.function_call.name == "google_search":
                    function_call = part.function_call
                    logger.info(f"[{model_name}] !!!! ОБНАРУЖЕН ВЫЗОВ ФУНКЦИИ google_search.")

                    # --- Логика обработки Function Call ---
                    if not google_search_tool:
                         logger.error(f"[{model_name}] !!! Инструмент поиска не настроен, хотя был запрошен!")
                         s_err = Struct(); s_err.update({"content": "Ошибка: Функция поиска не настроена."})
                         current_message_or_response = genai.protos.FunctionResponse(name="google_search", response=s_err)
                         # is_function_response = True # уже будет True для след. итерации
                         continue

                    args = {key: value for key, value in function_call.args.items()}
                    query = args.get("query")
                    logger.info(f"[{model_name}] Извлечен поисковый запрос: '{query}'")

                    if query:
                        # Вызываем функцию поиска
                        logger.info(f"[{model_name}] !!! НАЧАЛО вызова perform_google_search...")
                        search_result = await perform_google_search(query)
                        logger.info(f"[{model_name}] !!! ЗАВЕРШЕНИЕ вызова perform_google_search. Результат (начало): {search_result[:100]}...")

                        # Готовим ответ для Gemini
                        s_res = Struct(); s_res.update({"content": search_result})
                        current_message_or_response = genai.protos.FunctionResponse(name="google_search", response=s_res)
                        logger.info(f"[{model_name}] Подготовлен FunctionResponse для отправки.")
                        # is_function_response = True
                        continue # К следующей итерации
                    else: # Нет query
                         logger.warning(f"[{model_name}] !!! Вызов google_search без параметра 'query'.")
                         s_err = Struct(); s_err.update({"content": "Ошибка: Параметр 'query' не предоставлен."})
                         current_message_or_response = genai.protos.FunctionResponse(name="google_search", response=s_err)
                         # is_function_response = True
                         continue
                    # --- Конец логики обработки Function Call ---

                else: # Не function call - финальный ответ
                    try:
                        logger.info(f"[{model_name}] Это не вызов функции, извлекаем текст...")
                        final_text = response.text
                        logger.info(f"[{model_name}] Получен финальный текстовый ответ.")
                        return final_text
                    except ValueError as e: # Блокировка
                        logger.warning(f"[{model_name}] Ошибка извлечения текста (блокировка?): {e}")
                        reason = getattr(response.prompt_feedback, 'block_reason', 'Неизвестно') if hasattr(response, 'prompt_feedback') else 'Неизвестно'
                        raise ValueError(f"Ответ модели {model_name} заблокирован. Причина: {reason}") from e
                    except AttributeError: # Нет .text
                        logger.warning(f"[{model_name}] !!! Ответ не содержит атрибута .text")
                        # ... (попытка собрать из частей, как раньше) ...
                        try:
                            final_text = "".join(p.text for p in response.parts if hasattr(p, 'text'))
                            if final_text: return final_text
                            else: raise Exception("Нет текста в .parts")
                        except Exception as e_inner: raise Exception("Не удалось извлечь текст") from e_inner

            else: # Пустой ответ без частей
                 logger.warning(f"[{model_name}] !!! Получен пустой ответ без кандидатов/частей.")
                 reason = getattr(response.prompt_feedback, 'block_reason', 'Неизвестно') if hasattr(response, 'prompt_feedback') else 'Неизвестно'
                 if reason != 'BLOCK_REASON_UNSPECIFIED': raise ValueError(f"Пустой ответ {model_name} заблокирован: {reason}")
                 raise Exception(f"Модель {model_name} вернула пустой ответ.")

        except (ResourceExhausted, FailedPrecondition, GoogleAPIError) as e:
             logger.error(f"[{model_name}] !!! Ошибка API: {e}")
             raise e
        except ValueError as ve: # Блокировка
             logger.error(f"[{model_name}] !!! Ошибка ValueError (блокировка?): {ve}")
             raise ve
        except Exception as e:
             logger.exception(f"[{model_name}] !!! Непредвиденная ошибка в цикле: {e}")
             raise e # Передаем выше

    # Если вышли из цикла
    logger.error(f"[{model_name}] !!! Превышен лимит ({attempt+1}) обработки функций.")
    raise Exception(f"Превышен лимит обработки функций для модели {model_name}.")


# --- Обработчики Telegram ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код start без изменений) ...
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in primary_chat_histories: del primary_chat_histories[chat_id]
    if chat_id in secondary_chat_histories: del secondary_chat_histories[chat_id]
    logger.info(f"Истории чатов сброшены для {chat_id}")
    search_status = "включен (если нужна)" if google_search_tool else "отключен"
    # Обновим сообщение start, т.к. поменяли модели местами
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Gemini бот (Модель: {PRIMARY_MODEL_NAME}).\n"
        f"🔍 Поиск Google {search_status} для обеих моделей.\n"
        f"⚡ При перегрузке используется запасная ({SECONDARY_MODEL_NAME}).\n"
        f"⚠️ Лимиты запасной модели малы!",
        reply_to_message_id=update.message.message_id
    )
    logger.info(f"/start от {user.id}")

# ДОБАВЛЯЕМ ТЕСТОВУЮ КОМАНДУ /testsearch
async def test_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Тестирует функцию perform_google_search."""
    query = " ".join(context.args)
    chat_id = update.effective_chat.id
    if not query:
        await update.message.reply_text("Пожалуйста, укажите поисковый запрос после команды /testsearch.")
        return

    logger.info(f"Тестовый поиск для чата {chat_id} по запросу: '{query}'")
    await update.message.reply_text(f"Выполняю тестовый поиск по запросу: '{query}'...")
    try:
        search_result = await perform_google_search(query)
        logger.info(f"Тестовый поиск для чата {chat_id} вернул: {search_result[:200]}...")
        # Отправляем результат поиска, обрезая для лимитов Telegram
        await update.message.reply_text(f"Результат тестового поиска:\n\n{search_result[:4000]}", parse_mode=ParseMode.HTML) # Можно попробовать HTML для ссылок, если библиотека их так вернет
    except Exception as e:
        logger.exception(f"Ошибка во время выполнения тестового поиска для чата {chat_id}: {e}")
        await update.message.reply_text(f"Ошибка во время тестового поиска: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код handle_message без изменений в логике, используем последнюю рабочую версию с правильными отступами) ...
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")
    if not primary_model or not secondary_model: await update.message.reply_text("Ошибка: Модели не готовы."); return
    final_text: Optional[str] = None; used_fallback: bool = False; error_message: Optional[str] = None

    try: # --- Основная модель (теперь Flash) ---
        if chat_id not in primary_chat_histories: primary_chat_histories[chat_id] = primary_model.start_chat(history=[]); logger.info(f"Начат основной чат {chat_id}")
        primary_chat = primary_chat_histories[chat_id]; logger.info(f"Попытка с {PRIMARY_MODEL_NAME}")
        final_text = await process_gemini_chat_turn(primary_chat, PRIMARY_MODEL_NAME, user_message, context, chat_id)
    except ResourceExhausted as e_primary: logger.warning(f"{PRIMARY_MODEL_NAME} квота исчерпана: {e_primary}"); used_fallback = True
    except FailedPrecondition as e_precondition: logger.error(f"{PRIMARY_MODEL_NAME} FailedPrecondition: {e_precondition}. Сброс."); error_message = "..."; del primary_chat_histories[chat_id]; if chat_id in secondary_chat_histories: del secondary_chat_histories[chat_id]
    except ValueError as e_blocked: logger.warning(f"{PRIMARY_MODEL_NAME} блокировка: {e_blocked}"); error_message = f"⚠️ {e_blocked}"
    except (GoogleAPIError, Exception) as e_primary_other: logger.exception(f"Ошибка {PRIMARY_MODEL_NAME}: {e_primary_other}"); error_message = f"Ошибка основной модели: {e_primary_other}"

    if used_fallback: # --- Запасная модель (теперь Pro) ---
        logger.info(f"Переключение на {SECONDARY_MODEL_NAME}")
        try:
            if chat_id not in secondary_chat_histories: secondary_chat_histories[chat_id] = secondary_model.start_chat(history=[]); logger.info(f"Начат запасной чат {chat_id}")
            secondary_chat = secondary_chat_histories[chat_id]; logger.info(f"Попытка с {SECONDARY_MODEL_NAME}")
            final_text = await process_gemini_chat_turn(secondary_chat, SECONDARY_MODEL_NAME, user_message, context, chat_id)
            error_message = None # Успех
        except ResourceExhausted as e_secondary: logger.error(f"{SECONDARY_MODEL_NAME} ТОЖЕ квота исчерпана: {e_secondary}"); error_message = f"😔 Обе AI модели ({PRIMARY_MODEL_NAME}, {SECONDARY_MODEL_NAME}) сейчас перегружены."
        except FailedPrecondition as e_precondition_fallback: logger.error(f"{SECONDARY_MODEL_NAME} FailedPrecondition: {e_precondition_fallback}. Сброс."); error_message = "..."; if chat_id in secondary_chat_histories: del secondary_chat_histories[chat_id]
        except ValueError as e_blocked_fallback: logger.warning(f"{SECONDARY_MODEL_NAME} блокировка: {e_blocked_fallback}"); error_message = f"⚠️ {e_blocked_fallback}"
        except (GoogleAPIError, Exception) as e_fallback_other: logger.exception(f"Ошибка {SECONDARY_MODEL_NAME}: {e_fallback_other}"); error_message = f"Ошибка запасной модели: {e_fallback_other}"

    # --- Отправка ответа ---
    if final_text:
        bot_response = final_text[:4090]; prefix = f"⚡️ [{SECONDARY_MODEL_NAME}]:\n" if used_fallback else "" # Поправил префикс
        try: await update.message.reply_text(f"{prefix}{bot_response}", reply_to_message_id=update.message.message_id); logger.info(f"Ответ{' (fallback)' if used_fallback else ''} отправлен {user.id}")
        except Exception as e: logger.exception(f"Ошибка отправки ответа: {e}"); try: await update.message.reply_text("Не смог отправить ответ AI.", reply_to_message_id=update.message.message_id) except Exception: pass
    elif error_message:
        try: await update.message.reply_text(error_message, reply_to_message_id=update.message.message_id); logger.info(f"Сообщение об ошибке отправлено: {error_message[:100]}...")
        except Exception as e: logger.error(f"Не удалось отправить сообщение об ошибке '{error_message[:100]}...': {e}")
    else: logger.warning(f"Нет финального текста и сообщения об ошибке для {chat_id}."); if "История чата" not in (error_message or "") and "Ответ модели" not in (error_message or "") : try: await update.message.reply_text("Не удалось обработать запрос.", reply_to_message_id=update.message.message_id) except Exception: pass


# --- main ---
def main() -> None:
    """Запускает бота."""
    if not primary_model or not secondary_model:
         logger.critical("Модели не инициализированы."); print("Крит. ошибка: Модели не готовы."); return
    if not google_search_sync:
         logger.warning("Запуск БЕЗ поиска Google.")
    logger.info("Инициализация Telegram..."); application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    # Добавляем обработчик для /testsearch
    application.add_handler(CommandHandler("testsearch", test_search))
    application.add_handler(CommandHandler("start", start)); application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Запуск бота..."); application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
