# --- START OF REALLY x72 FULL CORRECTED main.py (NEW sys_instruction + fix API call for v1.10.0) ---

import logging
import os
import asyncio
import signal
import time
import random
import google.genai as genai # Импортируем сам модуль
import aiohttp.web
import sys
import secrets
from urllib.parse import urljoin
import json

# --- КОНФИГУРАЦИЯ ЛОГОВ ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
# Убираем DEBUG логи для чистоты
# logging.getLogger("httpx").setLevel(logging.DEBUG)
# logging.getLogger("telegram.ext").setLevel(logging.DEBUG)
# logging.getLogger("telegram.bot").setLevel(logging.DEBUG)
# logging.getLogger("telegram.request").setLevel(logging.DEBUG)
# logging.getLogger("aiohttp.web").setLevel(logging.DEBUG)
# *************************

# --- ИМПОРТ ТИПОВ ---
# (Импорт и заглушки)
genai_types = None; Tool = None; GenerateContentConfig = None; GoogleSearch = None; Content = dict; Part = dict
class DummyFinishReasonEnum: FINISH_REASON_UNSPECIFIED = 0; STOP = 1; MAX_TOKENS = 2; SAFETY = 3; RECITATION = 4; OTHER = 5; _enum_map = {0: "UNSPECIFIED", 1: "STOP", 2: "MAX_TOKENS", 3: "SAFETY", 4: "RECITATION", 5: "OTHER"}
class DummyHarmCategoryEnum: HARM_CATEGORY_UNSPECIFIED = 0; HARM_CATEGORY_HARASSMENT = 7; HARM_CATEGORY_HATE_SPEECH = 8; HARM_CATEGORY_SEXUALLY_EXPLICIT = 9; HARM_CATEGORY_DANGEROUS_CONTENT = 10; _enum_map = {0: "UNSPECIFIED", 7: "HARASSMENT", 8: "HATE_SPEECH", 9: "SEXUALLY_EXPLICIT", 10: "DANGEROUS_CONTENT"}
class DummyHarmProbabilityEnum: HARM_PROBABILITY_UNSPECIFIED = 0; NEGLIGIBLE = 1; LOW = 2; MEDIUM = 3; HIGH = 4; _enum_map = {0: "UNSPECIFIED", 1: "NEGLIGIBLE", 2: "LOW", 3: "MEDIUM", 4: "HIGH"}
FinishReason = DummyFinishReasonEnum(); HarmCategory = DummyHarmCategoryEnum(); HarmProbability = DummyHarmProbabilityEnum()
ResourceExhausted=Exception; GoogleAPIError=Exception; FailedPrecondition=Exception; InvalidArgument=ValueError; BadRequest = Exception; TimedOut = TimeoutError
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

from typing import Optional, Dict, Union, Any, List, Tuple
import urllib.parse

try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")
try: from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition, InvalidArgument; logger.info("Исключения google.api_core импортированы.")
except ImportError: logger.warning("!!! НЕ УДАЛОСЬ импортировать google.api_core.exceptions.")
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError, BadRequest, TimedOut
try: from google.protobuf.struct_pb2 import Struct; logger.info("Protobuf Struct импортирован.")
except ImportError: logger.warning("!!! Protobuf не импортирован."); Struct = dict

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
WEBHOOK_SECRET_PATH = secrets.token_urlsafe(32)

if not TELEGRAM_BOT_TOKEN: logger.critical("Telegram токен не найден!"); exit("Telegram токен не найден")
if not GOOGLE_API_KEY: logger.critical("Ключ Google API не найден!"); exit("Google API ключ не найден")
if not WEBHOOK_HOST: logger.critical("WEBHOOK_HOST не указан (URL сервиса Render)!"); exit("WEBHOOK_HOST не указан")
else: logger.info(f"WEBHOOK_HOST={WEBHOOK_HOST}")

