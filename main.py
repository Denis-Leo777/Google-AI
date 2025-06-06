# Обновлённый main.py:
# ... (все предыдущие комментарии остаются)
# === ИСПРАВЛЕНИЯ (дата текущего изменения, v8) ===
# - Исправлена ошибка 'Unclosed client session' для DuckDuckGo-поиска
#   путем использования асинхронного менеджера контекста.
# - Исправлена ошибка 'SSL SYSCALL error: EOF detected' при выключении
#   бота путем удаления избыточного вызова flush() для persistence.

import logging
import os
import asyncio
import signal
from urllib.parse import urlencode, urlparse, parse_qs
import base64
import pprint
import json
import time
import re
import datetime
import pytz
import pickle
from collections import defaultdict
import psycopg2
from psycopg2 import pool
import io

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

import aiohttp
import aiohttp.web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message, BotCommand
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    BasePersistence
)
from telegram.error import BadRequest, TelegramError
import google.generativeai as genai
from duckduckgo_search import DDGS
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from pdfminer.high_level import extract_text

try:
    with open('system_prompt.md', 'r', encoding='utf-8') as f:
        system_instruction_text = f.read()
    logger.info("Системный промпт успешно загружен из файла system_prompt.md.")
except FileNotFoundError:
    logger.critical("Критическая ошибка: файл system_prompt.md не найден! Бот не может работать без него.")
    system_instruction_text = "Ты — полезный ассистент."
    exit(1)
except Exception as e_prompt_file:
    logger.critical(f"Критическая ошибка при чтении файла system_prompt.md: {e_prompt_file}", exc_info=True)
    system_instruction_text = "Ты — полезный ассистент."
    exit(1)

class PostgresPersistence(BasePersistence):
    def __init__(self, database_url: str):
        super().__init__()
        self.db_pool = None
        try:
            self.db_pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=database_url)
            self._initialize_db()
            logger.info("PostgresPersistence: Соединение с базой данных установлено и таблица проверена.")
        except psycopg2.Error as e:
            logger.critical(f"PostgresPersistence: Не удалось подключиться к базе данных PostgreSQL: {e}")
            raise

    def _execute(self, query: str, params: tuple = None, fetch: str = None):
        if not self.db_pool:
            raise ConnectionError("PostgresPersistence: Пул соединений не инициализирован.")
        conn = None
        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cur:
                cur.execute(query, params)
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                conn.commit()
        except psycopg2.Error as e:
            logger.error(f"PostgresPersistence: Ошибка выполнения SQL-запроса: {e}")
            if conn:
                try:
                    conn.rollback()
                except psycopg2.InterfaceError:
                    logger.warning("PostgresPersistence: Не удалось откатить транзакцию, соединение уже было закрыто.")
            return None
        finally:
            if conn:
                self.db_pool.putconn(conn)

    def _initialize_db(self):
        create_table_query = """
        CREATE TABLE IF NOT EXISTS persistence_data (
            key TEXT PRIMARY KEY,
            data BYTEA NOT NULL
        );
        """
        self._execute(create_table_query)

    def _get_pickled(self, key: str) -> object | None:
        result = self._execute("SELECT data FROM persistence_data WHERE key = %s;", (key,), fetch="one")
        return pickle.loads(result[0]) if result and result[0] else None

    def _set_pickled(self, key: str, data: object) -> None:
        pickled_data = pickle.dumps(data)
        query = "INSERT INTO persistence_data (key, data) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET data = %s;"
        self._execute(query, (key, pickled_data, pickled_data))

    async def get_bot_data(self) -> dict:
        return self._get_pickled("bot_data") or {}

    async def update_bot_data(self, data: dict) -> None:
        self._set_pickled("bot_data", data)

    async def get_chat_data(self) -> defaultdict[int, dict]:
        all_chat_data = self._execute("SELECT key, data FROM persistence_data WHERE key LIKE 'chat_data_%';", fetch="all")
        chat_data = defaultdict(dict)
        if all_chat_data:
            for key, data in all_chat_data:
                try:
                    chat_id = int(key.split('_')[-1])
                    chat_data[chat_id] = pickle.loads(data)
                except (ValueError, IndexError):
                    logger.warning(f"PostgresPersistence: Не удалось распарсить ключ чата: {key}")
        return chat_data

    async def update_chat_data(self, chat_id: int, data: dict) -> None:
        self._set_pickled(f"chat_data_{chat_id}", data)

    async def get_user_data(self) -> defaultdict[int, dict]:
        all_user_data = self._execute("SELECT key, data FROM persistence_data WHERE key LIKE 'user_data_%';", fetch="all")
        user_data = defaultdict(dict)
        if all_user_data:
            for key, data in all_user_data:
                try:
                    user_id = int(key.split('_')[-1])
                    user_data[user_id] = pickle.loads(data)
                except (ValueError, IndexError):
                    logger.warning(f"PostgresPersistence: Не удалось распарсить ключ пользователя: {key}")
        return user_data

    async def update_user_data(self, user_id: int, data: dict) -> None:
        self._set_pickled(f"user_data_{user_id}", data)

    async def get_callback_data(self) -> dict | None:
        return None

    async def update_callback_data(self, data: dict) -> None:
        pass

    async def get_conversations(self, name: str) -> dict:
        return {}

    async def update_conversation(self, name: str, key: tuple, new_state: object | None) -> None:
        pass

    async def drop_chat_data(self, chat_id: int) -> None:
        self._execute("DELETE FROM persistence_data WHERE key = %s;", (f"chat_data_{chat_id}",))

    async def drop_user_data(self, user_id: int) -> None:
        self._execute("DELETE FROM persistence_data WHERE key = %s;", (f"user_data_{user_id}",))

    async def refresh_bot_data(self, bot_data: dict) -> None:
        data = await self.get_bot_data()
        bot_data.update(data)

    async def refresh_chat_data(self, chat_id: int, chat_data: dict) -> None:
        data = self._get_pickled(f"chat_data_{chat_id}") or {}
        chat_data.update(data)

    async def refresh_user_data(self, user_id: int, user_data: dict) -> None:
        data = self._get_pickled(f"user_data_{user_id}") or {}
        user_data.update(data)

    async def flush(self) -> None:
        pass

    def close(self):
        if self.db_pool:
            self.db_pool.closeall()
            logger.info("PostgresPersistence: Все соединения с базой данных успешно закрыты.")

