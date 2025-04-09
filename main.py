# --- START OF FULL CORRECTED main.py (Model Selection Feature) ---

import logging
import os
import asyncio
import google.generativeai as genai
# Используем псевдоним types для совместимости с v0.7.1
from google.generativeai import types as genai_types
import time
import random
from typing import Optional, Dict, Union

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Исключения
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
# Добавляем обработчик колбэков
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

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

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
# Словарь моделей: 'UserFriendlyName': 'gemini-model-id'
AVAILABLE_MODELS = {
    # Используем ваши последние имена, Flash теперь по умолчанию
    '⚡ 2.0 Flash': 'gemini-2.0-flash-001',
    '🧠 2.5 Pro': 'gemini-2.5-pro-exp-03-25',
    # '🐢 Pro 1.0 (Старый)': 'models/gemini-1.0-pro-001' # Пример добавления еще одной, если нужно
}
DEFAULT_MODEL_ALIAS = '⚡ 2.0 Flash' # Модель по умолчанию

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

# --- Загрузка и Настройка Моделей Gemini ---
LOADED_MODELS: Dict[str, genai.GenerativeModel] = {} # Словарь для хранения загруженных моделей
gemini_tools = [google_search_tool] if google_search_tool else None
try:
    genai.configure(api_key=GOOGLE_API_KEY)

    system_instruction_text = (
        "Отвечай в пределах 2000 знаков... "
        "ВАЖНО: Если вопрос касается текущих событий... обязательно используй инструмент google_search..."
    )

    for alias, model_id in AVAILABLE_MODELS.items():
        try:
            model = genai.GenerativeModel(
                model_id,
                # Установим чуть разные конфиги для примера
                generation_config={"temperature": 1 if 'Flash' in alias else 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
                system_instruction=system_instruction_text,
                tools=gemini_tools
            )
            LOADED_MODELS[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Search: {'Enabled' if gemini_tools else 'Disabled'}] успешно загружена.")
        except Exception as e:
            logger.error(f"!!! ОШИБКА загрузки модели '{alias}' ({model_id}): {e}")
            # Модель не будет доступна для выбора

    if not LOADED_MODELS:
         raise RuntimeError("Ни одна модель Gemini не была успешно загружена!")
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS:
        # Если модель по умолчанию не загрузилась, выберем первую доступную
        DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS))
        logger.warning(f"Модель по умолчанию не загрузилась. Установлена новая по умолчанию: {DEFAULT_MODEL_ALIAS}")


except GoogleAPIError as e:
    logger.exception(f"Критическая ошибка при конфигурации Gemini API: {e}")
    exit(f"Не удалось настроить Gemini (API Error): {e}")
except Exception as e:
    logger.exception("Критическая ошибка при инициализации моделей Gemini!")
    exit(f"Не удалось настроить Gemini (General Error): {e}")

# --- Хранение состояния пользователя ---
# chat_id -> 'UserFriendlyName' (alias)
user_selected_model: Dict[int, str] = {}
# chat_id -> ChatSession (для текущей выбранной модели)
# Сбрасывается при смене модели!
chat_histories: Dict[int, genai_types.ChatSession] = {}

