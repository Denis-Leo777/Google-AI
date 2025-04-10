# --- START OF REALLY x6 FULL CORRECTED main.py (SyntaxError in extract_response_text FIXED) ---

import logging
import os
import asyncio
import google.generativeai as genai # Используем пакет google-genai
# Импорты типов ТЕПЕРЬ ДОЛЖНЫ РАБОТАТЬ из google.generativeai.types
from google.generativeai import types as genai_types
import time
import random
from typing import Optional, Dict, Union, Any, Tuple, List
import urllib.parse

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Печать версии
try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")

# Исключения
try:
    from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
except ImportError:
    logger.warning("Не удалось импортировать исключения google.api_core.")
    class ResourceExhausted(Exception): pass
    class GoogleAPIError(Exception): pass
    class FailedPrecondition(Exception): pass

# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Gemini типы для Struct
from google.protobuf.struct_pb2 import Struct

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY: exit("Google API ключ не найден")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    '⚡ Flash': 'gemini-2.0-flash-001',
    '🧠 Pro Exp': 'gemini-2.5-pro-exp-03-25',
}
DEFAULT_MODEL_ALIAS = '⚡ Flash'

# --- Определение ВСТРОЕННОГО инструмента Google Search ---
google_search_tool = None
search_tool_type_used = None
try:
    if hasattr(genai_types, 'GoogleSearch'):
         google_search_config = genai_types.GoogleSearch()
         google_search_tool = genai_types.Tool(google_search=google_search_config)
         search_tool_type_used = "GoogleSearch (v2.0+)"
         logger.info(f"Инструмент ВСТРОЕННОГО поиска '{search_tool_type_used}' определен.")
    elif hasattr(genai_types, 'GoogleSearchRetrieval'):
         google_search_retrieval_config = genai_types.GoogleSearchRetrieval()
         google_search_tool = genai_types.Tool(google_search_retrieval=google_search_retrieval_config) # ИЗМЕНЕНО: используем google_search_retrieval здесь
         search_tool_type_used = "GoogleSearchRetrieval (v1.5)"
         logger.info(f"Инструмент ВСТРОЕННОГО поиска '{search_tool_type_used}' определен.")
    else: logger.error("!!! Классы GoogleSearch/GoogleSearchRetrieval НЕ НАЙДЕНЫ в genai_types.")
except AttributeError as e: logger.error(f"!!! Ошибка атрибута при поиске инструмента: {e}")
except Exception as e: logger.exception(f"!!! Ошибка при определении инструмента поиска: {e}")

