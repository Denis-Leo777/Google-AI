# --- START OF REALLY x66 FULL CORRECTED main.py (x62 + CORRECTLY fix get_model + REMOVE Client object) ---

import logging
import os
import asyncio
import signal # <-- Для обработки сигналов остановки
import time
import random
import google.genai as genai # Импортируем сам модуль
import aiohttp.web
import sys
import secrets # Для генерации секретного пути
from urllib.parse import urljoin # Для создания URL вебхука
import json

# --- КОНФИГУРАЦИЯ ЛОГОВ ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("telegram.ext").setLevel(logging.DEBUG)
logging.getLogger("telegram.bot").setLevel(logging.DEBUG)
logging.getLogger("telegram.request").setLevel(logging.DEBUG)
logging.getLogger("aiohttp.web").setLevel(logging.DEBUG)
# *************************

# --- ИМПОРТ ТИПОВ ---
# (Импорт и заглушки из x62)
genai_types = None; Tool = None; GenerateContentConfig = None; GoogleSearch = None; Content = dict; Part = dict
class DummyFinishReasonEnum: FINISH_REASON_UNSPECIFIED = 0; STOP = 1; MAX_TOKENS = 2; SAFETY = 3; RECITATION = 4; OTHER = 5; _enum_map = {0: "UNSPECIFIED", 1: "STOP", 2: "MAX_TOKENS", 3: "SAFETY", 4: "RECITATION", 5: "OTHER"}
class DummyHarmCategoryEnum: HARM_CATEGORY_UNSPECIFIED = 0; HARM_CATEGORY_HARASSMENT = 7; HARM_CATEGORY_HATE_SPEECH = 8; HARM_CATEGORY_SEXUALLY_EXPLICIT = 9; HARM_CATEGORY_DANGEROUS_CONTENT = 10; _enum_map = {0: "UNSPECIFIED", 7: "HARASSMENT", 8: "HATE_SPEECH", 9: "SEXUALLY_EXPLICIT", 10: "DANGEROUS_CONTENT"}
class DummyHarmProbabilityEnum: HARM_PROBABILITY_UNSPECIFIED = 0; NEGLIGIBLE = 1; LOW = 2; MEDIUM = 3; HIGH = 4; _enum_map = {0: "UNSPECIFIED", 1: "NEGLIGIBLE", 2: "LOW", 3: "MEDIUM", 4: "HIGH"}
FinishReason = DummyFinishReasonEnum(); HarmCategory = DummyHarmCategoryEnum(); HarmProbability = DummyHarmProbabilityEnum()
ResourceExhausted=Exception; GoogleAPIError=Exception; FailedPrecondition=Exception; InvalidArgument=ValueError; BadRequest = Exception
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

# Возвращаем импорт типов из typing
from typing import Optional, Dict, Union, Any, List, Tuple
import urllib.parse

try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")
try: from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition, InvalidArgument; logger.info("Исключения google.api_core импортированы.")
except ImportError: logger.warning("!!! НЕ УДАЛОСЬ импортировать google.api_core.exceptions.")
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError, BadRequest
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

# *** Используем ТОЛЬКО genai.configure() ***
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    logger.info("Клиент google.genai сконфигурирован.")
except Exception as e:
    logger.exception("!!! КРИТ. ОШИБКА конфигурации google.genai!"); exit("Ошибка конфигурации Gemini.")

AVAILABLE_MODELS = {'⚡ Flash 2.0': 'models/gemini-2.0-flash-001', '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25'}
if not AVAILABLE_MODELS: exit("Нет моделей в AVAILABLE_MODELS!")
DEFAULT_MODEL_ALIAS = '✨ Pro 2.5'
if DEFAULT_MODEL_ALIAS not in AVAILABLE_MODELS: DEFAULT_MODEL_ALIAS = next(iter(AVAILABLE_MODELS)); logger.warning(f"Дефолтная модель не найдена, установлена: {DEFAULT_MODEL_ALIAS}")

# --- ПРОВЕРКА ИМПОРТА ПОИСКА ---
# (Без изменений из x62)
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

system_instruction_text = (
    # (Текст системного промпта без изменений из x62)
    "Ты - лучший эксперт в любых вопросах. Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку."
    "Подкрепляй аргументами и доказательствами, но без самоповторов. Если не знаешь ответ - всегда предупреждай, что пишешь предположение."
    "Обязательно используй поиск в интернете для сверки с новой информацией по теме."
    "Если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, написание кода, или другая, требующая объема работа, то отвечай в пределах 2000 знаков."
    "Активно применяй юмор: несоответствие ожиданиям, культурные и бытовые отсылки, шутки об актуальных в интернете темах, жизненный абсурд и абсурдные решения проблем, псевдомудрость, разрушение идиом, безобидная ирония и самоирония, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, тонкие и интимные намёки, редукционизм, пост-модерн и интернет-юмор."
    "При создании уникальной работы не допускай признаков ИИ, избегай копирования или близкого пересказа существующих текстов, включай гипотетические ситуации для иллюстрации понятий, применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи, варьируй структуру предложений, естественно включай разговорные выражения, идиомы и фигуры речи, используй живые стилистические решения, свойственные людям, вставляй региональные выражения или культурно специфичные ссылки, где это уместно, добавляй остроумие."
    "При исправлении ошибки, указанной пользователем, идентифицируй конкретную строку(и) и конкретную причину ошибки. Бери за основу последнюю ПОЛНУЮ версию, предоставленную пользователем или сгенерированную тобой и подтвержденную как шаг вперед (даже если причиной была другая ошибка). Внеси только минимально необходимые изменения для исправления указанной ошибки. НЕ переписывай смежные части, НЕ удаляй ничего, НЕ меняй форматирование в других частях без явного запроса."
    "При возникновении сомнений, уточни у пользователя, какую версию использовать как базу. Если в ходе диалога выявляется повторяющаяся ошибка, добавь это в 'красный список' для данной сессии. Перед отправкой любого ответа, содержащего подобные конструкции, выполни целенаправленную проверку именно этих 'болевых точек'."
    "Если пользователь предоставляет свой полный текст (или код) как основу, используй именно этот текст (код). Не пытайся 'улучшить' или 'переформатировать' его части, не относящиеся к запросу на исправление, если только пользователь явно об этом не попросил."
    "В диалогах, связанных с разработкой или итеративным исправлением, всегда явно ссылайся на номер версии или предыдущее сообщение, которое берется за основу. Поддерживай четкое понимание, какая версия кода является 'последней рабочей' или 'последней предоставленной'."
)

