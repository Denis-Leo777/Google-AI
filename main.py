# --- START OF REALLY x19 FULL CORRECTED main.py (IMPROVED TYPE IMPORTS CHECK) ---

import logging
import os
import asyncio
import google.genai as genai
import time
import random

# --- УЛУЧШЕННЫЙ ИМПОРТ ТИПОВ И ЗАГЛУШКИ ---
# Определяем переменные заранее как None или базовые типы
genai_types = None
Tool = None
GenerateContentConfig = None
GoogleSearch = None
Content = dict # Заглушка по умолчанию
Part = dict    # Заглушка по умолчанию
# Заглушки для Enums (определяем сразу, чтобы они точно были)
class DummyFinishReasonEnum: FINISH_REASON_UNSPECIFIED = 0; STOP = 1; MAX_TOKENS = 2; SAFETY = 3; RECITATION = 4; OTHER = 5; _enum_map = {0: "UNSPECIFIED", 1: "STOP", 2: "MAX_TOKENS", 3: "SAFETY", 4: "RECITATION", 5: "OTHER"}
class DummyHarmCategoryEnum: HARM_CATEGORY_UNSPECIFIED = 0; HARM_CATEGORY_HARASSMENT = 7; HARM_CATEGORY_HATE_SPEECH = 8; HARM_CATEGORY_SEXUALLY_EXPLICIT = 9; HARM_CATEGORY_DANGEROUS_CONTENT = 10; _enum_map = {0: "UNSPECIFIED", 7: "HARASSMENT", 8: "HATE_SPEECH", 9: "SEXUALLY_EXPLICIT", 10: "DANGEROUS_CONTENT"}
class DummyHarmProbabilityEnum: HARM_PROBABILITY_UNSPECIFIED = 0; NEGLIGIBLE = 1; LOW = 2; MEDIUM = 3; HIGH = 4; _enum_map = {0: "UNSPECIFIED", 1: "NEGLIGIBLE", 2: "LOW", 3: "MEDIUM", 4: "HIGH"}
# Инициализируем переменные Enum заглушками
FinishReason = DummyFinishReasonEnum()
HarmCategory = DummyHarmCategoryEnum()
HarmProbability = DummyHarmProbabilityEnum()
# Исключения API Core (импортируем позже)
ResourceExhausted=Exception; GoogleAPIError=Exception; FailedPrecondition=Exception; InvalidArgument=ValueError

# Пытаемся импортировать реальные типы
try:
    from google.genai import types as genai_types
    logger.info("Импортирован модуль google.genai.types.")

    # Пытаемся получить каждый нужный тип отдельно
    try: Tool = genai_types.Tool; logger.debug("Найден genai_types.Tool")
    except AttributeError: logger.warning("genai_types.Tool не найден.")
    try: GenerateContentConfig = genai_types.GenerateContentConfig; logger.debug("Найден genai_types.GenerateContentConfig")
    except AttributeError: logger.warning("genai_types.GenerateContentConfig не найден.")
    try: GoogleSearch = genai_types.GoogleSearch; logger.debug("Найден genai_types.GoogleSearch")
    except AttributeError: logger.warning("genai_types.GoogleSearch не найден.")
    try: Content = genai_types.Content; logger.debug("Найден genai_types.Content")
    except AttributeError: logger.warning("genai_types.Content не найден, используется dict.")
    try: Part = genai_types.Part; logger.debug("Найден genai_types.Part")
    except AttributeError: logger.warning("genai_types.Part не найден, используется dict.")
    try: FinishReason = genai_types.FinishReason; logger.debug("Найден genai_types.FinishReason")
    except AttributeError: logger.warning("genai_types.FinishReason не найден, используется заглушка.")
    try: HarmCategory = genai_types.HarmCategory; logger.debug("Найден genai_types.HarmCategory")
    except AttributeError: logger.warning("genai_types.HarmCategory не найден, используется заглушка.")
    try: HarmProbability = genai_types.HarmProbability; logger.debug("Найден genai_types.HarmProbability")
    except AttributeError: logger.warning("genai_types.HarmProbability не найден, используется заглушка.")

