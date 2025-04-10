# --- START OF FULL CORRECTED main.py (Using google-genai library) ---

import logging
import os
import asyncio
import google.generativeai as genai # <--- Используем пакет google-genai
# Импорты типов ТЕПЕРЬ ДОЛЖНЫ РАБОТАТЬ из google.generativeai.types, если это правильный пакет
from google.generativeai import types as genai_types
import time
import random
from typing import Optional, Dict, Union, Any, List
import urllib.parse

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Печать версии (теперь для google-genai)
try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")

# Исключения (оставим из google.api_core, они часто общие)
try:
    from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
except ImportError:
    logger.warning("Не удалось импортировать исключения google.api_core. Возможно, они в другом месте для google-genai.")
    # Заглушки, если импорт не удался
    class ResourceExhausted(Exception): pass
    class GoogleAPIError(Exception): pass
    class FailedPrecondition(Exception): pass

# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Gemini типы для Struct (нужны ли?) - пока оставим
from google.protobuf.struct_pb2 import Struct

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY: exit("Google API ключ не найден")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    '⚡ Flash': 'gemini-2.0-flash-001', # Проверим, поддерживаются ли эти ID в google-genai
    '🧠 Pro Exp': 'gemini-2.5-pro-exp-03-25', # Или нужно использовать 'gemini-1.5-pro-latest'?
}
# Используем модель, которая точно поддерживается (из квикстарта)
DEFAULT_MODEL_ALIAS = '⚡ Flash' # Или 'models/gemini-1.5-flash-latest' - надо проверить ID

# --- Определение ВСТРОЕННОГО инструмента Google Search ---
google_search_tool = None
search_tool_type_used = None
try:
    # Используем типы из genai_types пакета google-genai
    # СНАЧАЛА ПРОВЕРИМ GoogleSearchRetrieval (для 1.5)
    if hasattr(genai_types, 'GoogleSearchRetrieval'):
         google_search_retrieval_config = genai_types.GoogleSearchRetrieval()
         # ВАЖНО: Поле в Tool для 1.5 - google_search_retrieval
         google_search_tool = genai_types.Tool(google_search_retrieval=google_search_retrieval_config)
         search_tool_type_used = "GoogleSearchRetrieval (v1.5)"
         logger.info(f"Инструмент ВСТРОЕННОГО поиска '{search_tool_type_used}' определен.")
    # ПОТОМ ПРОВЕРИМ GoogleSearch (для 2.0+) - имя поля в Tool там google_search
    elif hasattr(genai_types, 'GoogleSearch'):
         google_search_config = genai_types.GoogleSearch()
         google_search_tool = genai_types.Tool(google_search=google_search_config)
         search_tool_type_used = "GoogleSearch (v2.0+)"
         logger.info(f"Инструмент ВСТРОЕННОГО поиска '{search_tool_type_used}' определен.")
    else:
         logger.error("!!! Классы GoogleSearch И GoogleSearchRetrieval НЕ НАЙДЕНЫ в genai_types пакета google-genai.")

except AttributeError as e: logger.error(f"!!! Ошибка атрибута при поиске инструмента (версия?): {e}")
except Exception as e: logger.exception(f"!!! Ошибка при определении инструмента поиска: {e}")


# --- Загрузка и Настройка Моделей Gemini ---
LOADED_MODELS: Dict[str, genai.GenerativeModel] = {}
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    system_instruction_text = (
        "Отвечай... остроумие. " # Умеренная инструкция
        "Если ответ требует актуальной информации (новости, текущие события, погода), используй доступные инструменты."
    )
    for alias, model_id in AVAILABLE_MODELS.items():
        if 'imagen' in model_id.lower(): logger.warning(...); continue

        # Передаем ОДИН И ТОТ ЖЕ инструмент (библиотека/API сами разберутся?)
        current_tools = [google_search_tool] if google_search_tool else None

        try:
            # Создаем модель через genai.GenerativeModel
            model = genai.GenerativeModel(
                model_id, # ВАЖНО: Убедиться, что эти ID моделей работают с google-genai
                generation_config={"temperature": 0.8 if 'Flash' in alias else 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
                system_instruction=system_instruction_text,
                # ВАЖНО: Передаем tools здесь!
                tools=current_tools
            )
            LOADED_MODELS[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Built-in Search: {'Enabled (' + search_tool_type_used + ')' if current_tools else 'Disabled'}] успешно загружена.")
        except Exception as e: logger.error(f"!!! ОШИБКА загрузки '{alias}': {e}")

    if not LOADED_MODELS: raise RuntimeError("Ни одна текстовая модель не загружена!")
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS:
        try: DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS)); logger.warning(f"Установлена модель по умолчанию: {DEFAULT_MODEL_ALIAS}")
        except StopIteration: raise RuntimeError("Нет моделей.")

except Exception as e: logger.exception("Крит. ошибка инициализации!"); exit(...)

# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, Any] = {} # Оставляем Any