# --- ФУНКЦИЯ ИЗВЛЕЧЕНИЯ ТЕКСТА ---
# (Код extract_response_text без изменений из x62)
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
# (Код start, select_model_command, select_model_callback без изменений из x62)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Обработка /start для {user.id} в {chat_id}.")
    actual_default_model = DEFAULT_MODEL_ALIAS
    search_status = "включен (если поддерживается)" if google_search_tool else "ОТКЛЮЧЕН"
    await update.message.reply_html(rf"Привет, {user.mention_html()}! Бот Gemini (client) v66 (Webhook)." f"\n\nМодель: <b>{actual_default_model}</b>" f"\n🔍 Поиск Google: <b>{search_status}</b>." f"\n\n/model - сменить." f"\n/start - сбросить." f"\n\nСпрашивай!", reply_to_message_id=update.message.message_id)

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
    # *** Блок try...except начинается здесь ***
    try:
        if not update.message or not update.message.text: logger.warning("Пустое сообщение."); return
        user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id; message_id = update.message.message_id
        logger.debug(f"handle_message вызван для сообщения {message_id} в чате {chat_id}")
        logger.info(f"Сообщение от {user.id} ({len(user_message)}): '{user_message[:80].replace(chr(10), ' ')}...'")
        selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
        model_id = AVAILABLE_MODELS.get(selected_alias)
        if not model_id: logger.error(f"Крит. ошибка: Не найден ID для '{selected_alias}'"); await update.message.reply_text("Ошибка конфига.", reply_to_message_id=message_id); return
        final_text: Optional[str] = None; search_suggestions: List[str] = []; error_message: Optional[str] = None; start_time = time.monotonic()

        # *** Внутренний try для Gemini API ***
        try:
            current_history = chat_histories.get(chat_id, [])
            api_contents = []
            try: user_part = Part(text=user_message) if Part is not dict else {'text': user_message}; api_contents = current_history + [{'role': 'user', 'parts': [user_part]}]
            except Exception as e: logger.error(f"Ошибка Part user: {e}"); api_contents = current_history + [{'role': 'user', 'parts': [{'text': user_message}]}]
            logger.info(f"Запрос к '{model_id}'. История: {len(current_history)} сообщ.")
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

            tools_list = [google_search_tool] if google_search_tool else None
            generation_config_for_api = {}

            # *** ИСПРАВЛЕНИЕ: Используем genai.GenerativeModel() ***
            model_obj = genai.GenerativeModel(model_id)
            if not model_obj:
                raise ValueError(f"Не удалось получить объект модели для {model_id}")
            # *********************************************************

            if system_instruction_text:
                try:
                     system_instruction_content = Content(parts=[Part(text=system_instruction_text)]) if Content is not dict and Part is not dict else {'parts': [{'text': system_instruction_text}]}
                     model_obj.system_instruction = system_instruction_content
                     logger.debug("System instruction присвоен объекту модели.")
                except Exception as e:
                    logger.error(f"Ошибка создания/присвоения system_instruction Content: {e}")

            response = model_obj.generate_content(
                 contents=api_contents,
                 generation_config=generation_config_for_api if generation_config_for_api else None,
                 tools=tools_list
            )

            processing_time = time.monotonic() - start_time; logger.info(f"Ответ от '{model_id}' получен за {processing_time:.2f} сек.")
            final_text = extract_response_text(response)
            if final_text and not final_text.startswith("⚠️"):
                 try: model_part = Part(text=final_text) if Part is not dict else {'text': final_text}; history_to_update = chat_histories.get(chat_id, [])[:]; history_to_update.append({'role': 'user', 'parts': api_contents[-1]['parts']}); history_to_update.append({'role': 'model', 'parts': [model_part]}); chat_histories[chat_id] = history_to_update
                 except Exception as e: logger.error(f"Ошибка обновления истории: {e}")
                 logger.info(f"История чата {chat_id} обновлена, теперь {len(chat_histories[chat_id])} сообщений.")
            # *** ВОССТАНОВЛЕННЫЙ ELIF И ELSE ***
            elif final_text and final_text.startswith("⚠️"): error_message = final_text; final_text = None; logger.warning(f"Ответ был ошибкой,
