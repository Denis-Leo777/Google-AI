# Версия 27 (Final Production Release)

import logging
import os
import asyncio
import signal
import re
import pickle
from collections import defaultdict, OrderedDict
import psycopg2
from psycopg2 import pool
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

if not all([TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, WEBHOOK_HOST, GEMINI_WEBHOOK_PATH]):
    logger.critical("Критическая ошибка: не заданы переменные окружения!")
    exit(1)

# --- МОДЕЛИ И ЛИМИТЫ ---
DEFAULT_MODEL = 'gemini-2.5-flash-preview-09-2025'
FALLBACK_MODEL = 'gemini-2.5-flash-lite-preview-09-2025'

MAX_CONTEXT_CHARS = 100000
MAX_HISTORY_ITEMS = 100
MAX_HISTORY_RESPONSE_LEN = 4000
MAX_MEDIA_CONTEXTS = 100
MEDIA_CONTEXT_TTL_SECONDS = 47 * 3600
TELEGRAM_FILE_LIMIT_MB = 20
MEDIA_GROUP_BUFFER_SECONDS = 2.0
THINKING_BUDGET = 24000 

YOUTUBE_REGEX = r'(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|v\/|shorts\/)|youtu\.be\/|youtube-nocookie\.com\/embed\/)([a-zA-Z0-9_-]{11})'
URL_REGEX = r'https?:\/\/[^\s/$.?#].[^\s]*'
DATE_TIME_REGEX = r'^\s*(какой\s+)?(день|дата|число|время|который\s+час)\??\s*$'

# --- ИНСТРУМЕНТЫ ---
TEXT_TOOLS = [types.Tool(google_search=types.GoogleSearch(), code_execution=types.ToolCodeExecution(), url_context=types.UrlContext())]
MEDIA_TOOLS = [types.Tool(google_search=types.GoogleSearch(), url_context=types.UrlContext())] 

SAFETY_SETTINGS = [
    types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for c in (types.HarmCategory.HARM_CATEGORY_HARASSMENT, types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
              types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT)
]

try:
    with open('system_prompt.md', 'r', encoding='utf-8') as f: SYSTEM_INSTRUCTION = f.read()
except FileNotFoundError:
    SYSTEM_INSTRUCTION = """(System Note: Today is {current_time}.)"""