# --- Функция выполнения поиска Google ---
# (Без изменений)
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
# (Без изменений, использует genai_types.Part для v0.7.1)
async def process_gemini_chat_turn(
    chat_session: genai_types.ChatSession, # Используем type hint для v0.7.1
    model_name: str, # Принимаем имя модели для логов
    initial_content: Union[str, genai_types.Part], # Используем type hint для v0.7.1
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int
) -> str:
    """Обрабатывает один ход диалога с Gemini, включая Function Calling (для v0.7.1)."""
    current_message_or_response = initial_content
    # Определяем по типу Part
    is_function_response = isinstance(initial_content, genai_types.Part)

    for attempt in range(5):
        logger.info(f"[{model_name}] Итерация {attempt+1}. Отправка {'ОТВЕТА НА ФУНКЦИЮ' if is_function_response else 'СООБЩЕНИЯ'}.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        content_to_send = current_message_or_response # По умолчанию

        # ВАЖНО: В v0.7.1 Part создается НЕ ТАК, а из FunctionResponse напрямую
        if is_function_response:
             # Если current_message_or_response это protos.FunctionResponse,
             # библиотека должна сама уметь его отправить в send_message_async
             # Не нужно оборачивать в Part вручную в этой версии!
             logger.info(f"[{model_name}] Отправляем FunctionResponse как есть: {current_message_or_response.name}")
             # content_to_send остается current_message_or_response
        else:
            logger.info(f"[{model_name}] Отправляем как есть (строка): {str(content_to_send)[:100]}...")

        try:
            logger.info(f"[{model_name}] !!! НАЧАЛО вызова send_message_async...")
            # Передаем строку или FunctionResponse
            response = await chat_session.send_message_async(content=content_to_send)
            logger.info(f"[{model_name}] !!! ЗАВЕРШЕНИЕ вызова send_message_async.")

            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0]
                logger.info(f"[{model_name}] ПОЛУЧЕНА ЧАСТЬ ОТВЕТА: {part}")

                if hasattr(part, 'function_call') and part.function_call and part.function_call.name == "google_search":
                    function_call = part.function_call
                    logger.info(f"[{model_name}] !!!! ОБНАРУЖЕН ВЫЗОВ ФУНКЦИИ google_search.")

                    if not google_search_tool:
                         logger.error(f"[{model_name}] !!! Инструмент поиска не настроен!")
                         s_err = Struct(); s_err.update({"content": "Ошибка: Функция поиска не настроена."})
                         # Готовим protos.FunctionResponse
                         current_message_or_response = genai.protos.FunctionResponse(name="google_search", response=s_err)
                         continue

                    args = {key: value for key, value in function_call.args.items()}
                    query = args.get("query")
                    logger.info(f"[{model_name}] Извлечен поисковый запрос: '{query}'")

                    if query:
                        logger.info(f"[{model_name}] !!! НАЧАЛО вызова perform_google_search...")
                        search_result = await perform_google_search(query)
                        logger.info(f"[{model_name}] !!! ЗАВЕРШЕНИЕ вызова perform_google_search...")
                        s_res = Struct(); s_res.update({"content": search_result})
                        # Готовим protos.FunctionResponse
                        current_message_or_response = genai.protos.FunctionResponse(name="google_search", response=s_res)
                        logger.info(f"[{model_name}] Подготовлен FunctionResponse для отправки.")
                        continue
                    else: # Нет query
                         logger.warning(f"[{model_name}] !!! Вызов google_search без 'query'.")
                         s_err = Struct(); s_err.update({"content": "Ошибка: Параметр 'query' не предоставлен."})
                         # Готовим protos.FunctionResponse
                         current_message_or_response = genai.protos.FunctionResponse(name="google_search", response=s_err)
                         continue

                else: # Не function call
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
                        try:
                            final_text = "".join(p.text for p in response.parts if hasattr(p, 'text'))
                            if final_text: logger.info(f"[{model_name}] Текст собран из частей."); return final_text
                            else: raise Exception("Нет текста в .parts")
                        except Exception as e_inner: raise Exception("Не удалось извлечь текст") from e_inner

            else: # Пустой ответ
                 logger.warning(f"[{model_name}] !!! Получен пустой ответ без кандидатов/частей.")
                 reason = getattr(response.prompt_feedback, 'block_reason', 'Неизвестно') if hasattr(response, 'prompt_feedback') else 'Неизвестно'
                 if reason != 'BLOCK_REASON_UNSPECIFIED': raise ValueError(f"Пустой ответ {model_name} заблокирован: {reason}")
                 raise Exception(f"Модель {model_name} вернула пустой ответ.")

        except (ResourceExhausted, FailedPrecondition, GoogleAPIError) as e:
             logger.error(f"[{model_name}] !!! Ошибка API: {e}")
             raise e # Передаем выше
        except ValueError as ve: # Блокировка
             logger.error(f"[{model_name}] !!! Ошибка ValueError (блокировка?): {ve}")
             raise ve
        except Exception as e:
             logger.exception(f"[{model_name}] !!! Непредвиденная ошибка в цикле: {e}")
             raise e

    logger.error(f"[{model_name}] !!! Превышен лимит ({attempt+1}) обработки функций.")
    raise Exception(f"Превышен лимит обработки функций для модели {model_name}.")


# --- НОВЫЕ ОБРАБОТЧИКИ ДЛЯ ВЫБОРА МОДЕЛИ ---

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение с кнопками для выбора модели."""
    chat_id = update.effective_chat.id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)

    keyboard = []
    for alias in LOADED_MODELS.keys(): # Используем только загруженные модели
        text = f"✅ {alias}" if alias == current_alias else alias
        # callback_data будет алиасом модели
        keyboard.append([InlineKeyboardButton(text, callback_data=alias)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Текущая модель: *{current_alias}*\n\nВыберите модель для общения:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие кнопки выбора модели."""
    query = update.callback_query
    await query.answer() # Отвечаем на колбэк, чтобы убрать "часики" у пользователя

    selected_alias = query.data
    chat_id = query.message.chat_id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)

    if selected_alias not in LOADED_MODELS:
        await query.edit_message_text(text="Ошибка: Выбранная модель недоступна.")
        return

    if selected_alias != current_alias:
        user_selected_model[chat_id] = selected_alias
        logger.info(f"Пользователь {chat_id} сменил модель на '{selected_alias}'")
        # СБРАСЫВАЕМ ИСТОРИЮ при смене модели
        if chat_id in chat_histories:
            del chat_histories[chat_id]
            logger.info(f"История чата для {chat_id} сброшена из-за смены модели.")

        # Обновляем клавиатуру
        keyboard = []
        for alias in LOADED_MODELS.keys():
            text = f"✅ {alias}" if alias == selected_alias else alias
            keyboard.append([InlineKeyboardButton(text, callback_data=alias)])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text=f"✅ Модель изменена на: *{selected_alias}*\n"
                 f"⚠️ История предыдущего диалога сброшена.\n\n"
                 f"Выберите модель для общения:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        # Если нажали на уже выбранную модель
        await context.bot.send_message(chat_id=chat_id, text=f"Модель *{selected_alias}* уже выбрана.", parse_mode=ParseMode.MARKDOWN)


