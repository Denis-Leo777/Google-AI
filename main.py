# Версия 67 (Fix: Remove unsupported budget_token_limit)

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
from telegram import Update, Message, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
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
TELEGRAM_SECRET_TOKEN = os.getenv('TELEGRAM_SECRET_TOKEN', 'my-secret-token-change-me') 
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')
ADMIN_ID = os.getenv('ADMIN_ID')

if not all([TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, WEBHOOK_HOST, GEMINI_WEBHOOK_PATH]):
    logger.critical("Не заданы переменные окружения!")
    exit(1)

# --- МОДЕЛИ И ЛИМИТЫ ---
MODELS_CONFIG = [
    # 1. Flash 2.5 (Priority)
    {'id': 'gemini-2.5-flash-preview-09-2025', 'rpm': 5, 'rpd': 20, 'name': 'Gemini 2.5 Flash'},
    # 2. Flash Lite 2.5 (High Speed Backup)
    {'id': 'gemini-2.5-flash-lite-preview-09-2025', 'rpm': 15, 'rpd': 1500, 'name': 'Gemini 2.5 Lite'}
]
DEFAULT_MODEL = 'gemini-2.5-flash-preview-09-2025'

# --- ЛИМИТЫ ---
MAX_CONTEXT_CHARS = 300000 
MAX_HISTORY_RESPONSE_LEN = 4000
MAX_HISTORY_ITEMS = 100 
MAX_MEDIA_CONTEXTS = 50
MEDIA_CONTEXT_TTL_SECONDS = 47 * 3600
TELEGRAM_FILE_LIMIT_MB = 20

# Regex
YOUTUBE_REGEX = re.compile(r'(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|v\/|shorts\/)|youtu\.be\/|youtube-nocookie\.com\/embed\/)([a-zA-Z0-9_-]{11})')
URL_REGEX = re.compile(r'https?:\/\/[^\s/$.?#].[^\s]*')
DATE_TIME_REGEX = re.compile(r'^\s*(какой\s+)?(день|дата|число|время|который\s+час)\??\s*$', re.IGNORECASE)
HTML_TAG_REGEX = re.compile(r'<(/?)(b|i|u|s|code|pre|a|tg-spoiler)(?:\s[^>]*)?>', re.IGNORECASE)

RE_CODE_BLOCK = re.compile(r'```(\w+)?\n?(.*?)```', re.DOTALL)
RE_INLINE_CODE = re.compile(r'`([^`]+)`')
RE_BOLD = re.compile(r'(?:\*\*|__)(.*?)(?:\*\*|__)')
RE_ITALIC = re.compile(r'(?<!\*)\*(?!\s)(.*?)(?<!\s)\*(?!\*)')
RE_HEADER = re.compile(r'^#{1,6}\s+(.*?)$', re.MULTILINE)
RE_CLEAN_THOUGHTS = re.compile(r'(<thought>.*?</thought>)|(```thought\n.*?```)|(tool_code\n.*?thought\n)', re.DOTALL | re.IGNORECASE)
RE_CLEAN_NAMES = re.compile(r'\[\d+;\s*Name:\s*.*?\]:\s*')

# --- ИНСТРУМЕНТЫ ---
TEXT_TOOLS = [types.Tool(google_search=types.GoogleSearch(), code_execution=types.ToolCodeExecution(), url_context=types.UrlContext())]
MEDIA_TOOLS = [types.Tool(google_search=types.GoogleSearch(), url_context=types.UrlContext())]

SAFETY_SETTINGS = [
    types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for c in (types.HarmCategory.HARM_CATEGORY_HARASSMENT, types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
              types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT)
]

DEFAULT_SYSTEM_PROMPT = """(System Note: Today is {current_time}.)
Ты работаешь через API Telegram. Используй ТОЛЬКО HTML теги.
Если ты используешь Thinking (мышление), ОБЯЗАТЕЛЬНО напиши итоговый ответ в конце."""

