# --- START OF FULL CORRECTED main.py (Logger defined before use) ---

import logging # Переносим импорт logging наверх, если его там нет
import os
import asyncio
import google.generativeai as genai # Основной импорт
import time
import random
# НЕ ИМПОРТИРУЕМ ChatSession или Part из types
from typing import Optional, Tuple, Union

# --- Конфигурация логов ---
# ПЕРЕМЕЩАЕМ ЭТОТ БЛОК ВЫШЕ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__) # Теперь logger определен

# ПЕЧАТАЕМ ВЕРСИЮ БИБЛИОТЕКИ ДЛЯ ДИАГНОСТИКИ
try:
    # Теперь logger доступен
    logger.info(f"!!!!!!!!!! Используемая версия google-generativeai: {genai.__version__} !!!!!!!!!!")
except AttributeError:
    # И здесь logger доступен
    logger.warning("!!!!!!!!!! Не удалось определить версию google-generativeai !!!!!!!!!!")
except Exception as e:
    # На всякий случай ловим другие ошибки при доступе к версии
    logger.error(f"!!!!!!!!!! Ошибка при попытке получить версию google-generativeai: {e} !!!!!!!!!!")


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

# Gemini Function Calling типы - берем из google.protobuf
from google.protobuf.struct_pb2 import Struct


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
        description="Получает актуальную информацию из поиска Google...",
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

# --- Настройка Gemini ---
primary_model = None
secondary_model = None
gemini_tools = [google_search_tool] if google_search_tool else None
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    primary_model = genai.GenerativeModel(
        PRIMARY_MODEL_NAME,
        generation_config={"temperature": 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
        system_instruction="Отвечай в пределах 2000 знаков, если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, или другая, требующая объема работа. Ты - лучший эксперт в любых вопросах. Используй поиск в интернете для проверки актуальной информации. Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку. Подкрепляй аргументами и доказательствами, без самоповтора. Если не знаешь ответ, - всегда предупреждай, если это предположение. Активно применяй юмор: культурные и бытовые отсылки, жизненный абсурд, шутки об актуальных в интернете темах, ирония и самоирония, тонкие и интимные намёки, пост-модерн и интернет-юмор, несоответствие ожиданиям, абсурдные решения проблем, псевдомудрость, разрушение идиом, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, редукционизм. При создании уникальной работы: избегай копирования или близкого пересказа существующих текстов; включай гипотетические ситуации для иллюстрации понятий; применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи; не допускай признаков ИИ; варьируй структуру предложений, используя сочетание простых и сложных конструкций; естественно включай разговорные выражения, идиомы и фигуры речи; используй живые стилистические решения, свойственные людям; вставляй региональные выражения или культурно специфичные ссылки, где это уместно; добавляй остроумие.",
        tools=gemini_tools
    )
    logger.info(f"Основная модель Gemini ('{PRIMARY_MODEL_NAME}') ... сконфигурирована.")
    secondary_model = genai.GenerativeModel(
        SECONDARY_MODEL_NAME,
        generation_config={"temperature": 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
        system_instruction="Отвечай в пределах 2000 знаков, если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, или другая, требующая объема работа. Ты - лучший эксперт в любых вопросах. Используй поиск в интернете для проверки актуальной информации. Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку. Подкрепляй аргументами и доказательствами, без самоповтора. Если не знаешь ответ, - всегда предупреждай, если это предположение. Активно применяй юмор: культурные и бытовые отсылки, жизненный абсурд, шутки об актуальных в интернете темах, ирония и самоирония, тонкие и интимные намёки, пост-модерн и интернет-юмор, несоответствие ожиданиям, абсурдные решения проблем, псевдомудрость, разрушение идиом, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, редукционизм. При создании уникальной работы: избегай копирования или близкого пересказа существующих текстов; включай гипотетические ситуации для иллюстрации понятий; применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи; не допускай признаков ИИ; варьируй структуру предложений, используя сочетание простых и сложных конструкций; естественно включай разговорные выражения, идиомы и фигуры речи; используй живые стилистические решения, свойственные людям; вставляй региональные выражения или культурно специфичные ссылки, где это уместно; добавляй остроумие.",
        tools=gemini_tools
    )
    logger.info(f"Запасная модель Gemini ('{SECONDARY_MODEL_NAME}') ... сконфигурирована.")
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
    # УБИРАЕМ TYPE HINTS для chat_session и initial_content, чтобы избежать ошибки при запуске
    chat_session, # Было: chat_session: genai.ChatSession,
    model_name: str,
    initial_content, # Было: initial_content: Union[str, genai.Part],
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int
) -> str:
    """Обрабатывает один ход диалога с Gemini, включая Function Calling."""
    current_content = initial_content
    # Используем genai.Part для проверки ТИПА ВНУТРИ ФУНКЦИИ
    # Если здесь будет ошибка во время выполнения, это укажет на старую версию
    is_function_response = isinstance(initial_content, genai.Part)

    for attempt in range(5):
        logger.info(f"[{model_name}] Отправка {'ответа на функцию' if is_function_response else 'сообщения'}...")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        try:
            # model.start_chat() возвращает ChatSession, send_message_async должен работать
            response = await chat_session.send_message_async(content=current_content)
            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0]
                if part.function_call and part.function_call.name == "google_search":
                    function_call = part.function_call
                    if not google_search_tool:
                         s_err = Struct(); s_err.update({"content": "Ошибка: Функция поиска не настроена."})
                         # Используем genai.Part ЗДЕСЬ
                         current_content = genai.Part.from_function_response(name="google_search", response=s_err)
                         is_function_response = True
                         continue
                    args = {key: value for key, value in function_call.args.items()}
                    query = args.get("query")
                    logger.info(f"[{model_name}] Запрос функции: google_search(query='{query}')")
                    if query:
                        search_result = await perform_google_search(query)
                        s_res = Struct(); s_res.update({"content": search_result})
                         # Используем genai.Part ЗДЕСЬ
                        current_content = genai.Part.from_function_response(name="google_search", response=s_res)
                        is_function_response = True
                        continue
                    else: # Нет query
                         s_err = Struct(); s_err.update({"content": "Ошибка: Параметр 'query' не предоставлен."})
                         # Используем genai.Part ЗДЕСЬ
                         current_content = genai.Part.from_function_response(name="google_search", response=s_err)
                         is_function_response = True
                         continue
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
        except Exception as e:
             logger.exception(f"[{model_name}] Непредвиденная ошибка: {e}")
             raise e
    raise Exception(f"Превышен лимит ({attempt+1}) обработки функций для {model_name}.")


