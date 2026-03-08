# Версия 74 (Final Review - All Logic Verified)

import logging
import os
import asyncio
import signal
import re
import pickle
import io
import time
import datetime
from collections import defaultdict, OrderedDict

import psycopg2
from psycopg2 import pool, extensions
import pytz
import html
from functools import wraps

import aiohttp.web
from telegram import Update, BotCommand
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters, BasePersistence, PersistenceInput
)
from telegram.error import BadRequest

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

# --- КОНФИГУРАЦИЯ ЛОГОВ ---
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=log_level)
logger = logging.getLogger(__name__)

SILENCED_MODULES = [
    'aiohttp.access', 'httpx', 'telegram',
    'grpc', 'google', 'google.auth', 'google.api_core',
    'urllib3', 'httpcore', 'google.genai'
]
for mod in SILENCED_MODULES:
    logging.getLogger(mod).setLevel(logging.WARNING)

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_SECRET_TOKEN = os.getenv('TELEGRAM_SECRET_TOKEN', 'my-secret-token-change-me')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')

admin_id_raw = os.getenv('ADMIN_ID')
try:
    ADMIN_ID = int(admin_id_raw) if admin_id_raw else None
except ValueError:
    ADMIN_ID = None

required_env = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "GOOGLE_API_KEY": GOOGLE_API_KEY,
    "WEBHOOK_HOST": WEBHOOK_HOST,
    "GEMINI_WEBHOOK_PATH": GEMINI_WEBHOOK_PATH,
    "DATABASE_URL": DATABASE_URL,
}

missing = [k for k, v in required_env.items() if not v]
if missing:
    logger.critical(f"Не заданы обязательные переменные окружения: {', '.join(missing)}")
    exit(1)

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
MODEL_CASCADE = [
    {
        "id": "gemini-2.5-flash",
        "display": "2.5 Flash",
    },
    {
        "id": "gemini-2.5-flash-lite",
        "display": "2.5 Flash Lite",
    }
]

# Глобальные переменные
DAILY_REQUEST_COUNTS = defaultdict(int)
DAILY_REQUEST_DATE = None
GLOBAL_LOCK = asyncio.Lock()
LAST_REQUEST_TIME = 0
REQUEST_DELAY = 10
CHAT_LOCKS = {}
GEMINI_CLIENT = None
PROCESSING_MESSAGES = set()

# --- REGEX ---
YOUTUBE_REGEX = re.compile(
    r'(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/)|youtu\.be/|youtube-nocookie\.com/embed/)([a-zA-Z0-9_-]{11})'
)
URL_REGEX = re.compile(r'https?://[^\s/$.?#].[^\s]*')
DATE_TIME_REGEX = re.compile(
    r'^\s*(какой\s+)?(день|дата|число|время|который\s+час)\??\s*$', re.IGNORECASE
)
HTML_TAG_REGEX = re.compile(
    r'<(/?)(b|i|u|s|code|pre|a|tg-spoiler|blockquote)>', re.IGNORECASE
)
RE_CLEAN_NAMES = re.compile(r'\[\d+;\s*Name:\s*.*?\]:\s*')

MAX_CONTEXT_CHARS = 50000
MAX_HISTORY_RESPONSE_LEN = 4000
MAX_HISTORY_ITEMS = 100
MAX_MEDIA_CONTEXTS = 100
FILE_TTL_HOURS = 47
MEDIA_CONTEXT_TTL_SECONDS = FILE_TTL_HOURS * 3600
TELEGRAM_FILE_LIMIT_MB = 20

# --- ИНСТРУМЕНТЫ ---
TEXT_TOOLS = [types.Tool(
    google_search=types.GoogleSearch(),
    code_execution=types.ToolCodeExecution(),
    url_context=types.UrlContext()
)]

MEDIA_TOOLS = [types.Tool(
    google_search=types.GoogleSearch(),
    url_context=types.UrlContext()
)]

SAFETY_SETTINGS = [
    types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for c in (
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )
]

DEFAULT_SYSTEM_PROMPT = """(System Note: Today is {current_time}.)
ВАЖНОЕ ТЕХНИЧЕСКОЕ ТРЕБОВАНИЕ:
Форматируй текст ТОЛЬКО с использованием стандартного Markdown (звездочки для жирного/курсива).
СТРОГО ЗАПРЕЩЕНО использовать HTML теги вроде <br> или <b> напрямую.
Если пользователь задаёт вопрос, требующий актуальных данных — используй Google Search.
"""

try:
    with open('system_prompt.md', 'r', encoding='utf-8') as f:
        SYSTEM_INSTRUCTION = f.read()
except FileNotFoundError:
    SYSTEM_INSTRUCTION = DEFAULT_SYSTEM_PROMPT

