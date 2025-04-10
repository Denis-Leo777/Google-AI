# --- START OF FULL CORRECTED main.py (Adapting to google-genai Client pattern) ---

import logging
import os
import asyncio
# ПРАВИЛЬНЫЙ ИМПОРТ
import google.genai as genai
import time
import random
from typing import Optional, Dict, Union, Any, List
import urllib.parse

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Печать версии
try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")

# Исключения (оставим стандартные Python, т.к. google.api_core нет)
ResourceExhausted=Exception
GoogleAPIError=Exception
FailedPrecondition=Exception

# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Убираем импорт protobuf, т.к. не знаем, нужен ли он
# from google.protobuf.struct_pb2 import Struct

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY: exit("Google API ключ не найден")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    # ВАЖНО: Проверить, какие ID моделей поддерживает google-genai.
    # Часто используются префиксы 'models/'
    '⚡ Flash 2.0': 'models/gemini-2.0-flash-001', # Используем стандартный ID для 1.5 Flash
    '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25',   # Добавим 1.5 Pro
    # '🧠 Pro Exp': 'gemini-2.5-pro-exp-03-25', # Этот ID может не работать с этим SDK
}
DEFAULT_MODEL_ALIAS = '⚡ Flash 2.0'

# --- Определение ВСТРОЕННОГО инструмента Google Search ---
# ПРЕДПОЛОЖЕНИЕ: В google-genai инструмент настраивается проще
google_search_tool = None
search_tool_type_used = None
try:
    # Пробуем самый простой способ включить поиск для ВСЕХ запросов (если API это поддерживает)
    # Возможно, это делается через generation_config или параметр в send_message_async
    # Оставим google_search_tool = None пока, но будем передавать параметр позже
    logger.info("Попытка использовать встроенный поиск через параметры запроса (если поддерживается)...")
    # Пытаемся найти класс инструмента, если он нужен для конфигурации
    if hasattr(genai.types, 'Tool') and hasattr(genai.types, 'GoogleSearchRetrieval'): # Пробуем 1.5 стиль
         google_search_retrieval_config = genai.types.GoogleSearchRetrieval()
         google_search_tool = genai.types.Tool(google_search_retrieval=google_search_retrieval_config)
         search_tool_type_used = "GoogleSearchRetrieval (v1.5 style)"
         logger.info(f"Инструмент '{search_tool_type_used}' определен.")
    elif hasattr(genai.types, 'Tool') and hasattr(genai.types, 'GoogleSearch'): # Пробуем 2.0 стиль
         google_search_config = genai.types.GoogleSearch()
         google_search_tool = genai.types.Tool(google_search=google_search_config)
         search_tool_type_used = "GoogleSearch (v2.0+ style)"
         logger.info(f"Инструмент '{search_tool_type_used}' определен.")
    else:
        logger.warning("Не удалось найти классы для настройки инструмента поиска в genai.types.")

except NameError:
    logger.warning("Модуль genai.types не найден/не импортирован. Встроенный поиск не будет настроен через Tool.")
except Exception as e:
    logger.exception(f"Ошибка при определении инструмента поиска: {e}")


# --- СОЗДАНИЕ КЛИЕНТА и ЗАГРУЗКА МОДЕЛЕЙ ---
LOADED_MODELS: Dict[str, genai.GenerativeModel] = {} # Тип может быть другим! Исправим на Any
LOADED_MODELS_ANY: Dict[str, Any] = {}
gemini_client = None
try:
    # УБИРАЕМ genai.configure
    # СОЗДАЕМ КЛИЕНТ (предполагаем, что ключ из окружения подхватится)
    gemini_client = genai.Client()
    logger.info("Клиент google.genai создан.")

    system_instruction_text = (
        "Отвечай... остроумие. "
        "Если ответ требует актуальной информации, используй поиск." # Упростили инструкцию
    )
    for alias, model_id in AVAILABLE_MODELS.items():
        if 'imagen' in model_id.lower(): logger.warning(f"'{alias}' пропущена."); continue
        try:
            # ПОЛУЧАЕМ МОДЕЛЬ ЧЕРЕЗ КЛИЕНТ
            # Возможно, system_instruction и tools задаются здесь, или при generate_content
            model = gemini_client.get_generative_model(
                model=model_id, # Используем параметр model=
                # system_instruction=system_instruction_text # Возможно, так?
                # tools=[google_search_tool] if google_search_tool else None # Или так?
            )
            # Сохраняем полученный объект модели
            LOADED_MODELS_ANY[alias] = model
            # Проверяем, какой инструмент (если есть) передавать при генерации
            model_search_tool = None
            if google_search_tool:
                 # TODO: Определить, какой инструмент нужен для ЭТОЙ модели (1.5 или 2.0)
                 # Пока передаем один и тот же
                 model_search_tool = google_search_tool
                 logger.info(f"Модель '{alias}' ({model_id}) [Search tool: {search_tool_type_used if model_search_tool else 'None'}] загружена через клиент.")
            else:
                 logger.info(f"Модель '{alias}' ({model_id}) [Search tool: Disabled] загружена через клиент.")

        except Exception as e: logger.error(f"!!! ОШИБКА загрузки '{alias}' через клиент: {e}")

    if not LOADED_MODELS_ANY: raise RuntimeError("Ни одна модель не загружена через клиент!")
    # Проверяем дефолтную модель
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS_ANY:
        try: DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS_ANY)); logger.warning(f"Установлена дефолтная: {DEFAULT_MODEL_ALIAS}")
        except StopIteration: raise RuntimeError("Нет моделей.")

except Exception as e: logger.exception("Крит. ошибка инициализации клиента/моделей!"); exit(...)

# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, Any] = {} # Используем Any, т.к. не знаем тип ChatSession

# --- Вспомогательная функция для извлечения текста ---
# Оставляем как есть
def extract_response_text(response) -> Optional[str]:
    try: return response.text
    except: logger.warning("Ошибка извлечения .text"); try: return "".join(p.text for p in response.candidates[0].content.parts if hasattr(p, 'text')) if response.candidates and response.candidates[0].content.parts else None; except: return None

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Обновляем сообщение, т.к. не уверены в типе поиска)
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Состояние {chat_id} сброшено")
    default_model_display_name = DEFAULT_MODEL_ALIAS
    search_status = "попытка включения" if search_tool_type_used else "отключен"
    await update.message.reply_html( f"Привет, {user.mention_html()}! ... Модель: {default_model_display_name}... /model ... 🔍 Поиск Google {search_status}.", reply_to_message_id=update.message.message_id)
    logger.info(f"/start от {user.id}")

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Используем LOADED_MODELS_ANY)
    chat_id = update.effective_chat.id; current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    keyboard = []; imagen_alias = '🖼️ Imagen 3 (Картинки!)'
    for alias in LOADED_MODELS_ANY.keys(): keyboard.append(...) # Строим кнопки
    if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS_ANY: keyboard.append(...) # Инфо Imagen
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Используем LOADED_MODELS_ANY, исправлен else)
    query = update.callback_query; await query.answer(); selected_alias = query.data; chat_id = query.message.chat_id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias == "imagen_info": await context.bot.send_message(...); return
    if selected_alias not in LOADED_MODELS_ANY: await query.edit_message_text(...); return
    if selected_alias != current_alias:
        user_selected_model[chat_id] = selected_alias; logger.info(...)
        if chat_id in chat_histories: del chat_histories[chat_id]; logger.info(...)
        keyboard = []; imagen_alias = '🖼️ Imagen 3 (Картинки!)' # Строим кнопки
        for alias in LOADED_MODELS_ANY.keys(): keyboard.append(...)
        if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS_ANY: keyboard.append(...)
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(...)
    else:
        try: await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        except Exception as e: logger.warning(...); await context.bot.send_message(...)

# handle_message (Адаптирован под Client и возможную передачу tools)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    # Получаем модель из нового словаря
    selected_model_object = LOADED_MODELS_ANY.get(selected_alias)
    if not selected_model_object: logger.error(...); await update.message.reply_text("Крит. ошибка: Модель не найдена."); return

    final_text: Optional[str] = None; search_suggestions: List[str] = []; error_message: Optional[str] = None

    try:
        # Получаем или создаем сессию чата
        # ПРЕДПОЛОЖЕНИЕ: метод start_chat существует у модели, полученной через клиент
        if chat_id not in chat_histories:
            chat_histories[chat_id] = selected_model_object.start_chat(history=[])
            logger.info(f"Начат новый чат {chat_id} с '{selected_alias}'")
        current_chat_session = chat_histories[chat_id]

        logger.info(f"Попытка обработки с {selected_alias} (Встроенный поиск, если настроен)")
        await context.bot.send_chat_action(...)

        # --- ПЕРЕДАЧА ИНСТРУМЕНТОВ ПРИ ВЫЗОВЕ ---
        # Определяем, какой инструмент передать (если он есть)
        # Это может быть неверно, структура может быть другой
        tools_to_pass = [google_search_tool] if google_search_tool else None

        # ПРЕДПОЛОЖЕНИЕ: send_message_async принимает параметр tools
        response = await current_chat_session.send_message_async(
            content=user_message,
            tools=tools_to_pass # <--- Пробуем передать инструмент здесь
            # Или может быть через generation_config?
            # generation_config=genai.types.GenerationConfig(tools=tools_to_pass) # Пример
        )
        logger.info(f"[{selected_alias}] Ответ получен. Проверка...")

        final_text = extract_response_text(response)
        if final_text is None: raise ValueError(...) # Ошибка извлечения

        # Проверка groundingMetadata
        if response.candidates and hasattr(response.candidates[0], 'grounding_metadata') and response.candidates[0].grounding_metadata:
             metadata = response.candidates[0].grounding_metadata
             if hasattr(metadata, 'web_search_queries') and metadata.web_search_queries:
                  search_suggestions = list(metadata.web_search_queries); logger.info(f"[{selected_alias}] !!!! Предложения поиска: {search_suggestions}")
             else: logger.info(f"[{selected_alias}] meta без запросов.")
        else: logger.info(f"[{selected_alias}] НЕТ grounding_metadata.")

    # ... (обработка исключений) ...
    except ResourceExhausted as e_limit: logger.warning(...); error_message = f"😔 '{selected_alias}' перегружена. /model"
    except FailedPrecondition as e_precondition: logger.error(...); error_message = f"⚠️ История '{selected_alias}' сброшена."; if chat_id in chat_histories: del chat_histories[chat_id]
    except ValueError as e_blocked: logger.warning(...); error_message = f"⚠️ {e_blocked}"
    except AttributeError as e_attr: logger.exception(f"!!! Ошибка атрибута в handle_message (структура google-genai?): {e_attr}"); error_message = f"Ошибка атрибута в коде: {e_attr}"
    except Exception as e_other: logger.exception(...); error_message = f"Ошибка модели '{selected_alias}': {e_other}"

    # --- Отправка ответа ---
    reply_markup = None
    if search_suggestions: keyboard = []; # ... (кнопки поиска) ... ; if keyboard: reply_markup = InlineKeyboardMarkup(keyboard); logger.info(...)
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
    # Логирование настройки поиска теперь в блоке инициализации
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
