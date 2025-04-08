# --- START OF FULL CORRECTED main.py (Introspection, Part commented out, SyntaxError fixed) ---

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

# --- ДОБАВЛЕНА ИНТРОСПЕКЦИЯ ---
logger.info("--- Inspecting 'genai' module ---")
try:
    logger.info(f"genai.__version__: {getattr(genai, '__version__', 'N/A')}")
    logger.info(f"dir(genai): {dir(genai)}")
    if hasattr(genai, 'types'):
        logger.info("genai.types exists.")
        logger.info(f"dir(genai.types): {dir(genai.types)}")
        if hasattr(genai.types, 'Part'):
             logger.info("!!!! genai.types.Part IS FOUND via hasattr !!!!")
        else:
             logger.warning("!!!! genai.types.Part NOT FOUND via hasattr !!!!")
    else:
        logger.info("'genai' has no attribute 'types'")
    if hasattr(genai, 'Part'):
        logger.info("!!!! genai.Part IS FOUND via hasattr !!!!")
    else:
        logger.warning("!!!! genai.Part NOT FOUND via hasattr !!!!")
except Exception as inspect_e:
    logger.error(f"Error inspecting 'genai': {inspect_e}")
logger.info("--- End Inspecting 'genai' module ---")
# --- КОНЕЦ ИНТРОСПЕКЦИИ ---

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
PRIMARY_MODEL_NAME = 'gemini-2.5-pro-preview-03-25'
SECONDARY_MODEL_NAME = 'gemini-2.0-flash-thinking-exp-01-21' # Проверьте!

# --- Определение инструмента Google Search ---
google_search_tool = None
if google_search_sync:
    google_search_func = genai.protos.FunctionDeclaration(
        name="google_search", description="Поиск Google...",
        parameters=genai.protos.Schema(type=genai.protos.Type.OBJECT, properties={"query": genai.protos.Schema(type=genai.protos.Type.STRING)}, required=["query"])
    )
    google_search_tool = genai.protos.Tool(function_declarations=[google_search_func])
    logger.info("Инструмент Google Search определен.")
else:
    logger.warning("Инструмент Google Search не доступен.")

# --- Настройка Gemini ---
primary_model = None; secondary_model = None
gemini_tools = [google_search_tool] if google_search_tool else None
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    primary_model = genai.GenerativeModel(PRIMARY_MODEL_NAME, generation_config={"temperature": 1}, system_instruction="Ваша системная инструкция...", tools=gemini_tools)
    logger.info(f"Основная модель {PRIMARY_MODEL_NAME} ... сконфигурирована.")
    secondary_model = genai.GenerativeModel(SECONDARY_MODEL_NAME, generation_config={"temperature": 1}, system_instruction="Ваша системная инструкция...", tools=gemini_tools)
    logger.info(f"Запасная модель {SECONDARY_MODEL_NAME} ... сконфигурирована.")
except (GoogleAPIError, Exception) as e:
    logger.exception(f"Критическая ошибка конфигурации Gemini: {e}")
    exit("Ошибка настройки Gemini")

# --- Инициализация ИСТОРИЙ ЧАТА ---
primary_chat_histories = {}; secondary_chat_histories = {}

# --- Функция выполнения поиска Google ---
async def perform_google_search(query: str, num_results: int = 5) -> str:
    if not google_search_sync: return "Ошибка: Функция поиска недоступна."
    logger.info(f"Выполнение Google поиска по запросу: '{query}'")
    try:
        search_results = await asyncio.to_thread(google_search_sync, query, num_results=num_results, stop=num_results, lang="ru")
        results_list = list(search_results)
        if not results_list: return "Поиск Google не дал результатов."
        formatted_results = f"Результаты поиска Google по запросу '{query}':\n" + "".join(f"{i}. {r}\n" for i, r in enumerate(results_list, 1))
        logger.info(f"Поиск Google по '{query}' вернул {len(results_list)} ссылок.")
        return formatted_results[:1500]
    except Exception as e:
        logger.exception(f"Ошибка во время поиска Google '{query}': {e}")
        return f"Ошибка при выполнении поиска Google: {e}"

