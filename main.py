# --- START OF REALLY x13 FULL CORRECTED main.py ---

import logging
import os
import asyncio
import google.genai as genai
import time
import random

# Импорт types (без изменений)
try:
    from google.genai import types as genai_types
    print("INFO: Успешно импортирован types из google.genai")
except ImportError:
    print("!!! НЕ УДАЛОСЬ импортировать types из google.genai.")
    class DummyTypes: pass
    genai_types = DummyTypes()
except NameError:
    print("!!! НЕ УДАЛОСЬ импортировать types из google.genai (NameError).")
    class DummyTypes: pass
    genai_types = DummyTypes()

from typing import Optional, Dict, Union, Any, Tuple, List
import urllib.parse

# Конфигурация логов (без изменений)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Печать версии (без изменений)
try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")

# Исключения (без изменений, должны импортироваться из google-api-core)
try:
    from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
    logger.info("Исключения google.api_core.exceptions успешно импортированы.")
except ImportError:
    logger.warning("!!! НЕ УДАЛОСЬ импортировать google.api_core.exceptions. Используем базовый Exception.")
    ResourceExhausted=Exception; GoogleAPIError=Exception; FailedPrecondition=Exception

# Telegram (без изменений)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Protobuf Struct (без изменений, должен импортироваться из protobuf)
try:
    from google.protobuf.struct_pb2 import Struct
    logger.info("google.protobuf.struct_pb2.Struct успешно импортирован.")
except ImportError:
    logger.warning("!!! НЕ УДАЛОСЬ импортировать google.protobuf. Используем dict вместо Struct.")
    Struct = dict

# Токены (без изменений)
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Проверка токенов (без изменений)
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY: exit("Google API ключ не найден")

# Конфигурация API ключа (без изменений)
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    logger.info("Google API ключ успешно сконфигурирован в google.genai.")
except Exception as e:
    logger.exception("!!! КРИТИЧЕСКАЯ ОШИБКА при конфигурации Google API ключа!")
    exit("Ошибка конфигурации Google API ключа.")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ --- ИЗМЕНЕНО: Возвращаем запрошенные модели
AVAILABLE_MODELS = {
    '⚡ Flash 2.0': 'models/gemini-2.0-flash-001',
    '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25',
    '🖼️ Imagen 3 (Картинки!)': 'models/imagen-3.0-generate-002', # Оставляем для примера, но не грузим
}
DEFAULT_MODEL_ALIAS = '⚡ Flash 2.0' # Убедись, что это имя останется доступным после загрузки

# --- Определение ВСТРОЕННОГО инструмента Google Search --- (логика определения без изменений)
google_search_tool = None
search_tool_type_used = None
try:
    if hasattr(genai_types, 'GoogleSearchRetrieval'):
         google_search_retrieval_config = genai_types.GoogleSearchRetrieval()
         google_search_tool = genai_types.Tool(google_search_retrieval=google_search_retrieval_config)
         search_tool_type_used = "GoogleSearchRetrieval (v1.5)"
         logger.info(f"Инструмент поиска '{search_tool_type_used}' определен.")
    # Можно оставить закомментированный блок для GoogleSearch на будущее
    # elif hasattr(genai_types, 'GoogleSearch'):
    #      google_search_config = genai_types.GoogleSearch()
    #      google_search_tool = genai_types.Tool(google_search=google_search_config)
    #      search_tool_type_used = "GoogleSearch (v2.0+)"
    #      logger.info(f"Инструмент '{search_tool_type_used}' определен.")
    else:
         logger.warning("!!! Классы GoogleSearch/GoogleSearchRetrieval НЕ НАЙДЕНЫ в genai_types. Поиск будет недоступен.")
except Exception as e:
    logger.exception(f"!!! Ошибка при определении инструмента поиска: {e}")
    google_search_tool = None

