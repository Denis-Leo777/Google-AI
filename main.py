# --- START OF REALLY x18 FULL CORRECTED main.py (FIXED TYPE IMPORTS) ---

import logging
import os
import asyncio
# Используем правильную библиотеку
import google.genai as genai
import time
import random

# --- ИСПРАВЛЕННЫЙ ИМПОРТ ТИПОВ ---
# Пытаемся импортировать все нужные типы ИЗ google.genai.types
try:
    from google.genai import types as genai_types # Общий импорт пространства имен
    # Явно импортируем классы, используемые в коде
    from google.genai.types import Tool, GenerateContentConfig, GoogleSearch, Content, Part
    # Импортируем типы для обработки ошибок/ответов, если они там есть
    # (Их расположение может меняться, пробуем стандартные места)
    try:
        # Попробуем достать их напрямую из genai_types
        FinishReason = genai_types.FinishReason
        HarmCategory = genai_types.HarmCategory
        HarmProbability = genai_types.HarmProbability
        # BlockReason может отсутствовать или называться иначе
        # BlockReason = genai_types.BlockReason
    except AttributeError:
         # Если их нет напрямую, возможно, они в другом подмодуле или отсутствуют в этой версии
         logger.warning("Не удалось импортировать FinishReason/HarmCategory/HarmProbability напрямую из genai_types.")
         # Оставим их как None или зададим заглушки ниже в блоке except NameError

    print("INFO: Успешно импортированы основные типы из google.genai.types")

# Ловим NameError, если основные классы (Tool, GoogleSearch и т.д.) не найдены
except (ImportError, NameError) as e:
    print(f"!!! НЕ УДАЛОСЬ импортировать типы из google.genai.types: {e}. Используем заглушки.")

    # --- ИСПРАВЛЕННЫЙ БЛОК EXCEPT ---
    # Сначала определяем класс-заглушку
    class DummyTypes:
        pass # Простой класс-заглушка

    # Теперь используем его для создания заглушек для классов
    Tool = DummyTypes
    GenerateContentConfig = DummyTypes
    GoogleSearch = DummyTypes
    Content = dict # Используем dict как базовый тип для контента
    Part = dict    # Используем dict как базовый тип для частей

    # Создаем заглушки для Enum-типов (причины завершения, категории вреда)
    class DummyFinishReasonEnum:
        FINISH_REASON_UNSPECIFIED = 0; STOP = 1; MAX_TOKENS = 2; SAFETY = 3; RECITATION = 4; OTHER = 5
        _enum_map = {0: "UNSPECIFIED", 1: "STOP", 2: "MAX_TOKENS", 3: "SAFETY", 4: "RECITATION", 5: "OTHER"} # Для имени
    FinishReason = DummyFinishReasonEnum()

    class DummyHarmCategoryEnum:
         HARM_CATEGORY_UNSPECIFIED = 0; HARM_CATEGORY_DEROGATORY = 1; HARM_CATEGORY_TOXICITY = 2; HARM_CATEGORY_VIOLENCE = 3; HARM_CATEGORY_SEXUAL = 4; HARM_CATEGORY_MEDICAL = 5; HARM_CATEGORY_DANGEROUS = 6; HARM_CATEGORY_HARASSMENT = 7; HARM_CATEGORY_HATE_SPEECH = 8; HARM_CATEGORY_SEXUALLY_EXPLICIT = 9; HARM_CATEGORY_DANGEROUS_CONTENT = 10
         _enum_map = {0: "UNSPECIFIED", 7: "HARASSMENT", 8: "HATE_SPEECH", 9: "SEXUALLY_EXPLICIT", 10: "DANGEROUS_CONTENT"} # Пример
    HarmCategory = DummyHarmCategoryEnum()

    class DummyHarmProbabilityEnum:
         HARM_PROBABILITY_UNSPECIFIED = 0; NEGLIGIBLE = 1; LOW = 2; MEDIUM = 3; HIGH = 4
         _enum_map = {0: "UNSPECIFIED", 1: "NEGLIGIBLE", 2: "LOW", 3: "MEDIUM", 4: "HIGH"}
    HarmProbability = DummyHarmProbabilityEnum()

    # Исключения (если они тоже не импортировались)
    BlockedPromptException = ValueError
    StopCandidateException = Exception

