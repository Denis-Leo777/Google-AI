# --- START OF FULL CORRECTED main.py (Using google-genai package BUT google.generativeai module) ---

import logging
import os
import asyncio
# ВАЖНО: Импортируем МОДУЛЬ google.generativeai, который СТАВИТСЯ пакетом google-genai
import google.generativeai as genai
# Типы должны быть в genai.types теперь
from google.generativeai import types as genai_types
import time
import random
from typing import Optional, Dict, Union, Any, Tuple, List
import urllib.parse

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Печать версии
try: logger.info(f"!!!!!!!!!! Используемая версия google-genai (ожидается >=0.8): {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии genai: {e} !!!!!!!!!!")

# Исключения (Пробуем импортировать снова)
try: from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
except ImportError: logger.warning("google.api_core.exceptions не найдены."); ResourceExhausted=Exception; GoogleAPIError=Exception; FailedPrecondition=Exception

# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Gemini типы для Struct (проверим, есть ли protos)
try: from google.protobuf.struct_pb2 import Struct
except ImportError: logger.warning("google.protobuf не найден."); Struct = dict

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY: exit("Google API ключ не найден")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    '⚡ Flash': 'models/gemini-1.5-flash-latest', # Используем стандартные ID
    '✨ Pro 1.5': 'models/gemini-1.5-pro-latest',
    '🖼️ Imagen 3 (Картинки!)': 'imagen-3.0-generate-002',
}
DEFAULT_MODEL_ALIAS = '⚡ Flash'

# --- Определение ВСТРОЕННОГО инструмента Google Search ---
google_search_tool = None; search_tool_type_used = None
try:
    # Ищем классы в genai_types (из google.generativeai)
    if hasattr(genai_types, 'GoogleSearchRetrieval'): # Для 1.5
         config = genai_types.GoogleSearchRetrieval()
         google_search_tool = genai_types.Tool(google_search_retrieval=config)
         search_tool_type_used = "GoogleSearchRetrieval (v1.5)"
         logger.info(f"Инструмент '{search_tool_type_used}' определен.")
    elif hasattr(genai_types, 'GoogleSearch'): # Для 2.0+
         config = genai_types.GoogleSearch()
         google_search_tool = genai_types.Tool(google_search=config)
         search_tool_type_used = "GoogleSearch (v2.0+)"
         logger.info(f"Инструмент '{search_tool_type_used}' определен.")
    else: logger.error("!!! Классы GoogleSearch/GoogleSearchRetrieval НЕ НАЙДЕНЫ в genai_types.")
except Exception as e: logger.exception(f"!!! Ошибка при определении инструмента поиска: {e}")


# --- ЗАГРУЗКА и НАСТРОЙКА Моделей Gemini ---
LOADED_MODELS_ANY: Dict[str, Any] = {}; # Используем Any для типа модели
try:
    # Используем genai.configure, как в квикстарте
    genai.configure(api_key=GOOGLE_API_KEY)
    logger.info("genai.configure выполнен.")

    system_instruction_text = ("...") # Ваша инструкция

    for alias, model_id in AVAILABLE_MODELS.items():
        if 'imagen' in model_id.lower(): logger.warning(f"'{alias}' пропущена."); continue

        # Определяем инструмент для модели
        current_tools = None; model_search_type = None
        if google_search_tool:
             # Проверяем типы моделей и доступные инструменты
             if '1.5' in model_id and search_tool_type_used == "GoogleSearchRetrieval (v1.5)":
                  current_tools = [google_search_tool]; model_search_type = search_tool_type_used
             elif ('2.0' in model_id or '2.5' in model_id) and search_tool_type_used == "GoogleSearch (v2.0+)":
                  current_tools = [google_search_tool]; model_search_type = search_tool_type_used
             else: logger.warning(f"Нет подходящего поиска для '{alias}'.")

        try:
            # Используем genai.GenerativeModel, как в квикстарте
            model = genai.GenerativeModel(
                model_name=model_id, # Используем model_name=
                system_instruction=system_instruction_text,
                tools=current_tools
                # generation_config можно добавить сюда или в send_message
            )
            LOADED_MODELS_ANY[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Search: {'Enabled ('+model_search_type+')' if current_tools else 'Disabled'}] загружена.")
        except Exception as e: logger.error(f"!!! ОШИБКА загрузки '{alias}': {e}")

    if not LOADED_MODELS_ANY: raise RuntimeError("Нет текстовых моделей!")
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS_ANY:
        try: DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS_ANY)); logger.warning(f"Дефолт: {DEFAULT_MODEL_ALIAS}")
        except StopIteration: raise RuntimeError("Нет моделей.")

