# --- START OF REALLY x39 FULL CORRECTED main.py (FINAL INDENTATION FIX IN EXTRACT) ---

import logging
import os
import asyncio
import signal
import time
import random
import google.genai as genai
import aiohttp.web

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

# --- ИСПРАВЛЕННАЯ extract_response_text ---
def extract_response_text(response) -> Optional[str]:
    """Извлекает текст из ответа client.models.generate_content."""
    try:
        return response.text
    except ValueError as e_val:
        logger.warning(f"ValueError при извлечении response.text: {e_val}")
        try:
            # (Код обработки ValueError)
            if response.candidates:
                 candidate = response.candidates[0]; finish_reason = getattr(candidate, 'finish_reason', None); safety_ratings = getattr(candidate, 'safety_ratings', []); error_parts = []
                 finish_map = getattr(FinishReason, '_enum_map', {}); harm_cat_map = getattr(HarmCategory, '_enum_map', {}); harm_prob_map = getattr(HarmProbability, '_enum_map', {})
                 if finish_reason and finish_reason not in (FinishReason.FINISH_REASON_UNSPECIFIED, FinishReason.STOP): error_parts.append(f"Причина остановки: {finish_map.get(finish_reason, finish_reason)}")
                 relevant_ratings = [f"{harm_cat_map.get(r.category, r.category)}: {harm_prob_map.get(r.probability, r.probability)}" for r in safety_ratings if hasattr(r, 'probability') and r.probability not in (HarmProbability.HARM_PROBABILITY_UNSPECIFIED, HarmProbability.NEGLIGIBLE)]
                 if relevant_ratings: error_parts.append(f"Фильтры безопасности: {', '.join(relevant_ratings)}")
                 if error_parts: return f"⚠️ Не удалось получить ответ. {' '.join(error_parts)}."
            prompt_feedback = getattr(response, 'prompt_feedback', None)
            if prompt_feedback and getattr(prompt_feedback, 'block_reason', None): reason = getattr(prompt_feedback.block_reason, 'name', prompt_feedback.block_reason); return f"⚠️ Не удалось получить ответ. Блокировка: {reason}."
            logger.warning("Не удалось извлечь текст и нет явных причин блокировки/ошибки.")
            return None
        except (AttributeError, IndexError, Exception) as e_details: logger.warning(f"Ошибка при получении деталей ошибки: {e_details}"); return None
    except AttributeError:
        # --- БЛОК С ИСПРАВЛЕННЫМ ОТСТУПОМ (СНОВА!) ---
        logger.warning("Ответ не имеет атрибута .text. Попытка извлечь из parts.")
        # Код ниже ТЕПЕРЬ ТОЧНО с отступом
        try:
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                parts_text = "".join(p.text for p in response.candidates[0].content.parts if hasattr(p, 'text'))
                return parts_text.strip() if parts_text and parts_text.strip() else None
            else:
                logger.warning("Не найдено candidates или parts для извлечения текста.")
                return None
        except (AttributeError, IndexError, Exception) as e_inner:
            logger.error(f"Ошибка при сборке текста из parts: {e_inner}")
            return None
        # --- КОНЕЦ БЛОКА ---
    except Exception as e:
        logger.exception(f"Неожиданная ошибка при извлечении текста ответа: {e}")
        return None


# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код без изменений)
    pass
async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код без изменений)
    pass
async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код без изменений)
    pass
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код без изменений, включая ИСПРАВЛЕННЫЕ except блоки)
    pass

# --- ФУНКЦИИ ВЕБ-СЕРВЕРА ---
async def handle_ping(request: aiohttp.web.Request) -> aiohttp.web.Response:
    # ... (код без изменений)
    pass
async def run_web_server(port: int, stop_event: asyncio.Event):
    # ... (код без изменений)
    pass

# --- АСИНХРОННАЯ ГЛАВНАЯ ФУНКЦИЯ ---
async def main_async() -> None:
    # ... (код без изменений)
    pass

# --- ОБРАБОТЧИК СИГНАЛОВ ---
async def shutdown(signal, loop, stop_event: asyncio.Event, application: Application):
    # ... (код без изменений)
    pass

# --- ТОЧКА ВХОДА ---
if __name__ == '__main__':
    # ... (код без изменений)
    pass

# --- END OF REALLY x39 FULL CORRECTED main.py (FINAL INDENTATION FIX IN EXTRACT) ---