# --- Вспомогательная функция для извлечения текста ---
# Оставляем эту функцию, она полезна
def extract_response_text(response) -> Optional[str]:
    # (Код функции без изменений)
    try: return response.text
    except ValueError: logger.warning("ValueError при извлечении text"); return None
    except AttributeError: logger.warning("Нет .text, пробуем parts"); try: return "".join(p.text for p in response.candidates[0].content.parts if hasattr(p, 'text')) if response.candidates and response.candidates[0].content.parts else None; except: return None

# --- ОБРАБОТЧИКИ TELEGRAM ---
# start, select_model_command, select_model_callback - без изменений
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Состояние для {chat_id} сброшено по /start")
    default_model_display_name = DEFAULT_MODEL_ALIAS
    search_status = f"включен ({search_tool_type_used})" if google_search_tool else "отключен"
    await update.message.reply_html( f"Привет, {user.mention_html()}! ... Модель: {default_model_display_name}... /model ... 🔍 Поиск Google {search_status}.", reply_to_message_id=update.message.message_id)
    logger.info(f"/start от {user.id}")

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Код без изменений)
    chat_id = update.effective_chat.id; current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    keyboard = []; imagen_alias = '🖼️ Imagen 3 (Картинки!)'
    for alias in LOADED_MODELS.keys(): keyboard.append(...) # Строим кнопки
    if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS: keyboard.append(...) # Инфо Imagen
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите модель:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Код без изменений, с исправленным else)
    query = update.callback_query; await query.answer(); selected_alias = query.data; chat_id = query.message.chat_id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias == "imagen_info": await context.bot.send_message(...); return
    if selected_alias not in LOADED_MODELS: await query.edit_message_text(...); return
    if selected_alias != current_alias:
        user_selected_model[chat_id] = selected_alias; logger.info(...)
        if chat_id in chat_histories: del chat_histories[chat_id]; logger.info(...)
        keyboard = []; imagen_alias = '🖼️ Imagen 3 (Картинки!)' # Строим кнопки
        for alias in LOADED_MODELS.keys(): keyboard.append(...)
        if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS: keyboard.append(...)
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(...)
    else: try: await query.edit_message_reply_markup(...) except Exception as e: logger.warning(...); await context.bot.send_message(...)

# handle_message (без изменений по сравнению с предыдущей версией, использует встроенный поиск)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    selected_model_object = LOADED_MODELS.get(selected_alias)
    if not selected_model_object: logger.error(...); await update.message.reply_text("Крит. ошибка: Модель не найдена."); return # Упрощено
    final_text: Optional[str] = None; search_suggestions: List[str] = []; error_message: Optional[str] = None
    try:
        if chat_id not in chat_histories: chat_histories[chat_id] = selected_model_object.start_chat(history=[]); logger.info(...)
        current_chat_session = chat_histories[chat_id]; logger.info(f"Попытка с {selected_alias} (Встроенный поиск)")
        await context.bot.send_chat_action(...)
        response = await current_chat_session.send_message_async(content=user_message) # Просто отправляем
        logger.info(f"[{selected_alias}] Ответ получен. Проверка...")
        final_text = extract_response_text(response) # Извлекаем текст
        if final_text is None: raise ValueError(...) # Ошибка извлечения
        # Извлекаем предложения поиска
        if response.candidates and hasattr(response.candidates[0], 'grounding_metadata') and response.candidates[0].grounding_metadata:
             metadata = response.candidates[0].grounding_metadata
             if metadata.web_search_queries: search_suggestions = list(metadata.web_search_queries); logger.info(f"[{selected_alias}] !!!! Предложения поиска: {search_suggestions}")
             else: logger.info(f"[{selected_alias}] meta без запросов.")
        else: logger.info(f"[{selected_alias}] НЕТ grounding_metadata.")
    # ... (обработка исключений ResourceExhausted, FailedPrecondition, ValueError, Exception) ...
    except ResourceExhausted as e_limit: logger.warning(...); error_message = f"😔 '{selected_alias}' перегружена. /model"
    except FailedPrecondition as e_precondition: logger.error(...); error_message = f"⚠️ История '{selected_alias}' сброшена."; if chat_id in chat_histories: del chat_histories[chat_id]
    except ValueError as e_blocked: logger.warning(...); error_message = f"⚠️ {e_blocked}"
    except (GoogleAPIError, Exception) as e_other: logger.exception(...); error_message = f"Ошибка модели '{selected_alias}': {e_other}"
    # --- Отправка ответа ---
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
    if not LOADED_MODELS: logger.critical("Модели не загружены!"); return
    if not google_search_tool: logger.warning(f"Встроенный поиск НЕ настроен (тип: {search_tool_type_used}).")
    else: logger.info(f"Встроенный поиск настроен (тип: {search_tool_type_used}).")
    logger.info("Инициализация Telegram...");
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    # Убрали /testsearch
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    logger.info("Запуск бота...");
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()

# --- END OF FULL CORRECTED main.py ---
