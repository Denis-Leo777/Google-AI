# Версия 75 (Beautiful & Clean: No Placeholders, No Raw Thoughts)

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

# --- МОДЕЛИ ---
MODELS_CONFIG = [
    {'id': 'gemini-2.5-flash-preview-09-2025', 'rpm': 5, 'rpd': 20, 'name': 'Gemini 2.5 Flash'},
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

# --- ИНСТРУМЕНТЫ ---
# Включаем только поиск. Code Execution отключен для стабильности.
TEXT_TOOLS = [types.Tool(google_search=types.GoogleSearch(), url_context=types.UrlContext())]
MEDIA_TOOLS = [types.Tool(google_search=types.GoogleSearch(), url_context=types.UrlContext())]

SAFETY_SETTINGS = [
    types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for c in (types.HarmCategory.HARM_CATEGORY_HARASSMENT, types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
              types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT)
]

DEFAULT_SYSTEM_PROMPT = """(System Note: Today is {current_time}.)
Ты работаешь через API Telegram.
Твоя задача — давать полные, красивые и полезные ответы.
Используй HTML теги (<b>bold</b>, <i>italic</i>, <code>code</code>) для оформления.
Если используешь режим мышления (Thinking), НИКОГДА не выводи мысли пользователю. Выводи только финальный результат."""

try:
    with open('system_prompt.md', 'r', encoding='utf-8') as f: SYSTEM_INSTRUCTION = f.read()
except FileNotFoundError:
    SYSTEM_INSTRUCTION = DEFAULT_SYSTEM_PROMPT

# --- MODEL MANAGER ---
class ModelCascade:
    def __init__(self):
        self.models = {}
        for m in MODELS_CONFIG:
            self.models[m['id']] = {'config': m, 'last_req': 0, 'day_reqs': 0, 'cooldown_until': 0, 'reset_day': datetime.date.today()}
        self.lock = asyncio.Lock()

    async def get_best_model(self):
        async with self.lock:
            now, today = time.time(), datetime.date.today()
            for m_conf in MODELS_CONFIG:
                mid = m_conf['id']
                state = self.models[mid]
                if state['reset_day'] != today: state['day_reqs'], state['reset_day'] = 0, today
                if state['day_reqs'] >= m_conf['rpd']: continue
                if now < state['cooldown_until']: continue
                
                interval = 60.0 / m_conf['rpm']
                passed = now - state['last_req']
                if passed >= interval: return mid, 0
                
                # Если модель хорошая, но надо подождать пару секунд - ждем
                wait = interval - passed
                if wait < 10: return mid, wait
                
            return None, 5.0

    async def mark_success(self, mid):
        async with self.lock:
            self.models[mid]['last_req'] = time.time()
            self.models[mid]['day_reqs'] += 1
            logger.info(f"✅ Used {mid}. Daily: {self.models[mid]['day_reqs']}/{self.models[mid]['config']['rpd']}")

    async def mark_exhausted(self, mid):
        async with self.lock:
            self.models[mid]['cooldown_until'] = time.time() + 60.0
            logger.warning(f"⛔ {mid} exhausted. Cooldown 60s.")

CASCADE = None

# --- WORKER & HELPERS ---
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

def get_current_time_str(timezone="Europe/Moscow"):
    now = datetime.datetime.now(pytz.timezone(timezone))
    return f"Сегодня {now.strftime('%d.%m.%Y')}, {now.strftime('%H:%M')} (MSK)."

# --- NEW BEAUTIFUL FORMATTER (NO PLACEHOLDERS) ---
def clean_and_format_text(text: str) -> str:
    """
    Превращает Markdown в HTML без использования заглушек CODE_0.
    1. Экранирует весь текст.
    2. Восстанавливает блоки кода <pre>.
    3. Применяет форматирование <b>, <i>, <code>.
    """
    if not text: return ""

    # 1. Сначала экранируем всё, чтобы защитить HTML Telegram
    safe_text = html.escape(text)

    # 2. Обрабатываем блоки кода ```code``` -> <pre>code</pre>
    # Мы ищем экранированные ``` (которые стали безопасными строками) и превращаем их в теги
    safe_text = re.sub(r'```(.*?)```', r'<pre>\1</pre>', safe_text, flags=re.DOTALL)

    # 3. Обрабатываем инлайн код `code` -> <code>code</code>
    safe_text = re.sub(r'`([^`]+)`', r'<code>\1</code>', safe_text)

    # 4. Жирный **text** -> <b>text</b>
    safe_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', safe_text)
    
    # 5. Курсив *text* -> <i>text</i> (аккуратно, чтобы не задеть списки)
    safe_text = re.sub(r'(?<!\*)\*(?!\s)(.*?)(?<!\s)\*(?!\*)', r'<i>\1</i>', safe_text)

    # 6. Заголовки # Header -> <b>Header</b>
    safe_text = re.sub(r'^#{1,6}\s+(.*?)$', r'<b>\1</b>', safe_text, flags=re.MULTILINE)

    return safe_text

def balance_html_tags(text: str) -> str:
    """Гарантирует, что все открытые теги закрыты."""
    stack = []
    # Находим все теги
    tags = re.findall(r'<(/?)(b|i|u|s|code|pre|a)(?:\s[^>]*)?>', text)
    
    for closing, tag in tags:
        if not closing:
            stack.append(tag)
        else:
            if stack and stack[-1] == tag:
                stack.pop()
    
    # Закрываем всё, что осталось в стеке
    for tag in reversed(stack):
        text += f"</{tag}>"
    return text

def html_safe_chunker(text: str, size=4096):
    chunks = []
    while len(text) > size:
        # Ищем перенос строки
        split_idx = text.rfind('\n', 0, size)
        if split_idx == -1: split_idx = size
        
        chunk = text[:split_idx]
        # Балансируем теги в куске, если режем посередине
        chunk = balance_html_tags(chunk)
        chunks.append(chunk)
        
        text = text[split_idx:].lstrip()
    
    if text:
        chunks.append(balance_html_tags(text))
    return chunks

# --- DATABASE ---
class PostgresPersistence(BasePersistence):
    def __init__(self, database_url: str):
        super().__init__()
        self.db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, dsn=database_url, keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5)
        self._init_db()

    def _init_db(self):
        with self.db_pool.getconn() as conn:
            with conn.cursor() as cur: cur.execute("CREATE TABLE IF NOT EXISTS persistence_data (key TEXT PRIMARY KEY, data BYTEA NOT NULL);")
            conn.commit()
            self.db_pool.putconn(conn)

    def _execute(self, sql, params=None, fetch=None):
        conn = self.db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                res = cur.fetchone() if fetch == 'one' else cur.fetchall() if fetch == 'all' else None
                conn.commit()
                return res
        except: conn.rollback(); raise
        finally: self.db_pool.putconn(conn)

    async def get_chat_data(self):
        data = await asyncio.to_thread(self._execute, "SELECT key, data FROM persistence_data WHERE key LIKE 'chat_data_%';", fetch='all')
        return {int(k.split('_')[-1]): pickle.loads(v) for k, v in data} if data else defaultdict(dict)

    async def update_chat_data(self, chat_id, data):
        await asyncio.to_thread(self._execute, "INSERT INTO persistence_data (key, data) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET data = %s;", (f"chat_data_{chat_id}", pickle.dumps(data), pickle.dumps(data)))

    async def get_bot_data(self): return {}
    async def update_bot_data(self, data): pass
    async def get_user_data(self): return defaultdict(dict)
    async def update_user_data(self, uid, data): pass
    async def drop_chat_data(self, cid): pass
    async def drop_user_data(self, uid): pass
    async def get_callback_data(self): return None
    async def update_callback_data(self, data): pass
    async def get_conversations(self, name): return {}
    async def update_conversation(self, name, key, new_state): pass
    async def refresh_bot_data(self, bot_data): pass
    async def refresh_user_data(self, user_id, user_data): pass
    async def refresh_chat_data(self, chat_id, chat_data): pass
    async def flush(self): pass

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
        return types.Part(file_data=types.FileData(file_uri=up.uri, mime_type=mime))
    except Exception as e:
        logger.error(f"Upload Fail: {e}")
        raise IOError(f"Upload Error: {e}")