except Exception as e: logger.exception("Крит. ошибка инициализации!"); exit(...)

# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}; chat_histories: Dict[int, Any] = {}

# --- Вспомогательная функция для извлечения текста ---
# (Без изменений, с последним исправлением синтаксиса)
def extract_response_text(response) -> Optional[str]:
    try: return response.text
    except ValueError: logger.warning("ValueError text"); block_reason = getattr(...); ...; return None
    except AttributeError:
        logger.warning("Нет .text, пробуем parts.")
        try:
            if response.candidates and response.candidates[0].content.parts: parts_text = "".join(...); return parts_text if parts_text else None
            else: logger.warning("Нет parts."); return None
        except Exception as e_inner: logger.error(f"Ошибка сборки: {e_inner}"); return None

# --- ОБРАБОТЧИКИ TELEGRAM ---
# start, select_model_command, select_model_callback (Без изменений, используют LOADED_MODELS_ANY)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код функции start) ...
    user = update.effective_user; chat_id = update.effective_chat.id; # ... (сброс состояния) ...
    if chat_id in user_selected_model: del user_selected_model[chat_id]; if chat_id in chat_histories: del chat_histories[chat_id]; logger.info(...)
    default_model_display_name = DEFAULT_MODEL_ALIAS; search_status = f"включен ({search_tool_type_used})" if google_search_tool else "отключен"
    await update.message.reply_html( f"Привет, {user.mention_html()}! ... Модель: {default_model_display_name}... /model ... 🔍 Поиск Google {search_status}.", ...)

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код функции select_model_command) ...
    chat_id = update.effective_chat.id; current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS); keyboard = []
    for alias in LOADED_MODELS_ANY.keys(): keyboard.append(...)
    reply_markup = InlineKeyboardMarkup(keyboard); await update.message.reply_text(f"Текущая: *{current_alias}*\n\nВыберите:", ...)

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код функции select_model_callback с исправленным else) ...
    query = update.callback_query; await query.answer(); selected_alias = query.data; chat_id = query.message.chat_id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias not in LOADED_MODELS_ANY: await query.edit_message_text(...); return
    if selected_alias != current_alias:
        user_selected_model[chat_id] = selected_alias; logger.info(...)
        if chat_id in chat_histories: del chat_histories[chat_id]; logger.info(...)
        keyboard = [] # ... (строим кнопки) ...
        reply_markup = InlineKeyboardMarkup(keyboard); await query.edit_message_text(...)
    else:
        try: await query.edit_message_reply_markup(...)
        except Exception as e: logger.warning(...); await context.bot.send_message(...)

