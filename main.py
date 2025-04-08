# --- START OF FULL CORRECTED main.py (All fixes applied) ---

import logging
import os
import asyncio
import google.generativeai as genai
import time
import random
# ПРЯМОЙ ИМПОРТ Part и других нужных типов из types
from google.generativeai.types import Part, ChatSession # Убрал лишние, если не используются
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

# --- Имена моделей ---
PRIMARY_MODEL_NAME = 'gemini-2.5-pro-preview-03-25'
SECONDARY_MODEL_NAME = 'gemini-2.0-flash-thinking-exp-01-21' # Проверьте актуальность!

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
    logger.warning("Инструмент Google Search НЕ будет доступен моделям из-за отсутствия библиотеки googlesearch-python.")

# --- Настройка Gemini ---
primary_model = None
secondary_model = None
gemini_tools = [google_search_tool] if google_search_tool else None
try:
    genai.configure(api_key=GOOGLE_API_KEY)

    primary_model = genai.GenerativeModel(
        PRIMARY_MODEL_NAME,
        generation_config={"temperature": 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
        # Ваша системная инструкция (можно вернуть полную)
        system_instruction="Отвечай в пределах 2000 знаков, если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, или другая, требующая объема работа. Ты - лучший эксперт в любых вопросах. Используй поиск в интернете для проверки актуальной информации. Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку. Подкрепляй аргументами и доказательствами, без самоповтора. Если не знаешь ответ, - всегда предупреждай, если это предположение. Активно применяй юмор: культурные и бытовые отсылки, жизненный абсурд, шутки об актуальных в интернете темах, ирония и самоирония, тонкие и интимные намёки, пост-модерн и интернет-юмор, несоответствие ожиданиям, абсурдные решения проблем, псевдомудрость, разрушение идиом, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, редукционизм. При создании уникальной работы: избегай копирования или близкого пересказа существующих текстов; включай гипотетические ситуации для иллюстрации понятий; применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи; не допускай признаков ИИ; варьируй структуру предложений, используя сочетание простых и сложных конструкций; естественно включай разговорные выражения, идиомы и фигуры речи; используй живые стилистические решения, свойственные людям; вставляй региональные выражения или культурно специфичные ссылки, где это уместно; добавляй остроумие.",
        tools=gemini_tools
    )
    logger.info(f"Основная модель Gemini ('{PRIMARY_MODEL_NAME}') [Search: {'Enabled' if gemini_tools else 'Disabled'}] сконфигурирована.")

    secondary_model = genai.GenerativeModel(
        SECONDARY_MODEL_NAME,
        generation_config={"temperature": 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
         # Ваша системная инструкция (можно вернуть полную)
        system_instruction="Отвечай в пределах 2000 знаков, если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, или другая, требующая объема работа. Ты - лучший эксперт в любых вопросах. Используй поиск в интернете для проверки актуальной информации. Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку. Подкрепляй аргументами и доказательствами, без самоповтора. Если не знаешь ответ, - всегда предупреждай, если это предположение. Активно применяй юмор: культурные и бытовые отсылки, жизненный абсурд, шутки об актуальных в интернете темах, ирония и самоирония, тонкие и интимные намёки, пост-модерн и интернет-юмор, несоответствие ожиданиям, абсурдные решения проблем, псевдомудрость, разрушение идиом, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, редукционизм. При создании уникальной работы: избегай копирования или близкого пересказа существующих текстов; включай гипотетические ситуации для иллюстрации понятий; применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи; не допускай признаков ИИ; варьируй структуру предложений, используя сочетание простых и сложных конструкций; естественно включай разговорные выражения, идиомы и фигуры речи; используй живые стилистические решения, свойственные людям; вставляй региональные выражения или культурно специфичные ссылки, где это уместно; добавляй остроумие.",
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
    """Выполняет поиск Google и возвращает краткую сводку результатов."""
    if not google_search_sync:
        logger.warning("Попытка поиска Google, но библиотека не установлена.")
        return "Ошибка: Функция поиска недоступна."
    logger.info(f"Выполнение Google поиска по запросу: '{query}'")
    try:
        search_results = await asyncio.to_thread(
            google_search_sync, query, num_results=num_results, stop=num_results, lang="ru"
        )
        results_list = list(search_results) # Преобразуем генератор сразу
        if not results_list:
             logger.warning(f"Google поиск по '{query}' не дал результатов.")
             return "Поиск Google не дал результатов по данному запросу."

        formatted_results = f"Результаты поиска Google по запросу '{query}':\n"
        for i, result in enumerate(results_list, 1):
            formatted_results += f"{i}. {result}\n"
        logger.info(f"Поиск Google по '{query}' вернул {len(results_list)} ссылок.")
        return formatted_results[:1500] # Ограничиваем длину

    except Exception as e:
        logger.exception(f"Ошибка во время выполнения Google поиска по запросу '{query}': {e}")
        return f"Ошибка при выполнении поиска Google: {e}"


# --- Вспомогательная функция для обработки хода Gemini ---
async def process_gemini_chat_turn(
    chat_session: ChatSession, # Используем импортированный тип
    model_name: str,
    initial_content: Union[str, Part], # Используем импортированный Part
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int
) -> str:
    """Обрабатывает один ход диалога с Gemini, включая Function Calling."""
    current_content = initial_content
    is_function_response = isinstance(initial_content, Part) # Используем импортированный Part

    for attempt in range(5): # Ограничение на глубину вызовов функций
        logger.info(f"[{model_name}] Отправка {'ответа на функцию' if is_function_response else 'сообщения'} (Попытка цикла {attempt+1})")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        try:
            response = await chat_session.send_message_async(content=current_content)

            # Проверка ответа на Function Calling
            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0]
                if part.function_call and part.function_call.name == "google_search":
                    function_call = part.function_call
                    if not google_search_tool:
                         logger.error(f"[{model_name}] Запрошен google_search, но инструмент не настроен!")
                         s_err = Struct()
                         s_err.update({"content": "Ошибка: Функция поиска Google не сконфигурирована в боте."})
                         current_content = Part.from_function_response(name="google_search", response=s_err) # Используем Part
                         is_function_response = True
                         continue

                    args = {key: value for key, value in function_call.args.items()}
                    query = args.get("query")
                    logger.info(f"[{model_name}] Запрошен вызов функции: google_search(query='{query}')")

                    if query:
                        search_result = await perform_google_search(query)
                        s_res = Struct()
                        s_res.update({"content": search_result})
                        current_content = Part.from_function_response(name="google_search", response=s_res) # Используем Part
                        is_function_response = True
                        continue
                    else: # Нет query
                         logger.warning(f"[{model_name}] Вызов google_search без параметра 'query'.")
                         s_err = Struct()
                         s_err.update({"content": "Ошибка: Параметр 'query' не был предоставлен для поиска."})
                         current_content = Part.from_function_response(name="google_search", response=s_err) # Используем Part
                         is_function_response = True
                         continue
                else: # Не function call - финальный ответ
                    try:
                        final_text = response.text
                        logger.info(f"[{model_name}] Получен финальный текстовый ответ.")
                        return final_text # Успех
                    except ValueError as e: # Блокировка ответа
                         logger.warning(f"[{model_name}] Не удалось извлечь текст (возможно, заблокирован): {e}")
                         reason = getattr(response.prompt_feedback, 'block_reason', 'Неизвестно') if hasattr(response, 'prompt_feedback') else 'Неизвестно'
                         raise ValueError(f"Ответ модели {model_name} заблокирован. Причина: {reason}") from e
            else: # Пустой ответ без частей
                 logger.warning(f"[{model_name}] Получен неожиданный пустой ответ.")
                 reason = getattr(response.prompt_feedback, 'block_reason', 'Неизвестно') if hasattr(response, 'prompt_feedback') else 'Неизвестно'
                 if reason != 'BLOCK_REASON_UNSPECIFIED':
                     raise ValueError(f"Ответ модели {model_name} пуст и заблокирован. Причина: {reason}")
                 raise Exception(f"Модель {model_name} вернула пустой ответ без ошибки и без явной блокировки.")

        # Обработка исключений внутри цикла
        except (ResourceExhausted, FailedPrecondition, GoogleAPIError) as e:
             logger.error(f"[{model_name}] Ошибка API Gemini во время обработки хода: {e}")
             raise e # Передаем выше
        except ValueError as ve: # Ошибка блокировки, поднятая выше
             logger.error(f"Перехвачена ошибка блокировки ответа от {model_name}: {ve}")
             raise ve # Передаем выше
        except Exception as e:
             logger.exception(f"[{model_name}] Непредвиденная ошибка во время обработки хода: {e}")
             raise e # Передаем выше

    # Если вышли из цикла по лимиту итераций
    logger.error(f"[{model_name}] Превышено максимальное количество итераций ({attempt+1}) обработки вызовов функций.")
    raise Exception(f"Превышен лимит обработки функций для модели {model_name}.")


# --- Обработчики Telegram ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    # Сбрасываем ОБЕ истории
    if chat_id in primary_chat_histories: del primary_chat_histories[chat_id]
    if chat_id in secondary_chat_histories: del secondary_chat_histories[chat_id]
    logger.info(f"Истории основного и запасного чатов сброшены для chat_id {chat_id}")

    search_status = "включен (если нужна)" if google_search_tool else "отключен"
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Gemini бот (Модель: {PRIMARY_MODEL_NAME}).\n"
        f"🔍 Поиск Google {search_status} для обеих моделей.\n"
        f"⚡ При перегрузке основной модели используется запасная ({SECONDARY_MODEL_NAME}).\n"
        f"⚠️ Бесплатные лимиты основной модели малы!",
        reply_to_message_id=update.message.message_id
    )
    logger.info(f"/start от {user.id} ({user.username}) в чате {chat_id}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id} ({user.username}) в чате {chat_id}: '{user_message[:50]}...'")

    if not primary_model or not secondary_model:
        logger.error("Одна или обе модели Gemini не инициализированы!")
        await update.message.reply_text("Ошибка: Модели AI не готовы.", reply_to_message_id=update.message.message_id)
        return

    final_text: Optional[str] = None
    used_fallback: bool = False
    error_message: Optional[str] = None

    # --- Попытка с основной моделью ---
    try:
        if chat_id not in primary_chat_histories:
            primary_chat_histories[chat_id] = primary_model.start_chat(history=[])
            logger.info(f"Начат новый основной чат для chat_id {chat_id}")
        primary_chat = primary_chat_histories[chat_id]

        logger.info(f"Попытка обработки с основной моделью: {PRIMARY_MODEL_NAME}")
        final_text = await process_gemini_chat_turn(
            primary_chat, PRIMARY_MODEL_NAME, user_message, context, chat_id
        )

    except ResourceExhausted as e_primary:
        logger.warning(f"Основная модель {PRIMARY_MODEL_NAME} исчерпала квоту: {e_primary}")
        used_fallback = True

    except FailedPrecondition as e_precondition:
        # ИСПРАВЛЕННЫЙ БЛОК 1
        logger.error(f"Основная модель {PRIMARY_MODEL_NAME} столкнулась с FailedPrecondition: {e_precondition}. Сброс истории.")
        error_message = "⚠️ История чата стала слишком длинной. Я ее сбросил. Повторите запрос."
        if chat_id in primary_chat_histories:
            del primary_chat_histories[chat_id] # Правильный отступ
        if chat_id in secondary_chat_histories:
            del secondary_chat_histories[chat_id] # Правильный отступ

    except ValueError as e_blocked: # Ошибка блокировки ответа
        logger.warning(f"Ошибка значения у основной модели (блокировка): {e_blocked}")
        error_message = f"⚠️ {e_blocked}"

    except (GoogleAPIError, Exception) as e_primary_other:
        logger.exception(f"Ошибка при обработке основной моделью {PRIMARY_MODEL_NAME}: {e_primary_other}")
        error_message = f"Произошла ошибка при обработке запроса основной моделью: {e_primary_other}"

    # --- Попытка с запасной моделью ---
    if used_fallback:
        logger.info(f"Переключение на запасную модель: {SECONDARY_MODEL_NAME}")
        try:
            if chat_id not in secondary_chat_histories:
                secondary_chat_histories[chat_id] = secondary_model.start_chat(history=[])
                logger.info(f"Начат новый запасной чат для chat_id {chat_id}")
            secondary_chat = secondary_chat_histories[chat_id]

            final_text = await process_gemini_chat_turn(
                secondary_chat, SECONDARY_MODEL_NAME, user_message, context, chat_id
            )
            error_message = None # Успешно, сбрасываем ошибку

        except ResourceExhausted as e_secondary:
            logger.error(f"Запасная модель {SECONDARY_MODEL_NAME} ТОЖЕ исчерпала квоту: {e_secondary}")
            error_message = f"😔 Обе AI модели ({PRIMARY_MODEL_NAME}, {SECONDARY_MODEL_NAME}) сейчас перегружены. Попробуйте позже."

        except FailedPrecondition as e_precondition_fallback:
             # ИСПРАВЛЕННЫЙ БЛОК 2
             logger.error(f"Запасная модель {SECONDARY_MODEL_NAME} столкнулась с FailedPrecondition: {e_precondition_fallback}. Сброс истории.")
             error_message = "⚠️ История чата с запасной моделью стала слишком длинной и была сброшена. Попробуйте еще раз."
             if chat_id in secondary_chat_histories:
                 del secondary_chat_histories[chat_id] # Правильный отступ

        except ValueError as e_blocked_fallback:
             logger.warning(f"Ошибка значения у запасной модели (блокировка): {e_blocked_fallback}")
             error_message = f"⚠️ {e_blocked_fallback}"

        except (GoogleAPIError, Exception) as e_fallback_other:
             logger.exception(f"Ошибка при обработке запасной моделью {SECONDARY_MODEL_NAME}: {e_fallback_other}")
             error_message = f"Произошла ошибка при обработке запроса запасной моделью: {e_fallback_other}"

    # --- Отправка ответа или сообщения об ошибке ---
    if final_text:
        bot_response = final_text[:4090]
        prefix = f"⚡️ [{SECONDARY_MODEL_NAME}]:\n" if used_fallback else ""
        try:
            await update.message.reply_text(f"{prefix}{bot_response}", reply_to_message_id=update.message.message_id)
            logger.info(f"Ответ{' (fallback)' if used_fallback else ''} успешно отправлен для {user.id} в чате {chat_id}")
        except Exception as e:
            logger.exception(f"Ошибка при отправке финального ответа в Telegram чат {chat_id}: {e}")
            try: await update.message.reply_text("Не смог отправить ответ AI (ошибка форматирования/длины).", reply_to_message_id=update.message.message_id)
            except: pass
    elif error_message:
        try:
             await update.message.reply_text(error_message, reply_to_message_id=update.message.message_id)
             logger.info(f"Сообщение об ошибке отправлено пользователю в чат {chat_id}: {error_message[:100]}...")
        except Exception as e:
             logger.error(f"Не удалось отправить сообщение об ошибке '{error_message[:100]}...' в чат {chat_id}: {e}")
    else:
        # Ситуация, когда final_text пуст, но error_message тоже
        logger.warning(f"Обработка завершена без финального текста и без явного сообщения об ошибке API для чата {chat_id}.")
        if "История чата стала слишком длинной" not in (error_message or "") and "Ответ модели" not in (error_message or "") :
             try: await update.message.reply_text("Извините, не удалось обработать ваш запрос (внутренняя ошибка обработки).", reply_to_message_id=update.message.message_id)
             except: pass


# --- main ---
def main() -> None:
    """Запускает бота."""
    if not primary_model or not secondary_model:
         logger.critical("Критическая ошибка: Модели Gemini не инициализированы.")
         print("Критическая ошибка: Модели Gemini не инициализированы.")
         return
    if not google_search_sync:
         logger.warning("Запуск бота БЕЗ функции поиска Google из-за отсутствия библиотеки googlesearch-python.")

    logger.info("Инициализация Telegram Application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Запуск бота...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
