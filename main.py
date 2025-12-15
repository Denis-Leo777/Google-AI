# Версия 80 (Golden Release: Fixed Imports for PTB v20+)

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

# --- ИСПРАВЛЕННЫЕ ИМПОРТЫ ДЛЯ PTB v20+ ---
from telegram import Update, Message
from telegram.constants import ChatAction, ParseMode  # Константы теперь живут здесь
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, BasePersistence
from telegram.error import BadRequest

from google import genai
from google.genai import types

# --- КОНФИГУРАЦИЯ ---
# Настройка логов
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger('aiohttp.access').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING) # Глушим логи httpx, который использует PTB

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_SECRET_TOKEN = os.getenv('TELEGRAM_SECRET_TOKEN', 'secret-token-replace-me') 
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')
ADMIN_ID = os.getenv('ADMIN_ID')

if not all([TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, WEBHOOK_HOST, GEMINI_WEBHOOK_PATH]):
    logger.critical("❌ Critical: Не все переменные окружения заданы!")
    exit(1)

# --- МОДЕЛИ ---
# Используем новейшие экспериментальные модели
MODELS_CONFIG = [
    {'id': 'gemini-2.0-flash-thinking-exp-01-21', 'rpm': 10, 'rpd': 1500, 'name': 'Gemini 2.0 Thinking'},
    {'id': 'gemini-2.0-flash', 'rpm': 15, 'rpd': 2000, 'name': 'Gemini 2.0 Flash'},
]

# --- ЛИМИТЫ ---
MAX_CONTEXT_CHARS = 100000 
MEDIA_CONTEXT_TTL_SECONDS = 47 * 3600

# --- ИНСТРУМЕНТЫ ---
TEXT_TOOLS = [types.Tool(google_search=types.GoogleSearch(), url_context=types.UrlContext())]
MEDIA_TOOLS = [types.Tool(google_search=types.GoogleSearch(), url_context=types.UrlContext())]

SAFETY_SETTINGS = [
    types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for c in (types.HarmCategory.HARM_CATEGORY_HARASSMENT, types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
              types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT)
]

# Системный промпт
DEFAULT_SYSTEM_PROMPT = """(System Note: Today is {current_time}.)
Ты — интеллектуальный помощник в Telegram.
Твоя задача — давать точные, глубокие и красиво оформленные ответы.

ВАЖНО ПРО ФОРМАТИРОВАНИЕ:
- Ты используешь HTML теги.
- Всегда оборачивай код в блоки: <pre language="python">...</pre> или ```python ... ```.
- Используй <b>Bold</b> для акцентов.
- Используй <blockquote>Цитата</blockquote> для важных выделений.

ВАЖНО ПРО МЫШЛЕНИЕ (THINKING):
- Ты думаешь перед ответом, но ПОЛЬЗОВАТЕЛЬ НЕ ДОЛЖЕН ВИДЕТЬ ТВОИ МЫСЛИ.
- Выводи в итоговый ответ только результат."""

# --- MODEL MANAGER (CASCADE) ---
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
                wait = interval - passed
                if wait < 8: return mid, wait 
            return None, 5.0

    async def mark_success(self, mid):
        async with self.lock:
            self.models[mid]['last_req'] = time.time()
            self.models[mid]['day_reqs'] += 1

    async def mark_exhausted(self, mid):
        async with self.lock:
            self.models[mid]['cooldown_until'] = time.time() + 60.0

CASCADE = None