# --- TYPING WORKER ---
class TypingWorker:
    def __init__(self, bot, chat_id, action=ChatAction.TYPING):
        self.bot = bot
        self.chat_id = chat_id
        self.action = action
        self.running = False
        self.task = None

    async def _worker(self):
        while self.running:
            try:
                await self.bot.send_chat_action(chat_id=self.chat_id, action=self.action)
                await asyncio.sleep(4.5)
            except Exception:
                break

    def start(self):
        self.running = True
        self.task = asyncio.create_task(self._worker())

    def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()


# --- PERSISTENCE ---
class PostgresPersistence(BasePersistence):
    def __init__(self, database_url: str):
        super().__init__(
            store_data=PersistenceInput(bot_data=False, callback_data=False)
        )
        self.db_pool = None
        self.dsn = database_url
        self._connect_with_retry()

    def _connect_with_retry(self, retries=5, delay=5):
        for attempt in range(retries):
            try:
                self._connect()
                self._initialize_db()
                logger.info("DB Connected.")
                return
            except psycopg2.Error as e:
                logger.error(f"DB Connect Error ({attempt + 1}): {e}")
                if attempt < retries - 1:
                    time.sleep(delay)
                else:
                    raise

    def _connect(self):
        if self.db_pool and not self.db_pool.closed:
            self.db_pool.closeall()
        dsn = self.dsn
        opts = "keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5"
        dsn = f"{dsn}&{opts}" if "?" in dsn else f"{dsn}?{opts}"
        self.db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, dsn=dsn)

    def _execute(self, query: str, params: tuple = None, fetch: str = None, retries=3):
        last_ex = None
        for attempt in range(retries):
            conn = None
            try:
                conn = self.db_pool.getconn()
                if conn.status == extensions.STATUS_IN_TRANSACTION:
                    conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    if fetch == "one":
                        res = cur.fetchone()
                    elif fetch == "all":
                        res = cur.fetchall()
                    else:
                        res = True
                    conn.commit()
                    return res
            except (psycopg2.OperationalError, psycopg2.InterfaceError, psycopg2.DatabaseError) as e:
                if "SSL connection has been closed" not in str(e):
                    logger.warning(f"DB Error ({attempt + 1}): {e}")
                last_ex = e
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    try:
                        self.db_pool.putconn(conn, close=True)
                    except Exception:
                        pass
                    conn = None
                if attempt == retries - 2:
                    try:
                        self._connect()
                    except Exception as reconnect_err:
                        logger.error(f"Reconnect failed: {reconnect_err}")
                if attempt < retries - 1:
                    time.sleep(0.5 * (attempt + 1))
            except Exception as e:
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    try:
                        self.db_pool.putconn(conn, close=True)
                    except Exception:
                        pass
                    conn = None
                raise e
            finally:
                if conn:
                    self.db_pool.putconn(conn)
        raise last_ex

    def _initialize_db(self):
        self._execute(
            "CREATE TABLE IF NOT EXISTS persistence_data (key TEXT PRIMARY KEY, data BYTEA NOT NULL);"
        )

    def _get_pickled(self, key: str):
        res = self._execute("SELECT data FROM persistence_data WHERE key = %s;", (key,), fetch="one")
        return pickle.loads(res[0]) if res and res[0] else None

    def _set_pickled(self, key: str, data: object):
        pickled = pickle.dumps(data)
        self._execute(
            "INSERT INTO persistence_data (key, data) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET data = %s;",
            (key, pickled, pickled),
        )

    async def get_bot_data(self):
        return {}

    async def update_bot_data(self, data):
        pass

    async def refresh_bot_data(self, bot_data):
        pass

    async def load_media_contexts(self):
        data = await asyncio.to_thread(self._get_pickled, "media_contexts")
        return data if isinstance(data, dict) else {}

    async def save_media_contexts(self, data):
        await asyncio.to_thread(self._set_pickled, "media_contexts", data)

    async def get_chat_data(self):
        all_data = await asyncio.to_thread(
            self._execute,
            "SELECT key, data FROM persistence_data WHERE key LIKE 'chat_data_%';",
            fetch="all",
        )
        chat_data = defaultdict(dict)
        if all_data:
            prefix = "chat_data_"
            for k, d in all_data:
                try:
                    chat_id = int(k.removeprefix(prefix))
                    chat_data[chat_id] = pickle.loads(d)
                except Exception:
                    pass
        return chat_data

    async def update_chat_data(self, chat_id, data):
        await asyncio.to_thread(self._set_pickled, f"chat_data_{chat_id}", data)

    async def drop_chat_data(self, chat_id):
        await asyncio.to_thread(
            self._execute,
            "DELETE FROM persistence_data WHERE key = %s;",
            (f"chat_data_{chat_id}",),
        )

    async def refresh_chat_data(self, chat_id, chat_data):
        data = await asyncio.to_thread(self._get_pickled, f"chat_data_{chat_id}") or {}
        chat_data.update(data)

    async def get_user_data(self):
        return defaultdict(dict)

    async def update_user_data(self, user_id, data):
        pass

    async def drop_user_data(self, user_id):
        pass

    async def get_callback_data(self):
        return None

    async def update_callback_data(self, data):
        pass

    async def get_conversations(self, name):
        return {}

    async def update_conversation(self, name, key, new_state):
        pass

    async def refresh_user_data(self, user_id, user_data):
        pass

    async def flush(self):
        pass

    def close(self):
        if self.db_pool:
            self.db_pool.closeall()


