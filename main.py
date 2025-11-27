# Версия 25 (Финальная: Улучшенный UX, экранирование кода, статус печати)

import logging
import os
import asyncio
import signal
import re
import pickle
from collections import defaultdict, OrderedDict
import psycopg2
from psycopg2 import pool, extensions
import io
import time
import datetime
import pytz
import html
from functools import wraps

import aiohttp
import aiohttp.web
from telegram import Update, Message, BotCommand, User, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, BasePersistence, CallbackQueryHandler
from telegram.error import BadRequest

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

# --- КОНФИГУРАЦИЯ ---
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=log_level)
logger = logging.getLogger(__name__)

logging.getLogger('aiohttp.access').setLevel(logging.WARNING)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')
ADMIN_ID = os.getenv('ADMIN_ID') # Опционально: ID админа для уведомлений

if not all([TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, WEBHOOK_HOST, GEMINI_WEBHOOK_PATH]):
    logger.critical("Критическая ошибка: не заданы все необходимые переменные окружения!")
    exit(1)

# --- КОНСТАНТЫ И НАСТРОЙКИ ---
DEFAULT_MODEL = 'gemini-flash-latest'
AVAILABLE_MODELS = {
    'flash': 'gemini-flash-latest',
    'pro': 'gemini-2.5-pro'
}

YOUTUBE_REGEX = r'(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|v\/|shorts\/)|youtu\.be\/|youtube-nocookie\.com\/embed\/)([a-zA-Z0-9_-]{11})'
URL_REGEX = r'https?:\/\/[^\s/$.?#].[^\s]*'
DATE_TIME_REGEX = r'^\s*(какой\s+)?(день|дата|число|время|который\s+час)\??\s*$'
MAX_CONTEXT_CHARS = 500000
MAX_HISTORY_RESPONSE_LEN = 4000
MAX_HISTORY_ITEMS = 50
MAX_MEDIA_CONTEXTS = 50
MEDIA_CONTEXT_TTL_SECONDS = 47 * 3600
TELEGRAM_FILE_LIMIT_MB = 20

# --- ИНСТРУМЕНТЫ И ПРОМПТЫ ---
TEXT_TOOLS = [types.Tool(google_search=types.GoogleSearch(), code_execution=types.ToolCodeExecution(), url_context=types.UrlContext())]
MEDIA_TOOLS = [types.Tool(google_search=types.GoogleSearch(), url_context=types.UrlContext())] 

SAFETY_SETTINGS = [
    types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for c in (types.HarmCategory.HARM_CATEGORY_HARASSMENT, types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
              types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT)
]

DEFAULT_SYSTEM_PROMPT = """(System Note: Today is {current_time}.)
ВАЖНОЕ ТЕХНИЧЕСКОЕ ТРЕБОВАНИЕ:
Ты работаешь через API Telegram.
1. Используй ТОЛЬКО HTML теги: <b>жирный</b>, <i>курсив</i>, <code>код</code>, <pre>блок кода</pre>.
2. СТРОГО ЗАПРЕЩЕНО использовать Markdown (**bold**, *italic*, ```code```), так как это ломает сообщение.
3. Если пишешь список, используй обычные символы (-, •) и перенос строки. Теги <ul>, <li> не поддерживаются.
"""

try:
    with open('system_prompt.md', 'r', encoding='utf-8') as f: SYSTEM_INSTRUCTION = f.read()
    logger.info("Системный промпт успешно загружен из файла.")
except FileNotFoundError:
    logger.warning("Файл system_prompt.md не найден! Использован дефолтный.")
    SYSTEM_INSTRUCTION = DEFAULT_SYSTEM_PROMPT

# --- МЕНЕДЖЕР СТАТУСА ПЕЧАТИ (НОВОЕ) ---
class TypingWorker:
    """Поддерживает статус 'печатает...', пока идет долгая обработка."""
    def __init__(self, bot, chat_id):
        self.bot = bot
        self.chat_id = chat_id
        self.running = False
        self.task = None

    async def _worker(self):
        while self.running:
            try:
                await self.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4.5) # Telegram сбрасывает статус каждые ~5 сек
            except Exception:
                break

    def start(self):
        self.running = True
        self.task = asyncio.create_task(self._worker())

    def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()

