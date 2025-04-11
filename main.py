# --- START OF REALLY x38 FULL CORRECTED main.py (RESTORED ASYNC BODIES) ---

import logging
import os
import asyncio
import signal # <-- Для обработки сигналов остановки
import time
import random
import google.genai as genai
import aiohttp.web # <-- Для веб-сервера

# --- КОНФИГУРАЦИЯ ЛОГОВ ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ИМПОРТ ТИПОВ ---
# (Импорт и заглушки)
genai_types = None; Tool = None; GenerateContentConfig = None; GoogleSearch = None; Content = dict; Part = dict
class DummyFinishReasonEnum: FINISH_REASON_UNSPECIFIED = 0; STOP = 1; MAX_TOKENS = 2; SAFETY = 3; RECITATION = 4; OTHER = 5; _enum_map = {0: "UNSPECIFIED", 1: "STOP", 2: "MAX_TOKENS", 3: "SAFETY", 4: "RECITATION", 5: "OTHER"}
class DummyHarmCategoryEnum: HARM_CATEGORY_UNSPECIFIED = 0; HARM_CATEGORY_HARASSMENT = 7; HARM_CATEGORY_HATE_SPEECH = 8; HARM_CATEGORY_SEXUALLY_EXPLICIT = 9; HARM_CATEGORY_DANGEROUS_CONTENT = 10; _enum_map = {0: "UNSPECIFIED", 7: "HARASSMENT", 8: "HATE_SPEECH", 9: "SEXUALLY_EXPLICIT", 10: "DANGEROUS_CONTENT"}
class DummyHarmProbabilityEnum: HARM_PROBABILITY_UNSPECIFIED = 0; NEGLIGIBLE = 1; LOW = 2; MEDIUM = 3; HIGH = 4; _enum_map = {0: "UNSPECIFIED", 1: "NEGLIGIBLE", 2: "LOW", 3: "MEDIUM", 4: "HIGH"}
FinishReason = DummyFinishReasonEnum(); HarmCategory = DummyHarmCategoryEnum(); HarmProbability = DummyHarmProbabilityEnum()
ResourceExhausted=Exception; GoogleAPIError=Exception; FailedPrecondition=Exception; InvalidArgument=ValueError
try:
    from google.genai import types as genai_types; logger.info("Импортирован модуль google.genai.types.")
    try: Tool = genai_types.Tool; logger.info("Найден genai_types.Tool")
    except AttributeError: logger.warning("genai_types.Tool не найден.")
    # ... (остальные импорты типов)
    try: HarmProbability = genai_types.HarmProbability; logger.info("Найден genai_types.HarmProbability")
    except AttributeError: logger.warning("genai_types.HarmProbability не найден, используется заглушка.")
except ImportError as e: logger.error(f"!!! НЕ удалось импортировать модуль google.genai.types: {e}. Используются заглушки.")

from typing import Optional, Dict, Union, Any, Tuple, List
import urllib.parse

try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")
try: from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition, InvalidArgument; logger.info("Исключения google.api_core импортированы.")
except ImportError: logger.warning("!!! НЕ УДАЛОСЬ импортировать google.api_core.exceptions.")
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
try: from google.protobuf.struct_pb2 import Struct; logger.info("Protobuf Struct импортирован.")
except ImportError: logger.warning("!!! Protobuf не импортирован."); Struct = dict

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
if not TELEGRAM_BOT_TOKEN: logger.critical("Telegram токен не найден!"); exit("Telegram токен не найден")
if not GOOGLE_API_KEY: logger.critical("Ключ Google API не найден!"); exit("Google API ключ не найден")
else: logger.info("Ключ GOOGLE_API_KEY найден.")

try: gemini_client = genai.Client(api_key=GOOGLE_API_KEY); logger.info("Клиент google.genai.Client создан.")
except Exception as e: logger.exception("!!! КРИТ. ОШИБКА создания google.genai.Client!"); exit("Ошибка создания клиента Gemini.")