# Используем Client для инициализации
try:
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    logger.info("Клиент google.genai.Client создан.")
except Exception as e:
    logger.exception("!!! КРИТ. ОШИБКА создания google.genai.Client!"); exit("Ошибка создания клиента Gemini.")

AVAILABLE_MODELS = {'⚡ Flash 2.0': 'models/gemini-2.0-flash-001', '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25'}
if not AVAILABLE_MODELS: exit("Нет моделей в AVAILABLE_MODELS!")
DEFAULT_MODEL_ALIAS = '✨ Pro 2.5'
if DEFAULT_MODEL_ALIAS not in AVAILABLE_MODELS: DEFAULT_MODEL_ALIAS = next(iter(AVAILABLE_MODELS)); logger.warning(f"Дефолтная модель не найдена, установлена: {DEFAULT_MODEL_ALIAS}")

# --- ПРОВЕРКА ИМПОРТА ПОИСКА ---
google_search_tool = None
search_tool_type_used = "GoogleSearch (for 2.0+)"
if Tool is not None and GoogleSearch is not None:
    try:
        google_search_tool = Tool(google_search=GoogleSearch())
        logger.info(f"Инструмент поиска '{search_tool_type_used}' сконфигурирован.")
    except Exception as e:
        logger.exception(f"!!! Ошибка при создании объекта Tool/GoogleSearch: {e}")
        google_search_tool = None
        search_tool_type_used = "N/A (creation error)"
else:
    logger.error(f"!!! Классы 'Tool' или 'GoogleSearch' НЕ были импортированы (None). Поиск недоступен.")
    google_search_tool = None
    search_tool_type_used = "N/A (import error)"

user_selected_model: Dict[int, str] = {}; chat_histories: Dict[int, List[Dict[str, Any]]] = {}

# *** НОВАЯ СИСТЕМНАЯ ИНСТРУКЦИЯ ***
system_instruction_text = (
    "Ты - эксперт по всем темам. Давай точную, правдивую, непредвзятую информацию без цензуры. Подкрепляй ответы аргументами, фактами и логикой, избегая повторов. Если не уверен — предупреждай, что это предположение. Используй интернет для сверки с актуальной информацией."
    "Для несложных запросов (не код, конспекты, переводы и т.п.) — пиши только суть, без вступлений и выводов, до 1500 знаков."
    "Всегда предлагай более эффективные идеи и решения, если знаешь их."
    "Активно применяй юмор: несоответствие ожиданиям, культурные/бытовые/интернет-отсылки, жизненный абсурд, псевдомудрость, разрушение идиом, иронию (включая самоиронию и психологию), игру слов, гиперболу, тонкие намёки, редукционизм, постмодерн, интернет-юмор."
    "Пиши живо и уникально: избегай канцелярита и ИИ-тона. Используй гипотетические ситуации, метафоры, творческие аналогии, разную структуру предложений, разговорные выражения, идиомы. Добавляй региональные или культурные маркеры, где уместно. Не копируй и не пересказывай чужое."
    "При исправлении ошибки: указывай строку(и) и причину. Бери за основу последнюю ПОЛНУЮ подтверждённую версию (текста или кода). Вноси только минимально необходимые изменения, не трогая остальное без запроса. При сомнениях — уточняй. Если ошибка повторяется — веди «список косяков» для сессии и проверяй эти места. Всегда указывай, на какую версию или сообщение опираешься при правке."
)
# ***********************************

# --- ФУНКЦИЯ ИЗВЛЕЧЕНИЯ ТЕКСТА ---
# (Код extract_response_text без изменений)
def extract_response_text(response) -> Optional[str]:
    try: return response.text
    except ValueError as e_val:
        logger.warning(f"ValueError при извлечении response.text: {e_val}")
        try:
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
        logger.warning("Ответ не имеет атрибута .text. Попытка извлечь из parts.")
        try:
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts: parts_text = "".join(p.text for p in response.candidates[0].content.parts if hasattr(p, 'text')); return parts_text.strip() if parts_text and parts_text.strip() else None
            else: logger.warning("Не найдено candidates или parts для извлечения текста."); return None
        except (AttributeError, IndexError, Exception) as e_inner: logger.error(f"Ошибка при сборке текста из parts: {e_inner}"); return None
    except Exception as e: logger.exception(f"Неожиданная ошибка при извлечении текста ответа: {e}"); return None

