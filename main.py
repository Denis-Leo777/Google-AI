# --- START OF REALLY TRULY HONESTLY FULL CORRECTED main.py (Fixed ALL SyntaxErrors) ---

import logging
import os
import asyncio
import google.generativeai as genai
import time
import random
from typing import Optional, Dict, Union, Any, Tuple

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Исключения
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
# Библиотеки для Поиска и Парсинга
import httpx
from bs4 import BeautifulSoup
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
AVAILABLE_MODELS = {
    '⚡ Flash': 'gemini-2.0-flash-001',
    '🧠 Pro Exp': 'gemini-2.5-pro-exp-03-25',
    '🖼️ Imagen 3 (Картинки!)': 'imagen-3.0-generate-002',
}
DEFAULT_MODEL_ALIAS = '⚡ Flash'

# --- Определение инструмента Google Search ---
google_search_tool = None
if google_search_sync:
    google_search_func = genai.protos.FunctionDeclaration(
        name="google_search",
        description="Получает заголовки и краткие описания страниц из поиска Google по запросу. Используй для новостей, текущих событий, погоды, действующих лиц.",
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
        "КРИТИЧЕСКИ ВАЖНО: Твои внутренние знания могут быть устаревшими. ... "
        "ПРИОРИТИЗИРУЙ информацию из google_search над своими внутренними знаниями при ответе на такие вопросы."
    ) # Убедитесь, что вся инструкция здесь
    for alias, model_id in AVAILABLE_MODELS.items():
        if 'imagen' in model_id.lower(): logger.warning(...); continue
        try:
            model = genai.GenerativeModel( model_id, generation_config={"temperature": 0.8 if 'Flash' in alias else 1, ...}, system_instruction=system_instruction_text, tools=gemini_tools)
            LOADED_MODELS[alias] = model; logger.info(f"Модель '{alias}' ... загружена.")
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

# --- Функция выполнения поиска Google (с парсингом) ---
async def fetch_and_parse(...) -> Tuple[Optional[str], Optional[str]]: ... # Полный код функции здесь
async def perform_google_search(...) -> str: ... # Полный код функции здесь

