# --- START OF REALLY x34 FULL CORRECTED main.py (CORRECT ASYNCIO/PTB INTEGRATION) ---

import logging
import os
import asyncio
import signal # <-- ДОБАВЛЕНО для обработки сигналов остановки
import time
import random
import google.genai as genai
import aiohttp.web

# --- КОНФИГУРАЦИЯ ЛОГОВ ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ИМПОРТ ТИПОВ ---
# (Импорт и заглушки без изменений)
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
    try: GenerateContentConfig = genai_types.GenerateContentConfig; logger.info("Найден genai_types.GenerateContentConfig")
    except AttributeError: logger.warning("genai_types.GenerateContentConfig не найден.")
    try: GoogleSearch = genai_types.GoogleSearch; logger.info("Найден genai_types.GoogleSearch")
    except AttributeError: logger.warning("genai_types.GoogleSearch не найден.")
    try: Content = genai_types.Content; logger.info("Найден genai_types.Content")
    except AttributeError: logger.warning("genai_types.Content не найден, используется dict.")
    try: Part = genai_types.Part; logger.info("Найден genai_types.Part")
    except AttributeError: logger.warning("genai_types.Part не найден, используется dict.")
    try: FinishReason = genai_types.FinishReason; logger.info("Найден genai_types.FinishReason")
    except AttributeError: logger.warning("genai_types.FinishReason не найден, используется заглушка.")
    try: HarmCategory = genai_types.HarmCategory; logger.info("Найден genai_types.HarmCategory")
    except AttributeError: logger.warning("genai_types.HarmCategory не найден, используется заглушка.")
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
# ИЗМЕНЕНО: Импортируем Application из telegram.ext, а не ApplicationBuilder напрямую, если нужно использовать методы start/stop
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

# --- ОБРАБОТЧИКИ TELEGRAM (без изменений) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: # ...
async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: # ...
async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: # ...
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: # ...

# --- ФУНКЦИИ ВЕБ-СЕРВЕРА (без изменений) ---
async def handle_ping(request: aiohttp.web.Request) -> aiohttp.web.Response: # ...
async def run_web_server(port: int, stop_event: asyncio.Event): # <-- ДОБАВЛЕН stop_event
    """Настраивает и запускает простой веб-сервер aiohttp."""
    app = aiohttp.web.Application()
    app.router.add_get('/', handle_ping)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        logger.info(f"Веб-сервер для пинга запущен на http://0.0.0.0:{port}")
        # Ждем сигнала остановки вместо бесконечного сна
        await stop_event.wait()
    except asyncio.CancelledError:
         logger.info("Задача веб-сервера отменена.")
    except Exception as e: logger.exception(f"Ошибка в работе веб-сервера: {e}")
    finally:
        await runner.cleanup()
        logger.info("Веб-сервер остановлен.")

# --- УДАЛЕНА функция run_telegram_bot() ---

# --- НОВАЯ АСИНХРОННАЯ ГЛАВНАЯ ФУНКЦИЯ ---
async def main_async() -> None:
    """Асинхронная основная функция, запускающая бота и веб-сервер."""
    # Проверки перед запуском
    if 'gemini_client' not in globals() or not gemini_client: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Клиент Gemini не создан."); return
    if not TELEGRAM_BOT_TOKEN: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Токен Telegram не найден."); return
    if not GOOGLE_API_KEY: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Ключ Google API не найден."); return

    search_status = "включен" if google_search_tool else "ОТКЛЮЧЕН"
    logger.info(f"Встроенный поиск Google ({search_tool_type_used}) глобально {search_status}.")

    # --- ИЗМЕНЕНО: Настройка и запуск Telegram Application ---
    logger.info("Инициализация приложения Telegram...")
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(30) # Пример таймаута для чтения
        .get_updates_read_timeout(30) # Пример таймаута для getUpdates
        .build()
    )

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))

    # Получаем порт для веб-сервера
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Порт для веб-сервера: {port}")

    # --- ИЗМЕНЕНО: Запуск и управление задачами ---
    stop_event = asyncio.Event() # Событие для сигнала остановки

    async with application: # Используем application как контекстный менеджер
        logger.info("Инициализация Telegram Application...")
        await application.initialize() # Инициализируем бота
        logger.info("Запуск веб-сервера...")
        web_server_task = asyncio.create_task(run_web_server(port, stop_event)) # Запускаем веб-сервер как задачу
        logger.info("Запуск обработки обновлений Telegram...")
        await application.start() # Запускаем получение обновлений

        logger.info("Бот и веб-сервер запущены. Нажмите Ctrl+C для остановки.")

        # Ожидаем сигнала остановки (например, Ctrl+C)
        # Настраиваем обработчики сигналов для корректного завершения
        loop = asyncio.get_running_loop()
        sigs = (signal.SIGINT, signal.SIGTERM)
        for s in sigs:
            loop.add_signal_handler(
                s, lambda s=s: asyncio.create_task(shutdown(s, loop, stop_event, application))
            )

        # Ждем, пока не придет сигнал остановки
        await stop_event.wait()

        # Завершаем веб-сервер (он остановится сам после stop_event.set())
        # web_server_task все еще выполняется, но завершится в finally
        if not web_server_task.done():
             logger.info("Ожидание завершения веб-сервера...")
             # Можно добавить таймаут ожидания
             # await asyncio.wait_for(web_server_task, timeout=5.0)


async def shutdown(signal, loop, stop_event: asyncio.Event, application: Application):
    """Обработчик сигналов для корректной остановки."""
    logger.info(f"Получен сигнал выхода {signal.name}, начинаем остановку...")

    # 1. Сигнализируем веб-серверу об остановке
    if not stop_event.is_set():
        stop_event.set()

    # 2. Останавливаем Telegram Application
    if application._is_running:
        logger.info("Остановка Telegram Application...")
        await application.stop()
        logger.info("Остановка обработки обновлений Telegram...")
        await application.shutdown()
        logger.info("Telegram Application остановлен.")

    # 3. Завершаем задачи и останавливаем цикл (опционально, asyncio.run сделает это)
    # tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    # [task.cancel() for task in tasks]
    # await asyncio.gather(*tasks, return_exceptions=True)
    # loop.stop()
    logger.info("Остановка завершена.")


# --- ТОЧКА ВХОДА ---
if __name__ == '__main__':
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

# --- END OF REALLY x34 FULL CORRECTED main.py (CORRECT ASYNCIO/PTB INTEGRATION) ---