# --- UTILS ---
def get_current_time_str(timezone="Europe/Moscow"):
    now = datetime.datetime.now(pytz.timezone(timezone))
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    return (
        f"Сегодня {days[now.weekday()]}, {now.day} {months[now.month - 1]} "
        f"{now.year} года, время {now.strftime('%H:%M')} (MSK)."
    )


def convert_markdown_to_html(text: str) -> str:
    if not text:
        return text

    code_blocks = {}

    def store_code(match):
        key = f"\x00CODE{len(code_blocks)}\x00"
        content = html.escape(match.group(1))
        tag = "pre" if match.group(0).startswith("``" + "`") else "code"
        code_blocks[key] = f"<{tag}>{content}</{tag}>"
        return key

    text = re.sub(r'```[ \w-]*\n?(.*?)```', store_code, text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', store_code, text)

    links = {}

    def store_link(match):
        key = f"\x00LINK{len(links)}\x00"
        url = html.escape(match.group(2), quote=True)
        link_text = html.escape(match.group(1), quote=False)
        links[key] = f'<a href="{url}">{link_text}</a>'
        return key

    text = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', store_link, text)

    text = html.escape(text, quote=False)

    # Заголовки
    text = re.sub(r'^(#{1,6})\s+(.+)$', r'<b>\2</b>', text, flags=re.MULTILINE)

    # Блок-цитаты (многострочные)
    def convert_blockquote(match):
        lines = match.group(0).strip().split('\n')
        content = '\n'.join(re.sub(r'^>\s?', '', line) for line in lines)
        return f'<blockquote>{content}</blockquote>'

    text = re.sub(r'(?:^>.*$\n?)+', convert_blockquote, text, flags=re.MULTILINE)

    # Bold-italic (ДО bold и italic)
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', text, flags=re.DOTALL)
    text = re.sub(r'___(.+?)___', r'<b><i>\1</i></b>', text, flags=re.DOTALL)

    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text, flags=re.DOTALL)

    # Italic — *text* (не ловит * item в списках за счёт (?!\s))
    text = re.sub(r'(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)', r'<i>\1</i>', text, flags=re.DOTALL)
    # Italic — _text_ (не ловит snake_case за счёт границ)
    text = re.sub(
        r'(?:^|(?<=\s))_(?!\s)(.+?)(?<!\s)_(?=\s|[.,;:!?\)\]"]|$)',
        r'<i>\1</i>',
        text,
        flags=re.DOTALL | re.MULTILINE,
    )

    # Strikethrough и spoiler
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text, flags=re.DOTALL)
    text = re.sub(r'\|\|(.+?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', text, flags=re.DOTALL)

    # Восстановление плейсхолдеров
    for key, val in code_blocks.items():
        text = text.replace(key, val)
    for key, val in links.items():
        text = text.replace(key, val)

    return text


def html_safe_chunker(text: str, size=4096):
    """Разбивает HTML-текст на чанки <=size, корректно закрывая/открывая теги."""
    chunks = []
    open_tags = []  # Стек открытых тегов, наследуемый между чанками

    while len(text) > size:
        split = text.rfind('\n', 0, size)
        if split == -1:
            split = size

        chunk = text[:split]

        # Парсим теги в текущем чанке, обновляя стек
        current_stack = list(open_tags)
        for m in HTML_TAG_REGEX.finditer(chunk):
            tag = m.group(2).lower()
            is_closing = bool(m.group(1))
            if is_closing:
                if current_stack and current_stack[-1] == tag:
                    current_stack.pop()
            else:
                current_stack.append(tag)

        # Закрываем все теги, которые остались открытыми в этом чанке
        if current_stack:
            chunk += ''.join(f'</{t}>' for t in reversed(current_stack))

        chunks.append(chunk)

        # Следующий чанк начинается с переоткрытия этих тегов
        open_tags = current_stack
        text = ''.join(f'<{t}>' for t in open_tags) + text[split:].lstrip()

    chunks.append(text)
    return chunks


