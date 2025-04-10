# --- START OF FULL CORRECTED main.py (Using BUILT-IN Search Grounding) ---

import logging
import os
import asyncio
import google.generativeai as genai
# Импортируем types для инструментов встроенного поиска (нужна >= 0.8.0)
from google.generativeai import types as genai_types
import time
import random
from typing import Optional, Dict, Union, Any

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Печать версии (важно после обновления requirements.txt)
try: logger.info(f"!!!!!!!!!! Используемая версия google-generativeai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-generativeai: {e} !!!!!!!!!!")

# Исключения
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
# Убираем импорты httpx, BeautifulSoup, googlesearch

# Gemini типы для Struct (если нужны, но вроде нет для встроенного поиска)
# from google.protobuf.struct_pb2 import Struct

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY: exit("Google API ключ не найден")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    '⚡ Flash': 'gemini-2.0-flash-001', # Эта модель должна поддерживать GoogleSearch()
    '🧠 Pro Exp': 'gemini-2.5-pro-exp-03-25', # Эта тоже должна
    # '🌀 1.5 Flash': 'gemini-1.5-flash-latest' # Если захотим добавить 1.5, ей нужен GoogleSearchRetrieval
    '🖼️ Imagen 3 (Картинки!)': 'imagen-3.0-generate-002',
}
DEFAULT_MODEL_ALIAS = '⚡ Flash'

# --- Определение ВСТРОЕННОГО инструмента Google Search ---
# Используем новый способ для моделей 2.0
try:
    # Используем типы из genai_types (нужна версия >= 0.8.0)
    # Проверяем, есть ли нужный класс
    if hasattr(genai_types, 'GoogleSearch'):
         google_search_config = genai_types.GoogleSearch()
         google_search_tool = genai_types.Tool(google_search=google_search_config)
         logger.info("Инструмент ВСТРОЕННОГО Google Search (v2.0+) определен.")
         # Определим и инструмент для 1.5 на всякий случай, если классы есть
         google_search_retrieval_tool = None
         if hasattr(genai_types, 'GoogleSearchRetrieval'):
              google_search_retrieval_config = genai_types.GoogleSearchRetrieval()
              google_search_retrieval_tool = genai_types.Tool(google_search=google_search_retrieval_config) # API может сам преобразовать для 2.0
              logger.info("Инструмент GoogleSearchRetrieval (v1.5) тоже определен.")
         else:
              logger.warning("Класс GoogleSearchRetrieval не найден в genai_types.")
    else:
         google_search_tool = None
         google_search_retrieval_tool = None
         logger.error("!!! Класс GoogleSearch не найден в genai_types. Встроенный поиск НЕ БУДЕТ работать. Убедитесь, что версия google-generativeai >= 0.8.0 !!!")

except AttributeError as e:
    logger.error(f"!!! Ошибка атрибута при определении инструмента поиска (проверьте версию библиотеки >= 0.8.0): {e}")
    google_search_tool = None
    google_search_retrieval_tool = None
except Exception as e:
    logger.exception(f"!!! Непредвиденная ошибка при определении инструмента поиска: {e}")
    google_search_tool = None
    google_search_retrieval_tool = None


# --- Загрузка и Настройка Моделей Gemini ---
LOADED_MODELS: Dict[str, genai.GenerativeModel] = {}
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    # УМЕРЕННАЯ ИНСТРУКЦИЯ (т.к. модель сама решает)
    system_instruction_text = (
        "Отвечай в пределах 2000 знаков... добавляй остроумие. " # Ваша инструкция
        "Если ответ требует актуальной информации (новости, текущие события, погода), используй доступные инструменты." # Убрали явное указание приоритета
    )
    for alias, model_id in AVAILABLE_MODELS.items():
        if 'imagen' in model_id.lower(): logger.warning(...); continue

        # Выбираем правильный инструмент (или никакой)
        # Для простоты пока будем передавать один и тот же (v2.0) - библиотека должна разобраться
        current_tools = [google_search_tool] if google_search_tool else None

        try:
            # Передаем инструменты при СОЗДАНИИ модели
            model = genai.GenerativeModel(
                model_id,
                generation_config={ "temperature": 0.8 if 'Flash' in alias else 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048 },
                system_instruction=system_instruction_text,
                tools=current_tools # <--- Передаем инструмент сюда
            )
            LOADED_MODELS[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Built-in Search: {'Enabled' if current_tools else 'Disabled'}] успешно загружена.")
        except Exception as e: logger.error(f"!!! ОШИБКА загрузки '{alias}': {e}")

    if not LOADED_MODELS: raise RuntimeError("Ни одна текстовая модель не загружена!")
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS:
        try: DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS)); logger.warning(f"Установлена модель по умолчанию: {DEFAULT_MODEL_ALIAS}")
        except StopIteration: raise RuntimeError("Нет моделей для установки по умолчанию.")