# --- БАЗА ДАННЫХ (PostgreSQL с защитой от сбоев) ---
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
                logger.info("БД подключена успешно.")
                return
            except psycopg2.Error as e:
                logger.error(f"Ошибка подключения БД (попытка {attempt+1}): {e}")
                if attempt < retries - 1: time.sleep(delay)
                else: raise

    def _connect(self):
        if self.db_pool and not self.db_pool.closed: self.db_pool.closeall()
        # Добавляем keepalives чтобы соединение не "тухло" при простое
        dsn = f"{self.dsn}&keepalives=1&keepalives_idle=60" if "?" in self.dsn else f"{self.dsn}?keepalives=1&keepalives_idle=60"
        self.db_pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=dsn)

    def _execute(self, query: str, params: tuple = None, fetch: str = None, retries=3):
        for attempt in range(retries):
            conn = None
            try:
                conn = self.db_pool.getconn()
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    res = cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else True
                    conn.commit()
                return res
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                # Если соединение разорвано, пробуем переподключиться
                if conn:
                    try: self.db_pool.putconn(conn, close=True)
                    except: pass
                    conn = None
                logger.warning(f"Сбой БД, ретрай {attempt+1}...")
                if attempt < retries - 1:
                    time.sleep(0.5)
                    continue
                else:
                    logger.error(f"Критическая ошибка БД: {e}")
                    return None
            except Exception as e:
                logger.error(f"SQL Ошибка: {e}")
                if conn: self.db_pool.putconn(conn)
                return None
            finally:
                if conn: self.db_pool.putconn(conn)

    def _initialize_db(self): self._execute("CREATE TABLE IF NOT EXISTS persistence_data (key TEXT PRIMARY KEY, data BYTEA NOT NULL);")
    def _get_pickled(self, key: str):
        res = self._execute("SELECT data FROM persistence_data WHERE key = %s;", (key,), fetch="one")
        return pickle.loads(res[0]) if res and res[0] else None
    def _set_pickled(self, key: str, data: object):
        self._execute("INSERT INTO persistence_data (key, data) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET data = %s;", (key, pickle.dumps(data), pickle.dumps(data)))
    
    async def get_bot_data(self) -> dict: return defaultdict(dict)
    async def update_bot_data(self, data: dict) -> None: pass
    async def get_chat_data(self) -> defaultdict[int, dict]:
        all_data = await asyncio.to_thread(self._execute, "SELECT key, data FROM persistence_data WHERE key LIKE 'chat_data_%';", fetch="all") or []
        chat_data = defaultdict(dict)
        for k, d in all_data:
            try: chat_data[int(k.split('_')[-1])] = pickle.loads(d)
            except: pass
        return chat_data
    async def update_chat_data(self, chat_id: int, data: dict) -> None: await asyncio.to_thread(self._set_pickled, f"chat_data_{chat_id}", data)
    async def drop_chat_data(self, chat_id: int) -> None: await asyncio.to_thread(self._execute, "DELETE FROM persistence_data WHERE key = %s;", (f"chat_data_{chat_id}",))
    async def refresh_chat_data(self, chat_id: int, chat_data: dict) -> None:
        data = await asyncio.to_thread(self._get_pickled, f"chat_data_{chat_id}") or {}
        chat_data.update(data)
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
def get_current_time_str() -> str:
    now = datetime.datetime.now(pytz.timezone("Europe/Moscow"))
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    return f"Сегодня {days[now.weekday()]}, {now.day} {months[now.month-1]} {now.year} года, время {now.strftime('%H:%M')} (MSK)."

def html_safe_chunker(text: str, chunk_size: int = 4096) -> list[str]:
    chunks, tag_stack, remaining_text = [], [], text
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
        chunks.append(current_chunk + ''.join(f'</{tag}>' for tag in reversed(temp_stack)))
        tag_stack = temp_stack
        remaining_text = ''.join(f'<{tag}>' for tag in tag_stack) + remaining_text[split_pos:].lstrip()
    chunks.append(remaining_text)
    return chunks