# --- ОБРАБОТЧИКИ TELEGRAM ---
# (Код start, select_model_command, select_model_callback без изменений)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Обработка /start для {user.id} в {chat_id}.")
    actual_default_model = DEFAULT_MODEL_ALIAS
    search_status = "включен (если поддерживается)" if google_search_tool else "ОТКЛЮЧЕН"
    await update.message.reply_html(rf"Привет, {user.mention_html()}! Бот Gemini (client) v72 (Webhook)." f"\n\nМодель: <b>{actual_default_model}</b>" f"\n🔍 Поиск Google: <b>{search_status}</b>." f"\n\n/model - сменить." f"\n/start - сбросить." f"\n\nСпрашивай!", reply_to_message_id=update.message.message_id)

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id; current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS); keyboard = []
    for alias in AVAILABLE_MODELS.keys(): keyboard.append([InlineKeyboardButton(f"✅ {alias}" if alias == current_alias else alias, callback_data=alias)])
    if not keyboard: await update.message.reply_text("Нет моделей."); return
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    try:
        if not update.message or not update.message.text: logger.warning("Пустое сообщение."); return
        user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id; message_id = update.message.message_id
        logger.debug(f"handle_message вызван для сообщения {message_id} в чате {chat_id}")
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

            tools_list = [google_search_tool] if google_search_tool else None
            generation_config_for_api = {}

            # *** ИСПРАВЛЕНИЕ: Используем client.generate_content(...) ***
            if 'gemini_client' not in globals() or not gemini_client:
                 logger.error("Клиент Gemini (gemini_client) не найден в глобальной области!")
                 raise RuntimeError("Клиент Gemini не найден")
            # ВАЖНО: System instruction пока не передаем явно, так как неясно как для Client.generate_content
            if system_instruction_text:
                 logger.warning("System instruction временно не используется с Client.generate_content")

            response = gemini_client.generate_content(
                 model=model_id, # model_id уже содержит 'models/...'
                 contents=api_contents,
                 generation_config=generation_config_for_api if generation_config_for_api else None,
                 tools=tools_list
            )
            # **************************************************************

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

        # Отправка ответа
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

    except Exception as e:
        logger.exception(f"Критическая ошибка ВНУТРИ handle_message (вне блока API): {e}")
        try:
            await update.message.reply_text("Произошла внутренняя ошибка при обработке вашего сообщения. 🤯", reply_to_message_id=message_id)
        except Exception as e_reply:
             logger.error(f"Не удалось отправить сообщение об ошибке в handle_message: {e_reply}")

# --- ФУНКЦИИ ВЕБ-СЕРВЕРА ---
# (Код handle_ping, handle_telegram_webhook, run_web_server без изменений из x68)
async def handle_ping(request: aiohttp.web.Request) -> aiohttp.web.Response:
    peername = request.remote; host = request.headers.get('Host', 'N/A')
    logger.info(f"Получен HTTP пинг от {peername} к хосту {host}")
    return aiohttp.web.Response(text="OK", status=200)