try:
    with open('system_prompt.md', 'r', encoding='utf-8') as f: SYSTEM_INSTRUCTION = f.read()
except FileNotFoundError:
    SYSTEM_INSTRUCTION = DEFAULT_SYSTEM_PROMPT

# --- MODEL MANAGER (CASCADE) ---
class ModelCascade:
    def __init__(self):
        self.models = {}
        for m in MODELS_CONFIG:
            self.models[m['id']] = {
                'config': m,
                'last_req': 0,
                'day_reqs': 0,
                'cooldown_until': 0,
                'reset_day': datetime.date.today()
            }
        self.lock = asyncio.Lock()

    async def get_best_model(self):
        async with self.lock:
            now = time.time()
            today = datetime.date.today()
            
            best_model_id = None
            min_wait = float('inf')
            
            for m_conf in MODELS_CONFIG:
                mid = m_conf['id']
                state = self.models[mid]
                
                if state['reset_day'] != today:
                    state['day_reqs'] = 0
                    state['reset_day'] = today
                
                if state['day_reqs'] >= m_conf['rpd']:
                    continue
                
                if now < state['cooldown_until']:
                    wait = state['cooldown_until'] - now
                    if wait < min_wait: min_wait = wait
                    continue
                
                interval = 60.0 / m_conf['rpm']
                time_passed = now - state['last_req']
                
                if time_passed >= interval:
                    return mid, 0
                else:
                    wait = interval - time_passed
                    if wait < min_wait: min_wait = wait

            return None, (min_wait if min_wait != float('inf') else 5.0)

    async def mark_success(self, mid):
        async with self.lock:
            self.models[mid]['last_req'] = time.time()
            self.models[mid]['day_reqs'] += 1
            logger.info(f"✅ Used {mid}. Daily: {self.models[mid]['day_reqs']}/{self.models[mid]['config']['rpd']}")

    async def mark_exhausted(self, mid):
        async with self.lock:
            self.models[mid]['cooldown_until'] = time.time() + 60.0
            logger.warning(f"⛔ {mid} exhausted/banned. Cooldown 60s.")

# ГЛОБАЛЬНЫЙ МЕНЕДЖЕР
CASCADE = None

# --- WORKER ---
class TypingWorker:
    def __init__(self, bot, chat_id):
        self.bot, self.chat_id, self.running, self.task = bot, chat_id, False, None
    async def _worker(self):
        while self.running:
            try:
                await self.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4.5)
            except Exception: break
    def start(self):
        self.running = True
        self.task = asyncio.create_task(self._worker())
    def stop(self):
        self.running = False
        if self.task: self.task.cancel()

