# Версия 78 (Thought Firewall + HTML Fix + Paranoid DB)

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
from telegram import Update, Message, ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, BasePersistence
from telegram.error import BadRequest

from google import genai
from google.genai import types

# --- КОНФИГУРАЦИЯ ---
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=log_level)
logger = logging.getLogger(__name__)
logging.getLogger('aiohttp.access').setLevel(logging.WARNING)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_SECRET_TOKEN = os.getenv('TELEGRAM_SECRET_TOKEN', 'secret-token-replace-me') 
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')
ADMIN_ID = os.getenv('ADMIN_ID')

if not all([TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, WEBHOOK_HOST, GEMINI_WEBHOOK_PATH]):
    logger.critical("❌ Critical: Env vars missing!")
    exit(1)

# --- МОДЕЛИ ---
# Используем Thinking Experimental для глубоких ответов
MODELS_CONFIG = [
    {'id': 'gemini-2.0-flash-thinking-exp-01-21', 'rpm': 5, 'rpd': 500, 'name': 'Gemini 2.0 Thinking'},
    {'id': 'gemini-2.0-flash-exp', 'rpm': 10, 'rpd': 1500, 'name': 'Gemini 2.0 Flash'},
]

# --- ЛИМИТЫ ---
MAX_CONTEXT_CHARS = 100000 
MEDIA_CONTEXT_TTL_SECONDS = 47 * 3600
URL_REGEX = re.compile(r'https?:\/\/[^\s/$.?#].[^\s]*')

# --- ИНСТРУМЕНТЫ ---
TEXT_TOOLS = [types.Tool(google_search=types.GoogleSearch(), url_context=types.UrlContext())]
MEDIA_TOOLS = [types.Tool(google_search=types.GoogleSearch(), url_context=types.UrlContext())]

SAFETY_SETTINGS = [
    types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for c in (types.HarmCategory.HARM_CATEGORY_HARASSMENT, types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
              types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT)
]

# Системный промпт специально настроен на HTML и отсутствие вывода мыслей
DEFAULT_SYSTEM_PROMPT = """(System Note: Today is {current_time}.)
Ты — умный помощник в Telegram.
Твоя задача — давать подробные, точные и эстетичные ответы.

ФОРМАТИРОВАНИЕ:
Используй HTML теги для структуры (Markdown НЕ поддерживается напрямую, только HTML):
- <b>Заголовки и важные акценты</b>
- <i>Курсив для терминов и нюансов</i>
- <code>Моноширинный текст для команд</code>
- <pre>Блоки кода (для скриптов)</pre>
- <blockquote>Цитаты и важные мысли</blockquote>

МЫШЛЕНИЕ (THINKING):
Ты используешь процесс мышления (Internal Monologue) для анализа.
НИКОГДА не выводи содержимое своих мыслей пользователю.
Пользователь должен видеть ТОЛЬКО финальный, отформатированный ответ."""

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

# --- PARANOID DATABASE PERSISTENCE ---
class PostgresPersistence(BasePersistence):
    def __init__(self, database_url: str):
        super().__init__()
        self.dsn = database_url
        self.db_pool = None
        self._init_pool()

    def _init_pool(self):
        if self.db_pool and not self.db_pool.closed: return
        logger.info("🔌 Initializing DB Pool...")
        # keepalives settings are crucial for cloud DBs
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
        """Гарантирует получение живого соединения"""
        retry_count = 0
        while retry_count < 3:
            try:
                if not self.db_pool or self.db_pool.closed: self._init_pool()
                conn = self.db_pool.getconn()
                if conn.closed or conn.status != extensions.STATUS_READY:
                    self.db_pool.putconn(conn, close=True)
                    continue
                # Active Ping
                with conn.cursor() as c: c.execute("SELECT 1")
                return conn
            except Exception as e:
                logger.warning(f"DB Connect Retry {retry_count}: {e}")
                if 'conn' in locals() and conn: 
                    try: self.db_pool.putconn(conn, close=True)
                    except: pass
                retry_count += 1
                time.sleep(0.5)
        raise Exception("DB Connection Failed")

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

    # Stubs
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