# --- LOGIC ---
async def generate_response(client, contents, tools=None):
    sys_prompt = SYSTEM_INSTRUCTION.format(current_time=get_current_time_str())
    
    while True:
        model_id, wait = await CASCADE.get_best_model()
        if model_id is None:
            await asyncio.sleep(wait)
            continue
        if wait > 0: await asyncio.sleep(wait)

        logger.info(f"🚀 Attempting: {model_id}")
        try:
            # ВКЛЮЧАЕМ THINKING, но фильтруем результат
            config = types.GenerateContentConfig(
                safety_settings=SAFETY_SETTINGS,
                tools=tools,
                system_instruction=types.Content(parts=[types.Part(text=sys_prompt)]),
                temperature=1.0,
                thinking_config=types.ThinkingConfig(include_thoughts=True)
            )
            response = await client.aio.models.generate_content(model=model_id, contents=contents, config=config)
            await CASCADE.mark_success(model_id)
            return response, model_id
        except Exception as e:
            if "resource_exhausted" in str(e).lower() or "429" in str(e):
                await CASCADE.mark_exhausted(model_id)
                continue
            return f"Error: {e}", model_id

def format_clean_response(response, model_id):
    if isinstance(response, str): return response
    if not response.candidates: return "Ответ заблокирован."
    cand = response.candidates[0]
    
    if cand.finish_reason.name == "SAFETY": return "⛔ Ответ скрыт фильтрами."

    text_parts = []
    thoughts_parts = []
    
    if cand.content and cand.content.parts:
        for p in cand.content.parts:
            # 1. Текст берем всегда
            if p.text: text_parts.append(p.text)
            # 2. Мысли сохраняем, но не показываем (пока не прижмет)
            try:
                if hasattr(p, 'thought') and p.thought: thoughts_parts.append(p.thought)
            except: pass

    # Сборка
    final_text = "".join(text_parts).strip()
    
    # СПАСЕНИЕ: Если текста нет вообще, но модель думала
    if not final_text and thoughts_parts:
        # Превращаем мысли в текст, но без пометок "Мысли"
        final_text = "\n\n".join(thoughts_parts)
    
    if not final_text: return "Пустой контент."

    # Форматирование
    html_text = clean_and_format_text(final_text)
    html_text = balance_html_tags(html_text)
    
    # Подпись модели (неброская)
    model_name = next((m['name'] for m in MODELS_CONFIG if m['id'] == model_id), model_id)
    html_text += f"\n\n🤖 <i>{model_name}</i>"
    
    return html_text