# --- Вспомогательная функция для обработки хода Gemini ---
async def process_gemini_chat_turn(
    chat_session, model_name: str, initial_content, context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> str:
    """Обрабатывает один ход диалога с Gemini, включая Function Calling."""
    current_content = initial_content
    # --- ВРЕМЕННО ЗАКОММЕНТИРОВАНО ИСПОЛЬЗОВАНИЕ genai.Part ---
    is_function_response = False # Временно
    logger.warning("!!!! ВРЕМЕННО ОТКЛЮЧЕНА ПРОВЕРКА isinstance(..., genai.Part) !!!!")
    # --- КОНЕЦ ВРЕМЕННОГО ИЗМЕНЕНИЯ ---

    for attempt in range(5):
        logger.info(f"[{model_name}] Отправка {'ответа на функцию (предположительно)' if is_function_response else 'сообщения'}...")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        try:
            response = await chat_session.send_message_async(content=current_content)
            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0]
                if part.function_call and part.function_call.name == "google_search":
                    # --- ВРЕМЕННО ЗАКОММЕНТИРОВАНО ИСПОЛЬЗОВАНИЕ genai.Part ---
                    logger.error(f"[{model_name}] Запрошен вызов функции, НО ОБРАБОТКА genai.Part ВРЕМЕННО ОТКЛЮЧЕНА!")
                    # Этот код ниже УПАДЕТ с AttributeError, если genai.Part недоступен
                    # ПРИМЕР: current_content = genai.Part.from_function_response(...)
                    return "Ошибка: Обработка внутренних функций временно отключена (genai.Part)."
                    # --- КОНЕЦ ВРЕМЕННОГО ИЗМЕНЕНИЯ ---
                else: # Не function call
                    try:
                        final_text = response.text
                        logger.info(f"[{model_name}] Получен финальный ответ.")
                        return final_text
                    except ValueError as e: # Блокировка
                         reason = getattr(response.prompt_feedback, 'block_reason', 'Неизвестно') if hasattr(response, 'prompt_feedback') else 'Неизвестно'
                         raise ValueError(f"Ответ модели {model_name} заблокирован. Причина: {reason}") from e
            else: # Пустой ответ
                 reason = getattr(response.prompt_feedback, 'block_reason', 'Неизвестно') if hasattr(response, 'prompt_feedback') else 'Неизвестно'
                 if reason != 'BLOCK_REASON_UNSPECIFIED':
                     raise ValueError(f"Ответ модели {model_name} пуст и заблокирован. Причина: {reason}")
                 raise Exception(f"Модель {model_name} вернула пустой ответ.")
        except (ResourceExhausted, FailedPrecondition, GoogleAPIError) as e:
             logger.error(f"[{model_name}] Ошибка API: {e}")
             raise e
        except ValueError as ve: # Уже ошибка блокировки
             logger.error(f"Перехвачена ошибка блокировки от {model_name}: {ve}")
             raise ve
        except AttributeError as ae: # Ловим AttributeError здесь
            logger.error(f"!!!! AttributeError ВНУТРИ process_gemini_chat_turn: {ae} !!!!")
            logger.error("!!!! Скорее всего, проблема с доступом к genai.Part сохраняется !!!!")
            raise ae
        except Exception as e:
             logger.exception(f"[{model_name}] Непредвиденная ошибка: {e}")
             raise e
    raise Exception(f"Превышен лимит ({attempt+1}) обработки функций для {model_name}.")