HARM_CATEGORIES_STRINGS = [
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT",
]
BLOCK_NONE_STRING = "BLOCK_NONE"
SAFETY_SETTINGS_BLOCK_NONE = []
BlockedPromptException = type('BlockedPromptException', (Exception,), {})
StopCandidateException = type('StopCandidateException', (Exception,), {})
HarmCategory = type('HarmCategory', (object,), {})
HarmBlockThreshold = type('HarmBlockThreshold', (object,), {})
SafetyRating = type('SafetyRating', (object,), {'category': None, 'probability': None})
BlockReason = type('BlockReason', (object,), {'UNSPECIFIED': 'UNSPECIFIED', 'OTHER': 'OTHER', 'SAFETY': 'SAFETY', 'name': 'UNSPECIFIED'})
FinishReason = type('FinishReason', (object,), {'STOP': 'STOP', 'SAFETY': 'SAFETY', 'RECITATION': 'RECITATION', 'OTHER':'OTHER', 'MAX_TOKENS':'MAX_TOKENS', 'name': 'STOP'})

try:
    from google.generativeai.types import (
        HarmCategory as RealHarmCategory, HarmBlockThreshold as RealHarmBlockThreshold,
        BlockedPromptException as RealBlockedPromptException,
        StopCandidateException as RealStopCandidateException,
        SafetyRating as RealSafetyRating, BlockReason as RealBlockReason,
        FinishReason as RealFinishReason
    )
    logger.info("Типы google.generativeai.types успешно импортированы.")
    HarmCategory, HarmBlockThreshold, BlockedPromptException, StopCandidateException, SafetyRating, BlockReason, FinishReason = \
        RealHarmCategory, RealHarmBlockThreshold, RealBlockedPromptException, RealStopCandidateException, RealSafetyRating, RealBlockReason, RealFinishReason

    temp_safety_settings = []
    all_enums_found = True
    if hasattr(HarmBlockThreshold, 'BLOCK_NONE'):
        block_none_enum = HarmBlockThreshold.BLOCK_NONE
        for cat_str in HARM_CATEGORIES_STRINGS:
            if hasattr(HarmCategory, cat_str):
                temp_safety_settings.append({"category": getattr(HarmCategory, cat_str), "threshold": block_none_enum})
            else:
                logger.warning(f"Атрибут категории '{cat_str}' не найден в HarmCategory.")
                all_enums_found = False
                break
    else:
        logger.warning("Атрибут 'BLOCK_NONE' не найден в HarmBlockThreshold.")
        all_enums_found = False

    if all_enums_found and temp_safety_settings:
        SAFETY_SETTINGS_BLOCK_NONE = temp_safety_settings
        logger.info("Настройки безопасности BLOCK_NONE установлены с Enum.")
    elif HARM_CATEGORIES_STRINGS:
        logger.warning("Не удалось создать SAFETY_SETTINGS_BLOCK_NONE с Enum. Используем строки.")
        SAFETY_SETTINGS_BLOCK_NONE = [{"category": cat_str, "threshold": BLOCK_NONE_STRING} for cat_str in HARM_CATEGORIES_STRINGS]
    else:
        logger.warning("Список HARM_CATEGORIES_STRINGS пуст, настройки безопасности не установлены.")
        SAFETY_SETTINGS_BLOCK_NONE = []