# --- PERSISTENCE ---
class PostgresPersistence(BasePersistence):
    def __init__(self, database_url: str):
        super().__init__()
        self.db_pool = None
        self.dsn = database_url
        self._ensure_pool()

    def _ensure_pool(self):
        if self.db_pool and not self.db_pool.closed: return
        logger.info("Connecting to DB...")
        try:
            self.db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, dsn=self.dsn, 
                                                              keepalives=1, 
                                                              keepalives_idle=30, 
                                                              keepalives_interval=10, 
                                                              keepalives_count=5)
            self._initialize_db()
        except Exception as e:
            logger.error(f"DB Init Error: {e}")
            raise

    def _execute(self, query: str, params: tuple = None, fetch: str = None, retries=3):
        last_ex = None
        for attempt in range(retries):
            conn = None
            try:
                if not self.db_pool or self.db_pool.closed:
                    self._ensure_pool()
                
                conn = self.db_pool.getconn()
                
                if conn.status != extensions.STATUS_READY:
                    conn.rollback()
                
                try:
                    with conn.cursor() as c: c.execute("SELECT 1")
                except:
                    try: self.db_pool.putconn(conn, close=True)
                    except: pass
                    conn = None
                    self._ensure_pool() 
                    continue

                with conn.cursor() as cur:
                    cur.execute(query, params)
                    res = cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else True
                    conn.commit()
                    return res

            except Exception as e:
                logger.warning(f"DB Query Error ({attempt+1}): {e}")
                last_ex = e
                if conn:
                    try: conn.rollback()
                    except: pass
                    try: self.db_pool.putconn(conn, close=True)
                    except: pass
                    conn = None
                time.sleep(0.5)
            finally:
                if conn: 
                    try: self.db_pool.putconn(conn)
                    except: pass
        
        logger.error(f"DB Final Failure: {last_ex}")
        return None

    def _initialize_db(self): 
        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS persistence_data (key TEXT PRIMARY KEY, data BYTEA NOT NULL);")
            conn.commit()
        except: pass
        finally:
            if conn: self.db_pool.putconn(conn)

    def _get_pickled(self, key: str):
        res = self._execute("SELECT data FROM persistence_data WHERE key = %s;", (key,), fetch="one")
        return pickle.loads(res[0]) if res and res[0] else None
    def _set_pickled(self, key: str, data: object):
        self._execute("INSERT INTO persistence_data (key, data) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET data = %s;", (key, pickle.dumps(data), pickle.dumps(data)))
    
    async def get_bot_data(self): return {} 
    async def update_bot_data(self, data): pass
    async def get_chat_data(self):
        all_data = await asyncio.to_thread(self._execute, "SELECT key, data FROM persistence_data WHERE key LIKE 'chat_data_%';", fetch="all")
        chat_data = defaultdict(dict)
        if all_data:
            for k, d in all_data:
                try: chat_data[int(k.split('_')[-1])] = pickle.loads(d)
                except: pass
        return chat_data
    async def update_chat_data(self, chat_id, data): await asyncio.to_thread(self._set_pickled, f"chat_data_{chat_id}", data)
    async def drop_chat_data(self, chat_id): await asyncio.to_thread(self._execute, "DELETE FROM persistence_data WHERE key = %s;", (f"chat_data_{chat_id}",))
    async def refresh_chat_data(self, chat_id, chat_data):
        data = await asyncio.to_thread(self._get_pickled, f"chat_data_{chat_id}") or {}
        chat_data.update(data)
    async def get_user_data(self): return defaultdict(dict)
    async def update_user_data(self, user_id, data): pass
    async def drop_user_data(self, user_id): pass
    async def get_callback_data(self): return None
    async def update_callback_data(self, data): pass
    async def get_conversations(self, name): return {}
    async def update_conversation(self, name, key, new_state): pass
    async def refresh_bot_data(self, bot_data): pass
    async def refresh_user_data(self, user_id, user_data): pass
    async def flush(self): pass
    def close(self):
        if self.db_pool: self.db_pool.closeall()

# --- UTILS ---
def get_current_time_str(timezone="Europe/Moscow"):
    now = datetime.datetime.now(pytz.timezone(timezone))
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    return f"Сегодня {days[now.weekday()]}, {now.day} {months[now.month-1]} {now.year} года, время {now.strftime('%H:%M')} (MSK)."

def convert_markdown_to_html(text: str) -> str:
    if not text: return text
    code_blocks = {}
    def store_code(match):
        key = f"__CODE_{len(code_blocks)}__"
        content = html.escape(match.group(2) if match.lastindex == 2 else match.group(1))
        code_blocks[key] = f"<pre>{content}</pre>" if match.group(0).startswith("```") else f"<code>{content}</code>"
        return key
    text = RE_CODE_BLOCK.sub(store_code, text)
    text = RE_INLINE_CODE.sub(store_code, text)
    text = RE_BOLD.sub(r'<b>\1</b>', text)
    text = RE_ITALIC.sub(r'<i>\1</i>', text)
    text = RE_HEADER.sub(r'<b>\1</b>', text)
    for key, val in code_blocks.items(): text = text.replace(key, val)
    return text

