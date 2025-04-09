# --- START OF FULL CORRECTED main.py (Updated Models List + Imagen Warning) ---

import logging
import os
import asyncio
import google.generativeai as genai
# Убрали импорт types, т.к. ChatSession там нет в v0.7.1
import time
import random
from typing import Optional, Dict, Union, Any # Добавили Any

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
# Обновленный словарь моделей
AVAILABLE_MODELS = {
    '⚡ Flash': 'gemini-2.0-flash-001', # Ваше имя
    '🧠 Pro Exp': 'gemini-2.5-pro-exp-03-25', # Ваше имя
    '🖼️ Imagen 3 (Картинки!)': 'imagen-3.0-generate-002', # Добавлено, но НЕ будет работать для чата!
}
# Модель по умолчанию - Проверим, существует ли '⚡ Flash' после загрузки
DEFAULT_MODEL_ALIAS = '⚡ Flash'

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
LOADED_MODELS: Dict[str, genai.GenerativeModel] = {}
gemini_tools = [google_search_tool] if google_search_tool else None
try:
    genai.configure(api_key=GOOGLE_API_KEY)

    system_instruction_text = (
        "Отвечай в пределах 2000 знаков... "
        "ВАЖНО: Если вопрос касается текущих событий... обязательно используй инструмент google_search..."
    )

    for alias, model_id in AVAILABLE_MODELS.items():
        # Пропускаем Imagen для загрузки через GenerativeModel
        if 'imagen' in model_id.lower():
             logger.warning(f"Модель '{alias}' ({model_id}) пропущена, т.к. это модель для генерации изображений и несовместима с текущим чат-ботом.")
             continue # Переходим к следующей модели

        try:
            model = genai.GenerativeModel(
                model_id,
                generation_config={"temperature": 0.8 if 'Flash' in alias else 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
                system_instruction=system_instruction_text,
                tools=gemini_tools
            )
            LOADED_MODELS[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Search: {'Enabled' if gemini_tools else 'Disabled'}] успешно загружена.")
        except Exception as e:
            # Логируем ошибку, но не прерываем загрузку других моделей
            logger.error(f"!!! ОШИБКА загрузки модели '{alias}' ({model_id}): {e}")

    if not LOADED_MODELS:
         raise RuntimeError("Ни одна *текстовая* модель Gemini не была успешно загружена!")
    # Проверяем, загрузилась ли модель по умолчанию
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS:
        # Если нет, берем первую доступную
        try:
            DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS))
            logger.warning(f"Модель по умолчанию '{DEFAULT_MODEL_ALIAS}' не загрузилась или не найдена. Установлена новая по умолчанию: '{DEFAULT_MODEL_ALIAS}'")
        except StopIteration:
             # Этот случай возможен только если LOADED_MODELS пуст, что уже обработано выше
             raise RuntimeError("Не удалось установить модель по умолчанию, т.к. ни одна модель не загружена.")


except GoogleAPIError as e:
    logger.exception(f"Критическая ошибка при конфигурации Gemini API: {e}")
    exit(f"Не удалось настроить Gemini (API Error): {e}")
except Exception as e:
    logger.exception("Критическая ошибка при инициализации моделей Gemini!")
    exit(f"Не удалось настроить Gemini (General Error): {e}")

# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
# УБРАН TYPE HINT для ChatSession
chat_histories: Dict[int, Any] = {}

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
# (Используем genai.protos.Part и genai.protos.FunctionResponse для v0.7.1)
async def process_gemini_chat_turn(
    chat_session, # Убран type hint
    model_name: str,
    initial_content, # Убран type hint
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int
) -> str:
    """Обрабатывает один ход диалога с Gemini, включая Function Calling (для v0.7.1)."""
    current_message_or_response = initial_content
    is_function_response = isinstance(current_message_or_response, genai.protos.FunctionResponse) # Проверяем на FunctionResponse

    for attempt in range(5):
        logger.info(f"[{model_name}] Итерация {attempt+1}. Отправка {'ОТВЕТА НА ФУНКЦИЮ' if is_function_response else 'СООБЩЕНИЯ'}.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        content_to_send = current_message_or_response # По умолчанию

        # ВАЖНО: Для v0.7.1 ПРИ ОТПРАВКЕ ответа на функцию, нужно отправлять сам FunctionResponse
        if is_function_response:
             logger.info(f"[{model_name}] Отправляем FunctionResponse как есть: {current_message_or_response.name}")
             # content_to_send остается current_message_or_response (типа FunctionResponse)
        else:
            logger.info(f"[{model_name}] Отправляем как есть (строка): {str(content_to_send)[:100]}...")

        try:
            logger.info(f"[{model_name}] !!! НАЧАЛО вызова send_message_async...")
            # Передаем строку или FunctionResponse
            response = await chat_session.send_message_async(content=content_to_send)
            logger.info(f"[{model_name}] !!! ЗАВЕРШЕНИЕ вызова send_message_async.")

            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0] # Это будет protos.Part
                logger.info(f"[{model_name}] ПОЛУЧЕНА ЧАСТЬ ОТВЕТА: {part}")

                if hasattr(part, 'function_call') and part.function_call and part.function_call.name == "google_search":
                    function_call = part.function_call
                    logger.info(f"[{model_name}] !!!! ОБНАРУЖЕН ВЫЗОВ ФУНКЦИИ google_search.")

                    if not google_search_tool:
                         logger.error(f"[{model_name}] !!! Инструмент поиска не настроен!")
                         s_err = Struct(); s_err.update({"content": "Ошибка: Функция поиска не настроена."})
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
                        current_message_or_response = genai.protos.FunctionResponse(name="google_search", response=s_res)
                        logger.info(f"[{model_name}] Подготовлен FunctionResponse для отправки.")
                        continue
                    else: # Нет query
                         logger.warning(f"[{model_name}] !!! Вызов google_search без 'query'.")
                         s_err = Struct(); s_err.update({"content": "Ошибка: Параметр 'query' не предоставлен."})
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
             raise e
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
    # Используем только УСПЕШНО загруженные модели из LOADED_MODELS
    for alias in LOADED_MODELS.keys():
        text = f"✅ {alias}" if alias == current_alias else alias
        keyboard.append([InlineKeyboardButton(text, callback_data=alias)])

    # Добавим кнопку для Imagen, если она в списке, но не загружена (просто для информации)
    imagen_alias = '🖼️ Imagen 3 (Картинки!)'
    if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS:
         keyboard.append([InlineKeyboardButton(f"{imagen_alias} (Недоступна для чата)", callback_data="imagen_info")])


    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Текущая модель: *{current_alias}*\n\nВыберите модель для общения:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие кнопки выбора модели."""
    query = update.callback_query
    await query.answer() # Отвечаем на колбэк

    selected_alias = query.data
    chat_id = query.message.chat_id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)

    # Обработка информационного нажатия на Imagen
    if selected_alias == "imagen_info":
        await context.bot.send_message(chat_id=chat_id, text="Модель Imagen предназначена для генерации изображений и не может использоваться для текстового чата в этом боте.")
        return

    if selected_alias not in LOADED_MODELS:
        # Этого не должно произойти, если кнопка создана правильно, но на всякий случай
        await query.edit_message_text(text="Ошибка: Выбранная модель недоступна или не загружена.")
        return

    if selected_alias != current_alias:
        user_selected_model[chat_id] = selected_alias
        logger.info(f"Пользователь {chat_id} сменил модель на '{selected_alias}'")
        if chat_id in chat_histories:
            del chat_histories[chat_id]
            logger.info(f"История чата для {chat_id} сброшена из-за смены модели.")

        keyboard = []
        for alias in LOADED_MODELS.keys():
            text = f"✅ {alias}" if alias == selected_alias else alias
            keyboard.append([InlineKeyboardButton(text, callback_data=alias)])
        # Добавим инфо-кнопку Imagen снова
        imagen_alias = '🖼️ Imagen 3 (Картинки!)'
        if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS:
             keyboard.append([InlineKeyboardButton(f"{imagen_alias} (Недоступна для чата)", callback_data="imagen_info")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=f"✅ Модель изменена на: *{selected_alias}*\n"
                 f"⚠️ История предыдущего диалога сброшена.\n\n"
                 f"Выберите модель для общения:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        # Если нажали на уже выбранную
        try: # Попробуем отредактировать с тем же текстом, чтобы убрать "часики"
             await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        except: # Если не получилось, просто отправим сообщение
             await context.bot.send_message(chat_id=chat_id, text=f"Модель *{selected_alias}* уже выбрана.", parse_mode=ParseMode.MARKDOWN)


# --- СТАРЫЕ ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Выбор модели и история чата сброшены для {chat_id} по команде /start")

    # Используем актуальный DEFAULT_MODEL_ALIAS после загрузки
    default_model_display_name = DEFAULT_MODEL_ALIAS

    search_status = "включен (если нужна)" if google_search_tool else "отключен"
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Gemini бот.\n"
        f"Модель по умолчанию: {default_model_display_name}\n"
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
        # Проверка и установка дефолтной
        selected_alias = DEFAULT_MODEL_ALIAS
        selected_model_object = LOADED_MODELS.get(DEFAULT_MODEL_ALIAS)
        if not selected_model_object:
            await update.message.reply_text("Критическая ошибка: Ни одна рабочая модель AI не найдена."); return
        else:
             await update.message.reply_text(f"Ошибка: Выбранная вами модель недоступна. Использую модель по умолчанию: {selected_alias}")
             user_selected_model[chat_id] = selected_alias

    final_text: Optional[str] = None; error_message: Optional[str] = None

    try: # --- Попытка с ВЫБРАННОЙ моделью ---
        if chat_id not in chat_histories:
            chat_histories[chat_id] = selected_model_object.start_chat(history=[])
            logger.info(f"Начат новый чат для {chat_id} с моделью '{selected_alias}'")
        current_chat_session = chat_histories[chat_id]

        logger.info(f"Попытка обработки с моделью: {selected_alias}")
        final_text = await process_gemini_chat_turn(
            current_chat_session, selected_alias, user_message, context, chat_id
        )

    except ResourceExhausted as e_limit:
        logger.warning(f"Модель '{selected_alias}' исчерпала квоту: {e_limit}")
        error_message = f"😔 Выбранная модель '{selected_alias}' сейчас перегружена или ее дневной лимит исчерпан. Попробуйте позже или выберите другую модель через /model."
    except FailedPrecondition as e_precondition:
        logger.error(f"Модель '{selected_alias}' FailedPrecondition: {e_precondition}. Сброс истории.")
        error_message = f"⚠️ История чата с моделью '{selected_alias}' стала слишком длинной. Я ее сбросил. Повторите запрос."
        if chat_id in chat_histories: del chat_histories[chat_id]
    except ValueError as e_blocked:
        logger.warning(f"Модель '{selected_alias}' блокировка: {e_blocked}")
        error_message = f"⚠️ {e_blocked}"
    except (GoogleAPIError, Exception) as e_other:
        logger.exception(f"Ошибка при обработке моделью '{selected_alias}': {e_other}")
        error_message = f"Произошла ошибка при обработке запроса моделью '{selected_alias}': {e_other}"

    # --- Отправка ответа ---
    if final_text:
        bot_response = final_text[:4090]
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
        if "История чата" not in (error_message or "") and "Ответ модели" not in (error_message or "") :
             try: await update.message.reply_text("Не удалось обработать запрос.", reply_to_message_id=update.message.message_id)
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
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))

    logger.info("Запуск бота...");
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