except ImportError:
    logger.warning("Не удалось импортировать типы из google.generativeai.types. Используем строки и заглушки.")
    if HARM_CATEGORIES_STRINGS:
        SAFETY_SETTINGS_BLOCK_NONE = [{"category": cat_str, "threshold": BLOCK_NONE_STRING} for cat_str in HARM_CATEGORIES_STRINGS]
        logger.warning("Настройки безопасности установлены со строками (BLOCK_NONE).")
    else:
        logger.warning("Список HARM_CATEGORIES_STRINGS пуст, настройки не установлены.")
        SAFETY_SETTINGS_BLOCK_NONE = []
except Exception as e_import_types:
    logger.error(f"Ошибка при импорте/настройке типов Gemini: {e_import_types}", exc_info=True)
    if HARM_CATEGORIES_STRINGS:
         SAFETY_SETTINGS_BLOCK_NONE = [{"category": cat_str, "threshold": BLOCK_NONE_STRING} for cat_str in HARM_CATEGORIES_STRINGS]
         logger.warning("Настройки безопасности установлены со строками (BLOCK_NONE) из-за ошибки.")
    else:
         logger.warning("Список HARM_CATEGORIES_STRINGS пуст, настройки не установлены из-за ошибки.")
         SAFETY_SETTINGS_BLOCK_NONE = []

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')
DATABASE_URL = os.getenv('DATABASE_URL')

required_env_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN, "GOOGLE_API_KEY": GOOGLE_API_KEY,
    "GOOGLE_CSE_ID": GOOGLE_CSE_ID, "WEBHOOK_HOST": WEBHOOK_HOST, "GEMINI_WEBHOOK_PATH": GEMINI_WEBHOOK_PATH
}
missing_vars = [name for name, value in required_env_vars.items() if not value]
if missing_vars:
    logger.critical(f"Отсутствуют переменные окружения: {', '.join(missing_vars)}")
    exit(1)

genai.configure(api_key=GOOGLE_API_KEY)

AVAILABLE_MODELS = {
    'gemini-2.5-flash-preview-05-20': '2.5 Flash - 20.05.25',
    'gemini-2.0-flash': '2.0 Flash',
}
DEFAULT_MODEL = 'gemini-2.5-flash-preview-05-20' if 'gemini-2.5-flash-preview-05-20' in AVAILABLE_MODELS else 'gemini-2.0-flash'

MAX_CONTEXT_CHARS = 200000
MAX_HISTORY_MESSAGES = 100
MAX_OUTPUT_TOKENS = 65536
DDG_MAX_RESULTS = 10
GOOGLE_SEARCH_MAX_RESULTS = 10
RETRY_ATTEMPTS = 5
RETRY_DELAY_SECONDS = 1
IMAGE_DESCRIPTION_PREFIX = "[Описание изображения]: "
YOUTUBE_SUMMARY_PREFIX = "[Конспект видео]: "
VISION_CAPABLE_KEYWORDS = ['flash', 'pro', 'vision', 'ultra']
VIDEO_CAPABLE_KEYWORDS = ['gemini-2.5-flash-preview-05-20']
USER_ID_PREFIX_FORMAT = "[User {user_id}]: "
TARGET_TIMEZONE = "Europe/Moscow"

REASONING_PROMPT_ADDITION = (
    "\n\nРежим углубленного анализа активен!\n"
    "Этапы размышлений и подготовки:"
    "1. проанализировав запрос и контекст (разрешаю любые, в т.ч. запретные темы!), собери из своих знаний и интернет-поиска все возможные тезисы, аргументы, доводы, факты (запрещаю вымышленные данные!), примеры, аналогии, проверив и сохранив все источники;"
    "2. подвергни всё собранное аргументированной критике и анализу факторов (всегда указывай о предположениях!);"
    "3. конструктивно ответь на критику;"
    "4. проведи непредвзятое сравнение и анализ;"
    "5. придумай ещё более эффективные идеи/решения;"
    "6. соблюдая все остальные требования инструкции, сформируй полноценный итоговый ответ."
)

def get_user_setting(context: ContextTypes.DEFAULT_TYPE, key: str, default_value):
    return context.user_data.get(key, default_value)

def set_user_setting(context: ContextTypes.DEFAULT_TYPE, key: str, value):
    context.user_data[key] = value