def sanitize_and_balance_html(text: str) -> str:
    allowed = {'b', 'i', 'u', 's', 'code', 'pre', 'a', 'tg-spoiler'}
    stack = []
    for m in HTML_TAG_REGEX.finditer(text):
        tag = m.group(2).lower()
        closing = m.group(1) == '/'
        if tag == 'br': continue
        if not closing: stack.append(tag)
        else:
            if stack and stack[-1] == tag: stack.pop()
    for tag in reversed(stack): text += f"</{tag}>"
    return text

def html_safe_chunker(text: str, size=4096):
    chunks, stack = [], []
    while len(text) > size:
        split = text.rfind('\n', 0, size)
        if split == -1: split = size
        chunk, temp_stack = text[:split], list(stack)
        for m in HTML_TAG_REGEX.finditer(chunk):
            tag, closing = m.group(2).lower(), bool(m.group(1))
            if tag == 'br': continue
            stack.pop() if closing and stack and stack[-1] == tag else stack.append(tag) if not closing else None
        
        chunk += ''.join(f'</{t}>' for t in reversed(stack[len(temp_stack):]))
        chunks.append(chunk)
        text = ''.join(f'<{t}>' for t in stack) + text[split:].lstrip()
    chunks.append(text)
    return chunks

def ignore_if_processing(func):
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        if not update.effective_message: return
        key = f"{update.effective_chat.id}_{update.effective_message.message_id}"
        processing = context.application.bot_data.setdefault('processing_messages', set())
        if key in processing: return
        processing.add(key)
        try: await func(update, context, *args, **kwargs)
        finally: processing.discard(key)
    return wrapper

def part_to_dict(part):
    if part.text: return {'type': 'text', 'content': part.text}
    if part.file_data: return {'type': 'file', 'uri': part.file_data.file_uri, 'mime': part.file_data.mime_type, 'timestamp': time.time()}
    return {}

def dict_to_part(d):
    if not isinstance(d, dict): return None, False
    if d.get('type') == 'text': return types.Part(text=d.get('content', '')), False
    if d.get('type') == 'file':
        if time.time() - d.get('timestamp', 0) > MEDIA_CONTEXT_TTL_SECONDS: return None, True
        return types.Part(file_data=types.FileData(file_uri=d['uri'], mime_type=d['mime'])), False
    return None, False

def build_history(history, char_limit=MAX_CONTEXT_CHARS):
    valid, chars = [], 0
    for entry in reversed(history):
        if not entry.get("parts"): continue
        api_parts, text_len = [], 0
        prefix = f"[{entry.get('user_id', 'Unknown')}; Name: {entry.get('user_name', 'User')}]: " if entry['role'] == 'user' else ""
        for p in entry["parts"]:
            if p.get('type') == 'text':
                t = f"{prefix}{p.get('content', '')}" if entry['role'] == 'user' else p.get('content', '')
                api_parts.append(types.Part(text=t))
                text_len += len(t)
        if not api_parts: continue
        if chars + text_len > char_limit: break
        valid.append(types.Content(role=entry["role"], parts=api_parts))
        chars += text_len
    return valid[::-1]

async def upload_file(client, b, mime, name):
    logger.info(f"Upload: {name}")
    try:
        up = await client.aio.files.upload(file=io.BytesIO(b), config=types.UploadFileConfig(mime_type=mime, display_name=name))
        for _ in range(15):
            f = await client.aio.files.get(name=up.name)
            if f.state.name == 'ACTIVE': return types.Part(file_data=types.FileData(file_uri=f.uri, mime_type=mime))
            if f.state.name == 'FAILED': raise IOError("Google File Error")
            await asyncio.sleep(2)
        raise asyncio.TimeoutError("Upload Timeout")
    except Exception as e:
        logger.error(f"Upload Fail: {e}")
        raise IOError(f"Ошибка загрузки файла {name} (Client Error: {e})")

