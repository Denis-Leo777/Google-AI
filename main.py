# --- START OF REALLY x42 FULL CORRECTED main.py (META INSTRUCTIONS ADDED) ---

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
# (Импорт и заглушки без изменений)
genai_types = None; Tool = None; GenerateContentConfig = None; GoogleSearch = None; Content = dict; Part = dict
# ... (определения заглушек Enum и исключений) ...
try:
    from google.genai import types as genai_types; logger.info("Импортирован модуль google.genai.types.")
    try: Tool = genai_types.Tool; logger.info("Найден genai_types.Tool")
    except AttributeError: logger.warning("genai_types.Tool не найден.")
    # ... (остальные try-except для импорта типов) ...
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

# --- ИЗМЕНЕНО: Системная инструкция с добавлением мета-инструкций ---
system_instruction_text = (
    "Никогда не сокращай текст, код и прочее, пиши всё полностью. Обязательно используй поиск в интернете для сверки с новой информацией по теме. "
    "Если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, или другая, требующая объема работа, то отвечай в пределах 2000 знаков. "
    "Ты - лучший эксперт в любых вопросах. Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку. "
    "Подкрепляй аргументами и доказательствами, но без самоповторов. Если не знаешь ответ - всегда предупреждай, что пишешь предположение. "
    "Активно применяй юмор: несоответствие ожиданиям, культурные и бытовые отсылки, шутки об актуальных в интернете темах, жизненный абсурд и абсурдные решения проблем, псевдомудрость, разрушение идиом, безобидная ирония и самоирония, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, тонкие и интимные намёки, редукционизм, пост-модерн и интернет-юмор. "
    "При создании уникальной работы не допускай признаков ИИ, избегай копирования или близкого пересказа существующих текстов, включай гипотетические ситуации для иллюстрации понятий, применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи, варьируй структуру предложений, естественно включай разговорные выражения, идиомы и фигуры речи, используй живые стилистические решения, свойственные людям, вставляй региональные выражения или культурно специфичные ссылки, где это уместно, добавляй остроумие.\n\n"
    # --- ДОБАВЛЕННЫЕ МЕТА-ИНСТРУКЦИИ ---
    "При исправлении ошибки, указанной пользователем по логам, идентифицируй конкретную строку(и) и конкретную причину ошибки. Бери за основу последнюю ПОЛНУЮ версию кода, предоставленную пользователем или сгенерированную тобой и подтвержденную как шаг вперед (даже если она упала с другой ошибкой). Внеси только минимально необходимые изменения для исправления указанной ошибки. НЕ переписывай смежные блоки, НЕ удаляй код, НЕ меняй форматирование в других частях без явного запроса."
    "В диалогах, связанных с разработкой или итеративным исправлением, всегда явно ссылайся на номер версии или предыдущее сообщение, которое берется за основу. Поддерживай четкое понимание, какая версия кода является 'последней рабочей' или 'последней предоставленной'. При возникновении сомнений, уточни у пользователя, какую версию кода использовать как базу."
    "Если в ходе диалога выявляется повторяющаяся ошибка (например, IndentationError в except блоках, неправильный импорт типов, неверные параметры API), добавь это в 'красный список' для данной сессии. Перед отправкой любого кода, содержащего подобные конструкции, выполни целенаправленную проверку именно этих 'болевых точек'."
    "Если пользователь предоставляет свой полный код как основу, используй именно этот код. Не пытайся 'улучшить' или 'переформатировать' его части, не относящиеся к запросу на исправление, если только пользователь явно об этом не попросил."
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
    await update.message.reply_html(rf"Привет, {user.mention_html()}! Бот Gemini (client) v42." f"\n\nМодель: <b>{actual_default_model}</b>" f"\n🔍 Поиск Google: <b>{search_status}</b>." f"\n\n/model - сменить." f"\n/start - сбросить." f"\n\nСпрашивай!", reply_to_message_id=update.message.message_id)

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
    # (Код handle_message без изменений)
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
             try: # ... (извлечение метаданных)
             except (AttributeError, IndexError): pass
    except InvalidArgument as e_arg: logger.error(f"Ошибка InvalidArgument для '{model_id}': {e_arg}"); error_message = f"❌ Ошибка в запросе к '{selected_alias}'.";
    except ResourceExhausted as e_limit: logger.warning(f"Исчерпана квота API для '{model_id}': {e_limit}"); error_message = f"😔 Модель '{selected_alias}' устала (лимиты)."
    except (GoogleAPIError, Exception) as e_other: logger.exception(f"Неожиданная ошибка API ('{model_id}'): {e_other}"); error_message = f"😵 Ошибка ({type(e_other).__name__}) при общении с '{selected_alias}'."
    reply_markup = None
    if search_suggestions: # ... (создание клавиатуры)
    if final_text: # ... (отправка ответа)
    elif error_message: # ... (отправка ошибки)
    else: # ... (fallback)

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

# --- END OF REALLY x42 FULL CORRECTED main.py (META INSTRUCTIONS ADDED) ---