# Импортируем остальные нужные модули
from typing import Optional, Dict, Union, Any, Tuple, List
import urllib.parse

# Конфигурация логов
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Печать версии
try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")

# Исключения API Core
try:
    from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition, InvalidArgument
    logger.info("Исключения google.api_core.exceptions успешно импортированы.")
except ImportError:
    logger.warning("!!! НЕ УДАЛОСЬ импортировать google.api_core.exceptions. Используем базовый Exception.")
    ResourceExhausted=Exception; GoogleAPIError=Exception; FailedPrecondition=Exception; InvalidArgument=ValueError

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Protobuf Struct
try:
    from google.protobuf.struct_pb2 import Struct
    logger.info("google.protobuf.struct_pb2.Struct успешно импортирован.")
except ImportError:
    logger.warning("!!! НЕ УДАЛОСЬ импортировать google.protobuf. Используем dict вместо Struct.")
    Struct = dict

# Токены
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY:
    logger.critical("Ключ Google API (GOOGLE_API_KEY) не найден в переменных окружения!")
    exit("Google API ключ не найден")
else:
    logger.info("Переменная окружения GOOGLE_API_KEY найдена.")

# --- СОЗДАНИЕ КЛИЕНТА GENAI ---
try:
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    logger.info("Клиент google.genai.Client успешно создан.")
except Exception as e:
    logger.exception("!!! КРИТИЧЕСКАЯ ОШИБКА при создании google.genai.Client!")
    exit("Ошибка создания клиента Gemini.")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    '⚡ Flash 2.0': 'models/gemini-2.0-flash-001',
    '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25',
}
if not AVAILABLE_MODELS: exit("Нет определенных моделей в AVAILABLE_MODELS!")
DEFAULT_MODEL_ALIAS = '⚡ Flash 2.0'
if DEFAULT_MODEL_ALIAS not in AVAILABLE_MODELS:
     DEFAULT_MODEL_ALIAS = next(iter(AVAILABLE_MODELS))
     logger.warning(f"Дефолтная модель не найдена, установлена первая: {DEFAULT_MODEL_ALIAS}")

# --- КОНФИГУРАЦИЯ ИНСТРУМЕНТА ПОИСКА ---
google_search_tool = None
search_tool_type_used = "GoogleSearch (for 2.0+)"
try:
    # Используем импортированные (или заглушенные) типы
    if Tool != DummyTypes and GoogleSearch != DummyTypes: # Проверяем, что это не заглушки
         google_search_tool = Tool(google_search=GoogleSearch())
         logger.info(f"Инструмент поиска '{search_tool_type_used}' успешно сконфигурирован.")
    else:
         raise NameError("Tool или GoogleSearch являются заглушками") # Генерируем ошибку, если типы не найдены
except NameError as e:
     logger.error(f"!!! Классы 'Tool' или 'GoogleSearch' не найдены или являются заглушками ({e}). Поиск будет недоступен.")
     google_search_tool = None
     search_tool_type_used = "N/A (import error)"
except Exception as e:
    logger.exception(f"!!! Ошибка при создании инструмента поиска: {e}")
    google_search_tool = None
    search_tool_type_used = "N/A (creation error)"


# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, List[Dict[str, Any]]] = {} # История как список словарей