def ignore_if_processing(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update or not update.effective_message: return
        key = f"{update.effective_chat.id}_{update.effective_message.message_id}"
        processing = context.application.bot_data.setdefault('processing_messages', set())
        if key in processing: return
        processing.add(key)
        try: await func(update, context, *args, **kwargs)
        finally: processing.discard(key)
    return wrapper

def isolated_request(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        original = list(context.chat_data.get("history", []))
        context.chat_data["history"] = []
        try: await func(update, context, *args, **kwargs)
        finally:
            context.chat_data["history"] = (original + context.chat_data.get("history", []))[-MAX_HISTORY_ITEMS:]
    return wrapper

def part_to_dict(part: types.Part) -> dict:
    if part.text: return {'type': 'text', 'content': part.text}
    if part.file_data: return {'type': 'file', 'uri': part.file_data.file_uri, 'mime': part.file_data.mime_type, 'timestamp': time.time()}
    return {}

def dict_to_part(part_dict: dict) -> tuple[types.Part | None, bool]:
    if not isinstance(part_dict, dict): return None, False
    if part_dict.get('type') == 'text': return types.Part(text=part_dict.get('content', '')), False
    if part_dict.get('type') == 'file':
        if time.time() - part_dict.get('timestamp', 0) > MEDIA_CONTEXT_TTL_SECONDS: return None, True
        return types.Part(file_data=types.FileData(file_uri=part_dict['uri'], mime_type=part_dict['mime'])), False
    return None, False

def build_history_for_request(chat_history: list) -> list[types.Content]:
    valid_history, current_chars = [], 0
    for entry in reversed(chat_history):
        if entry.get("role") not in ("user", "model"): continue
        api_parts = []
        for p in entry["parts"]:
            if p.get('type') == 'text':
                content = p.get('content', '')
                prefix = f"[{entry.get('user_id', 'User')}; Name: {entry.get('user_name', 'User')}]: " if entry.get('role') == 'user' else ""
                api_parts.append(types.Part(text=f"{prefix}{content}"))
        if not api_parts: continue
        txt_len = sum(len(p.text) for p in api_parts if p.text)
        if current_chars + txt_len > MAX_CONTEXT_CHARS: break
        valid_history.append(types.Content(role=entry["role"], parts=api_parts))
        current_chars += txt_len
    return valid_history[::-1]

async def upload_and_wait_for_file(client: genai.Client, file_bytes: bytes, mime_type: str, file_name: str) -> types.Part:
    try:
        up_res = await client.aio.files.upload(file=io.BytesIO(file_bytes), config=types.UploadFileConfig(mime_type=mime_type, display_name=file_name))
        file_res = await client.aio.files.get(name=up_res.name)
        for _ in range(15):
            if file_res.state.name == 'ACTIVE': return types.Part(file_data=types.FileData(file_uri=file_res.uri, mime_type=mime_type))
            if file_res.state.name == 'FAILED': raise IOError("Ошибка обработки файла на сервере Google")
            await asyncio.sleep(2)
            file_res = await client.aio.files.get(name=up_res.name)
        raise asyncio.TimeoutError("Тайм-аут обработки файла (30 сек)")
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise IOError(f"Не удалось загрузить файл: {e}")

# --- GEMINI ГЕНЕРАЦИЯ И FALLBACK ---
async def generate_response(client: genai.Client, request_contents: list, context: ContextTypes.DEFAULT_TYPE, tools: list, sys_instr: str | None = None) -> tuple[types.GenerateContentResponse | str, str]:
    final_sys_instr = sys_instr or SYSTEM_INSTRUCTION.format(current_time=get_current_time_str())
    
    config = types.GenerateContentConfig(
        safety_settings=SAFETY_SETTINGS, 
        tools=tools,
        system_instruction=types.Content(parts=[types.Part(text=final_sys_instr)]),
        temperature=1.0,
        thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET) 
    )
    
    user_pref = context.chat_data.get('model', DEFAULT_MODEL)
    # Если стоит дефолтная, пробуем её, потом Fallback. Если юзер выбрал Lite, только её.
    models = [DEFAULT_MODEL, FALLBACK_MODEL] if user_pref == DEFAULT_MODEL else [user_pref]

    for model in models:
        # 2 попытки на каждую модель
        for attempt in range(2):
            try:
                res = await client.aio.models.generate_content(model=model, contents=request_contents, config=config)
                if res and res.candidates and res.candidates[0].content: return res, model
            except genai_errors.APIError as e:
                err = str(e).lower()
                http_status = getattr(e, 'http_status', 0)
                
                # Главная логика fallback: если 429 или исчерпаны ресурсы -> следующая модель
                if "resource_exhausted" in err or http_status == 429:
                    logger.warning(f"Модель {model} исчерпана/перегружена.")
                    break # Выход из retry цикла, переход к следующей модели в списке
                
                if "input token count" in err: return "🤯 Слишком длинная история. Используйте /clear.", model
                await asyncio.sleep(2) # Пауза перед ретраем той же модели
                
            except Exception as e:
                logger.error(f"Gen error: {e}")
                return f"Error: {html.escape(str(e))}", model
                
    return "😔 Все модели перегружены или недоступны. Попробуйте через минуту.", "None"

def format_gemini_response(response: types.GenerateContentResponse) -> str:
    try:
        if not response.candidates[0].content.parts: return "Пустой ответ."
        parts = [p.text for p in response.candidates[0].content.parts if p.text]
        text = re.sub(r'\n{3,}', '\n\n', "".join(parts))
        # Убираем технические теги мышления, если они просачиваются
        text = re.sub(r'tool_code\n.*?thought\n', '', text, flags=re.DOTALL)
        return text.strip()
    except: return "Ошибка обработки ответа."

async def send_reply(msg: Message, text: str, hint: bool = False):
    if hint: text += "\n\n<i>💡 Ответьте на это сообщение, чтобы задать вопрос по этому файлу.</i>"
    for chunk in html_safe_chunker(text):
        try: await msg.reply_html(chunk)
        except BadRequest: await msg.reply_text(re.sub(r'<[^>]*>', '', chunk))
    return msg 

async def add_to_history(context: ContextTypes.DEFAULT_TYPE, role: str, parts: list[types.Part], user_id=None, user_name=None, **kwargs):
    hist = context.chat_data.setdefault("history", [])
    entry_parts = [part_to_dict(p) for p in parts if p.text or p.file_data]
    if not entry_parts and role == 'user': entry_parts.append({'type': 'text', 'content': ''})
    entry = {"role": role, "parts": entry_parts, **kwargs}
    if user_id: entry.update({'user_id': user_id, 'user_name': user_name})
    hist.append(entry)
    # Обрезка истории
    context.chat_data["history"] = hist[-MAX_HISTORY_ITEMS:]

# --- ЛОГИКА АЛЬБОМОВ ---
async def process_media_group_delayed(context: ContextTypes.DEFAULT_TYPE, mg_id: str):
    await asyncio.sleep(MEDIA_GROUP_BUFFER_SECONDS)
    data = context.bot_data.get('media_group_buffer', {}).pop(mg_id, None)
    if not data: return

    captions = [c for c in data['captions'] if c and c.strip()]
    unique_text = "\n".join(OrderedDict.fromkeys(captions))
    
    parts = data['parts']
    if unique_text: parts.append(types.Part(text=unique_text))
    elif not any(p.text for p in parts): parts.append(types.Part(text="Проанализируй эти медиа-файлы."))

    base_msg = data['messages'][0]
    # Используем base_msg как точку опоры для ответа
    await process_request(Update(0, base_msg), context, parts, reply_to_msg=base_msg)

async def buffer_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE, file_part: types.Part, caption: str):
    mg_id = update.message.media_group_id
    buf = context.bot_data.setdefault('media_group_buffer', {})
    if mg_id not in buf:
        buf[mg_id] = {'parts': [], 'captions': [], 'messages': [], 'task': asyncio.create_task(process_media_group_delayed(context, mg_id))}
    buf[mg_id]['parts'].append(file_part)
    buf[mg_id]['captions'].append(caption or "")
    buf[mg_id]['messages'].append(update.message)