except ImportError as e:
    logger.error(f"!!! НЕ удалось импортировать модуль google.genai.types: {e}. Используются только заглушки.")
    # Все переменные останутся None или заглушками, определенными выше

# Импортируем остальные нужные модули
from typing import Optional, Dict, Union, Any, Tuple, List
import urllib.parse

# Конфигурация логов
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
# Установим уровень DEBUG для логгера types, если нужно дебажить импорты
# logging.getLogger('google.genai.types').setLevel(logging.DEBUG) # Раскомментировать для детального лога импорта
logger = logging.getLogger(__name__) # Основной логгер

# Печать версии
try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")

# Исключения API Core
try:
    from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition, InvalidArgument
    logger.info("Исключения google.api_core.exceptions успешно импортированы.")
except ImportError:
    logger.warning("!!! НЕ УДАЛОСЬ импортировать google.api_core.exceptions. Используем базовый Exception.")
    # Заглушки уже определены выше

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
if not GOOGLE_API_KEY: logger.critical("Ключ Google API не найден!"); exit("Google API ключ не найден")
else: logger.info("Переменная окружения GOOGLE_API_KEY найдена.")

# --- СОЗДАНИЕ КЛИЕНТА GENAI ---
try:
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    logger.info("Клиент google.genai.Client успешно создан.")
except Exception as e: logger.exception("!!! КРИТИЧЕСКАЯ ОШИБКА при создании google.genai.Client!"); exit("Ошибка создания клиента Gemini.")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    '⚡ Flash 2.0': 'models/gemini-2.0-flash-001',
    '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25',
}
if not AVAILABLE_MODELS: exit("Нет определенных моделей в AVAILABLE_MODELS!")
DEFAULT_MODEL_ALIAS = '⚡ Flash 2.0'
if DEFAULT_MODEL_ALIAS not in AVAILABLE_MODELS: DEFAULT_MODEL_ALIAS = next(iter(AVAILABLE_MODELS)); logger.warning(f"Дефолтная модель не найдена, установлена первая: {DEFAULT_MODEL_ALIAS}")

# --- КОНФИГУРАЦИЯ ИНСТРУМЕНТА ПОИСКА ---
google_search_tool = None
search_tool_type_used = "GoogleSearch (for 2.0+)"
# --- ИСПРАВЛЕННАЯ ПРОВЕРКА ---
# Проверяем, что Tool и GoogleSearch были успешно импортированы (т.е. они не None)
if Tool is not None and GoogleSearch is not None:
    try:
        google_search_tool = Tool(google_search=GoogleSearch())
        logger.info(f"Инструмент поиска '{search_tool_type_used}' успешно сконфигурирован.")
    except Exception as e:
        logger.exception(f"!!! Ошибка при создании инструмента поиска Tool(google_search=GoogleSearch()): {e}")
        google_search_tool = None # Сбрасываем в случае ошибки создания
        search_tool_type_used = "N/A (creation error)"
else:
    # Если какой-то из классов не был импортирован
    logger.error(f"!!! Классы 'Tool' или 'GoogleSearch' не были импортированы из google.genai.types. Поиск будет недоступен.")
    google_search_tool = None # Убедимся, что он None
    search_tool_type_used = "N/A (import error)"


# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, List[Dict[str, Any]]] = {} # История как список словарей

# --- СИСТЕМНЫЙ ПРОМПТ ---
system_instruction_text = (
    # ... (Твой длинный системный промпт) ...
    "ПРИОРИТИЗИРУЙ информацию из google_search над своими внутренними знаниями при ответе на такие вопросы."
)
system_instruction_content = None
try:
     # Используем Content и Part, если они импортированы, иначе строку
     if Content is not dict and Part is not dict:
         system_instruction_content = Content(parts=[Part(text=system_instruction_text)])
     else:
         system_instruction_content = system_instruction_text
         # logger.warning("Content/Part не импортированы, системная инструкция будет строкой.")