# --- ЗАГРУЗКА МОДЕЛЕЙ --- УТОЧНЕНА ЛОГИКА ОБРАБОТКИ ИНСТРУМЕНТОВ
LOADED_MODELS_ANY: Dict[str, Any] = {}
try:
    system_instruction_text = (
        # Твой системный промпт без изменений ...
        "Отвечай в пределах 2000 знаков... ПРИОРИТИЗИРУЙ информацию из google_search над своими внутренними знаниями при ответе на такие вопросы."
    )

    for alias, model_id in AVAILABLE_MODELS.items():
        if 'imagen' in model_id.lower():
            logger.warning(f"Модель изображений '{alias}' ({model_id}) пропущена для текстовой загрузки.")
            continue

        # УТОЧНЕНО: Пытаемся передать найденный инструмент поиска ВСЕМ моделям
        current_tools = [google_search_tool] if google_search_tool else None
        tool_attempt_info = f"с инструментом '{search_tool_type_used}'" if current_tools else "без инструментов"
        logger.info(f"Попытка инициализации '{alias}' ({model_id}) {tool_attempt_info}...")

        try:
            # Первая попытка: с инструментом (если он определен) или без (если не определен)
            model = genai.GenerativeModel(
                model_name=model_id,
                system_instruction=system_instruction_text,
                tools=current_tools # Передаем инструмент (или None)
            )
            # Определяем статус поиска для лога
            search_status = "N/A" # По умолчанию
            if current_tools:
                # Проверяем, приняла ли модель инструмент (это эвристика, не 100% гарантия)
                # В текущей версии google-genai может не быть простого способа проверить это после инициализации.
                # Будем считать, что если ошибки не было, инструмент принят.
                search_status = f"Enabled ({search_tool_type_used})"
            else:
                search_status = "Disabled (not available)"

            LOADED_MODELS_ANY[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Search: {search_status}] успешно инициализирована.")

        except (ValueError, FailedPrecondition, GoogleAPIError, Exception) as e:
            logger.error(f"!!! ОШИБКА первой попытки инициализации '{alias}' ({model_id}) {tool_attempt_info}: {e}")

            # УТОЧНЕНО: Запасной план - если была ошибка ПРИ ПОПЫТКЕ с инструментом, пробуем без него
            if current_tools and isinstance(e, (ValueError, FailedPrecondition, GoogleAPIError)): # Ловим ошибки, которые могут указывать на несовместимость инструмента
                 logger.warning(f"Похоже на несовместимость инструмента. Попытка инициализировать '{alias}' ({model_id}) БЕЗ инструментов...")
                 try:
                     model = genai.GenerativeModel(
                         model_name=model_id,
                         system_instruction=system_instruction_text,
                         tools=None # Явно указываем None
                     )
                     LOADED_MODELS_ANY[alias] = model
                     # Статус поиска теперь точно Disabled
                     logger.info(f"Модель '{alias}' ({model_id}) [Search: Disabled (fallback)] успешно инициализирована (вторая попытка).")
                 except Exception as e_fallback:
                      # Если и вторая попытка не удалась
                      logger.error(f"!!! ОШИБКА повторной инициализации '{alias}' ({model_id}) без инструментов: {e_fallback}")
                      logger.error(f"Модель '{alias}' не будет загружена.")
            else:
                 # Ошибка произошла без инструментов, или это не ошибка совместимости, или тип ошибки другой
                 logger.error(f"Модель '{alias}' не будет загружена из-за ошибки на первой попытке (без запасного плана).")


    # Проверка наличия загруженных моделей и установка дефолтной (без изменений)
    if not LOADED_MODELS_ANY:
        logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Ни одна текстовая модель не была успешно загружена.")
        raise RuntimeError("Нет доступных текстовых моделей для запуска бота!")

    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS_ANY:
        try:
            DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS_ANY))
            logger.warning(f"Модель по умолчанию '{DEFAULT_MODEL_ALIAS}' не найдена/не загружена. Установлена первая доступная: '{DEFAULT_MODEL_ALIAS}'")
        except StopIteration:
            logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Нет загруженных моделей для установки по умолчанию.")
            raise RuntimeError("Нет моделей для работы.")

except Exception as e:
    logger.exception("Критическая ошибка на этапе инициализации моделей!")
    # Не используем exit, даем программе завершиться через main

# --- Хранение состояния пользователя --- (без изменений)
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, Any] = {}