# --- КЛАСС PERSISTENCE ---
class PostgresPersistence(BasePersistence):
    def __init__(self, database_url: str):
        super().__init__()
        self.db_pool = None
        self.dsn = database_url
        self._connect_with_retry()

    def _connect_with_retry(self, retries=5, delay=5):
        for attempt in range(retries):
            try:
                self._connect()
                self._initialize_db()
                logger.info("PostgresPersistence: Успешное подключение к БД.")
                return
            except psycopg2.Error as e:
                logger.error(f"PostgresPersistence: Не удалось подключиться к БД (попытка {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(delay)
                else:
                    raise

    def _connect(self):
        if self.db_pool and not self.db_pool.closed:
            self.db_pool.closeall()
        dsn = self.dsn
        keepalive_options = "keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5"
        if "?" in dsn:
            if "keepalives" not in dsn: dsn = f"{dsn}&{keepalive_options}"
        else:
            dsn = f"{dsn}?{keepalive_options}"
        self.db_pool = psycopg2.pool.SimpleConnectionPool(1, 20, dsn=dsn)

    def _execute(self, query: str, params: tuple = None, fetch: str = None, retries=3):
        last_exception = None
        for attempt in range(retries):
            conn = None
            try:
                conn = self.db_pool.getconn()
                if conn.status == extensions.STATUS_IN_TRANSACTION:
                    conn.rollback()
                
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    if fetch == "one":
                        result = cur.fetchone()
                        conn.commit()
                        return result
                    if fetch == "all":
                        result = cur.fetchall()
                        conn.commit()
                        return result
                    conn.commit()
                return True
            except (psycopg2.OperationalError, psycopg2.InterfaceError, psycopg2.DatabaseError) as e:
                logger.warning(f"Postgres: Сбой соединения (попытка {attempt + 1}/{retries}): {e}")
                last_exception = e
                if conn:
                    try: conn.rollback()
                    except: pass
                    try: self.db_pool.putconn(conn, close=True)
                    except psycopg2.pool.PoolError: pass
                    conn = None
                if attempt < retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                continue
            except Exception as e:
                logger.error(f"Postgres: Неизвестная ошибка: {e}", exc_info=True)
                if conn:
                    try: conn.rollback()
                    except: pass
                    self.db_pool.putconn(conn, close=True)
                    conn = None
                raise e
            finally:
                if conn: self.db_pool.putconn(conn)
        logger.error(f"Postgres: Ошибка после {retries} попыток. {last_exception}")
        if last_exception: raise last_exception

    def _initialize_db(self): self._execute("CREATE TABLE IF NOT EXISTS persistence_data (key TEXT PRIMARY KEY, data BYTEA NOT NULL);")
    def _get_pickled(self, key: str) -> object | None:
        res = self._execute("SELECT data FROM persistence_data WHERE key = %s;", (key,), fetch="one")
        return pickle.loads(res[0]) if res and res[0] else None
    def _set_pickled(self, key: str, data: object) -> None: self._execute("INSERT INTO persistence_data (key, data) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET data = %s;", (key, pickle.dumps(data), pickle.dumps(data)))
    async def get_bot_data(self) -> dict: return defaultdict(dict)
    async def update_bot_data(self, data: dict) -> None: pass
    async def get_chat_data(self) -> defaultdict[int, dict]:
        all_data = await asyncio.to_thread(self._execute, "SELECT key, data FROM persistence_data WHERE key LIKE 'chat_data_%';", fetch="all")
        chat_data = defaultdict(dict)
        if all_data:
            for k, d in all_data:
                try: chat_data[int(k.split('_')[-1])] = pickle.loads(d)
                except (ValueError, IndexError, pickle.UnpicklingError): logger.warning(f"Битая запись чата в БД: '{k}'.")
        return chat_data
    async def update_chat_data(self, chat_id: int, data: dict) -> None: await asyncio.to_thread(self._set_pickled, f"chat_data_{chat_id}", data)
    async def drop_chat_data(self, chat_id: int) -> None: await asyncio.to_thread(self._execute, "DELETE FROM persistence_data WHERE key = %s;", (f"chat_data_{chat_id}",))
    async def refresh_chat_data(self, chat_id: int, chat_data: dict) -> None:
        try:
            data = await asyncio.to_thread(self._get_pickled, f"chat_data_{chat_id}") or {}
            chat_data.update(data)
        except psycopg2.Error as e:
            logger.critical(f"Ошибка БД при обновлении чата {chat_id}: {e}")
    async def get_user_data(self) -> defaultdict[int, dict]: return defaultdict(dict)
    async def update_user_data(self, user_id: int, data: dict) -> None: pass
    async def drop_user_data(self, user_id: int) -> None: pass
    async def get_callback_data(self) -> dict | None: return None
    async def update_callback_data(self, data: dict) -> None: pass
    async def get_conversations(self, name: str) -> dict: return {}
    async def update_conversation(self, name: str, key: tuple, new_state: object | None) -> None: pass
    async def refresh_bot_data(self, bot_data: dict) -> None: pass
    async def refresh_user_data(self, user_id: int, user_data: dict) -> None: pass
    async def flush(self) -> None: pass
    def close(self):
        if self.db_pool: self.db_pool.closeall()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_current_time_str(timezone: str = "Europe/Moscow") -> str:
    now = datetime.datetime.now(pytz.timezone(timezone))
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    day_of_week = days[now.weekday()]
    return f"Сегодня {day_of_week}, {now.day} {months[now.month-1]} {now.year} года, время {now.strftime('%H:%M')} (MSK)."

def convert_markdown_to_html(text: str) -> str:
    """Конвертирует Markdown в HTML и безопасно экранирует содержимое блоков кода."""
    if not text: return text
    
    # 1. Сначала извлекаем блоки кода и заменяем их на плейсхолдеры,
    # чтобы случайно не отформатировать код как жирный/курсив.
    code_blocks = {}
    
    def store_code_block(match):
        key = f"__CODE_BLOCK_{len(code_blocks)}__"
        content = match.group(2) if match.group(2) else match.group(1) # для ``` и `
        # ЭКРАНИРУЕМ содержимое кода (заменяем < на &lt;)
        escaped_content = html.escape(content)
        code_blocks[key] = f"<pre>{escaped_content}</pre>" if match.group(0).startswith("```") else f"<code>{escaped_content}</code>"
        return key

    # Ищем ```код```
    text = re.sub(r'```(\w+)?\n?(.*?)```', store_code_block, text, flags=re.DOTALL)
    # Ищем `код`
    text = re.sub(r'`([^`]+)`', store_code_block, text)

    # 2. Теперь безопасно форматируем остальной текст
    # Жирный: **text** или __text__
    text = re.sub(r'(?:\*\*|__)(.*?)(?:\*\*|__)', r'<b>\1</b>', text)
    
    # Курсив: *text* (аккуратно, чтобы не трогать списки)
    text = re.sub(r'(?<!\*)\*(?!\s)(.*?)(?<!\s)\*(?!\*)', r'<i>\1</i>', text)
    
    # Заголовки: ## Text
    text = re.sub(r'^#{1,6}\s+(.*?)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # 3. Возвращаем блоки кода на место
    for key, value in code_blocks.items():
        text = text.replace(key, value)

    return text

def html_safe_chunker(text_to_chunk: str, chunk_size: int = 4096) -> list[str]:
    chunks, tag_stack, remaining_text = [], [], text_to_chunk
    tag_regex = re.compile(r'<(/?)(b|i|code|pre|a|tg-spoiler|br)>', re.IGNORECASE)
    while len(remaining_text) > chunk_size:
        split_pos = remaining_text.rfind('\n', 0, chunk_size)
        if split_pos == -1: split_pos = chunk_size
        current_chunk = remaining_text[:split_pos]
        temp_stack = list(tag_stack)
        for match in tag_regex.finditer(current_chunk):
            tag_name, is_closing = match.group(2).lower(), bool(match.group(1))
            if tag_name == 'br': continue
            if not is_closing: temp_stack.append(tag_name)
            elif temp_stack and temp_stack[-1] == tag_name: temp_stack.pop()
        closing_tags = ''.join(f'</{tag}>' for tag in reversed(temp_stack))
        chunks.append(current_chunk + closing_tags)
        tag_stack = temp_stack
        opening_tags = ''.join(f'<{tag}>' for tag in tag_stack)
        remaining_text = opening_tags + remaining_text[split_pos:].lstrip()
    chunks.append(remaining_text)
    return chunks

def ignore_if_processing(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update or not update.effective_message:
            return

        message_id = update.effective_message.message_id
        chat_id = update.effective_chat.id
        processing_key = f"{chat_id}_{message_id}"
        
        processing_messages = context.application.bot_data.setdefault('processing_messages', set())

        if processing_key in processing_messages:
            logger.warning(f"Сообщение {processing_key} дубль/в обработке.")
            return

        processing_messages.add(processing_key)
        try:
            await func(update, context, *args, **kwargs)
        finally:
            processing_messages.discard(processing_key)
            
    return wrapper

def isolated_request(handler_func):
    @wraps(handler_func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat_id = update.effective_chat.id
        original_history = list(context.chat_data.get("history", []))
        context.chat_data["history"] = []
        try:
            await handler_func(update, context, *args, **kwargs)
        finally:
            newly_added = context.chat_data.get("history", [])
            context.chat_data["history"] = original_history + newly_added
            if len(context.chat_data["history"]) > MAX_HISTORY_ITEMS:
                context.chat_data["history"] = context.chat_data["history"][-MAX_HISTORY_ITEMS:]
    return wrapper

def part_to_dict(part: types.Part) -> dict:
    if part.text: return {'type': 'text', 'content': part.text}
    if part.file_data: return {'type': 'file', 'uri': part.file_data.file_uri, 'mime': part.file_data.mime_type, 'timestamp': time.time()}
    return {}

def dict_to_part(part_dict: dict) -> tuple[types.Part | None, bool]:
    if not isinstance(part_dict, dict): return None, False
    is_stale = False
    part = None
    if part_dict.get('type') == 'text':
        part = types.Part(text=part_dict.get('content', ''))
    if part_dict.get('type') == 'file':
        if time.time() - part_dict.get('timestamp', 0) > MEDIA_CONTEXT_TTL_SECONDS:
            is_stale = True
        else:
            part = types.Part(file_data=types.FileData(file_uri=part_dict['uri'], mime_type=part_dict['mime']))
    return part, is_stale

def build_history_for_request(chat_history: list) -> list[types.Content]:
    valid_history, current_chars = [], 0
    for entry in reversed(chat_history):
        if entry.get("role") in ("user", "model") and isinstance(entry.get("parts"), list):
            entry_api_parts = []
            entry_text_len = 0
            if entry.get("role") == "user":
                user_id = entry.get('user_id', 'Unknown')
                user_name = entry.get('user_name', 'User')
                user_prefix = f"[{user_id}; Name: {user_name}]: "
                for part_dict in entry["parts"]:
                    if part_dict.get('type') == 'text':
                        prefixed_text = f"{user_prefix}{part_dict.get('content', '')}"
                        entry_api_parts.append(types.Part(text=prefixed_text))
                        entry_text_len += len(prefixed_text)
            else:
                for part_dict in entry["parts"]:
                    if part_dict.get('type') == 'text':
                        text = part_dict.get('content', '')
                        entry_api_parts.append(types.Part(text=text))
                        entry_text_len += len(text)

            if not entry_api_parts: continue
            if current_chars + entry_text_len > MAX_CONTEXT_CHARS: break

            clean_content = types.Content(role=entry["role"], parts=entry_api_parts)
            valid_history.append(clean_content)
            current_chars += entry_text_len
    valid_history.reverse()
    return valid_history

async def upload_and_wait_for_file(client: genai.Client, file_bytes: bytes, mime_type: str, file_name: str) -> types.Part:
    logger.info(f"Загрузка файла '{file_name}'...")
    try:
        upload_config = types.UploadFileConfig(mime_type=mime_type, display_name=file_name)
        upload_response = await client.aio.files.upload(
            file=io.BytesIO(file_bytes),
            config=upload_config
        )
        file_response = await client.aio.files.get(name=upload_response.name)
        for _ in range(15):
            if file_response.state.name == 'ACTIVE':
                return types.Part(file_data=types.FileData(file_uri=file_response.uri, mime_type=mime_type))
            if file_response.state.name == 'FAILED':
                raise IOError("Ошибка обработки файла на стороне Google.")
            await asyncio.sleep(2)
            file_response = await client.aio.files.get(name=upload_response.name)
        raise asyncio.TimeoutError("Таймаут обработки файла.")
    except Exception as e:
        logger.error(f"Ошибка upload: {e}")
        raise IOError(f"Не удалось загрузить файл '{file_name}'.")

async def generate_response(client: genai.Client, request_contents: list, context: ContextTypes.DEFAULT_TYPE, tools: list, system_instruction_override: str | None = None) -> types.GenerateContentResponse | str:
    chat_id = context.chat_data.get('id', 'Unknown')
    
    if system_instruction_override:
        final_system_instruction = system_instruction_override
    else:
        try:
            final_system_instruction = SYSTEM_INSTRUCTION.format(current_time=get_current_time_str())
        except KeyError:
            final_system_instruction = SYSTEM_INSTRUCTION

    config = types.GenerateContentConfig(
        safety_settings=SAFETY_SETTINGS, 
        tools=tools,
        system_instruction=types.Content(parts=[types.Part(text=final_system_instruction)]),
        temperature=0.7, # Чуть меньше креативности для стабильности форматирования
        thinking_config=types.ThinkingConfig(thinking_budget=16384) # Оптимизированный бюджет
    )
    
    model_to_use = context.chat_data.get('model', DEFAULT_MODEL)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await client.aio.models.generate_content(
                model=model_to_use,
                contents=request_contents,
                config=config
            )
            if response and response.candidates and response.candidates[0].content:
                return response
            if attempt < max_retries - 1: await asyncio.sleep(2)

        except genai_errors.APIError as e:
            logger.error(f"API Error (try {attempt}): {e}")
            if "resource_exhausted" in str(e).lower(): return "⏳ Я перегружена, подождите минуту."
            if attempt < max_retries - 1: await asyncio.sleep(5)
            else: return f"❌ Ошибка API: {html.escape(str(e))}"
        except Exception as e:
            logger.error(f"Gen Error: {e}", exc_info=True)
            return f"❌ Внутренняя ошибка: {html.escape(str(e))}"
    return "Не удалось получить ответ от нейросети."

def format_gemini_response(response: types.GenerateContentResponse) -> str:
    try:
        candidate = response.candidates[0]
        if candidate.finish_reason.name == "SAFETY": return "Ответ скрыт фильтром безопасности."
        
        text_parts = [part.text for part in candidate.content.parts if part.text]
        if not text_parts: return "Получен пустой ответ."

        full_text = "".join(text_parts)
        # Убираем лишние "мысли" модели (chain of thought), если они протекли
        full_text = re.sub(r'tool_code\n.*?thought\n', '', full_text, flags=re.DOTALL)
        full_text = re.sub(r'\[\d+;\s*Name:\s*.*?\]:\s*', '', full_text)
        
        # Конвертация Markdown -> HTML (с защитой кода)
        return convert_markdown_to_html(full_text.strip())
        
    except Exception as e:
        logger.error(f"Ошибка парсинга: {e}")
        return "Ошибка обработки ответа."

async def send_reply(target_message: Message, response_text: str, add_context_hint: bool = False) -> Message | None:
    sanitized_text = re.sub(r'<br\s*/?>', '\n', response_text)
    chunks = html_safe_chunker(sanitized_text)
    
    if add_context_hint:
        hint = "\n\n<i>💡 Ответьте на это сообщение, чтобы задать вопрос по файлу.</i>"
        if len(chunks[-1]) + len(hint) <= 4096: chunks[-1] += hint
        else: chunks.append(hint)
            
    sent_message = None
    try:
        for i, chunk in enumerate(chunks):
            if i == 0: sent_message = await target_message.reply_html(chunk)
            else: sent_message = await target_message.get_bot().send_message(chat_id=target_message.chat_id, text=chunk, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.1)
        return sent_message
    except BadRequest as e:
        # Fallback: если HTML все равно кривой, шлем как текст
        logger.warning(f"HTML Error: {e}. Sending plain text.")
        plain_text = re.sub(r'<[^>]*>', '', sanitized_text)
        chunks = [plain_text[i:i+4096] for i in range(0, len(plain_text), 4096)]
        for chunk in chunks:
            sent_message = await target_message.reply_text(chunk)
        return sent_message
    except Exception as e:
        logger.error(f"Send Error: {e}")
        return None

async def add_to_history(context: ContextTypes.DEFAULT_TYPE, role: str, parts: list[types.Part], user_id: int | str = None, user_name: str = None, **kwargs):
    chat_history = context.chat_data.setdefault("history", [])
    entry_parts = []
    for part in parts:
        if part.text: entry_parts.append(part_to_dict(part))
    
    if not entry_parts and role == 'user':
         if not any(p.file_data for p in parts): return
         entry_parts.append({'type': 'text', 'content': ''})

    entry = {"role": role, "parts": entry_parts, **kwargs}
    if role == 'user' and user_id:
        entry['user_id'] = user_id
        entry['user_name'] = user_name or 'User'
    
    chat_history.append(entry)
    if len(chat_history) > MAX_HISTORY_ITEMS:
        context.chat_data["history"] = chat_history[-MAX_HISTORY_ITEMS:]

async def process_request(update: Update, context: ContextTypes.DEFAULT_TYPE, content_parts: list):
    message = update.message
    client = context.bot_data['gemini_client']
    chat_id = message.chat_id
    
    # Запуск фонового "печатает..."
    typer = TypingWorker(context.bot, chat_id)
    typer.start()

    try:
        effective_user_id = message.from_user.id
        effective_user_name = message.from_user.first_name
        
        # Логика обработки forward
        if message.forward_origin:
            if message.forward_origin.type == 'channel' and message.forward_origin.chat:
                effective_user_name = message.forward_origin.chat.title or "Channel"
            elif hasattr(message.forward_origin, 'sender_user') and message.forward_origin.sender_user:
                effective_user_name = message.forward_origin.sender_user.first_name

        # Проверка на вопрос о времени
        text_content = next((p.text for p in content_parts if p.text), None)
        if text_content and re.search(DATE_TIME_REGEX, text_content, re.IGNORECASE):
            await send_reply(message, get_current_time_str())
            return

        is_media_request = any(p.file_data for p in content_parts)
        history_for_api = build_history_for_request(context.chat_data.get("history", []))
        
        user_prefix = f"[{effective_user_id}; Name: {effective_user_name}]: "
        prompt_text = next((p.text for p in content_parts if p.text), "")
        has_url = bool(re.search(URL_REGEX, prompt_text))

        # Собираем промпт
        final_prompt = f"{user_prefix}{prompt_text}"
        if not is_media_request and not has_url:
            grounding = f"\nИспользуй Grounding with Google Search. Актуальная дата: {get_current_time_str()}.\n"
            final_prompt = grounding + final_prompt

        # Обновляем текстовую часть запроса
        req_parts = [p for p in content_parts if p.file_data]
        req_parts.append(types.Part(text=final_prompt))

        full_contents = history_for_api + [types.Content(parts=req_parts, role="user")]
        tools = MEDIA_TOOLS if is_media_request else TEXT_TOOLS
        
        # Генерация
        response_obj = await generate_response(client, full_contents, context, tools)
        reply_text = format_gemini_response(response_obj) if not isinstance(response_obj, str) else response_obj
        
        # Сохранение (обрезаем длинные ответы для истории)
        hist_text = reply_text[:MAX_HISTORY_RESPONSE_LEN] + ("..." if len(reply_text) > MAX_HISTORY_RESPONSE_LEN else "")
        
        sent_msg = await send_reply(message, reply_text, add_context_hint=is_media_request)
        
        if sent_msg:
            await add_to_history(context, "user", content_parts, effective_user_id, effective_user_name, original_message_id=message.message_id)
            await add_to_history(context, "model", [types.Part(text=hist_text)], original_message_id=message.message_id, bot_message_id=sent_msg.message_id)
            
            # Обновление reply_map
            reply_map = context.chat_data.setdefault('reply_map', {})
            reply_map[sent_msg.message_id] = message.message_id
            if len(reply_map) > MAX_HISTORY_ITEMS * 2:
                # Удаляем старые записи
                keys = list(reply_map.keys())
                for k in keys[:-MAX_HISTORY_ITEMS]: del reply_map[k]

            # Сохранение контекста файла
            if is_media_request:
                media_part = next((p for p in content_parts if p.file_data), None)
                if media_part:
                    media_store = context.application.bot_data.setdefault('media_contexts', {}).setdefault(chat_id, OrderedDict())
                    media_store[message.message_id] = part_to_dict(media_part)
                    if len(media_store) > MAX_MEDIA_CONTEXTS: media_store.popitem(last=False)
            
            await context.application.persistence.update_chat_data(chat_id, context.chat_data)

    except Exception as e:
        logger.error(f"Process Error: {e}", exc_info=True)
        await message.reply_text(f"❌ Критическая ошибка: {html.escape(str(e))}")
    finally:
        typer.stop()

# --- ОБРАБОТЧИКИ КОМАНД ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "👋 <b>Привет! Я Женя, ИИ-ассистент на базе Gemini.</b>\n\n"
        "Я умею:\n"
        "🔍 Искать актуальную информацию в Google\n"
        "👁️ Видеть фото, видео и файлы\n"
        "🎧 Слушать голосовые и аудио\n"
        "📺 Смотреть YouTube по ссылке\n\n"
        "Просто отправь мне сообщение или файл!"
    )

@ignore_if_processing
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.chat_data.clear()
    context.application.bot_data.get('media_contexts', {}).pop(chat_id, None)
    await context.application.persistence.update_chat_data(chat_id, context.chat_data)
    await update.message.reply_text("🧹 История очищена. Я забыла всё, что было до этого.")

@ignore_if_processing
async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    curr = context.chat_data.get('model', DEFAULT_MODEL)
    curr_name = 'Flash' if 'flash' in curr else 'Pro'
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 Flash", callback_data="model_switch_flash"),
        InlineKeyboardButton("🧠 Pro", callback_data="model_switch_pro")
    ]])
    await update.message.reply_html(f"Текущая модель: <b>{curr_name}</b>. Выбери:", reply_markup=kb)

