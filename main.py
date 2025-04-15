import os
import re
import logging
import aiohttp
import asyncio
import tempfile
import mimetypes
from io import BytesIO
from PIL import Image
from aiogram import Bot, Dispatcher, types
from aiogram.types import FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.utils.markdown import hbold
from aiogram import F
from aiogram.filters import CommandStart, Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from openai import AsyncOpenAI
import pytesseract
from PyPDF2 import PdfReader

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "False") == "True"
WEBHOOK_PATH = f"/bot/{TOKEN}"
WEBHOOK_URL = os.getenv("WEBHOOK_URL") + WEBHOOK_PATH if USE_WEBHOOK else None

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

user_history = {}
MODEL = "gpt-4"
TEMPERATURE = 0.5

# OCR: распознавание текста с изображений
async def extract_text_from_image(file_bytes):
    image = Image.open(BytesIO(file_bytes))
    text = pytesseract.image_to_string(image)
    return text.strip()

# Извлечение текста из PDF
async def extract_text_from_pdf(file_bytes):
    with BytesIO(file_bytes) as f:
        reader = PdfReader(f)
        text = "\n".join(page.extract_text() or '' for page in reader.pages)
    return text.strip()

# Обработка вложений (изображения, PDF и т.д.)
async def process_file(message: types.Message, file: types.File):
    file_bytes = await bot.download_file(file.file_path)
    data = file_bytes.read()
    mime_type, _ = mimetypes.guess_type(file.file_path)

    if mime_type == "application/pdf":
        return await extract_text_from_pdf(data)
    elif mime_type and mime_type.startswith("image"):
        return await extract_text_from_image(data)
    return "Не удалось распознать формат файла."

# Генерация ответа через OpenAI
async def ask_openai(user_id, text):
    history = user_history.get(user_id, [])
    history.append({"role": "user", "content": text})

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=history,
            temperature=TEMPERATURE,
        )
        answer = response.choices[0].message.content
        history.append({"role": "assistant", "content": answer})
        user_history[user_id] = history[-20:]  # Ограничим историю до последних 20 сообщений
        return answer
    except Exception as e:
        logging.error(f"Ошибка OpenAI: {e}")
        return "Произошла ошибка при обращении к OpenAI."

# Обработка команды /start
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer("Привет! Отправь текст или файл, и я постараюсь помочь.")

# Обработка любого текстового сообщения
@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    response = await ask_openai(user_id, message.text)
    await message.reply(response)

# Обработка документов и изображений
@dp.message(F.document | F.photo)
async def handle_file(message: types.Message):
    file = await bot.get_file(message.document.file_id if message.document else message.photo[-1].file_id)
    extracted_text = await process_file(message, file)
    response = await ask_openai(message.from_user.id, extracted_text)
    await message.reply(response)

# Запуск через webhook или polling
async def main():
    if USE_WEBHOOK:
        app = web.Application()
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)
        await bot.set_webhook(WEBHOOK_URL)
        web.run_app(app, port=8000)
    else:
        await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