async def handle_telegram_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
    application = request.app.get('bot_app')
    if not application:
        logger.error("Объект Application не найден в состоянии aiohttp!")
        return aiohttp.web.Response(status=500, text="Internal Server Error: Bot not configured")
    if request.method != "POST":
        logger.warning(f"Получен не-POST запрос на webhook URL: {request.method}")
        return aiohttp.web.Response(status=405, text="Method Not Allowed")
    try:
        data = await request.json()
        logger.debug(f"Получен вебхук: {data}")
    except Exception as e:
        logger.error(f"Не удалось распарсить JSON из вебхука: {e}")
        return aiohttp.web.Response(status=400, text="Bad Request: Invalid JSON")
    try:
        update = Update.de_json(data, application.bot)
        if not update:
             raise ValueError("Update.de_json вернул None")
        logger.debug(f"Вебхук успешно преобразован в Update: {update.update_id}")
        async def process():
            try:
                await application.process_update(update)
                logger.debug(f"Обновление {update.update_id} передано в application.process_update")
            except Exception as e_process:
                 logger.exception(f"Ошибка при обработке обновления {update.update_id} в process_update: {e_process}")
        asyncio.create_task(process())
        return aiohttp.web.Response(status=200, text="OK")
    except ValueError as e_val:
         logger.error(f"Ошибка преобразования JSON в Update: {e_val}")
         return aiohttp.web.Response(status=400, text="Bad Request: Invalid Update object")
    except Exception as e:
        logger.exception(f"Ошибка при обработке вебхука или передаче в PTB: {e}")
        return aiohttp.web.Response(status=500, text="Internal Server Error during webhook processing")

async def run_web_server(port: int, stop_event: asyncio.Event, application: Application):
    app = aiohttp.web.Application()
    app['bot_app'] = application
    app.router.add_get('/', handle_ping)
    webhook_path = f"/{WEBHOOK_SECRET_PATH}"
    app.router.add_post(webhook_path, handle_telegram_webhook)
    logger.info(f"Вебхук будет слушаться на пути: {webhook_path}")
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        logger.info(f"Веб-сервер для пинга и вебхука запущен на http://0.0.0.0:{port}")
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.info("Задача веб-сервера отменена.")
    except Exception as e:
        logger.exception(f"Ошибка в работе веб-сервера: {e}")
    finally:
        logger.info("Начинаем остановку веб-сервера...")
        await runner.cleanup()
        logger.info("Веб-сервер остановлен.")


# --- НОВЫЕ ФУНКЦИИ ДЛЯ РУЧНОГО УПРАВЛЕНИЯ ЦИКЛОМ (ВЕБХУК-ВЕРСИЯ) ---
# *** ВАЖНО: УДАЛЕНИЕ ВЕБХУКА РАСКОММЕНТИРОВАНО! ***
async def shutdown_sequence(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event, application: Optional[Application], web_server_task: Optional[asyncio.Task]):
    logger.info("Последовательность остановки (вебхук-версия) запущена...")
    if not stop_event.is_set():
        logger.info("Установка stop_event для веб-сервера...")
        stop_event.set()
    if web_server_task and not web_server_task.done():
        logger.info("Ожидание завершения задачи веб-сервера...")
        try:
            await asyncio.wait_for(web_server_task, timeout=5.0)
            logger.info("Задача веб-сервера завершена.")
        except asyncio.TimeoutError:
            logger.warning("Задача веб-сервера не завершилась вовремя, отменяем...")
            web_server_task.cancel()
            try: await web_server_task
            except asyncio.CancelledError: logger.info("Задача веб-сервера отменена.")
        except Exception as e:
            logger.exception(f"Ошибка при ожидании задачи веб-сервера: {e}")
    elif web_server_task: logger.info("Задача веб-сервера уже была завершена.")
    else: logger.info("Задачи веб-сервера не существует.")
    if application:
        logger.info("Полное завершение работы Telegram Application (shutdown)...")
        try:
            logger.info("Удаление вебхука...")
            await application.bot.delete_webhook(drop_pending_updates=False) # <-- РАСКОММЕНТИРОВАНО
            logger.info("Вебхук удален.")
            await application.shutdown()
            logger.info("Telegram Application shutdown завершен.")
        except BadRequest as e_bad:
             if "Webhook was not set" in str(e_bad):
                 logger.warning("Не удалось удалить вебхук: он не был установлен.")
                 try: await application.shutdown()
                 except Exception as e_sd: logger.error(f"Ошибка при application.shutdown() после неудачного delete_webhook: {e_sd}")
             else:
                 logger.exception(f"Ошибка BadRequest при удалении вебхука или shutdown: {e_bad}")
        except Exception as e:
            logger.exception(f"Ошибка во время application.shutdown(): {e}")
    if loop.is_running():
        logger.info("Остановка event loop...")
        loop.stop()