def ignore_if_processing(func):
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        if not update.effective_message:
            return
        key = f"{update.effective_chat.id}_{update.effective_message.message_id}"
        if key in PROCESSING_MESSAGES:
            return
        PROCESSING_MESSAGES.add(key)
        try:
            await func(update, context, *args, **kwargs)
        finally:
            PROCESSING_MESSAGES.discard(key)
    return wrapper


def part_to_dict(part):
    if part.text:
        return {'type': 'text', 'content': part.text}
    if part.file_data:
        return {
            'type': 'file',
            'uri': part.file_data.file_uri,
            'mime': part.file_data.mime_type,
            'timestamp': time.time(),
        }
    return {}


def dict_to_part(d):
    if not isinstance(d, dict):
        return None, False
    if d.get('type') == 'text':
        return types.Part(text=d.get('content', '')), False
    if d.get('type') == 'file':
        if time.time() - d.get('timestamp', 0) > MEDIA_CONTEXT_TTL_SECONDS:
            return None, True
        return types.Part(file_data=types.FileData(file_uri=d['uri'], mime_type=d['mime'])), False
    return None, False


def build_history(history):
    valid = []
    chars = 0
    for entry in reversed(history):
        if not entry.get("parts"):
            continue
        api_parts = []
        text_len = 0
        prefix = (
            f"[{entry.get('user_id', 'Unknown')}; Name: {entry.get('user_name', 'User')}]: "
            if entry['role'] == 'user' else ""
        )
        has_text = False
        prefix_added = False

        for p in entry["parts"]:
            if p.get('type') == 'text':
                if entry['role'] == 'user' and not prefix_added:
                    t = f"{prefix}{p.get('content', '')}"
                    prefix_added = True
                else:
                    t = p.get('content', '')
                api_parts.append(types.Part(text=t))
                text_len += len(t)
                has_text = True
            elif p.get('type') == 'file':
                part, is_stale = dict_to_part(p)
                if part and not is_stale:
                    api_parts.append(part)
                    text_len += 1000

        if api_parts and not has_text and entry['role'] == 'user':
            api_parts.append(types.Part(text=f"{prefix.strip()} (sent a file)"))

        if not api_parts:
            continue
        if chars + text_len > MAX_CONTEXT_CHARS:
            break

        valid.append(types.Content(role=entry["role"], parts=api_parts))
        chars += text_len

    return valid[::-1]


async def upload_file(client, b, mime, name):
    logger.info(f"Uploading: {name}")
    try:
        up = await client.aio.files.upload(
            file=io.BytesIO(b),
            config=types.UploadFileConfig(mime_type=mime, display_name=name),
        )
        for _ in range(15):
            f = await client.aio.files.get(name=up.name)
            if f.state.name == 'ACTIVE':
                return types.Part(file_data=types.FileData(file_uri=f.uri, mime_type=mime))
            if f.state.name == 'FAILED':
                raise IOError("Google File Error")
            await asyncio.sleep(2)
        raise asyncio.TimeoutError("Upload Timeout")
    except Exception as e:
        logger.error(f"Upload Fail: {e}")
        raise IOError(f"Ошибка загрузки файла {name}: {e}")