# --- UTILS & HELPERS ---
class TypingWorker:
    """Показывает статус 'печатает', пока модель думает"""
    def __init__(self, bot, chat_id):
        self.bot, self.chat_id, self.running, self.task = bot, chat_id, False, None
    async def _worker(self):
        while self.running:
            try:
                await self.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4.5)
            except: break
    def start(self):
        self.running = True
        self.task = asyncio.create_task(self._worker())
    def stop(self):
        self.running = False
        if self.task: self.task.cancel()

def get_current_time_str():
    now = datetime.datetime.now(pytz.timezone("Europe/Moscow"))
    return f"Сегодня {now.strftime('%d.%m.%Y')}, {now.strftime('%H:%M')} (MSK)."

async def upload_file(client, b, mime, name):
    """Загрузка файлов в Gemini (для Vision/Audio)"""
    try:
        up = await client.aio.files.upload(file=io.BytesIO(b), config=types.UploadFileConfig(mime_type=mime, display_name=name))
        return types.Part(file_data=types.FileData(file_uri=up.uri, mime_type=mime))
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        return None

# --- FORMATTER (CRITICAL: THOUGHT FILTER & HTML) ---
def clean_and_format_text(text: str) -> str:
    if not text: return ""
    
    # 1. Сначала экранируем всё (защита от XSS и инъекций тегов пользователя)
    safe_text = html.escape(text, quote=False) 
    
    # 2. Конвертируем Markdown-подобные конструкции в HTML
    # Bold **text** -> <b>text</b>
    safe_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', safe_text, flags=re.DOTALL)
    # Header # Text -> <b>Text</b>
    safe_text = re.sub(r'^#{1,6}\s+(.*?)$', r'<b>\1</b>', safe_text, flags=re.MULTILINE)
    # Italic *text* (с проверкой, чтобы не ломать математику типа 2 * 3)
    safe_text = re.sub(r'(?<!\*)\*(?!\s)(.*?)(?<!\s)\*(?!\*)', r'<i>\1</i>', safe_text)
    # Monospace `text` -> <code>text</code>
    safe_text = re.sub(r'`([^`]+)`', r'<code>\1</code>', safe_text)
    # Code Blocks ```text``` -> <pre>text</pre>
    safe_text = re.sub(r'```(.*?)```', r'<pre>\1</pre>', safe_text, flags=re.DOTALL)
    # Blockquote > text -> <blockquote>text</blockquote>
    safe_text = re.sub(r'^>\s?(.*?)$', r'<blockquote>\1</blockquote>', safe_text, flags=re.MULTILINE)
    
    # 3. Восстанавливаем теги, если они были заэкранированы (так как мы делали escape в начале)
    # Но восстанавливаем ТОЛЬКО безопасные.
    allowed = ['b', 'i', 'u', 's', 'code', 'pre', 'blockquote']
    for tag in allowed:
        safe_text = safe_text.replace(f'&lt;{tag}&gt;', f'<{tag}>').replace(f'&lt;/{tag}&gt;', f'</{tag}>')
    
    return safe_text

def balance_html_tags(text: str) -> str:
    """Закрывает теги, чтобы Telegram не ругался на незакрытый тег в конце сообщения"""
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
        # Пытаемся резать по переносу строки
        split_idx = text.rfind('\n', 0, size)
        if split_idx == -1: split_idx = size
        
        chunk = text[:split_idx]
        chunk = balance_html_tags(chunk) # Важно: балансируем каждый кусок
        chunks.append(chunk)
        text = text[split_idx:].lstrip()
    
    if text:
        chunks.append(balance_html_tags(text))
    return chunks