async def model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    new_model = AVAILABLE_MODELS.get(query.data.split('_')[-1])
    if new_model:
        context.chat_data['model'] = new_model
        await context.application.persistence.update_chat_data(query.effective_chat.id, context.chat_data)
        await query.edit_message_text(f"✅ Установлена модель: <b>{query.data.split('_')[-1].capitalize()}</b>", parse_mode=ParseMode.HTML)

# Утилиты для команд анализа
async def _get_media_for_utility(update: Update, context: ContextTypes.DEFAULT_TYPE) -> types.Part | None:
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("⚠️ Ответьте этой командой на сообщение с файлом.")
        return None
    
    reply = msg.reply_to_message
    client = context.bot_data['gemini_client']
    
    # 1. Проверяем сохраненный контекст
    if reply.from_user.id == context.bot.id:
        orig_id = context.chat_data.get('reply_map', {}).get(reply.message_id)
        if orig_id:
            data = context.application.bot_data.get('media_contexts', {}).get(msg.chat_id, {}).get(orig_id)
            if data:
                part, stale = dict_to_part(data)
                if not stale: return part
                await msg.reply_text("⏳ Срок действия файла истек.")
                return None

    # 2. Проверяем файл в самом сообщении
    media = reply.audio or reply.voice or reply.video or reply.video_note or reply.photo or reply.document
    if media:
        if isinstance(media, list): media = media[-1] # Photo
        if media.file_size > TELEGRAM_FILE_LIMIT_MB * 1024 * 1024:
            await msg.reply_text("❌ Файл слишком большой.")
            return None
        
        f = await media.get_file()
        b = await f.download_as_bytearray()
        return await upload_and_wait_for_file(client, b, getattr(media, 'mime_type', 'application/octet-stream'), 'temp_file')

    # 3. YouTube
    if reply.text:
        match = re.search(YOUTUBE_REGEX, reply.text)
        if match:
             return types.Part(file_data=types.FileData(mime_type="video/youtube", file_uri=f"https://www.youtube.com/watch?v={match.group(1)}"))
    
    await msg.reply_text("❌ Медиа не найдено.")
    return None