# --- DATABASE PERSISTENCE (PARANOID MODE) ---
class PostgresPersistence(BasePersistence):
    def __init__(self, database_url: str):
        super().__init__()
        self.dsn = database_url
        self.db_pool = None
        self._init_pool()

    def _init_pool(self):
        if self.db_pool and not self.db_pool.closed: return
        logger.info("🔌 DB Pool Init...")
        self.db_pool = psycopg2.pool.ThreadedConnectionPool(
            1, 20, dsn=self.dsn, 
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5
        )
        self._init_tables()

    def _init_tables(self):
        conn = self._get_valid_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS persistence_data (key TEXT PRIMARY KEY, data BYTEA NOT NULL);")
            conn.commit()
        finally:
            self.db_pool.putconn(conn)

    def _get_valid_connection(self):
        """Гарантирует, что соединение не закрыто"""
        for i in range(3): # 3 попытки
            try:
                if not self.db_pool or self.db_pool.closed: self._init_pool()
                conn = self.db_pool.getconn()
                if conn.closed or conn.status != extensions.STATUS_READY:
                    self.db_pool.putconn(conn, close=True)
                    continue
                # PING
                with conn.cursor() as c: c.execute("SELECT 1")
                return conn
            except Exception as e:
                logger.warning(f"DB Connect Fail ({i}): {e}")
                if 'conn' in locals() and conn: 
                    try: self.db_pool.putconn(conn, close=True)
                    except: pass
                time.sleep(0.5)
        raise Exception("DB Connection completely failed")

    def _execute(self, sql, params=None, fetch=None):
        conn = None
        try:
            conn = self._get_valid_connection()
            with conn.cursor() as cur:
                cur.execute(sql, params)
                res = cur.fetchone() if fetch == 'one' else cur.fetchall() if fetch == 'all' else None
                conn.commit()
                return res
        except Exception as e:
            logger.error(f"DB Query Error: {e}")
            if conn: 
                try: conn.rollback() 
                except: pass
            raise
        finally:
            if conn: 
                try: self.db_pool.putconn(conn)
                except: pass

    async def get_chat_data(self):
        try:
            data = await asyncio.to_thread(self._execute, "SELECT key, data FROM persistence_data WHERE key LIKE 'chat_data_%';", fetch='all')
            return {int(k.split('_')[-1]): pickle.loads(v) for k, v in data} if data else defaultdict(dict)
        except: return defaultdict(dict)

    async def update_chat_data(self, chat_id, data):
        await asyncio.to_thread(self._execute, "INSERT INTO persistence_data (key, data) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET data = %s;", (f"chat_data_{chat_id}", pickle.dumps(data), pickle.dumps(data)))

    async def drop_chat_data(self, cid): 
        await asyncio.to_thread(self._execute, "DELETE FROM persistence_data WHERE key = %s;", (f"chat_data_{cid}",))

    # Заглушки для интерфейса
    async def get_bot_data(self): return {}
    async def update_bot_data(self, data): pass
    async def get_user_data(self): return defaultdict(dict)
    async def update_user_data(self, uid, data): pass
    async def drop_user_data(self, uid): pass
    async def get_callback_data(self): return None
    async def update_callback_data(self, data): pass
    async def get_conversations(self, name): return {}
    async def update_conversation(self, name, key, new_state): pass
    async def refresh_bot_data(self, bot_data): pass
    async def refresh_user_data(self, user_id, user_data): pass
    async def refresh_chat_data(self, chat_id, chat_data): pass
    async def flush(self): pass
    def close(self):
        if self.db_pool: self.db_pool.closeall()

# --- FORMATTER (SMART HTML + CODE PROTECTION) ---
def clean_and_format_text(text: str) -> str:
    if not text: return ""
    
    # 1. ЗАЩИТА КОДА: Вырезаем блоки кода, чтобы не сломать их форматированием
    code_blocks = {}
    counter = 0
    
    def replacer(match):
        nonlocal counter
        key = f"__CODE_BLOCK_{counter}__"
        code_blocks[key] = match.group(1) # Сохраняем содержимое кода
        counter += 1
        return key

    # Ищем ```code```
    text = re.sub(r'```(?:\w+)?\n?(.*?)```', replacer, text, flags=re.DOTALL)
    
    # 2. ЭКРАНИРОВАНИЕ: Теперь экранируем весь оставшийся текст (защита от XSS)
    safe_text = html.escape(text, quote=False)
    
    # 3. ФОРМАТИРОВАНИЕ: Превращаем Markdown в HTML
    # Bold **text** -> <b>text</b>
    safe_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', safe_text, flags=re.DOTALL)
    # Header # Text -> <b>Text</b>
    safe_text = re.sub(r'^#{1,6}\s+(.*?)$', r'<b>\1</b>', safe_text, flags=re.MULTILINE)
    # Italic *text*
    safe_text = re.sub(r'(?<!\*)\*(?!\s)(.*?)(?<!\s)\*(?!\*)', r'<i>\1</i>', safe_text)
    # Inline code `text` -> <code>text</code>
    safe_text = re.sub(r'`([^`]+)`', r'<code>\1</code>', safe_text)
    # Blockquote > text
    safe_text = re.sub(r'^>\s?(.*?)$', r'<blockquote>\1</blockquote>', safe_text, flags=re.MULTILINE)
    
    # 4. ВОССТАНОВЛЕНИЕ КОДА
    for key, code_content in code_blocks.items():
        # Важно: Экранируем сам код, чтобы <print> не исчез
        safe_code = html.escape(code_content, quote=False)
        safe_text = safe_text.replace(key, f"<pre>{safe_code}</pre>")
    
    return safe_text