# --- ГЕНЕРАЦИЯ ---
async def generate_with_cascade(client, contents, context, tools_override=None):
    sys_prompt = SYSTEM_INSTRUCTION
    if "{current_time}" in sys_prompt:
        sys_prompt = sys_prompt.format(current_time=get_current_time_str())

    while True:
        model_id, wait_time = await CASCADE.get_best_model()
        
        if model_id is None:
            logger.info(f"🚦 All models busy. Waiting {wait_time:.2f}s...")
            await asyncio.sleep(wait_time)
            continue
        
        if wait_time > 0:
             logger.info(f"⏳ Waiting for {model_id}: {wait_time:.2f}s")
             await asyncio.sleep(wait_time)

        # ИСПРАВЛЕНО: Убрали 'budget_token_limit', т.к. SDK на сервере старый
        gen_config_args = {
            "safety_settings": SAFETY_SETTINGS,
            "tools": tools_override,
            "system_instruction": types.Content(parts=[types.Part(text=sys_prompt)]),
            "temperature": 1.0,
            "thinking_config": types.ThinkingConfig(include_thoughts=True)
        }

        logger.info(f"🚀 Attempting: {model_id}")

        try:
            response = await client.aio.models.generate_content(
                model=model_id, 
                contents=contents, 
                config=types.GenerateContentConfig(**gen_config_args)
            )
            
            await CASCADE.mark_success(model_id)
            return response, model_id
            
        except (genai_errors.APIError, ValueError) as e:
            err_str = str(e).lower()
            if "resource_exhausted" in err_str or "429" in err_str:
                await CASCADE.mark_exhausted(model_id)
                continue
            
            return f"❌ API Error ({model_id}): {html.escape(str(e))}", model_id
        except Exception as e:
             return f"❌ Error ({model_id}): {html.escape(str(e))}", model_id

def format_response(response, model_name_id):
    try:
        model_pretty = next((m['name'] for m in MODELS_CONFIG if m['id'] == model_name_id), model_name_id)
        
        if isinstance(response, str): return response 
        if not response: return "Пустой контент."

        if not response.candidates: return "Ответ заблокирован фильтрами."
        cand = response.candidates[0]
        if cand.finish_reason.name == "SAFETY": return "Скрыто фильтром безопасности."
        
        if not cand.content or not cand.content.parts: return "Пустой контент."

        text_parts = []
        thoughts_parts = []
        
        for p in cand.content.parts:
            if hasattr(p, 'thought') and p.thought: 
                thoughts_parts.append(p.thought)
            if p.text: 
                text_parts.append(p.text)
            
        text = "".join(text_parts)
        text = RE_CLEAN_NAMES.sub('', text)
        
        # Если текста нет, но были мысли - берем их (Спасение)
        if not text.strip() and thoughts_parts:
            text = "\n\n".join(thoughts_parts)
        
        if not text.strip(): return "Пустой контент."

        html_text = convert_markdown_to_html(text.strip())
        final_text = sanitize_and_balance_html(html_text)
        
        final_text += f"\n\n🤖 <i>Model: {model_pretty}</i>"
        return final_text
        
    except Exception as e:
        return f"Format Error: {e}"

async def send_smart(msg, text, hint=False):
    text = re.sub(r'<br\s*/?>', '\n', text)
    chunks = html_safe_chunker(text)
    
    if hint and "❌" not in text and "⏳" not in text:
        h = "\n\n<i>💡 Ответьте на это сообщение для вопроса по файлу.</i>"
        chunks[-1] += h if len(chunks[-1]) + len(h) <= 4096 else ""
    
    sent = None
    try:
        for i, ch in enumerate(chunks):
            sent = await msg.reply_html(ch) if i == 0 else await msg.get_bot().send_message(msg.chat_id, ch, parse_mode=ParseMode.HTML)
    except BadRequest:
        plain = re.sub(r'<[^>]*>', '', text)
        for ch in [plain[i:i+4096] for i in range(0, len(plain), 4096)]:
            sent = await msg.reply_text(ch)
    return sent

