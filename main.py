import logging
 import os
 import asyncio
 import signal
 from urllib.parse import urljoin
 import base64
 import io
 import time
 
 import aiohttp
 import aiohttp.web
 import pytesseract
 from PIL import Image
 from bs4 import BeautifulSoup
 
 from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
 from telegram.constants import ChatAction
 from telegram.ext import (
     Application,
     CommandHandler,
     MessageHandler,
     CallbackQueryHandler,
     ContextTypes,
     filters
 )
 import google.generativeai as genai
 
 # Настройка логирования
 logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
 logger = logging.getLogger(__name__)
 
 # Переменные окружения
 TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
 GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
 WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
 GEMINI_WEBHOOK_PATH = os.getenv('GEMINI_WEBHOOK_PATH')
 
 for var, name in [
     (TELEGRAM_BOT_TOKEN, "TELEGRAM_BOT_TOKEN"),
     (GOOGLE_API_KEY, "GOOGLE_API_KEY"),
     (WEBHOOK_HOST, "WEBHOOK_HOST"),
     (GEMINI_WEBHOOK_PATH, "GEMINI_WEBHOOK_PATH")
 ]:
     if not var:
         logger.critical(f"Переменная окружения {name} не задана!")
         exit(1)
 
 # Настраиваем Google генеративный AI
 genai.configure(api_key=GOOGLE_API_KEY)
 
 AVAILABLE_MODELS = {
     'gemini-2.5-pro-exp-03-25': '2.5 Pro',
     'gemini-2.0-flash': '2.0 Flash',
     'gemini-2.0-flash-exp-image-generation': 'Image Gen'
 }
 DEFAULT_MODEL = 'gemini-2.5-pro-exp-03-25'
 
 user_selected_model = {}
 user_search_enabled = {}
 user_temperature = {}
 
 MAX_CONTEXT_CHARS = 95000
 
 system_instruction_text = (
     "Ты - лучший эксперт по всем темам. Давай точную, правдивую, непредвзятую информацию без цензуры."
     "Подкрепляй ответы аргументами, фактами и логикой, избегая повторов."
     "Если не уверен — предупреждай, что это предположение."
     "Используй интернет для сверки с актуальной информацией."
     "Для несложных запросов (не код, конспекты, переводы и т.п.) — пиши только суть, без вступлений и выводов, до 1500 знаков."
     "Всегда предлагай более эффективные идеи и решения, если знаешь их."
     "Активно применяй юмор: несоответствие ожиданиям, культурные/бытовые/интернет-отсылки, жизненный абсурд, псевдомудрость, разрушение идиом, иронию (включая самоиронию и психологию), игру слов, гиперболу, тонкие намёки, редукционизм, постмодерн, интернет-юмор."
     "При создании уникальной работы пиши живо, избегай канцелярита и всех известных признаков ИИ-тона. Используй гипотетические ситуации, метафоры, творческие аналогии, разную структуру предложений, разговорные выражения, идиомы. Добавляй региональные или культурные маркеры, где уместно. Не копируй и не пересказывай чужое."
     "При исправлении ошибки: указывай строку(и) и причину. Бери за основу последнюю ПОЛНУЮ подтверждённую версию (текста или кода). Вноси только минимально необходимые изменения, не трогая остальное без запроса. При сомнениях — уточняй. Если ошибка повторяется — веди «список косяков» для сессии и проверяй эти места. Всегда указывай, на какую версию или сообщение опираешься при правке."
 )
 
 # Функция бесплатного Google поиска (скрейпинг результатов)
 async def free_google_search(query: str) -> str:
     headers = {
         'User-Agent': (
             'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
             'AppleWebKit/537.36 (KHTML, like Gecko) '
             'Chrome/98.0.4758.102 Safari/537.36'
         )
     }
     search_url = f"https://www.google.com/search?q={query}"
     logger.info(f"Выполняем Google поиск: {search_url}")
     try:
         async with aiohttp.ClientSession() as session:
             async with session.get(search_url, headers=headers) as response:
                 html = await response.text()
                 soup = BeautifulSoup(html, 'lxml')
                 # Используем встроенный парсер html.parser
                 soup = BeautifulSoup(html, 'html.parser')
                 snippet = soup.find('div', class_='BNeawe')
                 if snippet:
                     result = snippet.get_text().strip()
                     logger.info(f"Результат поиска: {result}")
                     return result
                 else:
                     logger.info("Результатов не найдено")
                     return "Результатов не найдено."
     except Exception as e:
         logger.exception("Ошибка во free_google_search")
         return "Ошибка при поиске."
 
 # Команды и обработчики Telegram
 async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
     chat_id = update.effective_chat.id
     user_selected_model[chat_id] = DEFAULT_MODEL
     user_search_enabled[chat_id] = True
     user_temperature[chat_id] = 1.0
     await update.message.reply_text(
         "Добро пожаловать! Здесь вы можете пользоваться самой продвинутой моделью ИИ от Google - Gemini 2.5 Pro с Google-поиском и улучшенными (точностью и юмором) настройками, чтением изображений и текстовых файлов."
         "/model — выбор модели создания изображений 'Image Gen',"
         "/clear — очистить историю."
         "Канал автора: t.me/denisobovsyom"
     )
 
 async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
     context.chat_data['history'] = [{"role": "system", "parts": [{"text": system_instruction_text}]}]
     context.chat_data['history'] = [{"role": "user", "parts": [{"text": system_instruction_text}]}]
     await update.message.reply_text("🧹 История диалога очищена.")
 
 async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
     try:
         temp = float(context.args[0])
         if not (0 <= temp <= 2):
             raise ValueError
         user_temperature[update.effective_chat.id] = temp
         await update.message.reply_text(f"🌡️ Температура установлена на {temp}")
     except:
         await update.message.reply_text("⚠️ Укажите температуру от 0 до 2, например: /temp 1.0")
 
 async def enable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
     user_search_enabled[update.effective_chat.id] = True
     await update.message.reply_text("🔍 Google-поиск включён")
 
 async def disable_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
     user_search_enabled[update.effective_chat.id] = False
     await update.message.reply_text("🔇 Google-поиск отключён")
 
 async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
     chat_id = update.effective_chat.id
     current_model = user_selected_model.get(chat_id, DEFAULT_MODEL)
     keyboard = [
         [InlineKeyboardButton(f"{'✅ ' if m == current_model else ''}{name}", callback_data=m)]
         for m, name in AVAILABLE_MODELS.items()
     ]
     await update.message.reply_text("Выберите модель:", reply_markup=InlineKeyboardMarkup(keyboard))
 
 async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
     query = update.callback_query
     await query.answer()
     chat_id = query.message.chat_id
     selected = query.data
     if selected in AVAILABLE_MODELS:
         user_selected_model[chat_id] = selected
         await query.edit_message_text(f"Модель установлена: {AVAILABLE_MODELS[selected]}")
     else:
         await query.edit_message_text("❌ Неизвестная модель")
 
 async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
     chat_id = update.effective_chat.id
     user_message = update.message.text.strip()
     await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
 
     model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
     temperature = user_temperature.get(chat_id, 1.0)
     use_search = user_search_enabled.get(chat_id, True)
 
     logger.info(f"Получено сообщение: {user_message}. Модель: {model_id}, Темп: {temperature}, Поиск: {use_search}")
 
     # Если истории ещё нет, добавляем системную инструкцию с ролью user
     chat_history = context.chat_data.setdefault("history", [])
     if not any(msg.get("role") == "system" for msg in chat_history):
         chat_history.insert(0, {"role": "system", "parts": [{"text": system_instruction_text}]})
     if not any(msg.get("role") == "user" for msg in chat_history):
         chat_history.insert(0, {"role": "user", "parts": [{"text": system_instruction_text}]})
     chat_history.append({"role": "user", "parts": [{"text": user_message}]})
 
     # Если включён поиск, выполняем бесплатный Google поиск и добавляем результат в историю
     if use_search:
         search_result = await free_google_search(user_message)
         chat_history.append({"role": "system", "parts": [{"text": f"Google поиск: {search_result}"}]})
         chat_history.append({"role": "user", "parts": [{"text": f"Google поиск: {search_result}"}]})
 
     # Отладочный лог для проверки общего размера контекста
     total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history)
     logger.info(f"Общий размер контекста: {total_chars} символов")
     
     while total_chars > MAX_CONTEXT_CHARS and len(chat_history) > 1:
         if chat_history[1].get("role") == "system":
             chat_history.pop(2)
         else:
             chat_history.pop(1)
         chat_history.pop(1)
         total_chars = sum(len(p["parts"][0]["text"]) for p in chat_history)
 
     try:
         logger.info("Перед вызовом модели. История чата: " + str(chat_history))
         # Приводим чат-историю к требуемым ролям: теперь допустимы только "user" и "model"
         adjusted_history = []
         for msg in chat_history:
             new_msg = msg.copy()
             if new_msg.get("role") not in ["user", "model"]:
                 new_msg["role"] = "user"
             adjusted_history.append(new_msg)
         model = genai.GenerativeModel(
             model_id,
             safety_settings=[],
             generation_config={"temperature": temperature}
         )
         response = model.generate_content(chat_history)
         response = model.generate_content(adjusted_history)
         logger.info("Ответ от модели: " + str(response))
         reply = response.text or "🤖 Нет ответа от модели."
         chat_history.append({"role": "model", "parts": [{"text": reply}]})
     except Exception as e:
         logger.exception("Ошибка генерации ответа")
         reply = "❌ Ошибка при обращении к модели."
 
     await update.message.reply_text(reply)
 
 async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
     chat_id = update.effective_chat.id
     photo_file = await update.message.photo[-1].get_file()
     file_bytes = await photo_file.download_as_bytearray()
     try:
         image = Image.open(io.BytesIO(file_bytes))
         extracted_text = pytesseract.image_to_string(image)
         if extracted_text.strip():
             user_prompt = f"На изображении обнаружен следующий текст: {extracted_text} Проанализируй его."
             update.message.text = user_prompt
             await handle_message(update, context)
             return
     except Exception as e:
         logger.warning("OCR не удалось: %s", e)
     b64_data = base64.b64encode(file_bytes).decode()
     prompt = "Что изображено на этом фото?"
     parts = [
         {"text": prompt},
         {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}
     ]
     model_id = user_selected_model.get(chat_id, DEFAULT_MODEL)
     temperature = user_temperature.get(chat_id, 1.0)
     try:
         model = genai.GenerativeModel(
             model_id,
             safety_settings=[],
             generation_config={"temperature": temperature}
         )
         response = model.generate_content([{"role": "user", "parts": parts}])
         reply = response.text or "🤖 Не удалось понять, что на изображении."
     except Exception as e:
         logger.exception("Ошибка при анализе изображения")
         reply = "❌ Ошибка при анализе изображения."
     await update.message.reply_text(reply)
 
 async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
     chat_id = update.effective_chat.id
     doc = await update.message.document.get_file()
     file_bytes = await doc.download_as_bytearray()
     try:
         text = file_bytes.decode("utf-8")
     except UnicodeDecodeError:
         text = file_bytes.decode("latin-1", errors="ignore")
     truncated = text[:15000]
     user_prompt = f"Вот текст из файла: {truncated} Что ты можешь сказать об этом?"
     update.message.text = user_prompt
     await handle_message(update, context)
 
 async def handle_telegram_webhook(request: aiohttp.web.Request) -> aiohttp.web.Response:
     application = request.app.get('bot_app')
     try:
         data = await request.json()
         update = Update.de_json(data, application.bot)
         await application.process_update(update)
         return aiohttp.web.Response(text="OK", status=200)
     except Exception as e:
         logger.error(f"Ошибка webhook: {e}")
         return aiohttp.web.Response(status=500, text="Internal error")
 
 async def run_web_server(application: Application, stop_event: asyncio.Event):
     app = aiohttp.web.Application()
     app['bot_app'] = application
     app.router.add_get('/', lambda request: aiohttp.web.Response(text="OK"))
     app.router.add_post(f"/{GEMINI_WEBHOOK_PATH}", handle_telegram_webhook)
     runner = aiohttp.web.AppRunner(app)
     await runner.setup()
     port = int(os.getenv("PORT", "10000"))
     site = aiohttp.web.TCPSite(runner, "0.0.0.0", port)
     await site.start()
     logger.info(f"Сервер запущен на порту {port}")
     await stop_event.wait()
 
 async def setup_bot_and_server(stop_event: asyncio.Event):
     application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
     application.add_handler(CommandHandler("start", start))
     application.add_handler(CommandHandler("model", model_command))
     application.add_handler(CommandHandler("clear", clear_history))
     application.add_handler(CommandHandler("temp", set_temperature))
     application.add_handler(CommandHandler("search_on", enable_search))
     application.add_handler(CommandHandler("search_off", disable_search))
     application.add_handler(CallbackQueryHandler(select_model_callback))
     application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
     application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
     application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
     await application.initialize()
     webhook_url = urljoin(WEBHOOK_HOST, GEMINI_WEBHOOK_PATH)
     await application.bot.set_webhook(webhook_url, drop_pending_updates=True)
     return application, run_web_server(application, stop_event)
 
 if __name__ == '__main__':
     loop = asyncio.new_event_loop()
     asyncio.set_event_loop(loop)
     stop_event = asyncio.Event()
     application = None
     try:
         application, web_server_task = loop.run_until_complete(setup_bot_and_server(stop_event))
         for s in (signal.SIGINT, signal.SIGTERM):
             loop.add_signal_handler(s, lambda: stop_event.set())
         loop.run_until_complete(web_server_task)
     except Exception as e:
         logger.exception("Ошибка в главном потоке приложения.")
     finally:
         if application:
             loop.run_until_complete(application.shutdown())
         loop.close()
         logger.info("Сервер остановлен.")