# --- СТАРЫЕ ОБРАБОТЧИКИ ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    # Сбрасываем выбор модели и историю
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Выбор модели и история чата сброшены для {chat_id} по команде /start")

    default_model = LOADED_MODELS.get(DEFAULT_MODEL_ALIAS)
    model_display_name = DEFAULT_MODEL_ALIAS if default_model else "Ошибка загрузки модели"

    search_status = "включен (если нужна)" if google_search_tool else "отключен"
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Google AI бот.\n"
        f"Модель по умолчанию: {model_display_name}\n"
        f"Используйте /model для выбора другой модели.\n"
        f"🔍 Поиск Google {search_status}.",
        reply_to_message_id=update.message.message_id
    )
    logger.info(f"/start от {user.id}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")

    # Определяем выбранную модель
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    selected_model_object = LOADED_MODELS.get(selected_alias)

    if not selected_model_object:
        logger.error(f"Выбранная модель '{selected_alias}' для чата {chat_id} не найдена среди загруженных!")
        # Попытка использовать модель по умолчанию, если она есть
        selected_alias = DEFAULT_MODEL_ALIAS
        selected_model_object = LOADED_MODELS.get(DEFAULT_MODEL_ALIAS)
        if not selected_model_object:
            await update.message.reply_text("Критическая ошибка: Ни одна рабочая модель AI не найдена."); return
        else:
             await update.message.reply_text(f"Ошибка: Выбранная вами модель недоступна. Использую модель по умолчанию: {selected_alias}")
             user_selected_model[chat_id] = selected_alias # Запоминаем дефолтную

    final_text: Optional[str] = None; error_message: Optional[str] = None

    try: # --- Попытка с ВЫБРАННОЙ моделью ---
        # Получаем или создаем сессию чата (используем один словарь)
        if chat_id not in chat_histories:
            chat_histories[chat_id] = selected_model_object.start_chat(history=[])
            logger.info(f"Начат новый чат для {chat_id} с моделью '{selected_alias}'")
        current_chat_session = chat_histories[chat_id]

        logger.info(f"Попытка обработки с моделью: {selected_alias}")
        final_text = await process_gemini_chat_turn(
            current_chat_session, selected_alias, user_message, context, chat_id # Передаем имя для логов
        )

    except ResourceExhausted as e_limit:
        logger.warning(f"Модель '{selected_alias}' исчерпала квоту: {e_limit}")
        error_message = f"😔 Выбранная модель '{selected_alias}' сейчас перегружена или ее дневной лимит исчерпан. Попробуйте позже или выберите другую модель через /model."
        # НЕ используем автоматический fallback
    except FailedPrecondition as e_precondition:
        logger.error(f"Модель '{selected_alias}' FailedPrecondition: {e_precondition}. Сброс истории.")
        error_message = f"⚠️ История чата с моделью '{selected_alias}' стала слишком длинной. Я ее сбросил. Повторите запрос."
        if chat_id in chat_histories:
            del chat_histories[chat_id] # Сбрасываем только текущую сессию
    except ValueError as e_blocked:
        logger.warning(f"Модель '{selected_alias}' блокировка: {e_blocked}")
        error_message = f"⚠️ {e_blocked}"
    except (GoogleAPIError, Exception) as e_other:
        logger.exception(f"Ошибка при обработке моделью '{selected_alias}': {e_other}")
        error_message = f"Произошла ошибка при обработке запроса моделью '{selected_alias}': {e_other}"

    # --- Отправка ответа или сообщения об ошибке ---
    if final_text:
        bot_response = final_text[:4090]
        # Префикс больше не нужен, т.к. нет автоматического fallback
        try:
            await update.message.reply_text(bot_response, reply_to_message_id=update.message.message_id)
            logger.info(f"Ответ от '{selected_alias}' отправлен {user.id}")
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
        # Упростим сообщение об общей ошибке
        try: await update.message.reply_text("Извините, не удалось обработать ваш запрос.", reply_to_message_id=update.message.message_id)
        except Exception: pass


# --- main ---
def main() -> None:
    """Запускает бота."""
    if not LOADED_MODELS:
         logger.critical("Ни одна модель не загружена!"); print("Крит. ошибка: Модели не готовы."); return
    if not google_search_sync:
         logger.warning("Запуск БЕЗ поиска Google.")

    logger.info("Инициализация Telegram...");
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command)) # Команда выбора
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback)) # Обработчик нажатий кнопок

    logger.info("Запуск бота...");
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