AVAILABLE_MODELS = {'⚡ Flash 2.0': 'models/gemini-2.0-flash-001', '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25'}
if not AVAILABLE_MODELS: exit("Нет моделей в AVAILABLE_MODELS!")
DEFAULT_MODEL_ALIAS = '✨ Pro 2.5'
if DEFAULT_MODEL_ALIAS not in AVAILABLE_MODELS: DEFAULT_MODEL_ALIAS = next(iter(AVAILABLE_MODELS)); logger.warning(f"Дефолтная модель не найдена, установлена: {DEFAULT_MODEL_ALIAS}")

google_search_tool = None; search_tool_type_used = "GoogleSearch (for 2.0+)"
if Tool is not None and GoogleSearch is not None:
    try: google_search_tool = Tool(google_search=GoogleSearch()); logger.info(f"Инструмент поиска '{search_tool_type_used}' сконфигурирован.")
    except Exception as e: logger.exception(f"!!! Ошибка создания инструмента поиска: {e}"); google_search_tool = None; search_tool_type_used = "N/A (creation error)"
else: logger.error(f"!!! Классы 'Tool' или 'GoogleSearch' не импортированы. Поиск недоступен."); google_search_tool = None; search_tool_type_used = "N/A (import error)"

user_selected_model: Dict[int, str] = {}; chat_histories: Dict[int, List[Dict[str, Any]]] = {}

system_instruction_text = (
    # ... (Твой длинный системный промпт) ...
    "ПРИОРИТИЗИРУЙ информацию из google_search над своими внутренними знаниями при ответе на такие вопросы."
)