# --- ОБРАБОТКА ЗАПРОСОВ ---
async def process_request(update: Update, context: ContextTypes.DEFAULT_TYPE, content_parts: list, reply_to_msg: Message = None):
    msg = reply_to_msg or update.message
    client = context.bot_data['gemini_client']
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)

    txt = next((p.text for p in content_parts if p.text), None)
    if txt and re.search(DATE_TIME_REGEX, txt, re.IGNORECASE):
        await send_reply(msg, get_current_time_str())
        return

    hist = build_history_for_request(context.chat_data.get("history", []))
    is_media = any(p.file_data for p in content_parts)
    
    user_info = f"[{msg.from_user.id}; Name: {msg.from_user.first_name}]: "
    # Google Search включаем если нет медиа (текстовый запрос) или если это URL
    grounding = "" if is_media or (txt and re.search(URL_REGEX, txt)) else f"ИЩИ актуальные данные на {get_current_time_str()} через Google Search.\n"
    
    final_parts = [p for p in content_parts if p.file_data]
    text_found = False
    for p in content_parts:
        if p.text:
            final_parts.append(types.Part(text=f"{grounding}{user_info}{p.text}"))
            text_found = True
            break
    if not text_found: final_parts.append(types.Part(text=f"{grounding}{user_info}"))

    res_obj, model = await generate_response(client, hist + [types.Content(parts=final_parts, role="user")], context, MEDIA_TOOLS if is_media else TEXT_TOOLS)
    
    reply = res_obj if isinstance(res_obj, str) else format_gemini_response(res_obj)
    if model != "None": reply += f"\n\n🤖 <i>Model: {model}</i>"
    
    sent = await send_reply(msg, reply, hint=is_media)
    
    if sent:
        await add_to_history(context, "user", content_parts, msg.from_user.id, msg.from_user.first_name)
        await add_to_history(context, "model", [types.Part(text=reply)])
        
        context.chat_data.setdefault('reply_map', {})[sent.message_id] = msg.message_id
        if is_media:
             media_p = next((p for p in content_parts if p.file_data), None)
             if media_p:
                 mc = context.application.bot_data.setdefault('media_contexts', {}).setdefault(msg.chat_id, OrderedDict())
                 mc[msg.message_id] = part_to_dict(media_p)
                 if len(mc) > MAX_MEDIA_CONTEXTS: mc.popitem(last=False)
        await context.application.persistence.update_chat_data(msg.chat_id, context.chat_data)