async def send_reply(msg, text):
    chunks = html_safe_chunker(text)
    sent = None
    try:
        for i, ch in enumerate(chunks):
            sent = await msg.reply_html(ch) if i == 0 else await msg.get_bot().send_message(msg.chat_id, ch, parse_mode=ParseMode.HTML)
    except BadRequest:
        # Fallback на текст
        for ch in [text[i:i+4096] for i in range(0, len(text), 4096)]:
            sent = await msg.reply_text(ch)
    return sent

async def process_request(chat_id, bot_data, application):
    data = bot_data.get('media_buffer', {}).pop(chat_id, None)
    if not data: return
    
    # БЛОКИРОВКА ДУБЛЕЙ
    processing = bot_data.setdefault('processing_locks', set())
    if chat_id in processing: return # Уже обрабатываем
    processing.add(chat_id)

    try:
        parts, msg = data['parts'], data['msg']
        client = application.bot_data['gemini_client']
        
        # Индикатор
        typer = TypingWorker(application.bot, chat_id)
        typer.start()

        # История
        chat_data = await application.persistence.get_chat_data()
        c_data = chat_data.get(chat_id, {})
        
        is_media = any(p.file_data for p in parts)
        history = [] if is_media else build_history(c_data.get("history", []))
        
        # Промпт
        prompt_txt = next((p.text for p in parts if p.text), "")
        user_part = f"[{msg.from_user.id} {msg.from_user.first_name}]: {prompt_txt}"
        
        if not is_media and not URL_REGEX.search(prompt_txt):
            user_part = f"Используй Grounding. Дата: {get_current_time_str()}.\n" + user_part
        
        parts_final = [p for p in parts if p.file_data] + [types.Part(text=user_part)]
        
        # Генерация
        res, model = await generate_response(client, history + [types.Content(role="user", parts=parts_final)], tools=MEDIA_TOOLS if is_media else TEXT_TOOLS)
        reply = format_clean_response(res, model)
        
        sent = await send_reply(msg, reply)
        
        # Сохранение
        if sent and "Error" not in reply:
            hist_u = {"role": "user", "parts": [part_to_dict(p) for p in parts], "user_id": msg.from_user.id}
            c_data.setdefault("history", []).append(hist_u)
            
            clean_reply = reply.rsplit('\n\n🤖', 1)[0]
            hist_b = {"role": "model", "parts": [{'type': 'text', 'content': clean_reply[:4000]}]}
            c_data["history"].append(hist_b)
            
            if len(c_data["history"]) > 60: c_data["history"] = c_data["history"][-60:]
            
            rmap = c_data.setdefault('reply_map', {})
            rmap[sent.message_id] = msg.message_id
            if len(rmap) > 100: del rmap[list(rmap.keys())[0]]
            
            if is_media:
                m_store = application.bot_data.setdefault('media_contexts', {}).setdefault(chat_id, OrderedDict())
                m_store[msg.message_id] = part_to_dict(parts[0]) # Сохраняем первый файл
                if len(m_store) > 20: m_store.popitem(last=False)

            await application.persistence.update_chat_data(chat_id, c_data)

    except Exception as e:
        logger.error(f"Err: {e}")
        await msg.reply_text("❌ Ошибка.")
    finally:
        typer.stop()
        processing.discard(chat_id)

def ignore_if_processing(func):
    return func # Логика перенесена внутрь process_request