# --- Обработчики Telegram ---
# start и handle_message без изменений в логике, только правильные отступы
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
            primary_chat_histories[chat_id] = primary_model.start_chat(history=[]) # Должно вернуть ChatSession
            logger.info(f"Начат основной чат {chat_id}")
        primary_chat = primary_chat_histories[chat_id]
        logger.info(f"Попытка с {PRIMARY_MODEL_NAME}")
        final_text = await process_gemini_chat_turn(primary_chat, PRIMARY_MODEL_NAME, user_message, context, chat_id)

    except ResourceExhausted as e_primary:
        logger.warning(f"{PRIMARY_MODEL_NAME} квота исчерпана: {e_primary}")
        used_fallback = True
    except FailedPrecondition as e_precondition:
        logger.error(f"{PRIMARY_MODEL_NAME} FailedPrecondition: {e_precondition}. Сброс истории.")
        error_message = "⚠️ История чата стала слишком длинной. Я ее сбросил. Повторите запрос."
        if chat_id in primary_chat_histories:
            del primary_chat_histories[chat_id]
        if chat_id in secondary_chat_histories:
            del secondary_chat_histories[chat_id]
    except ValueError as e_blocked:
        logger.warning(f"{PRIMARY_MODEL_NAME} блокировка: {e_blocked}")
        error_message = f"⚠️ {e_blocked}"
    except (GoogleAPIError, Exception) as e_primary_other:
        logger.exception(f"Ошибка {PRIMARY_MODEL_NAME}: {e_primary_other}")
        error_message = f"Ошибка основной модели: {e_primary_other}"

    if used_fallback: # --- Попытка с запасной моделью ---
        logger.info(f"Переключение на {SECONDARY_MODEL_NAME}")
        try:
            if chat_id not in secondary_chat_histories:
                secondary_chat_histories[chat_id] = secondary_model.start_chat(history=[]) # Должно вернуть ChatSession
                logger.info(f"Начат запасной чат {chat_id}")
            secondary_chat = secondary_chat_histories[chat_id]
            logger.info(f"Попытка с {SECONDARY_MODEL_NAME}")
            final_text = await process_gemini_chat_turn(secondary_chat, SECONDARY_MODEL_NAME, user_message, context, chat_id)
            error_message = None # Успех

        except ResourceExhausted as e_secondary:
            logger.error(f"{SECONDARY_MODEL_NAME} ТОЖЕ квота исчерпана: {e_secondary}")
            error_message = f"😔 Обе AI модели ({PRIMARY_MODEL_NAME}, {SECONDARY_MODEL_NAME}) сейчас перегружены."
        except FailedPrecondition as e_precondition_fallback:
             logger.error(f"{SECONDARY_MODEL_NAME} FailedPrecondition: {e_precondition_fallback}. Сброс истории.")
             error_message = "⚠️ История чата с запасной моделью стала слишком длинной и была сброшена. Попробуйте еще раз."
             if chat_id in secondary_chat_histories:
                 del secondary_chat_histories[chat_id]
        except ValueError as e_blocked_fallback:
             logger.warning(f"{SECONDARY_MODEL_NAME} блокировка: {e_blocked_fallback}")
             error_message = f"⚠️ {e_blocked_fallback}"
        except (GoogleAPIError, Exception) as e_fallback_other:
             logger.exception(f"Ошибка {SECONDARY_MODEL_NAME}: {e_fallback_other}")
             error_message = f"Ошибка запасной модели: {e_fallback_other}"

    # --- Отправка ответа или сообщения об ошибке ---
    if final_text:
        bot_response = final_text[:4090]
        prefix = f"⚡️ [{SECONDARY_MODEL_NAME}]:\n" if used_fallback else ""
        try:
            await update.message.reply_text(f"{prefix}{bot_response}", reply_to_message_id=update.message.message_id)
            logger.info(f"Ответ{' (fallback)' if used_fallback else ''} отправлен {user.id}")
        except Exception as e:
            logger.exception(f"Ошибка отправки ответа: {e}")
            try: await update.message.reply_text("Не смог отправить ответ AI.", reply_to_message_id=update.message.message_id)
            except Exception: pass
    elif error_message:
        try:
            await update.message.reply_text(error_message, reply_to_message_id=update.message.message_id)
            logger.info(f"Сообщение об ошибке отправлено: {error_message[:100]}...")
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение об ошибке '{error_message[:100]}...': {e}")
    else:
        logger.warning(f"Нет финального текста и сообщения об ошибке для {chat_id}.")
        if "История чата стала слишком длинной" not in (error_message or "") and "Ответ модели" not in (error_message or "") :
             try: await update.message.reply_text("Не удалось обработать запрос.", reply_to_message_id=update.message.message_id)
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