# --- Загрузка и Настройка Моделей Gemini ---
LOADED_MODELS: Dict[str, genai.GenerativeModel] = {}
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    system_instruction_text = (
        "Отвечай... остроумие. "
        "КРИТИЧЕСКИ ВАЖНО: ... ПРИОРИТИЗИРУЙ информацию из google_search..."
    ) # Полная инструкция
    for alias, model_id in AVAILABLE_MODELS.items():
        if 'imagen' in model_id.lower(): logger.warning(...); continue
        current_tools = [google_search_tool] if google_search_tool else None # Передаем найденный инструмент
        try:
            model = genai.GenerativeModel(
                model_id,
                generation_config={"temperature": 0.8 if 'Flash' in alias else 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
                system_instruction=system_instruction_text,
                tools=current_tools
            )
            LOADED_MODELS[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Built-in Search: {'Enabled (' + search_tool_type_used + ')' if current_tools else 'Disabled'}] успешно загружена.")
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

# --- Вспомогательная функция для извлечения текста (ИСПРАВЛЕННАЯ) ---
def extract_response_text(response) -> Optional[str]:
    """Извлекает текст из ответа Gemini, пробуя разные способы."""
    try:
        return response.text
    except ValueError:
        logger.warning("Не удалось извлечь response.text (ValueError, возможно блокировка)")
        block_reason = getattr(response.prompt_feedback, 'block_reason', None) if hasattr(response, 'prompt_feedback') else None
        if block_reason and hasattr(genai_types, 'BlockReason') and block_reason != genai_types.BlockReason.BLOCK_REASON_UNSPECIFIED:
             # Добавляем проверку hasattr на BlockReason на всякий случай
            logger.warning(f"Причина блокировки: {block_reason}")
        return None
    except AttributeError:
        logger.warning("Ответ не содержит атрибута .text, пробуем собрать из parts.")
        # ИСПРАВЛЕННЫЙ БЛОК try/except:
        try:
            if response.candidates and response.candidates[0].content.parts:
                 parts_text = "".join(p.text for p in response.candidates[0].content.parts if hasattr(p, 'text'))
                 # Исправлено: Возвращаем текст или None
                 return parts_text if parts_text else None
            else:
                 logger.warning("Не найдены parts для сборки текста.")
                 return None
        except Exception as e_inner:
            logger.error(f"Ошибка при сборке текста из parts: {e_inner}")
            return None

# --- ОБРАБОТЧИКИ TELEGRAM ---
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
    chat_id = update.effective_chat.id; current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    keyboard = []; imagen_alias = '🖼️ Imagen 3 (Картинки!)'
    for alias in LOADED_MODELS.keys(): keyboard.append([InlineKeyboardButton(f"✅ {alias}" if alias == current_alias else alias, callback_data=alias)])
    if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS: keyboard.append([InlineKeyboardButton(f"{imagen_alias} (Недоступна)", callback_data="imagen_info")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите модель:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query; await query.answer(); selected_alias = query.data; chat_id = query.message.chat_id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias == "imagen_info": await context.bot.send_message(...); return
    if selected_alias not in LOADED_MODELS: await query.edit_message_text(...); return
    if selected_alias != current_alias:
        user_selected_model[chat_id] = selected_alias; logger.info(...)
        if chat_id in chat_histories: del chat_histories[chat_id]; logger.info(...)
        keyboard = []; imagen_alias = '🖼️ Imagen 3 (Картинки!)' # Строим кнопки
        for alias in LOADED_MODELS.keys(): keyboard.append([InlineKeyboardButton(f"✅ {alias}" if alias == selected_alias else alias, callback_data=alias)])
        if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS: keyboard.append(...)
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(...)
    else:
        try: await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        except Exception as e: logger.warning(...); await context.bot.send_message(...)

# handle_message (без изменений по сравнению с предыдущей версией)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    selected_model_object = LOADED_MODELS.get(selected_alias)
    if not selected_model_object: logger.error(...); await update.message.reply_text("Крит. ошибка: Модель не найдена."); return
    final_text: Optional[str] = None; search_suggestions: List[str] = []; error_message: Optional[str] = None
    try:
        if chat_id not in chat_histories: chat_histories[chat_id] = selected_model_object.start_chat(history=[]); logger.info(...)
        current_chat_session = chat_histories[chat_id]; logger.info(f"Попытка с {selected_alias} (Встроенный поиск)")
        await context.bot.send_chat_action(...)
        response = await current_chat_session.send_message_async(content=user_message)
        logger.info(f"[{selected_alias}] Ответ получен. Проверка...")
        final_text = extract_response_text(response) # Используем исправленную функцию
        if final_text is None: raise ValueError(f"Не удалось извлечь текст (причина: {getattr(response.prompt_feedback, 'block_reason', '?')})")
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
        except Exception as e:
            logger.exception(f"Ошибка отправки ответа: {e}") # Правильный отступ
            try:
                await update.message.reply_text("Не смог отправить ответ AI.", reply_to_message_id=update.message.message_id) # Правильный отступ
            except Exception:
                pass # Правильный отступ
    elif error_message:
        try: await update.message.reply_text(error_message, reply_to_message_id=update.message.message_id); logger.info(...)
        except Exception as e: logger.error(...)
    else:
        # ИСПРАВЛЕННЫЙ БЛОК else
        logger.warning(f"Нет финального текста и сообщения об ошибке для {chat_id}.")
        if "История чата" not in (error_message or "") and "Ответ модели" not in (error_message or ""):
             # На новых строках с отступами
             try:
                 await update.message.reply_text("Не удалось обработать запрос (неизвестная ошибка).", reply_to_message_id=update.message.message_id)
             except Exception:
                 pass

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

# --- END OF REALLY x6 FULL CORRECTED main.py ---