# --- ЯДРО ГЕНЕРАЦИИ ---
async def generate(client, contents, current_tools):
    global LAST_REQUEST_TIME, DAILY_REQUEST_DATE

    today = datetime.date.today()
    if DAILY_REQUEST_DATE != today:
        DAILY_REQUEST_COUNTS.clear()
        DAILY_REQUEST_DATE = today

    sys_prompt = SYSTEM_INSTRUCTION
    if "{current_time}" in sys_prompt:
        sys_prompt = sys_prompt.format(current_time=get_current_time_str())

    async with GLOBAL_LOCK:
        now = time.time()
        elapsed = now - LAST_REQUEST_TIME
        if elapsed < REQUEST_DELAY:
            wait_time = REQUEST_DELAY - elapsed
            LAST_REQUEST_TIME = now + wait_time
        else:
            wait_time = 0
            LAST_REQUEST_TIME = now

    if wait_time > 0:
        logger.info(f"Queue: Waiting {wait_time:.1f}s...")
        await asyncio.sleep(wait_time)

    for model_config in MODEL_CASCADE:
        model_id = model_config['id']
        max_attempts_per_model = 2

        for attempt in range(max_attempts_per_model):
            t_config = None
            if "thinking_level" in model_config:
                t_config = types.ThinkingConfig(
                    include_thoughts=False,
                    thinking_level=model_config['thinking_level'],
                )
            elif "thinking_budget" in model_config:
                t_config = types.ThinkingConfig(
                    include_thoughts=False,
                    thinking_budget=model_config['thinking_budget'],
                )

            gen_config_args = {
                "safety_settings": SAFETY_SETTINGS,
                "tools": current_tools,
                "system_instruction": types.Content(parts=[types.Part(text=sys_prompt)]),
                "temperature": 1.0,
            }
            if t_config:
                gen_config_args["thinking_config"] = t_config

            logger.info(f"Sending to: {model_id} (Attempt {attempt + 1})")

            try:
                config = types.GenerateContentConfig(**gen_config_args)
                res = await client.aio.models.generate_content(
                    model=model_id, contents=contents, config=config
                )

                if res and res.candidates and res.candidates[0].content:
                    DAILY_REQUEST_COUNTS[model_id] += 1
                    logger.info(f"Success: {model_config['display']}")
                    return {"type": "text", "obj": res}, model_config['display']

            except genai_errors.APIError as e:
                err_str = str(e).lower()
                if "429" in err_str or "resource_exhausted" in err_str:
                    logger.warning(f"Limit Hit on {model_config['display']}.")
                    if attempt < max_attempts_per_model - 1:
                        await asyncio.sleep(5)
                        continue
                    break
                elif "503" in err_str or "overloaded" in err_str:
                    await asyncio.sleep(5)
                    continue
                elif "404" in err_str or "not found" in err_str:
                    logger.warning(f"Model {model_id} unavailable (404).")
                    break
                logger.error(f"API Error on {model_id}: {e}")
                break
            except Exception as e:
                logger.error(f"General Error on {model_id}: {e}", exc_info=True)
                break

    return {
        "type": "error",
        "msg": "Ошибка генерации или исчерпаны лимиты бесплатного API.",
    }, "none"


def format_response(response_data):
    try:
        if response_data['type'] == 'error':
            return response_data['msg']

        response = response_data['obj']
        cand = response.candidates[0]

        # Проверка finish_reason с защитой от None
        if cand.finish_reason and cand.finish_reason.name == "SAFETY":
            return "Скрыто фильтром безопасности."

        if not cand.content or not cand.content.parts:
            return "Пустой ответ от модели."

        text = "".join([p.text for p in cand.content.parts if p.text])
        text = RE_CLEAN_NAMES.sub('', text)
        return convert_markdown_to_html(text.strip())
    except Exception as e:
        logger.error(f"Format Error: {e}")
        return f"Ошибка: {e}"


async def send_smart(msg, text, hint=False):
    chunks = html_safe_chunker(text)
    if hint:
        h = "\n\n<i>Ответьте на это сообщение для вопроса по файлу.</i>"
        if len(chunks[-1]) + len(h) <= 4096:
            chunks[-1] += h

    sent = None
    try:
        for i, ch in enumerate(chunks):
            if i == 0:
                sent = await msg.reply_html(ch)
            else:
                sent = await msg.get_bot().send_message(
                    msg.chat_id, ch, parse_mode=ParseMode.HTML
                )
    except BadRequest as e:
        logger.error(f"HTML parse error: {e}. Sending as plain text.")
        plain = re.sub(r'<[^>]*>', '', text)
        for ch in [plain[i:i + 4096] for i in range(0, len(plain), 4096)]:
            sent = await msg.reply_text(ch)
    return sent


def get_chat_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in CHAT_LOCKS:
        CHAT_LOCKS[chat_id] = asyncio.Lock()
    return CHAT_LOCKS[chat_id]


def get_media(msg):
    """Извлекает медиа-объект из сообщения."""
    return (
        msg.audio or msg.voice or msg.video or msg.video_note
        or (msg.photo[-1] if msg.photo else None)
        or msg.document
    )


def get_mime(msg, media):
    """Определяет MIME-тип на основе типа сообщения."""
    if msg.photo:
        return 'image/jpeg'
    if msg.voice:
        return 'audio/ogg'
    if msg.video_note:
        return 'video/mp4'
    return getattr(media, 'mime_type', 'application/octet-stream')


async def download_and_upload(msg, media, client, source_msg=None):
    """Скачивает медиа из Telegram и загружает в Google.
    
    Args:
        msg: Сообщение, куда отправить статус "📥"
        media: Медиа-объект для скачивания
        client: Gemini клиент
        source_msg: Сообщение-источник для определения MIME (если отличается от msg)
    
    Returns:
        (Part, status_message)
    """
    source = source_msg or msg
    mime = get_mime(source, media)
    st = await msg.reply_text("📥")
    try:
        f = await media.get_file()
        b = await f.download_as_bytearray()
        part = await upload_file(client, b, mime, getattr(media, 'file_name', 'file'))
        return part, st
    except Exception:
        await safe_delete(st)
        raise