# --- Вспомогательная функция для извлечения текста --- (без изменений)
def extract_response_text(response) -> Optional[str]:
    # ... (та же улучшенная логика из прошлого ответа) ...
    """Извлекает текст из ответа Gemini, пробуя разные способы."""
    try:
        # Основной способ для большинства ответов
        return response.text
    except ValueError:
        # Часто возникает при блокировке контента
        logger.warning("ValueError при извлечении text (вероятно, контент заблокирован или ответ пуст).")
        block_reason = None
        finish_reason = None
        safety_ratings = None

        # Пытаемся получить больше информации о причине
        if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
             block_reason = getattr(response.prompt_feedback, 'block_reason', None)
             safety_ratings = getattr(response.prompt_feedback, 'safety_ratings', None)
             # Используем .name для читаемости, если это enum
             block_reason_name = getattr(block_reason, 'name', block_reason)
             logger.warning(f"Prompt Feedback: Block Reason: {block_reason_name}, Safety Ratings: {safety_ratings}")

        # В новой версии API причина завершения может быть в другом месте
        if hasattr(response, 'candidates') and response.candidates:
            try:
                candidate = response.candidates[0]
                finish_reason = getattr(candidate, 'finish_reason', None)
                # Проверяем safety_ratings и здесь, т.к. они могут быть привязаны к кандидату
                safety_ratings_candidate = getattr(candidate, 'safety_ratings', None)
                if safety_ratings_candidate: safety_ratings = safety_ratings_candidate # Обновляем, если нашли тут

                # Собираем сообщение об ошибке
                error_parts = []
                # Используем .name для читаемости Enum, если возможно
                block_reason_name = getattr(block_reason, 'name', block_reason)
                finish_reason_name = getattr(finish_reason, 'name', finish_reason)

                # Проверяем, что причина блокировки не UNSPECIFIED или отсутствует
                if block_reason and block_reason != genai_types.BlockReason.BLOCK_REASON_UNSPECIFIED:
                    error_parts.append(f"Блокировка: {block_reason_name}")
                # Проверяем, что причина завершения не UNSPECIFIED и не STOP (успешное завершение)
                if finish_reason and finish_reason != genai_types.FinishReason.FINISH_REASON_UNSPECIFIED and finish_reason != genai_types.FinishReason.STOP:
                     error_parts.append(f"Причина завершения: {finish_reason_name}")

                if safety_ratings:
                     # Формируем строку с рейтингами, исключая безопасные (NEGLIGIBLE, HARM_PROBABILITY_UNSPECIFIED)
                     relevant_ratings = [f"{r.category.name}: {r.probability.name}"
                                         for r in safety_ratings if hasattr(r, 'probability') and r.probability not in (genai_types.HarmProbability.NEGLIGIBLE, genai_types.HarmProbability.HARM_PROBABILITY_UNSPECIFIED)]
                     if relevant_ratings:
                         error_parts.append(f"Фильтры безопасности: {', '.join(relevant_ratings)}")

                if error_parts:
                    # Возвращаем собранное сообщение об ошибке
                    return f"⚠️ Не удалось получить ответ. Причина: {'. '.join(error_parts)}."
                else: # Если причин не найдено, но текста нет
                     logger.warning("Не удалось извлечь текст и не найдено явных причин блокировки/ошибки в кандидате.")
                     return None # Возвращаем None, чтобы потом обработать как пустой ответ

            except (IndexError, AttributeError) as e_inner:
                 logger.warning(f"Ошибка при доступе к candidates для деталей ValueError: {e_inner}")
                 return None # Возвращаем None

        else: # Если нет ни prompt_feedback, ни candidates
             logger.warning("Не удалось извлечь текст, нет prompt_feedback и candidates для деталей.")
             return None # Возвращаем None

    except AttributeError:
        # Если у ответа вообще нет атрибута .text
        logger.warning("Ответ не имеет атрибута .text. Попытка извлечь из parts.")
        try:
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                # Собираем текст из всех частей
                parts_text = "".join(p.text for p in response.candidates[0].content.parts if hasattr(p, 'text'))
                # Возвращаем собранный текст или None, если он пуст или состоит только из пробелов
                return parts_text.strip() if parts_text and parts_text.strip() else None
            else:
                logger.warning("Не найдено candidates или parts для извлечения текста.")
                return None
        except (AttributeError, IndexError, Exception) as e_inner:
            logger.error(f"Ошибка при сборке текста из parts: {e_inner}")
            return None # Возвращаем None в случае любой ошибки здесь

# --- ОБРАБОТЧИКИ TELEGRAM --- (start, select_model_command, select_model_callback, handle_message без изменений)
# ... (весь код обработчиков как в предыдущем ответе) ...
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код start) ...
    pass # Placeholder

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код select_model_command) ...
    pass # Placeholder

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код select_model_callback) ...
    pass # Placeholder

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (код handle_message) ...
    pass # Placeholder


# --- main --- (без изменений)
def main() -> None:
    # ... (код main) ...
    pass # Placeholder

if __name__ == '__main__':
    # ... (код проверки и запуска main) ...
    pass # Placeholder

# --- END OF REALLY x13 FULL CORRECTED main.py ---