async def utility_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    part = await _get_media_for_utility(update, context)
    if part:
        await process_request(update, context, [part, types.Part(text=prompt)])

@ignore_if_processing
async def summarize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await utility_command_handler(update, context, "Сделай краткий конспект (summary) этого материала.")

@ignore_if_processing
async def transcript_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await utility_command_handler(update, context, "Сделай полную транскрипцию текста.")

# --- ОБРАБОТЧИКИ СООБЩЕНИЙ (COMMON) ---
async def universal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    context.chat_data['id'] = msg.chat_id
    
    # Определяем тип контента
    content_parts = []
    client = context.bot_data['gemini_client']
    text = msg.caption or msg.text or ""
    
    # 1. Медиа
    media_obj = msg.audio or msg.voice or msg.video or msg.video_note or (msg.photo[-1] if msg.photo else None) or msg.document
    
    if media_obj:
        if media_obj.file_size > TELEGRAM_FILE_LIMIT_MB * 1024 * 1024:
            await msg.reply_text("📂 Файл слишком большой для анализа (>20MB). Отвечу только на текст.")
        else:
            try:
                msg_status = await msg.reply_text("📥 Загружаю файл...")
                f = await media_obj.get_file()
                b = await f.download_as_bytearray()
                mime = getattr(media_obj, 'mime_type', 'application/octet-stream')
                if msg.photo: mime = 'image/jpeg'
                if msg.voice: mime = 'audio/ogg'
                if msg.video_note: mime = 'video/mp4'
                
                part = await upload_and_wait_for_file(client, b, mime, getattr(media_obj, 'file_name', 'file'))
                content_parts.append(part)
                await msg_status.delete()
            except Exception as e:
                await msg.reply_text(f"❌ Ошибка загрузки: {e}")
                return

    # 2. YouTube
    if not media_obj and re.search(YOUTUBE_REGEX, text):
        url = re.search(YOUTUBE_REGEX, text).group(0)
        content_parts.append(types.Part(file_data=types.FileData(mime_type="video/youtube", file_uri=url)))
        text = text.replace(url, '').strip()

    # 3. Reply Context (если ответили на сообщение бота с файлом)
    if not media_obj and msg.reply_to_message:
         orig_id = context.chat_data.get('reply_map', {}).get(msg.reply_to_message.message_id)
         if orig_id:
             ctx = context.application.bot_data.get('media_contexts', {}).get(msg.chat_id, {}).get(orig_id)
             if ctx:
                 p, _ = dict_to_part(ctx)
                 if p: content_parts.append(p)

    # 4. Текст
    if text:
        content_parts.append(types.Part(text=text))
    elif not content_parts:
        return # Пустое сообщение

    # Обработка
    await process_request(update, context, content_parts)