async def safe_delete(msg):
    """Безопасно удаляет сообщение, игнорируя ошибки."""
    if msg:
        try:
            await msg.delete()
        except Exception:
            pass


def is_audio_type(msg, mime):
    """Проверяет, является ли медиа аудио-типом."""
    return (
        msg.voice or msg.audio or msg.video or msg.video_note
        or (msg.document and mime and 'audio' in mime)
    )


TRANSCRIBE_PROMPT = (
    "Transcribe this audio file verbatim. "
    "Output ONLY the raw text, no introductory words."
)

OCR_PROMPT = (
    "Extract all visible text from this image (OCR). "
    "Output ONLY the raw extracted text, nothing else. "
    "No descriptions, no introductory words, no commentary."
)


async def process_request(update, context, parts, text_only=False):
    msg = update.message
    client = GEMINI_CLIENT

    typer = TypingWorker(context.bot, msg.chat_id, ChatAction.TYPING)
    typer.start()

    try:
        txt = next((p.text for p in parts if p.text), None)
        if txt and DATE_TIME_REGEX.search(txt):
            await send_smart(msg, get_current_time_str())
            return

        is_media_request = any(p.file_data for p in parts)
        history = [] if text_only else build_history(context.chat_data.get("history", []))

        user_name = msg.from_user.first_name

        parts_final = [p for p in parts if p.file_data]
        prompt_txt = next((p.text for p in parts if p.text), "")
        final_prompt = f"[{msg.from_user.id}; Name: {user_name}]: {prompt_txt}"
        parts_final.append(types.Part(text=final_prompt))

        current_tools = MEDIA_TOOLS if is_media_request else TEXT_TOOLS

        res_data, used_model_display = await generate(
            client,
            history + [types.Content(parts=parts_final, role="user")],
            current_tools,
        )

        clean_reply = format_response(res_data)
        reply_to_send = clean_reply

        if not text_only and used_model_display != "none":
            reply_to_send += f"\n\n<i>{used_model_display}</i>"

        sent = await send_smart(msg, reply_to_send, hint=(is_media_request and not text_only))

        if sent and not text_only:
            lock = get_chat_lock(msg.chat_id)
            async with lock:
                hist_item = {
                    "role": "user",
                    "parts": [part_to_dict(p) for p in parts],
                    "user_id": msg.from_user.id,
                    "user_name": user_name,
                }
                context.chat_data.setdefault("history", []).append(hist_item)
                bot_item = {
                    "role": "model",
                    "parts": [{'type': 'text', 'content': clean_reply[:MAX_HISTORY_RESPONSE_LEN]}],
                }
                context.chat_data["history"].append(bot_item)

                if len(context.chat_data["history"]) > MAX_HISTORY_ITEMS:
                    context.chat_data["history"] = context.chat_data["history"][-MAX_HISTORY_ITEMS:]

                rmap = context.chat_data.setdefault('reply_map', {})
                rmap[sent.message_id] = msg.message_id
                if len(rmap) > MAX_HISTORY_ITEMS * 2:
                    for k in list(rmap.keys())[:-MAX_HISTORY_ITEMS]:
                        del rmap[k]

                if is_media_request:
                    m_part = next((p for p in parts if p.file_data), None)
                    if m_part:
                        m_store = (
                            context.application.bot_data
                            .setdefault('media_contexts', {})
                            .setdefault(msg.chat_id, OrderedDict())
                        )
                        m_store[msg.message_id] = part_to_dict(m_part)
                        if len(m_store) > MAX_MEDIA_CONTEXTS:
                            m_store.popitem(last=False)
                        await context.application.persistence.save_media_contexts(
                            context.application.bot_data.get('media_contexts', {})
                        )

                await context.application.persistence.update_chat_data(
                    msg.chat_id, context.chat_data
                )

    except Exception as e:
        logger.error(f"Proc Error: {e}", exc_info=True)
        await msg.reply_text("Внутренняя ошибка.")
    finally:
        typer.stop()