def balance_html_tags(text: str) -> str:
    """Гарантирует закрытие всех тегов"""
    stack = []
    tags = re.findall(r'<(/?)(b|i|u|s|code|pre|blockquote)(?:\s[^>]*)?>', text)
    for closing, tag in tags:
        if not closing:
            stack.append(tag)
        else:
            if stack and stack[-1] == tag:
                stack.pop()
    for tag in reversed(stack):
        text += f"</{tag}>"
    return text

def html_safe_chunker(text: str, size=4090):
    chunks = []
    while len(text) > size:
        split_idx = text.rfind('\n', 0, size)
        if split_idx == -1: split_idx = size
        chunk = text[:split_idx]
        chunk = balance_html_tags(chunk)
        chunks.append(chunk)
        text = text[split_idx:].lstrip()
    if text:
        chunks.append(balance_html_tags(text))
    return chunks

# --- LOGIC ---
async def upload_file(client, b, mime, name):
    try:
        up = await client.aio.files.upload(file=io.BytesIO(b), config=types.UploadFileConfig(mime_type=mime, display_name=name))
        return types.Part(file_data=types.FileData(file_uri=up.uri, mime_type=mime))
    except: return None

async def generate_response(client, contents, tools=None):
    sys_prompt = DEFAULT_SYSTEM_PROMPT.format(current_time=datetime.datetime.now(pytz.timezone("Europe/Moscow")).strftime('%Y-%m-%d %H:%M'))
    
    while True:
        model_id, wait = await CASCADE.get_best_model()
        if model_id is None:
            await asyncio.sleep(wait)
            continue
        if wait > 0: await asyncio.sleep(wait)

        try:
            config = types.GenerateContentConfig(
                safety_settings=SAFETY_SETTINGS,
                tools=tools,
                system_instruction=types.Content(parts=[types.Part(text=sys_prompt)]),
                temperature=0.7, 
                thinking_config=types.ThinkingConfig(include_thoughts=True) # Включаем, но фильтруем
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
    if not response.candidates: return "⚠️ API вернул пустой ответ."
    
    cand = response.candidates[0]
    text_parts = []
    
    if cand.content and cand.content.parts:
        for p in cand.content.parts:
            # ЖЕЛЕЗНОЕ ПРАВИЛО: Игнорируем thoughts. Берем только текст.
            if p.text:
                text_parts.append(p.text)
    
    final_text = "".join(text_parts).strip()
    
    if not final_text:
        return "🤔 <i>(Модель проанализировала контекст, но не нашла, что ответить текстом.)</i>"

    # Форматирование с защитой блоков кода
    html_text = clean_and_format_text(final_text)
    html_text = balance_html_tags(html_text)
    
    return html_text

async def send_reply(msg, text):
    chunks = html_safe_chunker(text)
    sent = None
    try:
        for i, ch in enumerate(chunks):
            if i == 0: sent = await msg.reply_html(ch)
            else: sent = await msg.get_bot().send_message(msg.chat_id, ch, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        # Если HTML сломался, шлем плейнтекстом
        logger.error(f"HTML fail: {e}")
        clean = re.sub(r'<[^>]+>', '', text)
        for ch in [clean[i:i+4096] for i in range(0, len(clean), 4096)]:
            sent = await msg.reply_text(ch)
    return sent

class TypingWorker:
    def __init__(self, bot, chat_id):
        self.bot, self.chat_id, self.run = True, None
    async def work(self):
        while self.run:
            await self.bot.send_chat_action(self.chat_id, ChatAction.TYPING)
            await asyncio.sleep(4.5)
    def start(self): self.task = asyncio.create_task(self.work())
    def stop(self): 
        self.run = False
        if self.task: self.task.cancel()

async def process_request(chat_id, bot_data, application):
    data = bot_data.get('media_buffer', {}).pop(chat_id, None)
    if not data: return
    
    typer = TypingWorker(application.bot, chat_id)
    typer.start()
    
    try:
        parts, msg = data['parts'], data['msg']
        client = application.bot_data['gemini_client']
        
        # Получаем историю
        chat_data = await application.persistence.get_chat_data()
        c_data = chat_data.get(chat_id, {})
        
        history = []
        if "history" in c_data:
            for h in c_data["history"]:
                h_parts = []
                for p in h["parts"]:
                    if p.get('type') == 'text': h_parts.append(types.Part(text=p['content']))
                if h_parts: history.append(types.Content(role=h["role"], parts=h_parts))

        # Текущее сообщение
        user_content = types.Content(role="user", parts=parts)
        is_media = len(parts) > 1
        
        # Запрос
        res, model = await generate_response(client, history + [user_content], tools=MEDIA_TOOLS if is_media else TEXT_TOOLS)
        
        # Обработка (Фильтр мыслей)
        reply = format_clean_response(res, model)
        
        # Отправка
        sent = await send_reply(msg, reply)
        
        # Сохранение (без мыслей)
        if sent and "Error" not in reply:
            c_data.setdefault("history", []).append({"role": "user", "parts": [{'type': 'text', 'content': p.text} for p in parts if p.text]})
            c_data["history"].append({"role": "model", "parts": [{'type': 'text', 'content': reply}]})
            if len(c_data["history"]) > 20: c_data["history"] = c_data["history"][-20:]
            await application.persistence.update_chat_data(chat_id, c_data)
            
    except Exception as e:
        logger.error(f"Err: {e}", exc_info=True)
        await msg.reply_text("❌ Ошибка.")
    finally:
        typer.stop()

async def universal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    
    buffer = context.bot_data.setdefault('media_buffer', {})
    if msg.chat_id in buffer and buffer[msg.chat_id].get('task'):
        buffer[msg.chat_id]['task'].cancel()
    
    if msg.chat_id not in buffer:
        buffer[msg.chat_id] = {'parts': [], 'msg': msg, 'task': None}
    
    client = context.bot_data['gemini_client']
    text = msg.caption or msg.text or ""
    
    # Файлы
    media = msg.photo[-1] if msg.photo else (msg.audio or msg.voice or msg.document)
    if media:
        try:
            f = await media.get_file()
            b = await f.download_as_bytearray()
            mime = 'image/jpeg' if msg.photo else getattr(media, 'mime_type', 'application/octet-stream')
            part = await upload_file(client, b, mime, 'file')
            if part: buffer[msg.chat_id]['parts'].append(part)
        except: pass

    if text:
        buffer[msg.chat_id]['parts'].append(types.Part(text=text))
    buffer[msg.chat_id]['msg'] = msg # Обновляем на случай последнего сообщения в альбоме

    async def delayed():
        await asyncio.sleep(2.0)
        await process_request(msg.chat_id, context.bot_data, context.application)
    
    buffer[msg.chat_id]['task'] = asyncio.create_task(delayed())

async def main():
    global CASCADE
    CASCADE = ModelCascade()
    
    pers = PostgresPersistence(DATABASE_URL)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(pers).build()
    
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_html("🚀 <b>System Online.</b>")))
    app.add_handler(CommandHandler("clear", lambda u,c: (c.application.persistence.drop_chat_data(u.effective_chat.id), u.message.reply_text("🧹 Clean."))))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, universal_handler))
    
    await app.initialize()
    app.bot_data['gemini_client'] = genai.Client(api_key=GOOGLE_API_KEY)
    
    webhook_url = f"{WEBHOOK_HOST.rstrip('/')}/{GEMINI_WEBHOOK_PATH.strip('/')}"
    await app.bot.set_webhook(url=webhook_url, secret_token=TELEGRAM_SECRET_TOKEN)
    
    server = aiohttp.web.Application()
    async def wh(r):
        if r.headers.get("X-Telegram-Bot-Api-Secret-Token") != TELEGRAM_SECRET_TOKEN: return aiohttp.web.Response(status=403)
        try:
            await app.process_update(Update.de_json(await r.json(), app.bot))
            return aiohttp.web.Response(text='OK')
        except: return aiohttp.web.Response(status=500)
        
    server.router.add_post(f"/{GEMINI_WEBHOOK_PATH.strip('/')}", wh)
    server.router.add_get('/', lambda r: aiohttp.web.Response(text="Running"))
    
    runner = aiohttp.web.AppRunner(server)
    await runner.setup()
    await aiohttp.web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 10000))).start()
    
    logger.info("✅ Bot v80 Started Successfully.")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(s, stop.set)
    await stop.wait()
    await runner.cleanup()
    pers.close()

if __name__ == '__main__':
    asyncio.run(main())