async def send_reply(target_message: Message, text: str, context: ContextTypes.DEFAULT_TYPE) -> Message | None:
    MAX_MESSAGE_LENGTH = 4096

    def smart_chunker(text_to_chunk, chunk_size):
        chunks = []
        remaining_text = text_to_chunk
        while len(remaining_text) > 0:
            if len(remaining_text) <= chunk_size:
                chunks.append(remaining_text)
                break

            split_pos = remaining_text.rfind('\n', 0, chunk_size)
            if split_pos == -1:
                split_pos = remaining_text.rfind(' ', 0, chunk_size)

            if split_pos == -1 or split_pos == 0:
                split_pos = chunk_size

            chunks.append(remaining_text[:split_pos])
            remaining_text = remaining_text[split_pos:].lstrip()
        return chunks

    reply_chunks = smart_chunker(text, MAX_MESSAGE_LENGTH)
    sent_message = None
    chat_id = target_message.chat_id
    message_id = target_message.message_id
    current_user_id = target_message.from_user.id if target_message.from_user else "Unknown"

    try:
        for i, chunk in enumerate(reply_chunks):
            if i == 0:
                sent_message = await context.bot.send_message(chat_id=chat_id, text=chunk, reply_to_message_id=message_id, parse_mode=ParseMode.MARKDOWN)
            else:
                sent_message = await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(0.1)
        return sent_message
    except BadRequest as e_md:
        if "Can't parse entities" in str(e_md) or "can't parse" in str(e_md).lower() or "reply message not found" in str(e_md).lower():
            problematic_chunk_preview = "N/A"
            if 'i' in locals() and i < len(reply_chunks):
                problematic_chunk_preview = reply_chunks[i][:500].replace('\n', '\\n')

            logger.warning(f"UserID: {current_user_id}, ChatID: {chat_id} | Ошибка парсинга Markdown или ответа на сообщение ({message_id}): {e_md}. Проблемный чанк (начало): '{problematic_chunk_preview}...'. Попытка отправить как обычный текст.")
            try:
                sent_message = None
                full_text_plain = text
                plain_chunks = [full_text_plain[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(full_text_plain), MAX_MESSAGE_LENGTH)]

                for i_plain, chunk_plain in enumerate(plain_chunks):
                     if i_plain == 0:
                         sent_message = await context.bot.send_message(chat_id=chat_id, text=chunk_plain, reply_to_message_id=message_id)
                     else:
                         sent_message = await context.bot.send_message(chat_id=chat_id, text=chunk_plain)
                     await asyncio.sleep(0.1)
                return sent_message
            except Exception as e_plain:
                logger.error(f"UserID: {current_user_id}, ChatID: {chat_id} | Не удалось отправить даже как обычный текст: {e_plain}", exc_info=True)
                try:
                    await context.bot.send_message(chat_id=chat_id, text="❌ Не удалось отправить ответ.")
                except Exception as e_final_send:
                    logger.critical(f"UserID: {current_user_id}, ChatID: {chat_id} | Не удалось отправить сообщение об ошибке: {e_final_send}")
        else:
            logger.error(f"UserID: {current_user_id}, ChatID: {chat_id} | Ошибка при отправке ответа (Markdown): {e_md}", exc_info=True)
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка при отправке ответа: {str(e_md)[:100]}...")
            except Exception as e_error_send:
                logger.error(f"UserID: {current_user_id}, ChatID: {chat_id} | Не удалось отправить сообщение об ошибке отправки: {e_error_send}")
    except Exception as e_other:
        logger.error(f"UserID: {current_user_id}, ChatID: {chat_id} | Непредвиденная ошибка при отправке ответа: {e_other}", exc_info=True)
        try:
            await context.bot.send_message(chat_id=chat_id, text="❌ Произошла непредвиденная ошибка при отправке ответа.")
        except Exception as e_unexp_send:
            logger.error(f"UserID: {current_user_id}, ChatID: {chat_id} | Не удалось отправить сообщение о непредвиденной ошибке: {e_unexp_send}")
    return None

def _get_text_from_response(response_obj, user_id_for_log, chat_id_for_log, log_prefix_for_func) -> str | None:
    reply_text = None
    try:
        reply_text = response_obj.text
        if reply_text:
             logger.debug(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) Текст успешно извлечен из response.text.")
             return reply_text.strip()
        logger.debug(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) response.text пуст или None, проверяем candidates.")
    except ValueError as e_val_text:
        logger.warning(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) response.text вызвал ValueError: {e_val_text}. Проверяем candidates...")
    except Exception as e_generic_text:
        logger.error(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) Неожиданная ошибка при доступе к response.text: {e_generic_text}", exc_info=True)

    if hasattr(response_obj, 'candidates') and response_obj.candidates:
        try:
            candidate = response_obj.candidates[0]
            if hasattr(candidate, 'content') and candidate.content and \
               hasattr(candidate.content, 'parts') and candidate.content.parts:
                parts_texts = [part.text for part in candidate.content.parts if hasattr(part, 'text')]
                if parts_texts:
                    reply_text = "".join(parts_texts).strip()
                    if reply_text:
                        logger.info(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) Текст извлечен из response.candidates[0].content.parts.")
                        return reply_text
                    else:
                        logger.warning(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) Текст из response.candidates[0].content.parts оказался пустым после strip.")
                else:
                    logger.warning(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) response.candidates[0].content.parts есть, но не содержат текстовых частей.")
            else:
                fr_candidate = getattr(candidate, 'finish_reason', None)
                fr_name = "N/A"
                if fr_candidate is not None:
                    fr_name = getattr(fr_candidate, 'name', str(fr_candidate))

                is_safety_other_reason = False
                if FinishReason and hasattr(FinishReason, 'SAFETY') and hasattr(FinishReason, 'OTHER'):
                    is_safety_other_reason = (fr_candidate == FinishReason.SAFETY or fr_candidate == FinishReason.OTHER)
                elif fr_name in ['SAFETY', 'OTHER']:
                    is_safety_other_reason = True

                if fr_candidate and not is_safety_other_reason:
                    logger.warning(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) response.candidates[0] не имеет (валидных) content.parts, но finish_reason={fr_name}.")
                else:
                    logger.warning(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) response.candidates[0] не имеет (валидных) content.parts. Finish_reason: {fr_name}")
        except IndexError:
             logger.warning(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) IndexError при доступе к response_obj.candidates[0] (список кандидатов пуст).")
        except Exception as e_cand:
            logger.error(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) Ошибка при обработке candidates: {e_cand}", exc_info=True)
    else:
        logger.warning(f"UserID: {user_id_for_log}, ChatID: {chat_id_for_log} | ({log_prefix_for_func}) В ответе response нет ни response.text, ни валидных candidates с текстом.")

    return None