except GoogleAPIError as e: logger.exception(...); exit(...)
except Exception as e: logger.exception(...); exit(...)

# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, Any] = {}

# --- УДАЛЯЕМ функцию perform_google_search ---
# --- УДАЛЯЕМ функцию process_gemini_chat_turn ---
# --- УДАЛЯЕМ команду /testsearch и ее обработчик ---

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Выбор модели и история чата сброшены для {chat_id} по команде /start")
    default_model_display_name = DEFAULT_MODEL_ALIAS
    search_status = "включен (встроенный)" if google_search_tool else "отключен" # Обновили статус
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Gemini бот.\n"
        f"Модель по умолчанию: {default_model_display_name}\n"
        f"Используйте /model для выбора другой модели.\n"
        f"🔍 Поиск Google {search_status}.", # Используется встроенный поиск модели
        reply_to_message_id=update.message.message_id
    )
    logger.info(f"/start от {user.id}")

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Без изменений, использует LOADED_MODELS)
    chat_id = update.effective_chat.id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    keyboard = []
    for alias in LOADED_MODELS.keys():
        text = f"✅ {alias}" if alias == current_alias else alias
        keyboard.append([InlineKeyboardButton(text, callback_data=alias)])
    imagen_alias = '🖼️ Imagen 3 (Картинки!)'
    if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS:
         keyboard.append([InlineKeyboardButton(f"{imagen_alias} (Недоступна для чата)", callback_data="imagen_info")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите модель:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Без изменений)
    query = update.callback_query; await query.answer()
    selected_alias = query.data; chat_id = query.message.chat_id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias == "imagen_info": await context.bot.send_message(chat_id=chat_id, text="Imagen недоступна для чата."); return
    if selected_alias not in LOADED_MODELS: await query.edit_message_text(text="Ошибка: Модель недоступна."); return
    if selected_alias != current_alias:
        user_selected_model[chat_id] = selected_alias; logger.info(f"{chat_id} сменил модель на '{selected_alias}'")
        if chat_id in chat_histories: del chat_histories[chat_id]; logger.info(f"История {chat_id} сброшена.")
        keyboard = []
        for alias in LOADED_MODELS.keys(): text = f"✅ {alias}" if alias == selected_alias else alias; keyboard.append(...) # Создаем кнопки
        if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS: keyboard.append(...) # Добавляем инфо-кнопку Imagen
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=f"✅ Модель: *{selected_alias}*\n⚠️ История сброшена.\n\nВыберите модель:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    else: try: await query.edit_message_reply_markup(reply_markup=query.message.reply_markup) except: await context.bot.send_message(...)

# ИЗМЕНЕННАЯ handle_message (сильно упрощена)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")

    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    selected_model_object = LOADED_MODELS.get(selected_alias)

    if not selected_model_object:
        # ... (обработка ошибки недоступной модели) ...
        logger.error(f"Модель '{selected_alias}' для {chat_id} не найдена!")
        # ... (переключение на дефолтную, если возможно) ...
        await update.message.reply_text("Крит. ошибка: Модель не найдена."); return

    final_text: Optional[str] = None; error_message: Optional[str] = None

    try:
        # Получаем или создаем сессию чата
        if chat_id not in chat_histories:
            # Передаем инструменты здесь? Или они уже в модели? Документация неясна для ChatSession.
            # Попробуем БЕЗ передачи tools в start_chat, т.к. они уже в модели.
            chat_histories[chat_id] = selected_model_object.start_chat(history=[])
            logger.info(f"Начат новый чат {chat_id} с '{selected_alias}'")
        current_chat_session = chat_histories[chat_id]

        logger.info(f"Попытка обработки с моделью: {selected_alias} (Встроенный поиск)")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # Просто отправляем сообщение. Модель сама решит, использовать ли поиск.
        # Нам НЕ НУЖЕН цикл обработки function calling.
        response = await current_chat_session.send_message_async(content=user_message)

        logger.info(f"[{selected_alias}] Получен ответ. Проверяем grounding_metadata...")
        # Проверяем, есть ли метаданные поиска
        if response.candidates and hasattr(response.candidates[0], 'grounding_metadata') and response.candidates[0].grounding_metadata:
             if response.candidates[0].grounding_metadata.web_search_queries:
                  search_queries = response.candidates[0].grounding_metadata.web_search_queries
                  logger.info(f"[{selected_alias}] !!!! Модель ИСПОЛЬЗОВАЛА ВСТРОЕННЫЙ ПОИСК. Запросы: {search_queries}")
             else:
                  logger.info(f"[{selected_alias}] В ответе есть grounding_metadata, но без поисковых запросов.")
        else:
             logger.info(f"[{selected_alias}] В ответе НЕТ grounding_metadata (поиск не использовался?).")


        # Извлекаем текст
        try:
            final_text = response.text
            logger.info(f"[{selected_alias}] Извлечен финальный текст.")
        except ValueError as e: # Блокировка
            reason = getattr(response.prompt_feedback, 'block_reason', 'Неизвестно') if hasattr(response, 'prompt_feedback') else 'Неизвестно'
            raise ValueError(f"Ответ модели {selected_alias} заблокирован. Причина: {reason}") from e
        except AttributeError: # Нет .text
            logger.warning(f"[{selected_alias}] !!! Ответ не содержит .text")
            try:
                final_text = "".join(p.text for p in response.parts if hasattr(p, 'text'))
                if final_text: logger.info(f"[{selected_alias}] Текст собран из частей.")
                else: raise Exception("Нет текста")
            except Exception as e_inner: raise Exception("Не удалось извлечь текст") from e_inner

    except ResourceExhausted as e_limit: logger.warning(...); error_message = f"😔 Модель '{selected_alias}' перегружена. /model"
    except FailedPrecondition as e_precondition: logger.error(...); error_message = f"⚠️ История '{selected_alias}' сброшена."; if chat_id in chat_histories: del chat_histories[chat_id]
    except ValueError as e_blocked: logger.warning(...); error_message = f"⚠️ {e_blocked}"
    except (GoogleAPIError, Exception) as e_other: logger.exception(...); error_message = f"Ошибка модели '{selected_alias}': {e_other}"

    # --- Отправка ответа ---
    if final_text:
        bot_response = final_text[:4090]
        try: await update.message.reply_text(bot_response, reply_to_message_id=update.message.message_id); logger.info(f"Ответ от '{selected_alias}' отправлен {user.id}")
        except Exception as e: logger.exception(...); try: await update.message.reply_text("Не смог отправить ответ.", reply_to_message_id=update.message.message_id) except: pass
    elif error_message:
        try: await update.message.reply_text(error_message, reply_to_message_id=update.message.message_id); logger.info(...)
        except Exception as e: logger.error(...)
    else: logger.warning(...); try: await update.message.reply_text("Не удалось обработать.", reply_to_message_id=update.message.message_id) except: pass

# --- main ---
def main() -> None:
    """Запускает бота."""
    if not LOADED_MODELS: logger.critical(...); return
    if not google_search_tool: logger.warning("Встроенный поиск НЕ настроен (возможно, из-за версии или ошибки).")
    else: logger.info("Встроенный поиск настроен.")

    logger.info("Инициализация Telegram...");
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    # УДАЛИЛИ обработчик /testsearch
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))

    logger.info("Запуск бота...");
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