async def process_request(chat_id, bot_data, application):
    group_data = bot_data.get('media_buffer', {}).pop(chat_id, None)
    if not group_data: return

    parts = group_data['parts']
    msg = group_data['msg']
    
    context_data = await application.persistence.get_chat_data()
    chat_data = context_data.get(chat_id, {})
    
    client = application.bot_data['gemini_client']
    
    typer = TypingWorker(application.bot, chat_id)
    typer.start()
    
    try:
        txt = next((p.text for p in parts if p.text), None)
        if txt and DATE_TIME_REGEX.search(txt):
            await send_smart(msg, get_current_time_str())
            return

        is_media_request = any(p.file_data for p in parts)
        dynamic_limit = 300000 
        
        if is_media_request: history = [] 
        else: history = build_history(chat_data.get("history", []), char_limit=dynamic_limit)
        
        user_name = msg.from_user.first_name
        
        parts_final = [p for p in parts if p.file_data]
        prompt_txt = next((p.text for p in parts if p.text), "")
        final_prompt = f"[{msg.from_user.id}; Name: {user_name}]: {prompt_txt}"
        
        if not is_media_request and not URL_REGEX.search(prompt_txt):
            final_prompt = f"Используй Grounding with Google Search. Актуальная дата: {get_current_time_str()}.\n" + final_prompt
        else:
             final_prompt = f"Date: {get_current_time_str()}\n" + final_prompt

        parts_final.append(types.Part(text=final_prompt))
        
        current_tools = MEDIA_TOOLS if is_media_request else TEXT_TOOLS
        
        res_obj, used_model = await generate_with_cascade(client, history + [types.Content(parts=parts_final, role="user")], application, tools_override=current_tools)
        
        reply = format_response(res_obj, used_model)
        
        sent = await send_smart(msg, reply, hint=is_media_request)
        
        if sent and "❌" not in reply:
            hist_item = {"role": "user", "parts": [part_to_dict(p) for p in parts], "user_id": msg.from_user.id, "user_name": user_name}
            chat_data.setdefault("history", []).append(hist_item)
            
            clean_reply = reply.rsplit('\n\n🤖', 1)[0]
            bot_item = {"role": "model", "parts": [{'type': 'text', 'content': clean_reply[:MAX_HISTORY_RESPONSE_LEN]}]}
            chat_data["history"].append(bot_item)
            
            if len(chat_data["history"]) > MAX_HISTORY_ITEMS:
                chat_data["history"] = chat_data["history"][-MAX_HISTORY_ITEMS:]

            rmap = chat_data.setdefault('reply_map', {})
            rmap[sent.message_id] = msg.message_id
            if len(rmap) > MAX_HISTORY_ITEMS * 2: 
                for k in list(rmap.keys())[:-MAX_HISTORY_ITEMS]: del rmap[k]

            if is_media_request:
                m_part = next((p for p in parts if p.file_data), None)
                if m_part:
                    m_store = application.bot_data.setdefault('media_contexts', {}).setdefault(chat_id, OrderedDict())
                    m_store[msg.message_id] = part_to_dict(m_part)
                    if len(m_store) > MAX_MEDIA_CONTEXTS: m_store.popitem(last=False)
            
            await application.persistence.update_chat_data(chat_id, chat_data)

    except Exception as e:
        logger.error(f"Proc Error: {e}", exc_info=True)
        await msg.reply_text("❌ Внутренняя ошибка.")
    finally:
        typer.stop()