# --- GENERATION LOGIC ---
async def generate_response(client, contents, tools=None):
    sys_prompt = SYSTEM_INSTRUCTION.format(current_time=get_current_time_str())
    
    while True:
        model_id, wait = await CASCADE.get_best_model()
        if model_id is None:
            await asyncio.sleep(wait)
            continue
        if wait > 0: await asyncio.sleep(wait)

        try:
            # Thinking включен, но мы его фильтруем при приеме
            config = types.GenerateContentConfig(
                safety_settings=SAFETY_SETTINGS,
                tools=tools,
                system_instruction=types.Content(parts=[types.Part(text=sys_prompt)]),
                temperature=0.7, 
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
    """
    ФИЛЬТР МЫСЛЕЙ (THOUGHT FIREWALL)
    """
    if isinstance(response, str): return response # Если пришла ошибка строкой
    
    if not response.candidates: return "⚠️ Пустой ответ от API."
    cand = response.candidates[0]
    
    text_parts = []
    
    if cand.content and cand.content.parts:
        for p in cand.content.parts:
            # САМОЕ ВАЖНОЕ: Игнорируем p.thought. Берем только p.text
            if p.text:
                text_parts.append(p.text)
            # p.thought просто пропускается. Он не попадает в text_parts.

    final_text = "".join(text_parts).strip()
    
    if not final_text:
        # Если текста нет, значит модель только думала.
        return "🤔 <i>(Модель задумалась, но не выдала текстовый ответ. Попробуйте уточнить запрос.)</i>"

    # Форматирование и балансировка HTML
    html_text = clean_and_format_text(final_text)
    html_text = balance_html_tags(html_text)
    
    return html_text

# --- TELEGRAM HANDLERS ---
async def send_reply(msg, text):
    chunks = html_safe_chunker(text)
    sent = None
    try:
        for i, ch in enumerate(chunks):
            if i == 0: sent = await msg.reply_html(ch)
            else: sent = await msg.get_bot().send_message(msg.chat_id, ch, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        logger.error(f"HTML Parse Error: {e}. Sending plain text.")
        # Fallback: шлем без форматирования, если Telegram отверг HTML
        plain_text = re.sub(r'<[^>]+>', '', text) # Удаляем теги
        for ch in [plain_text[i:i+4096] for i in range(0, len(plain_text), 4096)]:
            sent = await msg.reply_text(ch)
    return sent

async def process_request(chat_id, bot_data, application):
    data = bot_data.get('media_buffer', {}).pop(chat_id, None)
    if not data: return
    
    try:
        parts, msg = data['parts'], data['msg']
        client = application.bot_data['gemini_client']
        
        # Запускаем "печатает..."
        typer = TypingWorker(application.bot, chat_id)
        typer.start()

        chat_data = await application.persistence.get_chat_data()
        c_data = chat_data.get(chat_id, {})
        
        # Восстановление истории (упрощенное для стабильности)
        history = []
        if "history" in c_data:
            for h in c_data["history"]:
                role = h["role"]
                h_parts = []
                for p in h["parts"]:
                    if p.get('type') == 'text': h_parts.append(types.Part(text=p['content']))
                    # Файлы из истории пока пропускаем для экономии токенов, если нужно - можно добавить
                if h_parts: history.append(types.Content(role=role, parts=h_parts))

        # Текущий запрос
        user_content = types.Content(role="user", parts=parts)
        is_media = len(parts) > 1 # Если есть что-то кроме текста
        
        # Генерация
        res, model = await generate_response(
            client, 
            history + [user_content], 
            tools=MEDIA_TOOLS if is_media else TEXT_TOOLS
        )
        
        # Обработка ответа (УДАЛЕНИЕ МЫСЛЕЙ ТУТ)
        reply = format_clean_response(res, model)
        
        typer.stop()
        
        # Отправка
        sent = await send_reply(msg, reply)
        
        # Сохранение в историю (без мыслей)
        if sent and "Error" not in reply:
            # User entry
            u_parts_store = []
            for p in parts:
                if p.text: u_parts_store.append({'type': 'text', 'content': p.text})
                elif p.file_data: u_parts_store.append({'type': 'file', 'uri': 'stored'})
            
            c_data.setdefault("history", []).append({"role": "user", "parts": u_parts_store})
            
            # Model entry (сохраняем уже очищенный текст, чтобы мысли не всплыли в контексте)
            c_data["history"].append({"role": "model", "parts": [{'type': 'text', 'content': reply}]})
            
            # Ротация истории
            if len(c_data["history"]) > 20: c_data["history"] = c_data["history"][-20:]
            await application.persistence.update_chat_data(chat_id, c_data)

    except Exception as e:
        logger.error(f"Process Error: {e}", exc_info=True)
        typer.stop()
        await msg.reply_text("❌ Внутренняя ошибка бота.")

async def universal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    
    buffer = context.bot_data.setdefault('media_buffer', {})
    
    # Отмена предыдущей задачи таймера (Debounce)
    if msg.chat_id in buffer and buffer[msg.chat_id].get('task'):
        buffer[msg.chat_id]['task'].cancel()
    else:
        buffer[msg.chat_id] = {'parts': [], 'msg': msg, 'task': None}
    
    # Сбор контента
    client = context.bot_data['gemini_client']
    text = msg.caption or msg.text or ""
    
    # Обработка медиа
    media = msg.photo[-1] if msg.photo else (msg.audio or msg.voice or msg.document)
    if media:
        try:
            f = await media.get_file()
            b = await f.download_as_bytearray()
            mime = 'image/jpeg' if msg.photo else getattr(media, 'mime_type', 'application/octet-stream')
            part = await upload_file(client, b, mime, 'upload')
            if part: buffer[msg.chat_id]['parts'].append(part)
        except Exception as e:
            logger.error(f"File load err: {e}")

    if text:
        buffer[msg.chat_id]['parts'].append(types.Part(text=text))
    
    # Обновляем ссылку на сообщение (чтобы отвечать на последнее)
    buffer[msg.chat_id]['msg'] = msg

    # Таймер запуска (ждет, пока догрузятся все картинки альбома)
    async def delayed():
        await asyncio.sleep(2.0)
        await process_request(msg.chat_id, context.bot_data, context.application)

    buffer[msg.chat_id]['task'] = asyncio.create_task(delayed())

async def start_c(u, c): 
    await u.message.reply_html("👋 <b>Привет!</b> Я готов к работе. (v78 Stable)")
    
async def clear_c(u, c):
    await c.application.persistence.drop_chat_data(u.effective_chat.id)
    await u.message.reply_text("🧹 История очищена.")

# --- MAIN SETUP ---
async def main():
    global CASCADE
    CASCADE = ModelCascade()

    pers = PostgresPersistence(DATABASE_URL)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(pers).build()

    app.add_handler(CommandHandler("start", start_c))
    app.add_handler(CommandHandler("clear", clear_c))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, universal_handler))

    await app.initialize()
    app.bot_data['gemini_client'] = genai.Client(api_key=GOOGLE_API_KEY)
    
    webhook_url = f"{WEBHOOK_HOST.rstrip('/')}/{GEMINI_WEBHOOK_PATH.strip('/')}"
    await app.bot.set_webhook(url=webhook_url, secret_token=TELEGRAM_SECRET_TOKEN)
    
    server = aiohttp.web.Application()
    async def wh(r):
        token = r.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if token != TELEGRAM_SECRET_TOKEN: return aiohttp.web.Response(status=403)
        try:
            await app.process_update(Update.de_json(await r.json(), app.bot))
            return aiohttp.web.Response(text='OK')
        except: return aiohttp.web.Response(status=500)
    
    server.router.add_post(f"/{GEMINI_WEBHOOK_PATH.strip('/')}", wh)
    server.router.add_get('/', lambda r: aiohttp.web.Response(text="Running v78"))
    
    runner = aiohttp.web.AppRunner(server)
    await runner.setup()
    await aiohttp.web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 10000))).start()
    
    logger.info("✅ Bot Started. Waiting for signals...")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(s, stop.set)
    await stop.wait()
    
    await runner.cleanup()
    pers.close()

if __name__ == '__main__':
    asyncio.run(main())