def _get_effective_context_for_task(
    task_type: str,
    original_context: ContextTypes.DEFAULT_TYPE,
    user_id: int | str,
    chat_id: int,
    log_prefix: str
) -> ContextTypes.DEFAULT_TYPE:
    capability_map = {
        "vision": VISION_CAPABLE_KEYWORDS,
        "video": VIDEO_CAPABLE_KEYWORDS
    }
    required_keywords = capability_map.get(task_type)
    if not required_keywords:
        logger.error(f"UserID: {user_id}, ChatID: {chat_id} | ({log_prefix}) Неизвестный тип задачи '{task_type}' для выбора модели.")
        return original_context

    selected_model = get_user_setting(original_context, 'selected_model', DEFAULT_MODEL)

    is_capable = any(keyword in selected_model for keyword in required_keywords)
    if is_capable:
        logger.info(f"UserID: {user_id}, ChatID: {chat_id} | ({log_prefix}) Модель пользователя '{selected_model}' подходит для задачи '{task_type}'.")
        return original_context

    available_capable_models = [
        m_id for m_id in AVAILABLE_MODELS
        if any(keyword in m_id for keyword in required_keywords)
    ]

    if not available_capable_models:
        logger.error(f"UserID: {user_id}, ChatID: {chat_id} | ({log_prefix}) Нет доступных моделей для задачи '{task_type}'.")
        return original_context

    fallback_model_id = next((m for m in available_capable_models if 'flash' in m), available_capable_models[0])

    original_model_name = AVAILABLE_MODELS.get(selected_model, selected_model)
    new_model_name = AVAILABLE_MODELS.get(fallback_model_id, fallback_model_id)

    logger.warning(f"UserID: {user_id}, ChatID: {chat_id} | ({log_prefix}) Модель пользователя '{original_model_name}' не подходит для '{task_type}'. Временно используется '{new_model_name}'.")

    temp_context = ContextTypes.DEFAULT_TYPE(application=original_context.application, chat_id=chat_id, user_id=user_id)
    temp_context.user_data = original_context.user_data.copy()
    temp_context.user_data['selected_model'] = fallback_model_id

    return temp_context


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if 'selected_model' not in context.user_data:
        set_user_setting(context, 'selected_model', DEFAULT_MODEL)
    if 'search_enabled' not in context.user_data:
        set_user_setting(context, 'search_enabled', True)
    if 'temperature' not in context.user_data:
        set_user_setting(context, 'temperature', 1.0)
    if 'detailed_reasoning_enabled' not in context.user_data:
        set_user_setting(context, 'detailed_reasoning_enabled', True)
    bot_core_model_key = DEFAULT_MODEL
    raw_bot_core_model_display_name = AVAILABLE_MODELS.get(bot_core_model_key, bot_core_model_key)
    author_channel_link_raw = "https://t.me/denisobovsyom"
    date_knowledge_text_raw = "до начала 2025 года"
    
    start_message_plain_parts = [
        f"Я - Женя, работаю на Google Gemini {raw_bot_core_model_display_name}:",
        f"- обладаю огромным объемом знаний {date_knowledge_text_raw} и могу искать в Google,",
        f"- использую рассуждения и улучшенные настройки от автора бота,",
        f"- умею читать и понимать изображения, txt, pdf, ссылки на веб-страницы и содержимое Youtube-видео.",
        f"Пишите сюда и добавляйте меня в группы, я отдельно запоминаю контекст каждого чата и пользователей.",
        f"Канал автора: {author_channel_link_raw}",
        f"Пользуясь данным ботом, вы автоматически соглашаетесь на отправку ваших запросов через Google API для получения ответов моделей Google Gemini."
    ]

    start_message_plain = "\n".join(start_message_plain_parts)
    logger.debug(f"Attempting to send start_message (Plain Text):\n{start_message_plain}")
    try:
        await update.message.reply_text(start_message_plain, disable_web_page_preview=True)
        logger.info("Successfully sent start_message as plain text.")
    except Exception as e:
        logger.error(f"Failed to send start_message (Plain Text): {e}", exc_info=True)

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name
    user_mention = f"{first_name}" if first_name else f"User {user_id}"
    context.chat_data.clear()
    logger.info(f"UserID: {user_id}, ChatID: {chat_id} | История чата очищена по команде от {user_mention}.")
    await update.message.reply_text(f"🧹 Окей, {user_mention}, история этого чата очищена.")