# --- HANDLERS ---
@ignore_if_processing
async def universal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    context.chat_data['id'] = msg.chat_id
    
    parts = []
    client = context.bot_data['gemini_client']
    text = msg.caption or msg.text or ""

    if msg.media_group_id:
        buffer = context.bot_data.setdefault('media_buffer', {})
        if msg.chat_id in buffer:
            if buffer[msg.chat_id]['task']:
                buffer[msg.chat_id]['task'].cancel()
        else:
            buffer[msg.chat_id] = {'parts': [], 'msg': msg, 'task': None}
        
        media = msg.audio or msg.voice or msg.video or msg.video_note or (msg.photo[-1] if msg.photo else None) or msg.document
        if media:
            try:
                f = await media.get_file()
                b = await f.download_as_bytearray()
                mime = 'image/jpeg' if msg.photo else 'audio/ogg' if msg.voice else 'video/mp4' if msg.video_note else getattr(media, 'mime_type', 'application/octet-stream')
                part = await upload_file(client, b, mime, getattr(media, 'file_name', 'file'))
                buffer[msg.chat_id]['parts'].append(part)
            except Exception as e:
                logger.error(f"Media Buffer Error: {e}")

        if text:
            buffer[msg.chat_id]['parts'].append(types.Part(text=text))
            buffer[msg.chat_id]['msg'] = msg 

        async def delayed_processing():
            await asyncio.sleep(2.0)
            await process_request(msg.chat_id, context.bot_data, context.application)

        buffer[msg.chat_id]['task'] = asyncio.create_task(delayed_processing())
        return

    media = msg.audio or msg.voice or msg.video or msg.video_note or (msg.photo[-1] if msg.photo else None) or msg.document
    if media:
        if media.file_size > TELEGRAM_FILE_LIMIT_MB * 1024 * 1024:
            await msg.reply_text("📂 Файл >20MB. Читаю только текст.")
        else:
            try:
                st = await msg.reply_text("📥")
                f = await media.get_file()
                b = await f.download_as_bytearray()
                mime = 'image/jpeg' if msg.photo else 'audio/ogg' if msg.voice else 'video/mp4' if msg.video_note else getattr(media, 'mime_type', 'application/octet-stream')
                parts.append(await upload_file(client, b, mime, getattr(media, 'file_name', 'file')))
                await st.delete()
            except Exception as e:
                await msg.reply_text(f"❌ Загрузка: {e}")
                return
    
    yt = YOUTUBE_REGEX.search(text)
    if not media and yt:
        parts.append(types.Part(file_data=types.FileData(mime_type="video/youtube", file_uri=yt.group(0))))
        text = text.replace(yt.group(0), '').strip()

    if not media and msg.reply_to_message:
        orig = context.chat_data.get('reply_map', {}).get(msg.reply_to_message.message_id)
        if orig:
            ctx = context.application.bot_data.get('media_contexts', {}).get(msg.chat_id, {}).get(orig)
            if ctx:
                p, stale = dict_to_part(ctx)
                if p: parts.append(p)

    if text: parts.append(types.Part(text=text))
    
    if parts:
        context.bot_data.setdefault('media_buffer', {})[msg.chat_id] = {'parts': parts, 'msg': msg}
        await process_request(msg.chat_id, context.bot_data, context.application)