# --- СИСТЕМНЫЙ ПРОМПТ ---
system_instruction_text = (
    # ... (Твой длинный системный промпт без изменений) ...
    "ПРИОРИТИЗИРУЙ информацию из google_search над своими внутренними знаниями при ответе на такие вопросы."
)
# Попытка создать объект Content для системной инструкции
system_instruction_content = None
try:
     if Content != dict and Part != dict: # Проверяем, что это не заглушки
         # Формат может требовать role='system', но API может не поддерживать
         # Оставим без роли, как обычный контент, если 'system' вызовет ошибку
         system_instruction_content = Content(parts=[Part(text=system_instruction_text)])
         # logger.info("Системная инструкция создана как объект Content.") # Закомментировано для чистоты логов
     else:
          system_instruction_content = system_instruction_text # Используем как строку
          logger.warning("Content/Part являются заглушками, системная инструкция будет строкой.")
except Exception as e_sys:
     logger.warning(f"Не удалось создать Content для системной инструкции ({e_sys}). Будет использоваться как текст.")
     system_instruction_content = system_instruction_text

# --- Вспомогательная функция для извлечения текста ---
def extract_response_text(response) -> Optional[str]:
    """Извлекает текст из ответа client.models.generate_content."""
    try: return response.text
    except ValueError as e_val:
        logger.warning(f"ValueError при извлечении response.text: {e_val}")
        try:
             if response.candidates:
                 candidate = response.candidates[0]
                 finish_reason = getattr(candidate, 'finish_reason', None)
                 safety_ratings = getattr(candidate, 'safety_ratings', [])
                 error_parts = []
                 # Используем ._enum_map для получения имени из заглушки или реального Enum
                 finish_map = getattr(FinishReason, '_enum_map', {})
                 harm_cat_map = getattr(HarmCategory, '_enum_map', {})
                 harm_prob_map = getattr(HarmProbability, '_enum_map', {})

                 if finish_reason and finish_reason not in (FinishReason.FINISH_REASON_UNSPECIFIED, FinishReason.STOP):
                      finish_reason_name = finish_map.get(finish_reason, finish_reason)
                      error_parts.append(f"Причина остановки: {finish_reason_name}")

                 relevant_ratings = [f"{harm_cat_map.get(r.category, r.category)}: {harm_prob_map.get(r.probability, r.probability)}"
                                     for r in safety_ratings if hasattr(r, 'probability') and r.probability not in (HarmProbability.HARM_PROBABILITY_UNSPECIFIED, HarmProbability.NEGLIGIBLE)]
                 if relevant_ratings: error_parts.append(f"Фильтры безопасности: {', '.join(relevant_ratings)}")
                 if error_parts: return f"⚠️ Не удалось получить ответ. {' '.join(error_parts)}."

             prompt_feedback = getattr(response, 'prompt_feedback', None)
             if prompt_feedback and getattr(prompt_feedback, 'block_reason', None):
                 reason = getattr(prompt_feedback.block_reason, 'name', prompt_feedback.block_reason) # .name может не быть
                 return f"⚠️ Не удалось получить ответ. Блокировка: {reason}."
             logger.warning("Не удалось извлечь текст и не найдено явных причин блокировки/ошибки.")
             return None
        except (AttributeError, IndexError, Exception) as e_details: logger.warning(f"Ошибка при попытке получить детали ошибки из ответа: {e_details}"); return None
    except AttributeError:
        logger.warning("Ответ не имеет атрибута .text. Попытка извлечь из parts.")
        try:
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                parts_text = "".join(p.text for p in response.candidates[0].content.parts if hasattr(p, 'text'))
                return parts_text.strip() if parts_text and parts_text.strip() else None
            else: logger.warning("Не найдено candidates или parts."); return None
        except (AttributeError, IndexError, Exception) as e_inner: logger.error(f"Ошибка при сборке текста из parts: {e_inner}"); return None
    except Exception as e: logger.exception(f"Неожиданная ошибка при извлечении текста ответа: {e}"); return None