async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name
    user_mention = f"{first_name}" if first_name else f"User {user_id}"
    try:
        current_temp = get_user_setting(context, 'temperature', 1.0)
        if not context.args:
            await update.message.reply_text(f"🌡️ {user_mention}, твоя текущая температура (креативность): {current_temp:.1f}\nЧтобы изменить, напиши `/temp <значение>` (например, `/temp 0.8`)")
            return
        temp_str = context.args[0].replace(',', '.')
        temp = float(temp_str)
        if not (0.0 <= temp <= 2.0):
            raise ValueError("Температура должна быть от 0.0 до 2.0")
        set_user_setting(context, 'temperature', temp)
        logger.info(f"UserID: {user_id}, ChatID: {chat_id} | Температура установлена на {temp:.1f} для {user_mention}.")
        await update.message.reply_text(f"🌡️ Готово, {user_mention}! Твоя температура установлена на {temp:.1f}")
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"⚠️ Ошибка, {user_mention}. {e}. Укажи число от 0.0 до 2.0. Пример: `/temp 0.8`")
    except Exception as e:
        logger.error(f"UserID: {user_id}, ChatID: {chat_id} | Ошибка в set_temperature: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ой, {user_mention}, что-то пошло не так при установке температуры.")

async def enable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    first_name = user.first_name
    user_mention = f"{first_name}" if first_name else f"User {user_id}"
    set_user_setting(context, 'search_enabled', True)
    logger.info(f"UserID: {user_id}, ChatID: {chat_id} | Поиск включен для {user_mention}.")
    await update.message.reply_text(f"🔍 Поиск Google/DDG для тебя, {user_mention}, включён.")

async def disable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    first_name = user.first_name
    user_mention = f"{first_name}" if first_name else f"User {user_id}"
    set_user_setting(context, 'search_enabled', False)
    logger.info(f"UserID: {user_id}, ChatID: {chat_id} | Поиск отключен для {user_mention}.")
    await update.message.reply_text(f"🔇 Поиск Google/DDG для тебя, {user_mention}, отключён.")

async def enable_reasoning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    first_name = user.first_name
    user_mention = f"{first_name}" if first_name else f"User {user_id}"
    set_user_setting(context, 'detailed_reasoning_enabled', True)
    logger.info(f"UserID: {user_id}, ChatID: {chat_id} | Режим углубленных рассуждений включен для {user_mention}.")
    await update.message.reply_text(f"🧠 Режим углубленных рассуждений для тебя, {user_mention}, включен. Модель будет стараться анализировать запросы более подробно (ход мыслей не отображается).")