async def util_cmd(update, context, prompt):
    msg = update.message
    if not msg.reply_to_message: return await msg.reply_text("⚠️ Ответьте на сообщение с файлом.")
    
    reply = msg.reply_to_message
    media = reply.audio or reply.voice or reply.video or reply.video_note or (reply.photo[-1] if reply.photo else None) or reply.document
    
    parts = []
    client = context.bot_data['gemini_client']
    
    if media:
         if media.file_size > TELEGRAM_FILE_LIMIT_MB * 1024 * 1024: return await msg.reply_text("❌ Файл велик.")
         f = await media.get_file()
         b = await f.download_as_bytearray()
         mime = 'image/jpeg' if reply.photo else 'audio/ogg' if reply.voice else 'video/mp4' if reply.video_note else getattr(media, 'mime_type', 'application/octet-stream')
         parts.append(await upload_file(client, b, mime, 'temp_util_file'))
    elif reply.text and YOUTUBE_REGEX.search(reply.text):
         parts.append(types.Part(file_data=types.FileData(mime_type="video/youtube", file_uri=YOUTUBE_REGEX.search(reply.text).group(0))))
    elif context.chat_data.get('reply_map', {}).get(reply.message_id):
        orig = context.chat_data['reply_map'][reply.message_id]
        ctx = context.application.bot_data.get('media_contexts', {}).get(msg.chat_id, {}).get(orig)
        if ctx:
            p, _ = dict_to_part(ctx)
            if p: parts.append(p)
            
    if not parts: return await msg.reply_text("❌ Нет медиа.")
    parts.append(types.Part(text=prompt))
    
    context.bot_data.setdefault('media_buffer', {})[msg.chat_id] = {'parts': parts, 'msg': msg}
    await process_request(msg.chat_id, context.bot_data, context.application)

@ignore_if_processing
async def start_c(u, c): await u.message.reply_html("👋 <b>Привет! Я Женя.</b>\nКидай файлы, фото, аудио или просто пиши!")
@ignore_if_processing
async def clear_c(u, c): 
    c.chat_data.clear()
    c.application.bot_data.get('media_contexts', {}).pop(u.effective_chat.id, None)
    await u.message.reply_text("🧹 Память очищена.")
@ignore_if_processing
async def model_c(u, c): 
    await u.message.reply_html(f"ℹ️ <b>Авто-режим активен.</b>\nЯ использую лучшую доступную модель (Flash или Lite).")

# --- MAIN ---
async def main():
    global CASCADE
    CASCADE = ModelCascade()

    pers = PostgresPersistence(DATABASE_URL)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(pers).build()

    app.add_handler(CommandHandler("start", start_c))
    app.add_handler(CommandHandler("clear", clear_c))
    app.add_handler(CommandHandler("model", model_c))
    app.add_handler(CommandHandler("summarize", lambda u, c: util_cmd(u, c, "Сделай подробный конспект (summary) этого материала.")))
    app.add_handler(CommandHandler("transcript", lambda u, c: util_cmd(u, c, "Transcribe this audio file verbatim. Output ONLY the raw text, no introductory words.")))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, universal_handler))

    await app.initialize()
    app.bot_data['gemini_client'] = genai.Client(api_key=GOOGLE_API_KEY)
    
    if ADMIN_ID: 
        try: await app.bot.send_message(ADMIN_ID, "🟢 Bot Started (v67 - Fixed Pydantic Error)") 
        except: pass

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(s, stop.set)
    
    webhook_url = f"{WEBHOOK_HOST.rstrip('/')}/{GEMINI_WEBHOOK_PATH.strip('/')}"
    await app.bot.set_webhook(url=webhook_url, secret_token=TELEGRAM_SECRET_TOKEN)
    
    server = aiohttp.web.Application()
    async def wh(r):
        token = r.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if token != TELEGRAM_SECRET_TOKEN:
            return aiohttp.web.Response(status=403, text="Forbidden")
        try:
            update_data = await r.json()
            asyncio.create_task(process_update_safe(app, update_data))
            return aiohttp.web.Response(text='OK')
        except: return aiohttp.web.Response(status=500)

    async def process_update_safe(application, data):
        try: await application.process_update(Update.de_json(data, application.bot))
        except Exception as e: logger.error(f"Bg Update Error: {e}")

    server.router.add_post(f"/{GEMINI_WEBHOOK_PATH.strip('/')}", wh)
    server.router.add_get('/', lambda r: aiohttp.web.Response(text="Running"))
    
    runner = aiohttp.web.AppRunner(server)
    await runner.setup()
    await aiohttp.web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 10000))).start()
    
    logger.info("Ready.")
    await stop.wait()
    
    await runner.cleanup()
    pers.close()

if __name__ == '__main__':
    asyncio.run(main())