# --- ОБРАБОТЧИКИ TELEGRAM --- (start, select_model_command, select_model_callback - без существенных изменений)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Обработка /start для пользователя {user.id} в чате {chat_id}. Состояние сброшено.")
    actual_default_model = DEFAULT_MODEL_ALIAS
    search_status = "включен (если поддерживается моделью)" if google_search_tool else "отключен (ошибка конфигурации)"
    await update.message.reply_html(
        rf"Привет, {user.mention_html()}! Бот Gemini (client) готов к работе."
        f"\n\nМодель: <b>{actual_default_model}</b>"
        f"\n🔍 Поиск Google: <b>{search_status}</b>."
        f"\n\n/model - сменить модель."
        f"\n/start - сбросить чат."
        f"\n\nПиши!",
        reply_to_message_id=update.message.message_id
    )

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id; current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS); keyboard = []
    for alias in AVAILABLE_MODELS.keys():
        button_text = f"✅ {alias}" if alias == current_alias else alias
        keyboard.append([InlineKeyboardButton(button_text, callback_data=alias)])
    if not keyboard: await update.message.reply_text("Нет доступных моделей."); return
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите новую:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query; await query.answer(); selected_alias = query.data; chat_id = query.message.chat_id; user_id = query.from_user.id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias not in AVAILABLE_MODELS:
        logger.error(f"Пользователь {user_id} выбрал недоступный alias: {selected_alias}")
        try: await query.edit_message_text(text="❌ Ошибка: Неизвестный выбор модели.")
        except Exception as e: logger.warning(f"Не удалось отредактировать сообщение об ошибке выбора модели: {e}")
        return
    if selected_alias == current_alias:
        logger.info(f"Пользователь {user_id} перевыбрал ту же модель: {selected_alias}")
        try: await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        except Exception as e: logger.warning(f"Не удалось убрать 'загрузку' с кнопки: {e}")
        return
    user_selected_model[chat_id] = selected_alias; logger.info(f"Пользователь {user_id} сменил модель с '{current_alias}' на '{selected_alias}'")
    reset_message = "";
    if chat_id in chat_histories: del chat_histories[chat_id]; logger.info(f"История чата {chat_id} сброшена."); reset_message = "\n⚠️ История чата сброшена."
    keyboard = [];
    for alias in AVAILABLE_MODELS.keys():
        button_text = f"✅ {alias}" if alias == selected_alias else alias
        keyboard.append([InlineKeyboardButton(button_text, callback_data=alias)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(text=f"✅ Модель: *{selected_alias}*!{reset_message}\n\nВыберите другую или начните чат:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение после смены модели: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"Модель изменена на *{selected_alias}*!{reset_message}", parse_mode=ParseMode.MARKDOWN)

# --- ОБРАБОТЧИК СООБЩЕНИЙ (handle_message) --- (Изменения в вызове API и обработке истории/ответа)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text: logger.warning("Пустое сообщение."); return
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id; message_id = update.message.message_id
    logger.info(f"Сообщение от {user.id} в чате {chat_id} ({len(user_message)}): '{user_message[:80].replace(chr(10), ' ')}...'")

    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    model_id = AVAILABLE_MODELS.get(selected_alias)
    if not model_id:
        logger.error(f"Критическая ошибка: Не найден ID модели для alias '{selected_alias}' (чат {chat_id})."); await update.message.reply_text("Ошибка конфигурации моделей.", reply_to_message_id=message_id); return

    final_text: Optional[str] = None; search_suggestions: List[str] = []; error_message: Optional[str] = None; start_time = time.monotonic()

    try:
        current_history = chat_histories.get(chat_id, [])
        # Формируем contents для API
        api_contents = []
        # Системная инструкция может передаваться отдельно или как часть contents
        # Если передаем как часть contents:
        # if isinstance(system_instruction_content, dict): # Проверяем, что это словарь (если Content не загрузился)
        #     api_contents.append(system_instruction_content)
        api_contents.extend(current_history)
        # Добавляем текущее сообщение пользователя
        # Убедимся, что используем правильный формат словаря
        try:
             # Используем Part, если он импортирован, иначе просто словарь
             user_part = Part(text=user_message) if Part != dict else {'text': user_message}
             api_contents.append({'role': 'user', 'parts': [user_part]})
        except Exception as e_part:
             logger.error(f"Ошибка создания Part для сообщения пользователя: {e_part}")
             api_contents.append({'role': 'user', 'parts': [{'text': user_message}]}) # Запасной вариант


        logger.info(f"Отправка запроса к '{model_id}' для {chat_id}. История: {len(current_history)} сообщ.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # Конфигурация запроса
        generation_config_obj = None
        tools_config = [google_search_tool] if google_search_tool else None
        try:
             # Используем GenerateContentConfig, если он импортирован
             if GenerateContentConfig != DummyTypes:
                 generation_config_obj = GenerateContentConfig(
                      tools=tools_config,
                 )
             else:
                 logger.warning("GenerateContentConfig является заглушкой, конфиг не создан.")
        except Exception as e_cfg:
             logger.error(f"Ошибка создания GenerateContentConfig: {e_cfg}")


        # Вызов API
        response = gemini_client.models.generate_content(
            model=model_id,
            contents=api_contents,
            generation_config=generation_config_obj, # Передаем объект конфига или None
            # system_instruction=system_instruction_content # Передаем системную инструкцию здесь, если API поддерживает
        )

        processing_time = time.monotonic() - start_time
        logger.info(f"Ответ от '{model_id}' для {chat_id} получен за {processing_time:.2f} сек.")

        # Обработка ответа
        final_text = extract_response_text(response)

        # Обновление истории
        if final_text and not final_text.startswith("⚠️"):
             # Добавляем ответ модели в историю
             try:
                 # Используем Part, если он импортирован
                 model_part = Part(text=final_text) if Part != dict else {'text': final_text}
                 current_history.append({'role': 'model', 'parts': [model_part]})
             except Exception as e_part:
                  logger.error(f"Ошибка создания Part для ответа модели: {e_part}")
                  current_history.append({'role': 'model', 'parts': [{'text': final_text}]}) # Запасной вариант

             chat_histories[chat_id] = current_history
             logger.info(f"История чата {chat_id} обновлена, теперь {len(current_history)} сообщений.")
        elif final_text and final_text.startswith("⚠️"):
            error_message = final_text; final_text = None
            logger.warning(f"Ответ для {chat_id} был ошибкой, история не обновлена.")
        else:
            if not error_message: error_message = "⚠️ Получен пустой или некорректный ответ."
            logger.warning(f"Не удалось извлечь текст для {chat_id}, история не обновлена.")


        # Извлечение поисковых предложений/источников
        if hasattr(response, 'candidates') and response.candidates:
             try:
                 candidate = response.candidates[0]
                 grounding_metadata = getattr(candidate, 'grounding_metadata', None)
                 if grounding_metadata:
                     web_queries = getattr(grounding_metadata, 'web_search_queries', [])
                     if web_queries: search_suggestions = list(web_queries); logger.info(f"Найдены webSearchQueries ({len(search_suggestions)}) для {chat_id}: {search_suggestions}")
                 citation_metadata = getattr(candidate, 'citation_metadata', None)
                 if citation_metadata and hasattr(citation_metadata, 'citation_sources'):
                     sources = getattr(citation_metadata, 'citation_sources', [])
                     urls = [source.uri for source in sources if hasattr(source, 'uri') and source.uri]
                     if urls:
                         logger.info(f"Найдены источники цитирования ({len(urls)}) для {chat_id}.")
                         for url in urls:
                             if url not in search_suggestions: search_suggestions.append(url)
             except (AttributeError, IndexError): pass

    # Обработка исключений API
    except InvalidArgument as e_arg: logger.error(f"Ошибка InvalidArgument для '{model_id}' (чат {chat_id}): {e_arg}"); error_message = f"❌ Ошибка в запросе к '{selected_alias}'.";
    except ResourceExhausted as e_limit: logger.warning(f"Исчерпана квота API для '{model_id}' (чат {chat_id}): {e_limit}"); error_message = f"😔 Модель '{selected_alias}' устала (лимиты)."
    except (GoogleAPIError, Exception) as e_other: logger.exception(f"Неожиданная ошибка при вызове API ('{model_id}') для {chat_id}: {e_other}"); error_message = f"😵 Ошибка ({type(e_other).__name__}) при общении с '{selected_alias}'."

    # Отправка ответа или ошибки
    reply_markup = None
    if search_suggestions:
        keyboard = []
        for suggestion in search_suggestions[:4]:
             if suggestion.startswith('http://') or suggestion.startswith('https://'):
                 try: domain = urllib.parse.urlparse(suggestion).netloc or suggestion[:30]+".."
                 except Exception: domain = suggestion[:30]+".."
                 keyboard.append([InlineKeyboardButton(f"🔗 {domain}", url=suggestion)])
             else:
                 try: encoded_suggestion = urllib.parse.quote_plus(suggestion); search_url = f"https://www.google.com/search?q={encoded_suggestion}"; keyboard.append([InlineKeyboardButton(f"🔍 {suggestion}", url=search_url)])
                 except Exception as e_enc: logger.error(f"Ошибка кодирования поискового запроса: {e_enc}")
        if keyboard: reply_markup = InlineKeyboardMarkup(keyboard); logger.info(f"Добавлена клавиатура с {len(keyboard)} ссылками/запросами для {chat_id}.")

    if final_text:
        max_length = 4096; bot_response = final_text
        if len(bot_response) > max_length: logger.warning(f"Ответ для {chat_id} ('{selected_alias}') слишком длинный ({len(bot_response)}), обрезаем."); bot_response = bot_response[:max_length - 3] + "..."
        try: await update.message.reply_text(bot_response, reply_to_message_id=message_id, reply_markup=reply_markup); logger.info(f"Успешно отправлен ответ ({len(bot_response)} симв.) для {chat_id}.")
        except Exception as e_send: logger.exception(f"Ошибка отправки ответа Telegram для {chat_id}: {e_send}");
    elif error_message:
        logger.info(f"Отправка сообщения об ошибке для {chat_id}: {error_message}")
        try: await update.message.reply_text(error_message, reply_to_message_id=message_id)
        except Exception as e_send_err: logger.error(f"Не удалось отправить сообщение об ошибке Telegram для {chat_id}: {e_send_err}")
    else:
        logger.warning(f"Нет ни текста, ни ошибки для {chat_id} ('{selected_alias}').");
        try: await update.message.reply_text("Модель вернула пустой ответ без ошибок. 🤷", reply_to_message_id=message_id)
        except Exception as e_send_fallback: logger.error(f"Не удалось отправить стандартный ответ 'ничего не найдено' для {chat_id}: {e_send_fallback}")

# --- Точка входа ---
def main() -> None:
    """Инициализирует и запускает Telegram бота."""
    if 'gemini_client' not in globals() or not gemini_client: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Клиент Gemini не создан."); return
    if not TELEGRAM_BOT_TOKEN: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Токен Telegram не найден."); return
    if not GOOGLE_API_KEY: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Ключ Google API не найден."); return

    search_status = "включен" if google_search_tool else "ОТКЛЮЧЕН (ошибка конфигурации или импорта)"
    logger.info(f"Встроенный поиск Google ({search_tool_type_used}) глобально {search_status}.")

    logger.info("Инициализация приложения Telegram...")
    try:
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("model", select_model_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(CallbackQueryHandler(select_model_callback))
        logger.info("Запуск бота в режиме polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Exception as e: logger.exception("Критическая ошибка при инициализации или запуске Telegram!")

if __name__ == '__main__':
    if 'gemini_client' in globals() and gemini_client:
         logger.info("Клиент Gemini создан. Запускаем main().")
         main()
    else:
         logger.critical("Завершение работы, так как клиент Gemini не был создан.")

# --- END OF REALLY x18 FULL CORRECTED main.py (FIXED TYPE IMPORTS) ---