# --- ЗАПУСК ---
async def web_server(app_bot, stop_event):
    app = aiohttp.web.Application()
    app['bot'] = app_bot
    
    async def webhook(request):
        try:
            await app_bot.process_update(Update.de_json(await request.json(), app_bot.bot))
            return aiohttp.web.Response(text='OK')
        except: return aiohttp.web.Response(status=500)

    app.router.add_post(f"/{GEMINI_WEBHOOK_PATH.strip('/')}", webhook)
    app.router.add_get('/', lambda r: aiohttp.web.Response(text="Bot Running"))
    
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 10000)))
    await site.start()
    logger.info("Web server started.")
    await stop_event.wait()
    await runner.cleanup()

async def main():
    persistence = PostgresPersistence(DATABASE_URL)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    app.bot_data['gemini_client'] = genai.Client(api_key=GOOGLE_API_KEY)

    # Команды
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(CommandHandler("summarize", summarize_command))
    app.add_handler(CommandHandler("transcript", transcript_command))
    app.add_handler(CallbackQueryHandler(model_callback, pattern='^model_switch_'))

    # Единый обработчик всего
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, universal_handler))

    await app.initialize()
    if ADMIN_ID:
        try: await app.bot.send_message(ADMIN_ID, "🚀 Бот успешно перезапущен и готов к работе!")
        except: pass

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(s, stop_event.set)
    
    webhook_url = f"{WEBHOOK_HOST.rstrip('/')}/{GEMINI_WEBHOOK_PATH.strip('/')}"
    await app.bot.set_webhook(webhook_url)
    
    try: await web_server(app, stop_event)
    finally:
        persistence.close()
        logger.info("Bot stopped.")

if __name__ == '__main__':
    asyncio.run(main())