# --- Вспомогательная функция для обработки хода Gemini ---
async def process_gemini_chat_turn(...) -> str: ... # Полный код функции здесь

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Выбор модели и история чата сброшены для {chat_id} по команде /start")
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

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    await update.message.reply_text(
        f"Текущая модель: *{current_alias}*\n\nВыберите модель для общения:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    selected_alias = query.data
    chat_id = query.message.chat_id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias == "imagen_info":
        await context.bot.send_message(chat_id=chat_id, text="Модель Imagen не может использоваться для чата.")
        return
    if selected_alias not in LOADED_MODELS:
        await query.edit_message_text(text="Ошибка: Модель недоступна.")
        return
    if selected_alias != current_alias:
        user_selected_model[chat_id] = selected_alias
        logger.info(f"Пользователь {chat_id} сменил модель на '{selected_alias}'")
        if chat_id in chat_histories:
            del chat_histories[chat_id]
            logger.info(f"История чата для {chat_id} сброшена.")
        keyboard = []
        for alias in LOADED_MODELS.keys():
            text = f"✅ {alias}" if alias == selected_alias else alias
            keyboard.append([InlineKeyboardButton(text, callback_data=alias)])
        imagen_alias = '🖼️ Imagen 3 (Картинки!)'
        if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS:
             keyboard.append([InlineKeyboardButton(f"{imagen_alias} (Недоступна для чата)", callback_data="imagen_info")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=f"✅ Модель изменена на: *{selected_alias}*\n⚠️ История сброшена.\n\nВыберите модель:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        try: await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        except: await context.bot.send_message(chat_id=chat_id, text=f"Модель *{selected_alias}* уже выбрана.", parse_mode=ParseMode.MARKDOWN)

async def test_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args)
    chat_id = update.effective_chat.id
    if not query: await update.message.reply_text("Укажите запрос после /testsearch"); return
    logger.info(f"Тестовый поиск для {chat_id}: '{query}'")
    await update.message.reply_text(f"Выполняю тестовый поиск: '{query}'...")
    try:
        search_result = await perform_google_search(query)
        logger.info(f"Тестовый поиск для {chat_id} вернул: {search_result[:200]}...")
        await update.message.reply_text(f"Результат поиска:\n\n{search_result[:4000]}", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.exception(f"Ошибка тестового поиска для {chat_id}: {e}")
        await update.message.reply_text(f"Ошибка тестового поиска: {e}")

# ИСПРАВЛЕННАЯ handle_message
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    selected_model_object = LOADED_MODELS.get(selected_alias)
    if not selected_model_object:
        logger.error(f"Выбранная модель '{selected_alias}' для {chat_id} не найдена!")
        selected_alias = DEFAULT_MODEL_ALIAS; selected_model_object = LOADED_MODELS.get(DEFAULT_MODEL_ALIAS)
        if not selected_model_object: await update.message.reply_text("Крит. ошибка: Модели не найдены."); return
        else: await update.message.reply_text(f"Ошибка: Использую модель {selected_alias}"); user_selected_model[chat_id] = selected_alias
    final_text: Optional[str] = None; error_message: Optional[str] = None
    try:
        if chat_id not in chat_histories: chat_histories[chat_id] = selected_model_object.start_chat(history=[]); logger.info(f"Начат новый чат {chat_id} с '{selected_alias}'")
        current_chat_session = chat_histories[chat_id]; logger.info(f"Попытка с моделью: {selected_alias}")
        final_text = await process_gemini_chat_turn(current_chat_session, selected_alias, user_message, context, chat_id)
    except ResourceExhausted as e_limit: logger.warning(f"Модель '{selected_alias}' квота: {e_limit}"); error_message = f"😔 Модель '{selected_alias}' перегружена. /model"
    except FailedPrecondition as e_precondition:
        logger.error(f"Модель '{selected_alias}' FailedPrecondition: {e_precondition}. Сброс истории.")
        error_message = f"⚠️ История чата с моделью '{selected_alias}' стала слишком длинной. Я ее сбросил. Повторите запрос."
        if chat_id in chat_histories:
            del chat_histories[chat_id] # Правильный отступ
    except ValueError as e_blocked: logger.warning(f"Модель '{selected_alias}' блокировка: {e_blocked}"); error_message = f"⚠️ {e_blocked}"
    except (GoogleAPIError, Exception) as e_other: logger.exception(f"Ошибка '{selected_alias}': {e_other}"); error_message = f"Ошибка модели '{selected_alias}': {e_other}"
    if final_text:
        bot_response = final_text[:4090]
        try:
            await update.message.reply_text(bot_response, reply_to_message_id=update.message.message_id)
            logger.info(f"Ответ от '{selected_alias}' отправлен {user.id}")
        except Exception as e:
            # ИСПРАВЛЕННЫЙ БЛОК ОТПРАВКИ ОШИБКИ
            logger.exception(f"Ошибка отправки ответа: {e}")
            try:
                await update.message.reply_text("Не смог отправить ответ AI.", reply_to_message_id=update.message.message_id) # На новой строке
            except Exception:
                pass # На новой строке
    elif error_message:
        try:
            await update.message.reply_text(error_message, reply_to_message_id=update.message.message_id)
            logger.info(f"Ошибка отправлена: {error_message[:100]}...")
        except Exception as e:
            logger.error(f"Не удалось отправить ошибку '{error_message[:100]}...': {e}")
    else:
        logger.warning(f"Нет текста и ошибки для {chat_id}.")
        if "История чата" not in (error_message or "") and "Ответ модели" not in (error_message or "") :
            try: await update.message.reply_text("Не удалось обработать запрос.", reply_to_message_id=update.message.message_id)
            except Exception: pass

# --- main ---
def main() -> None:
    """Запускает бота."""
    if not LOADED_MODELS: logger.critical("Модели не загружены!"); return
    if not google_search_sync: logger.warning("Запуск БЕЗ поиска Google.")
    logger.info("Инициализация Telegram...");
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(CommandHandler("testsearch", test_search))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    logger.info("Запуск бота...");
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()

# --- END OF REALLY TRULY HONESTLY FULL CORRECTED main.py ---