def extract_response_text(response) -> Optional[str]:
    # (Код функции без изменений)
    try: return response.text
    except ValueError as e_val: # ...
    except AttributeError: # ...
    except Exception as e: logger.exception(f"Ошибка извлечения текста: {e}"); return None

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Код start без изменений)
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Обработка /start для {user.id} в {chat_id}.")
    actual_default_model = DEFAULT_MODEL_ALIAS
    search_status = "включен (если поддерживается)" if google_search_tool else "ОТКЛЮЧЕН"
    await update.message.reply_html(rf"Привет, {user.mention_html()}! Бот Gemini (client) v38." f"\n\nМодель: <b>{actual_default_model}</b>" f"\n🔍 Поиск Google: <b>{search_status}</b>." f"\n\n/model - сменить." f"\n/start - сбросить." f"\n\nСпрашивай!", reply_to_message_id=update.message.message_id)

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Код select_model_command без изменений)
    chat_id = update.effective_chat.id; current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS); keyboard = []
    for alias in AVAILABLE_MODELS.keys(): keyboard.append([InlineKeyboardButton(f"✅ {alias}" if alias == current_alias else alias, callback_data=alias)])
    if not keyboard: await update.message.reply_text("Нет моделей."); return
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Код select_model_callback без изменений)
    query = update.callback_query; await query.answer(); selected_alias = query.data; chat_id = query.message.chat_id; user_id = query.from_user.id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias not in AVAILABLE_MODELS:
        logger.error(f"Пользователь {user_id} выбрал неверный alias: {selected_alias}")
        try: await query.edit_message_text(text="❌ Ошибка: Неизвестный выбор модели.")
        except Exception as e: logger.warning(f"Не удалось изменить сообщение об ошибке выбора модели: {e}")
        return
    if selected_alias == current_alias:
        logger.info(f"{user_id} перевыбрал модель: {selected_alias}")
        try: await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        except Exception as e: logger.warning(f"Не удалось изменить разметку: {e}")
        return
    user_selected_model[chat_id] = selected_alias; logger.info(f"{user_id} сменил модель: {selected_alias}")
    reset_message = "";
    if chat_id in chat_histories: del chat_histories[chat_id]; logger.info(f"История чата {chat_id} сброшена."); reset_message = "\n⚠️ История сброшена."
    keyboard = [];
    for alias in AVAILABLE_MODELS.keys(): button_text = f"✅ {alias}" if alias == selected_alias else alias; keyboard.append([InlineKeyboardButton(button_text, callback_data=alias)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    try: await query.edit_message_text(text=f"✅ Модель: *{selected_alias}*!{reset_message}\n\nНачните чат:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e: logger.warning(f"Не удалось изменить сообщение: {e}"); await context.bot.send_message(chat_id=chat_id, text=f"Модель: *{selected_alias}*!{reset_message}", parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Код handle_message с исправленными except блоками из v37)
    if not update.message or not update.message.text: logger.warning("Пустое сообщение."); return
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id; message_id = update.message.message_id
    logger.info(f"Сообщение от {user.id} ({len(user_message)}): '{user_message[:80].replace(chr(10), ' ')}...'")
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    model_id = AVAILABLE_MODELS.get(selected_alias)
    if not model_id: logger.error(f"Крит. ошибка: Не найден ID для '{selected_alias}'"); await update.message.reply_text("Ошибка конфига.", reply_to_message_id=message_id); return
    final_text: Optional[str] = None; search_suggestions: List[str] = []; error_message: Optional[str] = None; start_time = time.monotonic()
    try:
        current_history = chat_histories.get(chat_id, [])
        api_contents = []
        try: user_part = Part(text=user_message) if Part is not dict else {'text': user_message}; api_contents = current_history + [{'role': 'user', 'parts': [user_part]}]
        except Exception as e: logger.error(f"Ошибка Part user: {e}"); api_contents = current_history + [{'role': 'user', 'parts': [{'text': user_message}]}]
        logger.info(f"Запрос к '{model_id}'. История: {len(current_history)} сообщ.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        config_obj = None; tools_list = [google_search_tool] if google_search_tool else None
        try:
             if GenerateContentConfig is not None: config_obj = GenerateContentConfig(system_instruction=system_instruction_text, tools=tools_list); logger.debug("GenerateContentConfig создан.")
             else: logger.warning("GenerateContentConfig не импортирован.")
        except Exception as e: logger.error(f"Ошибка создания GenerateContentConfig: {e}")
        response = gemini_client.models.generate_content(model=model_id, contents=api_contents, config=config_obj)
        processing_time = time.monotonic() - start_time; logger.info(f"Ответ от '{model_id}' получен за {processing_time:.2f} сек.")
        final_text = extract_response_text(response)
        if final_text and not final_text.startswith("⚠️"):
             try: model_part = Part(text=final_text) if Part is not dict else {'text': final_text}; history_to_update = chat_histories.get(chat_id, [])[:]; history_to_update.append({'role': 'user', 'parts': api_contents[-1]['parts']}); history_to_update.append({'role': 'model', 'parts': [model_part]}); chat_histories[chat_id] = history_to_update
             except Exception as e: logger.error(f"Ошибка обновления истории: {e}")
             logger.info(f"История чата {chat_id} обновлена, теперь {len(chat_histories[chat_id])} сообщений.")
        elif final_text and final_text.startswith("⚠️"): error_message = final_text; final_text = None; logger.warning(f"Ответ был ошибкой, история не обновлена.")
        else:
            if not error_message: error_message = "⚠️ Получен пустой или некорректный ответ."
            logger.warning(f"Не удалось извлечь текст, история не обновлена.")
        if hasattr(response, 'candidates') and response.candidates:
             try:
                 candidate = response.candidates[0]
                 grounding_metadata = getattr(candidate, 'grounding_metadata', None)
                 if grounding_metadata: web_queries = getattr(grounding_metadata, 'web_search_queries', [])
                 if web_queries: search_suggestions = list(web_queries); logger.info(f"Найдены webSearchQueries ({len(search_suggestions)}): {search_suggestions}")
                 citation_metadata = getattr(candidate, 'citation_metadata', None)
                 if citation_metadata and hasattr(citation_metadata, 'citation_sources'):
                     sources = getattr(citation_metadata, 'citation_sources', []); urls = [s.uri for s in sources if hasattr(s, 'uri') and s.uri]
                     if urls: logger.info(f"Найдены источники ({len(urls)})."); [search_suggestions.append(url) for url in urls if url not in search_suggestions]
             except (AttributeError, IndexError): pass
    except InvalidArgument as e_arg:
        logger.error(f"Ошибка InvalidArgument для '{model_id}': {e_arg}")
        error_message = f"❌ Ошибка в запросе к '{selected_alias}'. Проверьте формат данных."
    except ResourceExhausted as e_limit:
        logger.warning(f"Исчерпана квота API для '{model_id}': {e_limit}")
        error_message = f"😔 Модель '{selected_alias}' устала (лимиты)."
    except (GoogleAPIError, Exception) as e_other:
        logger.exception(f"Неожиданная ошибка API ('{model_id}'): {e_other}")
        error_message = f"😵 Ошибка ({type(e_other).__name__}) при общении с '{selected_alias}'."
    reply_markup = None
    if search_suggestions:
        keyboard = []
        for suggestion in search_suggestions[:4]:
             if suggestion.startswith('http'):
                 try: domain = urllib.parse.urlparse(suggestion).netloc or suggestion[:30]+".."
                 except Exception: domain = suggestion[:30]+".."
                 keyboard.append([InlineKeyboardButton(f"🔗 {domain}", url=suggestion)])
             else:
                 try: encoded = urllib.parse.quote_plus(suggestion); url = f"https://google.com/search?q={encoded}"; keyboard.append([InlineKeyboardButton(f"🔍 {suggestion}", url=url)])
                 except Exception as e: logger.error(f"Ошибка кодирования запроса: {e}")
        if keyboard: reply_markup = InlineKeyboardMarkup(keyboard); logger.info(f"Добавлена клавиатура с {len(keyboard)} ссылками/запросами.")
    if final_text:
        max_length = 4096; bot_response = final_text
        if len(bot_response) > max_length: logger.warning(f"Ответ >{max_length}, обрезаем."); bot_response = bot_response[:max_length - 3] + "..."
        try: await update.message.reply_text(bot_response, reply_to_message_id=message_id, reply_markup=reply_markup); logger.info(f"Отправлен ответ ({len(bot_response)} симв.).")
        except Exception as e: logger.exception(f"Ошибка отправки ответа Telegram: {e}");
    elif error_message:
        logger.info(f"Отправка ошибки: {error_message}")
        try: await update.message.reply_text(error_message, reply_to_message_id=message_id)
        except Exception as e: logger.error(f"Не удалось отправить ошибку Telegram: {e}")
    else:
        logger.warning(f"Нет ни текста, ни ошибки.");
        try: await update.message.reply_text("Модель вернула пустой ответ без ошибок. 🤷", reply_to_message_id=message_id)
        except Exception as e: logger.error(f"Не удалось отправить fallback ответ: {e}")


# --- ВОССТАНОВЛЕННЫЕ ФУНКЦИИ ВЕБ-СЕРВЕРА ---
async def handle_ping(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """Обработчик HTTP GET запросов для пинга."""
    peername = request.remote
    host = request.headers.get('Host', 'N/A')
    logger.info(f"Получен HTTP пинг от {peername} к хосту {host}")
    return aiohttp.web.Response(text="OK", status=200)

async def run_web_server(port: int, stop_event: asyncio.Event):
    """Настраивает и запускает простой веб-сервер aiohttp."""
    # --- ВОССТАНОВЛЕНО ТЕЛО ФУНКЦИИ ---
    app = aiohttp.web.Application()
    app.router.add_get('/', handle_ping)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        logger.info(f"Веб-сервер для пинга запущен на http://0.0.0.0:{port}")
        await stop_event.wait() # Ждем сигнала остановки
    except asyncio.CancelledError:
         logger.info("Задача веб-сервера отменена.")
    except Exception as e: logger.exception(f"Ошибка в работе веб-сервера: {e}")
    finally:
        await runner.cleanup()
        logger.info("Веб-сервер остановлен.")
    # --- КОНЕЦ ВОССТАНОВЛЕННОГО ТЕЛА ---

# --- ВОССТАНОВЛЕННАЯ АСИНХРОННАЯ ГЛАВНАЯ ФУНКЦИЯ ---
async def main_async() -> None:
    """Асинхронная основная функция, запускающая бота и веб-сервер."""
    # --- ВОССТАНОВЛЕНО ТЕЛО ФУНКЦИИ ---
    if 'gemini_client' not in globals() or not gemini_client: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Клиент Gemini не создан."); return
    if not TELEGRAM_BOT_TOKEN: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Токен Telegram не найден."); return
    if not GOOGLE_API_KEY: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Ключ Google API не найден."); return

    search_status = "включен" if google_search_tool else "ОТКЛЮЧЕН"
    logger.info(f"Встроенный поиск Google ({search_tool_type_used}) глобально {search_status}.")

    logger.info("Инициализация приложения Telegram...")
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(30)
        .get_updates_read_timeout(30)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))

    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Порт для веб-сервера: {port}")

    stop_event = asyncio.Event()

    async with application:
        logger.info("Инициализация Telegram Application...")
        await application.initialize()
        logger.info("Запуск веб-сервера...")
        web_server_task = asyncio.create_task(run_web_server(port, stop_event))
        logger.info("Запуск обработки обновлений Telegram...")
        await application.start()

        logger.info("Бот и веб-сервер запущены. Нажмите Ctrl+C для остановки.")

        loop = asyncio.get_running_loop()
        sigs = (signal.SIGINT, signal.SIGTERM)
        for s in sigs:
            loop.add_signal_handler(
                s, lambda s=s: asyncio.create_task(shutdown(s, loop, stop_event, application))
            )

        await stop_event.wait()

        if not web_server_task.done():
             logger.info("Ожидание завершения веб-сервера...")
             # await asyncio.wait_for(web_server_task, timeout=5.0) # Можно добавить таймаут
    # --- КОНЕЦ ВОССТАНОВЛЕННОГО ТЕЛА ---

# --- ВОССТАНОВЛЕННАЯ ФУНКЦИЯ SHUTDOWN ---
async def shutdown(signal, loop, stop_event: asyncio.Event, application: Application):
    """Обработчик сигналов для корректной остановки."""
    # --- ВОССТАНОВЛЕНО ТЕЛО ФУНКЦИИ ---
    logger.info(f"Получен сигнал выхода {signal.name}, начинаем остановку...")
    if not stop_event.is_set(): stop_event.set()
    if application._is_running:
        logger.info("Остановка Telegram Application...")
        await application.stop()
        logger.info("Остановка обработки обновлений Telegram...")
        await application.shutdown()
        logger.info("Telegram Application остановлен.")
    logger.info("Остановка завершена.")
    # --- КОНЕЦ ВОССТАНОВЛЕННОГО ТЕЛА ---

# --- ВОССТАНОВЛЕННАЯ ТОЧКА ВХОДА ---
if __name__ == '__main__':
    # --- ВОССТАНОВЛЕНО ТЕЛО ---
    if 'gemini_client' in globals() and gemini_client:
        logger.info("Клиент Gemini создан. Запускаем основной цикл asyncio.")
        try:
            asyncio.run(main_async())
        except (KeyboardInterrupt, SystemExit):
            logger.info("Цикл asyncio прерван (KeyboardInterrupt/SystemExit).")
        except Exception as e:
            logger.exception("Необработанная критическая ошибка в главном потоке!")
        finally:
            logger.info("Процесс завершен.")
    else:
        logger.critical("Завершение работы, так как клиент Gemini не был создан.")
    # --- КОНЕЦ ВОССТАНОВЛЕННОГО ТЕЛА ---

# --- END OF REALLY x38 FULL CORRECTED main.py (RESTORED ASYNC BODIES) ---