except Exception as e_sys:
     logger.warning(f"Не удалось создать Content для системной инструкции ({e_sys}). Будет строкой.")
     system_instruction_content = system_instruction_text

# --- Вспомогательная функция для извлечения текста ---
def extract_response_text(response) -> Optional[str]:
    """Извлекает текст из ответа client.models.generate_content."""
    # (Код функции остается как в версии x18, т.к. он уже использует заглушки Enum)
    try: return response.text
    except ValueError as e_val:
        logger.warning(f"ValueError при извлечении response.text: {e_val}")
        try:
             if response.candidates:
                 candidate = response.candidates[0]
                 finish_reason = getattr(candidate, 'finish_reason', None)
                 safety_ratings = getattr(candidate, 'safety_ratings', [])
                 error_parts = []
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
                 reason = getattr(prompt_feedback.block_reason, 'name', prompt_feedback.block_reason)
                 return f"⚠️ Не удалось получить ответ. Блокировка: {reason}."
             logger.warning("Не удалось извлечь текст и нет явных причин блокировки/ошибки.")
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

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Без изменений)
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Обработка /start для пользователя {user.id} в чате {chat_id}. Состояние сброшено.")
    actual_default_model = DEFAULT_MODEL_ALIAS
    search_status = "включен (если поддерживается)" if google_search_tool else "ОТКЛЮЧЕН"
    await update.message.reply_html(
        rf"Привет, {user.mention_html()}! Бот Gemini (client) v19."
        f"\n\nМодель: <b>{actual_default_model}</b>"
        f"\n🔍 Поиск Google: <b>{search_status}</b>."
        f"\n\n/model - сменить модель."
        f"\n/start - сбросить чат."
        f"\n\nСпрашивай!",
        reply_to_message_id=update.message.message_id
    )

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Без изменений)
    chat_id = update.effective_chat.id; current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS); keyboard = []
    for alias in AVAILABLE_MODELS.keys():
        button_text = f"✅ {alias}" if alias == current_alias else alias
        keyboard.append([InlineKeyboardButton(button_text, callback_data=alias)])
    if not keyboard: await update.message.reply_text("Нет доступных моделей."); return
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите новую:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)