# --- HANDLERS ---
@ignore_if_processing
async def universal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    context.chat_data['id'] = msg.chat_id

    parts = []
    client = GEMINI_CLIENT
    text = msg.caption or msg.text or ""

    media = get_media(msg)

    # ЛОГИКА STICKY TRANSCRIPTION MODE
    transcription_mode = context.chat_data.get('next_media_is_text', False)

    if transcription_mode:
        if media:
            st = None
            try:
                media_part, st = await download_and_upload(msg, media, client)
                mime = get_mime(msg, media)

                prompt_text = TRANSCRIBE_PROMPT if is_audio_type(msg, mime) else OCR_PROMPT

                parts = [media_part, types.Part(text=prompt_text)]
                await process_request(update, context, parts, text_only=True)
                return
            except Exception as e:
                await msg.reply_text(f"Загрузка: {e}")
                return
            finally:
                await safe_delete(st)

        elif text and not text.startswith('/'):
            # Текст выключает sticky-режим, затем обрабатывается как обычный запрос
            context.chat_data.pop('next_media_is_text', None)
            await context.application.persistence.update_chat_data(
                msg.chat_id, context.chat_data
            )
            # Код продолжит выполнение ниже

    if media:
        if media.file_size > TELEGRAM_FILE_LIMIT_MB * 1024 * 1024:
            await msg.reply_text("Файл >20MB. Читаю только текст.")
        else:
            st = None
            try:
                media_part, st = await download_and_upload(msg, media, client)
                parts.append(media_part)
            except Exception as e:
                await msg.reply_text(f"Загрузка: {e}")
                return
            finally:
                await safe_delete(st)

    yt = YOUTUBE_REGEX.search(text)
    if not media and yt:
        parts.append(types.Part(
            file_data=types.FileData(mime_type="video/youtube", file_uri=yt.group(0))
        ))
        text = text.replace(yt.group(0), '').strip()

    if not media and msg.reply_to_message:
        orig = context.chat_data.get('reply_map', {}).get(msg.reply_to_message.message_id)
        if orig:
            ctx = (
                context.application.bot_data
                .get('media_contexts', {})
                .get(msg.chat_id, {})
                .get(orig)
            )
            if ctx:
                p, stale = dict_to_part(ctx)
                if p:
                    parts.append(p)

    if text:
        parts.append(types.Part(text=text))
    if parts:
        await process_request(update, context, parts)


@ignore_if_processing
async def text_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    # Режим «ответ на сообщение» (разовый)
    if msg.reply_to_message:
        reply = msg.reply_to_message
        media = get_media(reply)
        if media:
            if media.file_size > TELEGRAM_FILE_LIMIT_MB * 1024 * 1024:
                return await msg.reply_text("Файл слишком велик.")
            st = None
            try:
                media_part, st = await download_and_upload(msg, media, client=GEMINI_CLIENT, source_msg=reply)
                mime = get_mime(reply, media)

                prompt_text = TRANSCRIBE_PROMPT if is_audio_type(reply, mime) else OCR_PROMPT

                parts = [media_part, types.Part(text=prompt_text)]
                await process_request(update, context, parts, text_only=True)
                return
            except Exception as e:
                return await msg.reply_text(f"Ошибка: {e}")
            finally:
                await safe_delete(st)
        else:
            return await msg.reply_text(
                "Ответьте этой командой на сообщение с аудио или картинкой."
            )

    # Режим «потоковый» — toggle
    if context.chat_data.get('next_media_is_text', False):
        context.chat_data.pop('next_media_is_text', None)
        await msg.reply_html("<b>Режим расшифровки выключен.</b>")
    else:
        context.chat_data['next_media_is_text'] = True
        await msg.reply_html(
            "<b>Включен режим потоковой расшифровки.</b>\n"
            "Кидай аудио, видео или фото — я буду сразу присылать текст.\n\n"
            "<i>Чтобы выйти, напиши /text ещё раз или отправь текстовое сообщение.</i>"
        )

    # Сохраняем изменение режима в persistence
    await context.application.persistence.update_chat_data(
        msg.chat_id, context.chat_data
    )


@ignore_if_processing
async def summarize_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await util_cmd(update, context, "Сделай подробный конспект (summary) этого материала.")


async def util_cmd(update, context, prompt):
    msg = update.message
    if not msg.reply_to_message:
        return await msg.reply_text("Ответьте на сообщение с файлом.")

    reply = msg.reply_to_message
    media = get_media(reply)
    parts = []
    client = GEMINI_CLIENT

    if media:
        if media.file_size > TELEGRAM_FILE_LIMIT_MB * 1024 * 1024:
            return await msg.reply_text("Файл велик.")
        st = None
        try:
            media_part, st = await download_and_upload(msg, media, client, source_msg=reply)
            parts.append(media_part)
        except Exception as e:
            return await msg.reply_text(f"❌ {e}")
        finally:
            await safe_delete(st)
    elif reply.text and YOUTUBE_REGEX.search(reply.text):
        parts.append(types.Part(file_data=types.FileData(
            mime_type="video/youtube",
            file_uri=YOUTUBE_REGEX.search(reply.text).group(0),
        )))
    elif context.chat_data.get('reply_map', {}).get(reply.message_id):
        orig = context.chat_data['reply_map'][reply.message_id]
        ctx = (
            context.application.bot_data
            .get('media_contexts', {})
            .get(msg.chat_id, {})
            .get(orig)
        )
        if ctx:
            p, _ = dict_to_part(ctx)
            if p:
                parts.append(p)

    if not parts:
        return await msg.reply_text("Нет медиа.")

    parts.append(types.Part(text=prompt))
    await process_request(update, context, parts)