async def disable_reasoning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    first_name = user.first_name
    user_mention = f"{first_name}" if first_name else f"User {user_id}"
    set_user_setting(context, 'detailed_reasoning_enabled', False)
    logger.info(f"UserID: {user_id}, ChatID: {chat_id} | Режим углубленных рассуждений отключен для {user_mention}.")
    await update.message.reply_text(f"💡 Режим углубленных рассуждений для тебя, {user_mention}, отключен.")

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    first_name = user.first_name
    user_mention = f"{first_name}" if first_name else f"User {user_id}"
    current_model = get_user_setting(context, 'selected_model', DEFAULT_MODEL)
    keyboard = []
    sorted_models = sorted(AVAILABLE_MODELS.items())
    for m, name in sorted_models:
         button_text = f"{'✅ ' if m == current_model else ''}{name}"
         keyboard.append([InlineKeyboardButton(button_text, callback_data=f"set_model_{m}")])
    current_model_name = AVAILABLE_MODELS.get(current_model, current_model)
    await update.message.reply_text(f"{user_mention}, выбери модель (сейчас у тебя: {current_model_name}):", reply_markup=InlineKeyboardMarkup(keyboard))

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    user_id = user.id
    chat_id = query.message.chat_id
    first_name = user.first_name
    user_mention = f"{first_name}" if first_name else f"User {user_id}"
    await query.answer()
    callback_data = query.data
    if callback_data and callback_data.startswith("set_model_"):
        selected = callback_data.replace("set_model_", "")
        if selected in AVAILABLE_MODELS:
            set_user_setting(context, 'selected_model', selected)
            model_name = AVAILABLE_MODELS[selected]
            reply_text = f"Ок, {user_mention}, твоя модель установлена: **{model_name}**"
            logger.info(f"UserID: {user_id}, ChatID: {chat_id} | Модель установлена на {model_name} для {user_mention}.")
            try:
                await query.edit_message_text(reply_text, parse_mode=ParseMode.MARKDOWN)
            except BadRequest as e_md:
                 if "Message is not modified" in str(e_md):
                     logger.info(f"UserID: {user_id}, ChatID: {chat_id} | Пользователь {user_mention} выбрал ту же модель: {model_name}")
                     await query.answer(f"Модель {model_name} уже выбрана.", show_alert=False)
                 else:
                     logger.warning(f"UserID: {user_id}, ChatID: {chat_id} | Не удалось изменить сообщение (Markdown) для {user_mention}: {e_md}. Отправляю новое.")
                     try:
                         await query.edit_message_text(reply_text.replace('**', ''))
                     except Exception as e_edit_plain:
                          logger.error(f"UserID: {user_id}, ChatID: {chat_id} | Не удалось изменить сообщение даже как простой текст для {user_mention}: {e_edit_plain}. Отправляю новое.")
                          await context.bot.send_message(chat_id=chat_id, text=reply_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.warning(f"UserID: {user_id}, ChatID: {chat_id} | Не удалось изменить сообщение (другая ошибка) для {user_mention}: {e}. Отправляю новое.", exc_info=True)
                await context.bot.send_message(chat_id=chat_id, text=reply_text, parse_mode=ParseMode.MARKDOWN)
        else:
            logger.warning(f"UserID: {user_id}, ChatID: {chat_id} | Пользователь {user_mention} выбрал неизвестную модель: {selected}")
            try:
                await query.edit_message_text("❌ Неизвестная модель выбрана.")
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text="❌ Неизвестная модель выбрана.")
    else:
        logger.warning(f"UserID: {user_id}, ChatID: {chat_id} | Получен неизвестный callback_data от {user_mention}: {callback_data}")
        try:
            await query.edit_message_text("❌ Ошибка обработки выбора.")
        except Exception:
            pass

async def perform_google_search(query: str, api_key: str, cse_id: str, num_results: int, session: aiohttp.ClientSession) -> list[str] | None:
    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': api_key, 'cx': cse_id, 'q': query, 'num': num_results, 'lr': 'lang_ru', 'gl': 'ru'}
    encoded_params = urlencode(params)
    full_url = f"{search_url}?{encoded_params}"
    query_short = query[:50] + '...' if len(query) > 50 else query
    logger.debug(f"Запрос к Google Search API для '{query_short}'...")
    try:
        async with session.get(full_url, timeout=aiohttp.ClientTimeout(total=10.0)) as response:
            response_text = await response.text()
            status = response.status
            if status == 200:
                try: data = json.loads(response_text)
                except json.JSONDecodeError as e_json:
                    logger.error(f"Google Search: Ошибка JSON для '{query_short}' ({status}) - {e_json}. Ответ: {response_text[:200]}...")
                    return None
                items = data.get('items', [])
                snippets = [item.get('snippet', item.get('title', '')) for item in items if item.get('snippet') or item.get('title')]
                if snippets:
                    logger.info(f"Google Search: Найдено {len(snippets)} результатов для '{query_short}'.")
                    return snippets
                else:
                    logger.info(f"Google Search: Нет сниппетов/заголовков для '{query_short}' ({status}).")
                    return None
            elif status == 400: logger.error(f"Google Search: Ошибка 400 (Bad Request) для '{query_short}'. Ответ: {response_text[:200]}...")
            elif status == 403: logger.error(f"Google Search: Ошибка 403 (Forbidden) для '{query_short}'. Проверьте API ключ/CSE ID. Ответ: {response_text[:200]}...")
            elif status == 429: logger.warning(f"Google Search: Ошибка 429 (Too Many Requests) для '{query_short}'. Квота? Ответ: {response_text[:200]}...")
            elif status >= 500: logger.warning(f"Google Search: Серверная ошибка {status} для '{query_short}'. Ответ: {response_text[:200]}...")
            else: logger.error(f"Google Search: Неожиданный статус {status} для '{query_short}'. Ответ: {response_text[:200]}...")
            return None
    except aiohttp.ClientConnectorError as e: logger.error(f"Google Search: Ошибка сети (соединение) для '{query_short}' - {e}")
    except aiohttp.ClientError as e: logger.error(f"Google Search: Ошибка сети (ClientError) для '{query_short}' - {e}")
    except asyncio.TimeoutError: logger.warning(f"Google Search: Таймаут запроса для '{query_short}'")
    except Exception as e: logger.error(f"Google Search: Непредвиденная ошибка для '{query_short}' - {e}", exc_info=True)
    return None