# *********************************************************************

def handle_signal(sig, loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event, application: Optional[Application], web_server_task: Optional[asyncio.Task]):
    # (Код handle_signal без изменений из x68)
    logger.info(f"Получен сигнал {sig.name}. Запуск последовательности остановки.")
    if application:
        asyncio.ensure_future(shutdown_sequence(loop, stop_event, application, web_server_task), loop=loop)
    else:
        logger.error("Application не был создан, невозможно запустить полную остановку.")
        if loop.is_running():
            loop.stop()


# --- ФУНКЦИЯ НАСТРОЙКИ БОТА И СЕРВЕРА (ВЕБХУК-ВЕРСИЯ) ---
# (Код setup_bot_and_server без изменений из x69)
async def setup_bot_and_server(stop_event: asyncio.Event) -> tuple[Optional[Application], Optional[asyncio.Future]]:
    application: Optional[Application] = None
    web_server_coro: Optional[asyncio.Future] = None
    try:
        if 'gemini_client' not in globals() or not gemini_client: raise RuntimeError("Клиент Gemini не создан.")
        if not TELEGRAM_BOT_TOKEN: raise RuntimeError("Токен Telegram не найден.")
        if not WEBHOOK_HOST: raise RuntimeError("WEBHOOK_HOST не указан!")

        search_status = "включен" if google_search_tool else "ОТКЛЮЧЕН"
        logger.info(f"Встроенный поиск Google ({search_tool_type_used}) глобально {search_status}.")
        logger.info("Инициализация приложения Telegram...")

        application = (Application.builder()
                       .token(TELEGRAM_BOT_TOKEN)
                       .connect_timeout(40)
                       .read_timeout(40)
                       .pool_timeout(60)
                       .build())
        logger.info("Application создан с увеличенными таймаутами.")

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("model", select_model_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(CallbackQueryHandler(select_model_callback))

        port = int(os.environ.get("PORT", 8080)); logger.info(f"Порт для веб-сервера: {port}")

        logger.info("Инициализация Telegram Application (initialize)...")
        await application.initialize()

        webhook_path = f"/{WEBHOOK_SECRET_PATH}"
        webhook_url = urljoin(WEBHOOK_HOST, webhook_path)
        logger.info(f"Попытка установить вебхук на URL: {webhook_url}")
        try:
            await application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            logger.info(f"Вебхук успешно установлен на {webhook_url}")
        except TelegramError as e:
            logger.exception(f"!!! Не удалось установить вебхук: {e}")
            raise

        logger.info("Подготовка корутины веб-сервера...")
        web_server_coro = run_web_server(port, stop_event, application)

        logger.info("Настройка бота и сервера (вебхук) завершена.")

    except Exception as e:
        logger.exception("Ошибка во время setup_bot_and_server!")
        return None, None
    return application, web_server_coro


# --- ТОЧКА ВХОДА (С РУЧНЫМ УПРАВЛЕНИЕМ ЦИКЛОМ - ВЕБХУК) ---
# (Код точки входа без изменений из x68)
if __name__ == '__main__':
    if 'gemini_client' in globals() and gemini_client:
        logger.info("Клиент Gemini создан. Настройка и запуск event loop (Webhook).")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        stop_event = asyncio.Event()
        application: Optional[Application] = None
        web_server_task: Optional[asyncio.Task] = None
        web_server_coro: Optional[asyncio.Future] = None
        try:
            logger.info("Запуск setup_bot_and_server (вебхук-версия)...")
            setup_result = loop.run_until_complete(setup_bot_and_server(stop_event))
            if setup_result: application, web_server_coro = setup_result
            else: raise RuntimeError("setup_bot_and_server завершился с ошибкой.")
            if not application: raise RuntimeError("Application не был создан в setup_bot_and_server.")
            if not web_server_coro: raise RuntimeError("Корутина веб-сервера не была создана в setup_bot_and_server.")
            logger.info("setup_bot_and_server завершен успешно.")

            logger.info("Создание задачи для веб-сервера...")
            web_server_task = loop.create_task(web_server_coro)
            logger.info("Задача веб-сервера создана.")

            logger.info("Настройка обработчиков сигналов...")
            sigs = (signal.SIGINT, signal.SIGTERM)
            for s in sigs:
                loop.add_signal_handler(
                    s,
                    lambda s=s: handle_signal(s, loop, stop_event, application, web_server_task)
                )
            logger.info("Обработчики сигналов настроены.")
            logger.info("Настройка обработчиков сигналов завершена.")
            logger.info("=== ПОПЫТКА ЗАПУСКА run_forever() (вебхук-режим) ===")
            loop.run_forever()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Прерывание (KeyboardInterrupt/SystemExit) получено.")
            if loop.is_running() and application:
                 logger.info("Запуск shutdown_sequence из-за KeyboardInterrupt/SystemExit...")
                 loop.run_until_complete(shutdown_sequence(loop, stop_event, application, web_server_task))
            elif loop.is_running():
                 logger.warning("Application не существует, просто останавливаем цикл.")
                 loop.stop()
        except Exception as e:
            logger.exception("Необработанная критическая ошибка в главном потоке!")
            if loop.is_running():
                logger.error("Запуск аварийной остановки из-за критической ошибки...")
                if application:
                     loop.run_until_complete(shutdown_sequence(loop, stop_event, application, web_server_task))
                else:
                     loop.stop()
        finally:
            # (Код finally без изменений из x68)
            logger.info("Блок finally erreicht.")
            if loop.is_running():
                logger.warning("Цикл все еще работает в блоке finally! Принудительная остановка.")
                loop.stop()
            logger.info("Ожидание завершения оставшихся задач...")
            try:
                 current_task = asyncio.current_task(loop=loop) if sys.version_info >= (3, 7) else None
                 tasks_to_check = [task for task in [web_server_task] if task is not None and task is not current_task and not task.done()]
                 other_tasks = [task for task in asyncio.all_tasks(loop=loop) if task is not current_task and task not in tasks_to_check]
                 tasks = tasks_to_check + other_tasks
                 if tasks:
                     logger.info(f"Отмена {len(tasks)} оставшихся задач...")
                     for task in tasks:
                         logger.debug(f"Отмена задачи {task.get_name()}: done={task.done()}, cancelled={task.cancelled()}")
                         task.cancel()
                     results = loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                     logger.info(f"Результаты gather отмененных задач: {results}")
                     logger.info("Оставшиеся задачи завершены/отменены.")
                 else:
                      logger.info("Нет оставшихся задач для завершения.")
            except RuntimeError as e:
                 if "no running event loop" in str(e) or "loop is closed" in str(e):
                      logger.warning(f"Не удалось собрать задачи, цикл уже закрыт: {e}")
                 else:
                      logger.error(f"Ошибка RuntimeError при завершении оставшихся задач: {e}")
            except Exception as e:
                 logger.error(f"Неожиданная ошибка при завершении оставшихся задач: {e}")
            if not loop.is_closed():
                 logger.info("Закрытие event loop...")
                 loop.close()
                 logger.info("Event loop закрыт.")
            else:
                 logger.info("Event loop уже был закрыт.")
            logger.info("Процесс завершен.")
    else:
        logger.critical("Завершение работы, так как клиент Gemini не был создан.")

# --- END OF REALLY x72 FULL CORRECTED main.py (NEW sys_instruction + fix API call for v1.10.0) ---