@ignore_if_processing
async def start_c(u, c):
    start_text = (
        "<b>Привет! Я Женя — твой умный ИИ-ассистент.</b>\n\n"
        "<b>Что я умею:</b>\n"
        "<b>Текст и файлы:</b> Пиши запросы, кидай документы, фото, аудио или видео "
        "— я всё прочитаю, проанализирую и отвечу текстом.\n"
        "<b>Транскрибация:</b> Напиши <code>/text</code>, чтобы войти в режим расшифровки. "
        "Кидай сколько угодно файлов — я переведу их в текст. "
        "Чтобы выйти, напиши /text ещё раз или отправь текстовое сообщение.\n\n"
        "<i>Работаю на базе быстрого и стабильного Gemini 2.5 Flash.</i>"
    )
    await u.message.reply_html(start_text)


@ignore_if_processing
async def clear_c(u, c):
    c.chat_data.clear()
    if "media_contexts" in c.application.bot_data:
        c.application.bot_data["media_contexts"].pop(u.effective_chat.id, None)
        await c.application.persistence.save_media_contexts(
            c.application.bot_data.get('media_contexts', {})
        )
    await c.application.persistence.update_chat_data(u.effective_chat.id, c.chat_data)
    await u.message.reply_text("Память очищена.")


@ignore_if_processing
async def status_c(u, c):
    date_str = DAILY_REQUEST_DATE.isoformat() if DAILY_REQUEST_DATE else "N/A"
    stats = "\n".join(
        [f"  {m['display']}: {DAILY_REQUEST_COUNTS[m['id']]}" for m in MODEL_CASCADE]
    )
    await u.message.reply_html(f"<b>Статистика за {date_str}:</b>\n{stats}")


# --- MAIN ---
async def main():
    global GEMINI_CLIENT

    pers = PostgresPersistence(DATABASE_URL)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(pers).build()

    app.add_handler(CommandHandler("start", start_c))
    app.add_handler(CommandHandler("text", text_cmd))
    app.add_handler(CommandHandler("summarize", summarize_cmd))
    app.add_handler(CommandHandler("clear", clear_c))
    app.add_handler(CommandHandler("status", status_c))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, universal_handler))

    await app.initialize()
    await app.start()

    GEMINI_CLIENT = genai.Client(api_key=GOOGLE_API_KEY)

    app.bot_data['media_contexts'] = await pers.load_media_contexts()

    commands = [
        BotCommand("start", "Справка"),
        BotCommand("text", "Режим расшифровки файлов"),
        BotCommand("summarize", "Конспект"),
        BotCommand("clear", "Сброс памяти"),
        BotCommand("status", "Статистика"),
    ]
    await app.bot.set_my_commands(commands)
    logger.info("Menu commands updated.")

    if ADMIN_ID:
        try:
            await app.bot.send_message(ADMIN_ID, "Bot Started (v74)")
        except Exception:
            pass

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, stop_event.set)
    except NotImplementedError:
        logger.warning("Signal handlers not supported on this platform.")

    clean_path = GEMINI_WEBHOOK_PATH.strip('/')
    webhook_url = f"{WEBHOOK_HOST.rstrip('/')}/{clean_path}"
    await app.bot.set_webhook(url=webhook_url, secret_token=TELEGRAM_SECRET_TOKEN)

    server = aiohttp.web.Application()

    async def wh(r):
        token = r.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if token != TELEGRAM_SECRET_TOKEN:
            return aiohttp.web.Response(status=403, text="Forbidden")
        try:
            data = await r.json()
            await app.process_update(Update.de_json(data, app.bot))
            return aiohttp.web.Response(text='OK')
        except Exception as e:
            logger.error(f"Webhook error: {e}", exc_info=True)
            return aiohttp.web.Response(status=500)

    server.router.add_post(f"/{clean_path}", wh)
    server.router.add_get('/', lambda r: aiohttp.web.Response(text="Running"))

    runner = aiohttp.web.AppRunner(server)
    await runner.setup()
    await aiohttp.web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 10000))).start()

    logger.info("Ready.")
    await stop_event.wait()

    await app.stop()
    await app.shutdown()
    await runner.cleanup()
    pers.close()


if __name__ == '__main__':
    asyncio.run(main())