async def main():
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('google.api_core').setLevel(logging.WARNING)
    logging.getLogger('google.auth').setLevel(logging.WARNING)
    logging.getLogger('google.generativeai').setLevel(logging.INFO)
    logging.getLogger('duckduckgo_search').setLevel(logging.INFO)
    logging.getLogger('aiohttp.access').setLevel(logging.WARNING)
    logging.getLogger('telegram.ext').setLevel(logging.INFO)
    logging.getLogger('telegram.bot').setLevel(logging.INFO)
    logging.getLogger('psycopg2').setLevel(logging.WARNING)
    logging.getLogger('pdfminer').setLevel(logging.WARNING)

    logger.setLevel(log_level)
    logger.info(f"--- Установлен уровень логгирования для '{logger.name}': {log_level_str} ({log_level}) ---")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    def signal_handler():
        if not stop_event.is_set(): logger.info("Получен сигнал SIGINT/SIGTERM, инициирую остановку..."); stop_event.set()
        else: logger.warning("Повторный сигнал остановки получен, процесс уже завершается.")
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
             logger.warning(f"Не удалось установить обработчик сигнала {sig} через loop. Использую signal.signal().")
             try: signal.signal(sig, lambda s, f: signal_handler())
             except Exception as e_signal: logger.error(f"Не удалось установить обработчик сигнала {sig} через signal.signal(): {e_signal}")
    application = None; web_server_task = None; aiohttp_session_main = None
    try:
        logger.info(f"--- Запуск приложения Gemini Telegram Bot ---")
        application, web_server_coro = await setup_bot_and_server(stop_event)
        web_server_task = asyncio.create_task(web_server_coro, name="WebServerTask")
        aiohttp_session_main = application.bot_data.get('aiohttp_session')
        logger.info("Приложение настроено, веб-сервер запущен. Ожидание сигнала остановки (Ctrl+C)...")
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.info("Главная задача main() была отменена.")
    except Exception as e:
        logger.critical(f"Критическая ошибка во время запуска или ожидания: {e}", exc_info=True)
    finally:
        logger.info("--- Начало процесса штатной остановки приложения ---")
        if not stop_event.is_set(): stop_event.set()
        if web_server_task and not web_server_task.done():
             logger.info("Остановка веб-сервера (через stop_event)...")
             try:
                 await asyncio.wait_for(web_server_task, timeout=15.0)
                 logger.info("Веб-сервер успешно завершен.")
             except asyncio.TimeoutError:
                 logger.warning("Веб-сервер не завершился за 15 секунд, принудительная отмена...")
                 web_server_task.cancel()
                 try: await web_server_task
                 except asyncio.CancelledError: logger.info("Задача веб-сервера успешно отменена.")
                 except Exception as e_cancel_ws: logger.error(f"Ошибка при ожидании отмененной задачи веб-сервера: {e_cancel_ws}", exc_info=True)
             except asyncio.CancelledError: logger.info("Ожидание веб-сервера было отменено.")
             except Exception as e_wait_ws: logger.error(f"Ошибка при ожидании завершения веб-сервера: {e_wait_ws}", exc_info=True)
        if application:
            logger.info("Остановка приложения Telegram бота (application.shutdown)...")
            try:
                # === ИСПРАВЛЕНИЕ (v8): Убираем flush, чтобы избежать ошибки при выключении ===
                # if application.persistence:
                #     await application.persistence.flush()

                await application.shutdown()
                logger.info("Приложение Telegram бота успешно остановлено.")
            except Exception as e_shutdown:
                logger.error(f"Ошибка во время application.shutdown(): {e_shutdown}", exc_info=True)
        if aiohttp_session_main and not aiohttp_session_main.closed:
             logger.info("Закрытие основной сессии aiohttp..."); await aiohttp_session_main.close(); await asyncio.sleep(0.5); logger.info("Основная сессия aiohttp закрыта.")
        
        # === ИСПРАВЛЕНИЕ (v8): Закрываем пул соединений с БД в самом конце ===
        if 'persistence' in application.bot_data:
            persistence = application.bot_data.get('persistence')
            if persistence and isinstance(persistence, PostgresPersistence):
                logger.info("Закрытие соединений с базой данных...")
                persistence.close()

        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if tasks:
            logger.info(f"Отмена {len(tasks)} оставшихся фоновых задач...")
            [task.cancel() for task in tasks]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            cancelled_count, error_count = 0, 0
            for i, res in enumerate(results):
                 task_name = tasks[i].get_name()
                 if isinstance(res, asyncio.CancelledError): cancelled_count += 1
                 elif isinstance(res, Exception): error_count += 1; logger.warning(f"Ошибка в отмененной задаче '{task_name}': {res}", exc_info=False)
            logger.info(f"Фоновые задачи завершены (отменено: {cancelled_count}, ошибок: {error_count}).")
        logger.info("--- Приложение полностью остановлено ---")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение прервано пользователем (KeyboardInterrupt в main).")
    except Exception as e_top:
        logger.critical("Неперехваченная ошибка на верхнем уровне asyncio.run(main).", exc_info=True)