# --- КОМАНДЫ ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    text = """👋 <b>Привет! Я бот на базе Gemini 2.5 Flash.</b>

🤖 <b>Модели:</b>
• <b>Default:</b> Flash Preview (быстрая, умная).
• <b>Fallback:</b> Flash Lite (если Default кончится).
🔥 <b>Thinking:</b> Включено "мышление" (24k) для сложных задач.

📤 <b>Отправляй мне:</b>
• Текст (я умею гуглить!)
• Фото, Видео, Аудио, Голосовые
• Документы (PDF, TXT и др.)
• Ссылки на YouTube (я посмотрю видео)
• Альбомы (группы файлов)

⚙️ <b>Команды:</b>
/clear - Очистить память
/model - Выбрать модель вручную
/newtopic - Забыть старые файлы
/transcript - Расшифровка (реплаем)
/summarize - Саммари (реплаем)"""
    await u.message.reply_html(text)

@ignore_if_processing
async def clear(u: Update, c: ContextTypes.DEFAULT_TYPE): 
    c.chat_data.clear()
    c.application.bot_data.get('media_contexts', {}).pop(u.effective_chat.id, None)
    await c.application.persistence.update_chat_data(u.effective_chat.id, c.chat_data)
    await u.message.reply_text("✅ Память очищена.")

@ignore_if_processing
async def newtopic(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.application.bot_data.get('media_contexts', {}).pop(u.effective_chat.id, None)
    await u.message.reply_text("✅ Контекст файлов сброшен.")

@ignore_if_processing
async def model_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cur = c.chat_data.get('model', DEFAULT_MODEL)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 Auto (Default)", callback_data=f"model_{DEFAULT_MODEL}"),
        InlineKeyboardButton("⚠️ Force Lite", callback_data=f"model_{FALLBACK_MODEL}")
    ]])
    await u.message.reply_html(f"Текущая модель: <b>{cur}</b>", reply_markup=kb)

async def model_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    new = q.data.split('_', 1)[1]
    c.chat_data['model'] = new
    await c.application.persistence.update_chat_data(q.effective_chat.id, c.chat_data)
    await q.edit_message_text(f"✅ Установлена модель: {new}")

# Утилиты
async def _get_reply_media_part(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message.reply_to_message:
        await u.message.reply_text("Ответьте этой командой на сообщение с файлом.")
        return None
    replied = u.message.reply_to_message
    
    # 1. Проверяем, есть ли контекст в памяти бота (если отвечаем боту)
    if replied.from_user.id == c.bot.id:
        orig_id = c.chat_data.get('reply_map', {}).get(replied.message_id)
        if orig_id:
            ctx = c.application.bot_data.get('media_contexts', {}).get(u.effective_chat.id, {}).get(orig_id)
            p, s = dict_to_part(ctx) if ctx else (None, False)
            if not s and p: return p

    # 2. Проверяем прямое вложение в сообщении
    m_obj = replied.audio or replied.voice or replied.video or replied.video_note or replied.photo or replied.document
    if m_obj:
        if isinstance(m_obj, list): m_obj = m_obj[-1]
        f = await m_obj.get_file()
        b = await f.download_as_bytearray()
        mime = getattr(m_obj, 'mime_type', 'image/jpeg' if replied.photo else 'application/octet-stream')
        return await upload_and_wait_for_file(c.bot_data['gemini_client'], b, mime, f"{f.file_unique_id}")
    
    # 3. YouTube (ссылка текстом)
    yt_match = re.search(YOUTUBE_REGEX, replied.text or "")
    if yt_match:
        # Для утилит мы не можем загрузить "видео" по ссылке в File API.
        # Поэтому просто отправляем текст с инструкцией для модели.
        # В этом случае `part` будет текстовым.
        return types.Part(text=f"Analyze this video: https://www.youtube.com/watch?v={yt_match.group(1)}")

    await u.message.reply_text("Медиа не найдено.")
    return None

async def transcript_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    p = await _get_reply_media_part(u, c)
    if p:
        # Если это YouTube (текстовая часть), добавляем к промпту.
        contents = [types.Content(parts=[p, types.Part(text="Transcribe this verbatim.")], role="user")]
        res, _ = await generate_response(c.bot_data['gemini_client'], contents, c, MEDIA_TOOLS, "Transcribe verbatim.")
        await send_reply(u.message, format_gemini_response(res) if not isinstance(res, str) else res)

async def summarize_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    p = await _get_reply_media_part(u, c)
    if p:
        contents = [types.Content(parts=[p, types.Part(text="Сделай подробный конспект.")], role="user")]
        res, m = await generate_response(c.bot_data['gemini_client'], contents, c, MEDIA_TOOLS)
        await send_reply(u.message, (format_gemini_response(res) if not isinstance(res, str) else res) + f"\n\n🤖 {m}")

async def keypoints_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    p = await _get_reply_media_part(u, c)
    if p:
        contents = [types.Content(parts=[p, types.Part(text="Выдели ключевые тезисы.")], role="user")]
        res, m = await generate_response(c.bot_data['gemini_client'], contents, c, MEDIA_TOOLS)
        await send_reply(u.message, (format_gemini_response(res) if not isinstance(res, str) else res) + f"\n\n🤖 {m}")

# --- ОБРАБОТЧИКИ СООБЩЕНИЙ ---
@ignore_if_processing
async def handle_media(u: Update, c: ContextTypes.DEFAULT_TYPE):
    msg = u.message
    c.chat_data['id'] = msg.chat_id
    m_obj, mime, ext = None, "", ""
    
    if msg.photo: m_obj, mime, ext = msg.photo[-1], 'image/jpeg', '.jpg'
    elif msg.video: m_obj, mime, ext = msg.video, 'video/mp4', '.mp4'
    elif msg.voice: m_obj, mime, ext = msg.voice, 'audio/ogg', '.ogg'
    elif msg.audio: m_obj, mime, ext = msg.audio, msg.audio.mime_type or 'audio/mp3', '.mp3'
    elif msg.video_note: m_obj, mime, ext = msg.video_note, 'video/mp4', '.mp4'
    elif msg.document: m_obj, mime, ext = msg.document, msg.document.mime_type, ""

    if not m_obj: return
    if hasattr(m_obj, 'file_size') and m_obj.file_size > TELEGRAM_FILE_LIMIT_MB * 1024 * 1024:
        await msg.reply_text("Файл слишком большой (>20MB).")
        return

    try:
        if not msg.media_group_id: # Если не альбом, пишем "Загружаю"
            await msg.reply_text("Загружаю...", reply_to_message_id=msg.message_id)
            
        f = await m_obj.get_file()
        b = await f.download_as_bytearray()
        part = await upload_and_wait_for_file(c.bot_data['gemini_client'], b, mime, f"{f.file_unique_id}{ext}")
        
        if msg.media_group_id: await buffer_media_group(u, c, part, msg.caption)
        else: await process_request(u, c, [part, types.Part(text=msg.caption or "")])
    except Exception as e:
        logger.error(f"Media handler error: {e}")
        await msg.reply_text(f"Ошибка загрузки: {e}")

@ignore_if_processing
async def handle_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    msg = u.message
    txt = msg.text or ""
    if not txt: return
    c.chat_data['id'] = msg.chat_id
    parts = []

    # Исправление для YouTube: передаем URL в тексте, НЕ создаем FileData
    yt_match = re.search(YOUTUBE_REGEX, txt)
    if yt_match:
        # Просто передаем текст, но можем добавить явное указание
        # Модель сама использует инструменты поиска для просмотра
        parts.append(types.Part(text=f"Analyze this YouTube video: {txt}"))
    else:
        parts.append(types.Part(text=txt))
    
    # Проверяем, был ли реплай на сообщение с файлом (контекст)
    if msg.reply_to_message:
        orig_id = c.chat_data.get('reply_map', {}).get(msg.reply_to_message.message_id)
        if orig_id:
            ctx = c.application.bot_data.get('media_contexts', {}).get(msg.chat_id, {}).get(orig_id)
            p, s = dict_to_part(ctx) if ctx else (None, False)
            if not s and p: parts.insert(0, p)
            
    await process_request(u, c, parts)

# --- SERVER ---
async def health_check(req): return aiohttp.web.Response(text="OK", status=200)

async def webhook_handler(req):
    app = req.app['bot_app']
    try:
        data = await req.json()
        await app.process_update(Update.de_json(data, app.bot))
        return aiohttp.web.Response(text="OK")
    except: return aiohttp.web.Response(status=500)

async def main():
    persistence = PostgresPersistence(DATABASE_URL) if DATABASE_URL else None
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    await app.initialize()
    app.bot_data['gemini_client'] = genai.Client(api_key=GOOGLE_API_KEY)
    
    # Регистрируем команды, чтобы кнопка "Меню" появилась
    commands = [
        BotCommand("start", "Инфо и перезапуск"),
        BotCommand("clear", "Очистить историю"),
        BotCommand("newtopic", "Сбросить файлы"),
        BotCommand("model", "Выбор модели"),
        BotCommand("transcript", "Транскрипция (reply)"),
        BotCommand("summarize", "Саммари (reply)")
    ]
    await app.bot.set_my_commands(commands)
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("newtopic", newtopic))
    app.add_handler(CommandHandler("model", model_cmd))
    app.add_handler(CommandHandler("transcript", transcript_cmd))
    app.add_handler(CommandHandler("summarize", summarize_cmd))
    app.add_handler(CommandHandler("keypoints", keypoints_cmd))
    app.add_handler(CallbackQueryHandler(model_cb, pattern='^model_'))
    
    media_filters = (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE | filters.Document.ALL) & ~filters.COMMAND
    app.add_handler(MessageHandler(media_filters, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(s, stop.set)
    
    await app.bot.set_webhook(url=f"{WEBHOOK_HOST.rstrip('/')}/{GEMINI_WEBHOOK_PATH.strip('/')}")
    
    server = aiohttp.web.Application()
    server['bot_app'] = app
    server.router.add_post('/' + GEMINI_WEBHOOK_PATH.strip('/'), webhook_handler)
    server.router.add_get('/', health_check)
    
    runner = aiohttp.web.AppRunner(server)
    await runner.setup()
    await aiohttp.web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", "10000"))).start()
    
    logger.info("Бот запущен!")
    await stop.wait()
    await runner.cleanup()
    if persistence: persistence.close()

if __name__ == '__main__':
    asyncio.run(main())