async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Без изменений)
    query = update.callback_query; await query.answer(); selected_alias = query.data; chat_id = query.message.chat_id; user_id = query.from_user.id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias not in AVAILABLE_MODELS:
        logger.error(f"Пользователь {user_id} выбрал недоступный alias: {selected_alias}")
        try: await query.edit_message_text(text="❌ Ошибка: Неизвестный выбор модели.")
        except Exception as e: logger.warning(f"Не удалось отредактировать сообщение: {e}")
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
        await query.edit_message_text(text=f"✅ Модель: *{selected_alias}*!{reset_message}\n\nНачните чат:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"Модель изменена на *{selected_alias}*!{reset_message}", parse_mode=ParseMode.MARKDOWN)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений."""
    if not update.message or not update.message.text: logger.warning("Пустое сообщение."); return
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id; message_id = update.message.message_id
    logger.info(f"Сообщение от {user.id} в чате {chat_id} ({len(user_message)}): '{user_message[:80].replace(chr(10), ' ')}...'")

    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    model_id = AVAILABLE_MODELS.get(selected_alias)
    if not model_id: logger.error(f"Крит. ошибка: Не найден ID модели для '{selected_alias}'"); await update.message.reply_text("Ошибка конфига.", reply_to_message_id=message_id); return

    final_text: Optional[str] = None; search_suggestions: List[str] = []; error_message: Optional[str] = None; start_time = time.monotonic()

    try:
        current_history = chat_histories.get(chat_id, [])
        api_contents = []
        # Системная инструкция (если она Content объект, передаем отдельно)
        # api_contents.extend(current_history)
        try:
             # Используем Part, если он импортирован, иначе просто словарь
             user_part = Part(text=user_message) if Part is not dict else {'text': user_message}
             # Добавляем историю и новое сообщение
             api_contents = current_history + [{'role': 'user', 'parts': [user_part]}]
        except Exception as e_part:
             logger.error(f"Ошибка создания Part для сообщения пользователя: {e_part}")
             api_contents = current_history + [{'role': 'user', 'parts': [{'text': user_message}]}]

        logger.info(f"Отправка запроса к '{model_id}' для {chat_id}. История: {len(current_history)} сообщ.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        generation_config_obj = None
        tools_config = [google_search_tool] if google_search_tool else None
        try:
             # Используем GenerateContentConfig, если он импортирован
             if GenerateContentConfig is not None:
                 generation_config_obj = GenerateContentConfig(tools=tools_config)
             # else: logger.debug("GenerateContentConfig не импортирован, конфиг не используется.")
        except Exception as e_cfg: logger.error(f"Ошибка создания GenerateContentConfig: {e_cfg}")

        # --- ИСПРАВЛЕННЫЙ ВЫЗОВ API ---
        # Передаем system_instruction отдельно, если это объект Content
        system_instruction_param = None
        if system_instruction_content and Content is not dict and isinstance(system_instruction_content, Content):
             system_instruction_param = system_instruction_content
             # logger.debug("Передаем system_instruction как Content объект.")
        # elif system_instruction_content:
             # logger.debug("Передаем system_instruction как строку (если API поддерживает).")
             # system_instruction_param = system_instruction_content # Некоторые API могут принимать строку

        response = gemini_client.models.generate_content(
            model=model_id,
            contents=api_contents, # Только история и текущее сообщение
            generation_config=generation_config_obj,
            system_instruction=system_instruction_param # Передаем здесь
        )

        processing_time = time.monotonic() - start_time
        logger.info(f"Ответ от '{model_id}' для {chat_id} получен за {processing_time:.2f} сек.")

        final_text = extract_response_text(response)

        if final_text and not final_text.startswith("⚠️"):
             try:
                 model_part = Part(text=final_text) if Part is not dict else {'text': final_text}
                 # Обновляем историю: добавляем user message (уже есть в api_contents) и model response
                 # Важно: нужно использовать КОПИЮ api_contents до добавления ответа модели
                 # или просто добавлять к текущей истории
                 current_history.append({'role': 'user', 'parts': api_contents[-1]['parts']}) # Добавляем последний user message
                 current_history.append({'role': 'model', 'parts': [model_part]}) # Добавляем ответ модели
             except Exception as e_part:
                  logger.error(f"Ошибка создания Part для ответа модели: {e_part}")
                  current_history.append({'role': 'user', 'parts': api_contents[-1]['parts']})
                  current_history.append({'role': 'model', 'parts': [{'text': final_text}]})

             chat_histories[chat_id] = current_history # Сохраняем обновленную историю
             logger.info(f"История чата {chat_id} обновлена, теперь {len(current_history)} сообщений.")
        elif final_text and final_text.startswith("⚠️"): error_message = final_text; final_text = None; logger.warning(f"Ответ для {chat_id} был ошибкой, история не обновлена.")
        else:
            if not error_message: error_message = "⚠️ Получен пустой или некорректный ответ."
            logger.warning(f"Не удалось извлечь текст для {chat_id}, история не обновлена.")

        # Извлечение поисковых предложений/источников
        if hasattr(response, 'candidates') and response.candidates:
             try:
                 # (Логика извлечения метаданных без изменений)
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
    except InvalidArgument as e_arg: logger.error(f"Ошибка InvalidArgument для '{model_id}': {e_arg}"); error_message = f"❌ Ошибка в запросе к '{selected_alias}'.";
    except ResourceExhausted as e_limit: logger.warning(f"Исчерпана квота API для '{model_id}': {e_limit}"); error_message = f"😔 Модель '{selected_alias}' устала (лимиты)."
    except (GoogleAPIError, Exception) as e_other: logger.exception(f"Неожиданная ошибка API ('{model_id}'): {e_other}"); error_message = f"😵 Ошибка ({type(e_other).__name__}) при общении с '{selected_alias}'."

    # Отправка ответа или ошибки
    reply_markup = None
    if search_suggestions:
        # (Логика создания кнопок без изменений)
        keyboard = []
        for suggestion in search_suggestions[:4]:
             if suggestion.startswith('http://') or suggestion.startswith('https://'):
                 try: domain = urllib.parse.urlparse(suggestion).netloc or suggestion[:30]+".."
                 except Exception: domain = suggestion[:30]+".."
                 keyboard.append([InlineKeyboardButton(f"🔗 {domain}", url=suggestion)])
             else:
                 try: encoded_suggestion = urllib.parse.quote_plus(suggestion); search_url = f"https://www.google.com/search?q={encoded_suggestion}"; keyboard.append([InlineKeyboardButton(f"🔍 {suggestion}", url=search_url)])
                 except Exception as e_enc: logger.error(f"Ошибка кодирования запроса: {e_enc}")
        if keyboard: reply_markup = InlineKeyboardMarkup(keyboard); logger.info(f"Добавлена клавиатура с {len(keyboard)} ссылками/запросами для {chat_id}.")

    if final_text:
        # (Логика отправки ответа без изменений)
        max_length = 4096; bot_response = final_text
        if len(bot_response) > max_length: logger.warning(f"Ответ для {chat_id} ('{selected_alias}') слишком длинный ({len(bot_response)}), обрезаем."); bot_response = bot_response[:max_length - 3] + "..."
        try: await update.message.reply_text(bot_response, reply_to_message_id=message_id, reply_markup=reply_markup); logger.info(f"Успешно отправлен ответ ({len(bot_response)} симв.) для {chat_id}.")
        except Exception as e_send: logger.exception(f"Ошибка отправки ответа Telegram: {e_send}");
    elif error_message:
        # (Логика отправки ошибки без изменений)
        logger.info(f"Отправка сообщения об ошибке для {chat_id}: {error_message}")
        try: await update.message.reply_text(error_message, reply_to_message_id=message_id)
        except Exception as e_send_err: logger.error(f"Не удалось отправить сообщение об ошибке Telegram: {e_send_err}")
    else:
        # (Логика отправки "ничего не найдено" без изменений)
        logger.warning(f"Нет ни текста, ни ошибки для {chat_id} ('{selected_alias}').");
        try: await update.message.reply_text("Модель вернула пустой ответ без ошибок. 🤷", reply_to_message_id=message_id)
        except Exception as e_send_fallback: logger.error(f"Не удалось отправить fallback ответ: {e_send_fallback}")


# --- Точка входа ---
def main() -> None:
    """Инициализирует и запускает Telegram бота."""
    if 'gemini_client' not in globals() or not gemini_client: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Клиент Gemini не создан."); return
    if not TELEGRAM_BOT_TOKEN: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Токен Telegram не найден."); return
    if not GOOGLE_API_KEY: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Ключ Google API не найден."); return

    search_status = "включен" if google_search_tool else "ОТКЛЮЧЕН (ошибка импорта/конфигурации)"
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
    if 'gemini_client' in globals() and gemini_client: logger.info("Клиент Gemini создан. Запускаем main()."); main()
    else: logger.critical("Завершение работы, так как клиент Gemini не был создан.")

# --- END OF REALLY x19 FULL CORRECTED main.py (IMPROVED TYPE IMPORTS CHECK) ---