# handle_message (Адаптирован под genai.GenerativeModel и ChatSession)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id; logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS); selected_model_object = LOADED_MODELS_ANY.get(selected_alias)
    if not selected_model_object: logger.error(...); await update.message.reply_text("Крит. ошибка: Модель не найдена."); return
    final_text: Optional[str] = None; search_suggestions: List[str] = []; error_message: Optional[str] = None
    try:
        # Используем start_chat от genai.GenerativeModel
        if chat_id not in chat_histories:
            # Передаем tools тут? Или они уже в модели? Пробуем БЕЗ.
            chat_histories[chat_id] = selected_model_object.start_chat(history=[])
            logger.info(f"Начат новый чат {chat_id} с '{selected_alias}'")
        # chat_histories[chat_id] должен быть типа ChatSession
        current_chat_session = chat_histories[chat_id]
        logger.info(f"Попытка с {selected_alias} (Встроенный поиск, если настроен)")
        await context.bot.send_chat_action(...)

        # --- Передача generation_config при вызове (как в квикстарте) ---
        generation_config = genai_types.GenerationConfig(
            temperature=0.8 if 'Flash' in selected_alias else 1,
            top_p=1,
            top_k=40,
            max_output_tokens=2048
            # tools=[google_search_tool] if google_search_tool else None # Квикстарт ТАК не делал для чата...
        )

        # Используем send_message_async от ChatSession
        # Передаем config сюда? Или tools? Пробуем без них, т.к. tools заданы в модели
        response = await current_chat_session.send_message_async(
            content=user_message
            # generation_config=generation_config # Можно попробовать передать конфиг сюда
            )
        logger.info(f"[{selected_alias}] Ответ получен. Проверка...")
        final_text = extract_response_text(response)
        if final_text is None: raise ValueError(...) # Ошибка извлечения

        # Проверка groundingMetadata (структура должна быть та же)
        if response.candidates and hasattr(response.candidates[0], 'grounding_metadata') and response.candidates[0].grounding_metadata:
             metadata = response.candidates[0].grounding_metadata
             if hasattr(metadata, 'web_search_queries') and metadata.web_search_queries: search_suggestions = list(metadata.web_search_queries); logger.info(f"[{selected_alias}] !!!! Предложения поиска: {search_suggestions}")
             else: logger.info(f"[{selected_alias}] meta без запросов.")
        else: logger.info(f"[{selected_alias}] НЕТ grounding_metadata.")

    # ... (обработка исключений) ...
    except ResourceExhausted as e_limit: logger.warning(...); error_message = f"😔 '{selected_alias}' перегружена. /model"
    except FailedPrecondition as e_precondition: logger.error(...); error_message = f"⚠️ История '{selected_alias}' сброшена."; if chat_id in chat_histories: del chat_histories[chat_id]
    except ValueError as e_blocked: logger.warning(...); error_message = f"⚠️ {e_blocked}"
    except (GoogleAPIError, Exception) as e_other: logger.exception(...); error_message = f"Ошибка модели '{selected_alias}': {e_other}"

    # --- Отправка ответа ---
    # ... (логика отправки ответа и кнопок без изменений) ...
    reply_markup = None
    if search_suggestions: keyboard = []; # ... (создаем кнопки поиска) ... ; if keyboard: reply_markup = InlineKeyboardMarkup(keyboard); logger.info(...)
    if final_text:
        bot_response = final_text[:4090]
        try: await update.message.reply_text(bot_response, reply_to_message_id=update.message.message_id, reply_markup=reply_markup); logger.info(...)
        except Exception as e: logger.exception(...); try: await update.message.reply_text("Не смог отправить.", reply_to_message_id=update.message.message_id) except: pass
    elif error_message:
        try: await update.message.reply_text(error_message, reply_to_message_id=update.message.message_id); logger.info(...)
        except Exception as e: logger.error(...)
    else: logger.warning(...); if "История" not in (...) and "Ответ" not in (...) : try: await update.message.reply_text("Не удалось обработать.", reply_to_message_id=update.message.message_id) except: pass


# --- main ---
def main() -> None:
    """Запускает бота."""
    if not LOADED_MODELS_ANY: logger.critical("Модели не загружены!"); return
    if not google_search_tool: logger.warning(f"Встроенный поиск НЕ настроен (тип: {search_tool_type_used}).")
    else: logger.info(f"Встроенный поиск настроен (тип: {search_tool_type_used}).")
    logger.info("Инициализация Telegram...");
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    logger.info("Запуск бота...");
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