async def universal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    
    parts = []
    client = context.bot_data['gemini_client']
    text = msg.caption or msg.text or ""

    # 1. Media Group / Single Media
    media = msg.audio or msg.voice or msg.video or msg.video_note or (msg.photo[-1] if msg.photo else None) or msg.document
    
    # Буфер
    buffer = context.bot_data.setdefault('media_buffer', {})
    
    if msg.media_group_id:
        if msg.chat_id in buffer and buffer[msg.chat_id]['task']:
            buffer[msg.chat_id]['task'].cancel()
        else:
            buffer[msg.chat_id] = {'parts': [], 'msg': msg, 'task': None}
    else:
        # Для одиночных сообщений тоже используем буфер для унификации
        if msg.chat_id in buffer and buffer[msg.chat_id]['task']:
             buffer[msg.chat_id]['task'].cancel()
        buffer[msg.chat_id] = {'parts': [], 'msg': msg, 'task': None}

    if media:
        try:
            f = await media.get_file()
            b = await f.download_as_bytearray()
            mime = 'image/jpeg' if msg.photo else 'audio/ogg' if msg.voice else getattr(media, 'mime_type', 'application/octet-stream')
            part = await upload_file(client, b, mime, 'file')
            buffer[msg.chat_id]['parts'].append(part)
        except: pass
    
    if text:
        buffer[msg.chat_id]['parts'].append(types.Part(text=text))
        # Если текст есть, обновляем ссылку на сообщение (чтобы отвечать на него)
        buffer[msg.chat_id]['msg'] = msg

    # Запуск таймера
    async def delayed():
        await asyncio.sleep(2.5) # Ждем чуть дольше для надежности
        await process_request(msg.chat_id, context.bot_data, context.application)

    buffer[msg.chat_id]['task'] = asyncio.create_task(delayed())

async def util_cmd(update, context, prompt):
    msg = update.message
    if not msg.reply_to_message: return await msg.reply_text("⚠️ Ответьте на сообщение с файлом.")
    
    # Эмуляция входящего сообщения для буфера
    # Упрощаем: просто кидаем в тот же буфер с принудительным текстом
    buffer = context.bot_data.setdefault('media_buffer', {})
    if msg.chat_id in buffer and buffer[msg.chat_id]['task']: buffer[msg.chat_id]['task'].cancel()
    
    # Достаем файл из реплая
    reply = msg.reply_to_message
    parts = []
    client = context.bot_data['gemini_client']
    media = reply.audio or reply.voice or reply.video or reply.video_note or (reply.photo[-1] if reply.photo else None) or reply.document
    
    if media:
        try:
            f = await media.get_file()
            b = await f.download_as_bytearray()
            mime = 'image/jpeg' if reply.photo else getattr(media, 'mime_type', 'application/octet-stream')
            parts.append(await upload_file(client, b, mime, 'util_file'))
        except: pass
    
    parts.append(types.Part(text=prompt))
    buffer[msg.chat_id] = {'parts': parts, 'msg': msg, 'task': None}
    
    # Запускаем сразу
    await process_request(msg.chat_id, context.bot_data, context.application)

async def start_c(u, c): await u.message.reply_html("👋 <b>Привет!</b> Я готов.")
async def clear_c(u, c): 
    c.application.persistence.drop_chat_data(u.effective_chat.id)
    await u.message.reply_text("🧹 Память очищена.")
async def model_c(u, c): await u.message.reply_html(f"ℹ️ Использую: <b>{DEFAULT_MODEL}</b>")

# --- MAIN ---
async def main():
    global CASCADE
    CASCADE = ModelCascade()

    pers = PostgresPersistence(DATABASE_URL)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(pers).build()

    app.add_handler(CommandHandler("start", start_c))
    app.add_handler(CommandHandler("clear", clear_c))
    app.add_handler(CommandHandler("model", model_c))
    app.add_handler(CommandHandler("summarize", lambda u, c: util_cmd(u, c, "Сделай подробный конспект.")))
    app.add_handler(CommandHandler("transcript", lambda u, c: util_cmd(u, c, "Transcribe audio verbatim.")))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, universal_handler))

    await app.initialize()
    app.bot_data['gemini_client'] = genai.Client(api_key=GOOGLE_API_KEY)
    
    if ADMIN_ID: 
        try: await app.bot.send_message(ADMIN_ID, "🟢 Bot Started (v75 - Clean & Beautiful)") 
        except: pass

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(s, stop.set)
    
    webhook_url = f"{WEBHOOK_HOST.rstrip('/')}/{GEMINI_WEBHOOK_PATH.strip('/')}"
    await app.bot.set_webhook(url=webhook_url, secret_token=TELEGRAM_SECRET_TOKEN)
    
    server = aiohttp.web.Application()
    async def wh(r):
        token = r.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if token != TELEGRAM_SECRET_TOKEN: return aiohttp.web.Response(status=403, text="Forbidden")
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