# --- Обработчики Telegram ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in primary_chat_histories: del primary_chat_histories[chat_id]
    if chat_id in secondary_chat_histories: del secondary_chat_histories[chat_id]
    logger.info(f"Истории чатов сброшены для {chat_id}")
    search_status = "включен (если нужна)" if google_search_tool else "отключен"
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Gemini бот ({PRIMARY_MODEL_NAME}).\n"
        f"🔍 Поиск Google {search_status} для обеих моделей.\n"
        f"⚡ При перегрузке основной модели используется запасная ({SECONDARY_MODEL_NAME}).\n"
        f"⚠️ Бесплатные лимиты основной модели малы!",
        reply_to_message_id=update.message.message_id
    )
    logger.info(f"/start от {user.id}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")
    if not primary_model or not secondary_model:
        await update.message.reply_text("Ошибка: Модели не готовы."); return
    final_text: Optional[str] = None; used_fallback: bool = False; error_message: Optional[str] = None

    try: # --- Попытка с основной моделью ---
        if chat_id not in primary_chat_histories:
            primary_chat_histories[chat_id] = primary_model.start_chat(history=[])
            logger.info(f"Начат основной чат {chat_id}")
        primary_chat = primary_chat_histories[chat_id]
        logger.info(f"Попытка с {PRIMARY_MODEL_NAME}")
        final_text = await process_gemini_chat_turn(primary_chat, PRIMARY_MODEL_NAME, user_message, context, chat_id)

    except ResourceExhausted as e_primary:
        logger.warning(f"{PRIMARY_MODEL_NAME} квота: {e_primary}"); used_fallback = True
    except FailedPrecondition as e_precondition:
        # ИСПРАВЛЕНО
        logger.error(f"{PRIMARY_MODEL_NAME} FailedPrecondition: {e_precondition}. Сброс.")
        error_message = "⚠️ История чата слишком длинная. Я ее сбросил. Повторите запрос."
        if chat_id in primary_chat_histories:
            del primary_chat_histories[chat_id]
        if chat_id in secondary_chat_histories:
            del secondary_chat_histories[chat_id]
    except ValueError as e_blocked:
        logger.warning(f"{PRIMARY_MODEL_NAME} блок: {e_blocked}"); error_message = f"⚠️ {e_blocked}"
    except AttributeError as ae_outer:
        logger.error(f"!!!! AttributeError ВНЕШНИЙ (осн. модель): {ae_outer} !!!!"); error_message = f"Ошибка атрибута (осн. модель): {ae_outer}"
    except (GoogleAPIError, Exception) as e_primary_other:
        logger.exception(f"Ошибка {PRIMARY_MODEL_NAME}: {e_primary_other}"); error_message = f"Ошибка осн. модели: {e_primary_other}"

    if used_fallback: # --- Попытка с запасной моделью ---
        logger.info(f"Переключение на {SECONDARY_MODEL_NAME}")
        try:
            if chat_id not in secondary_chat_histories:
                secondary_chat_histories[chat_id] = secondary_model.start_chat(history=[])
                logger.info(f"Начат зап. чат {chat_id}")
            secondary_chat = secondary_chat_histories[chat_id]
            logger.info(f"Попытка с {SECONDARY_MODEL_NAME}")
            final_text = await process_gemini_chat_turn(secondary_chat, SECONDARY_MODEL_NAME, user_message, context, chat_id)
            error_message = None # Успех

        except ResourceExhausted as e_secondary:
            logger.error(f"{SECONDARY_MODEL_NAME} ТОЖЕ квота: {e_secondary}"); error_message = f"😔 Обе модели ({PRIMARY_MODEL_NAME}, {SECONDARY_MODEL_NAME}) перегружены."
        except FailedPrecondition as e_precondition_fallback:
             # ИСПРАВЛЕНО
             logger.error(f"{SECONDARY_MODEL_NAME} FailedPrecondition: {e_precondition_fallback}. Сброс.")
             error_message = "⚠️ История чата с запасной моделью стала слишком длинной и была сброшена. Попробуйте еще раз."
             if chat_id in secondary_chat_histories:
                 del secondary_chat_histories[chat_id]
        except ValueError as e_blocked_fallback:
             logger.warning(f"{SECONDARY_MODEL_NAME} блок: {e_blocked_fallback}"); error_message = f"⚠️ {e_blocked_fallback}"
        except AttributeError as ae_fallback:
             logger.error(f"!!!! AttributeError FALLBACK: {ae_fallback} !!!!"); error_message = f"Ошибка атрибута зап. модели: {ae_fallback}"
        except (GoogleAPIError, Exception) as e_fallback_other:
             logger.exception(f"Ошибка {SECONDARY_MODEL_NAME}: {e_fallback_other}"); error_message = f"Ошибка зап. модели: {e_fallback_other}"

    # --- Отправка ответа или сообщения об ошибке ---
    if final_text:
        bot_response = final_text[:4090]; prefix = f"⚡️ [{SECONDARY_MODEL_NAME}]:\n" if used_fallback else ""
        try: await update.message.reply_text(f"{prefix}{bot_response}", reply_to_message_id=update.message.message_id); logger.info(f"Ответ{' (fallback)' if used_fallback else ''} отправлен {user.id}")
        except Exception as e: logger.exception(f"Ошибка отправки: {e}"); try: await update.message.reply_text("Не смог отправить ответ AI.", reply_to_message_id=update.message.message_id) except Exception: pass
    elif error_message:
        try: await update.message.reply_text(error_message, reply_to_message_id=update.message.message_id); logger.info(f"Сообщение об ошибке: {error_message[:100]}...")
        except Exception as e: logger.error(f"Не удалось отправить ошибку '{error_message[:100]}...': {e}")
    else:
        logger.warning(f"Нет текста и ошибки для {chat_id}.");
        if error_message is None: # Строгая проверка перед отправкой стандартной ошибки
            try: await update.message.reply_text("Не удалось обработать запрос (неизвестная причина).", reply_to_message_id=update.message.message_id)
            except Exception: pass


# --- main ---
def main() -> None:
    """Запускает бота."""
    if not primary_model or not secondary_model:
         logger.critical("Модели не инициализированы."); print("Крит. ошибка: Модели не готовы."); return
    if not google_search_sync:
         logger.warning("Запуск БЕЗ поиска Google.")
    logger.info("Инициализация Telegram..."); application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start)); application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Запуск бота..."); application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
