# --- START OF CORRECTED main.py (from user's main.txt with retry logic) ---

import logging
import os
import asyncio
import google.generativeai as genai
import time # <-- Добавлено для time.sleep (хотя будем использовать asyncio.sleep)
import random # <-- Добавлено для jitter в задержке
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError # <-- Добавлено для отлова ошибки квоты
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Конфигурация логов ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка наличия токенов ---
if not TELEGRAM_BOT_TOKEN:
    logger.critical("ОШИБКА: Не найден TELEGRAM_BOT_TOKEN! Проверьте переменные окружения.")
    exit("Telegram токен не найден")
if not GOOGLE_API_KEY:
    logger.critical("ОШИБКА: Не найден GOOGLE_API_KEY! Проверьте переменные окружения.")
    exit("Google API ключ не найден")

# --- Параметры для повторных попыток ---
MAX_RETRIES = 4  # Максимальное количество повторных попыток (всего 5 с первой)
INITIAL_BACKOFF_SECONDS = 2  # Начальная задержка в секундах
MAX_BACKOFF_SECONDS = 30 # Максимальная задержка между попытками

# --- Настройка Gemini ---
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    # ВНИМАНИЕ: 'gemini-2.5-pro-preview-03-25' - может быть превью-моделью с ограничениями или недоступна.
    # Рассмотрите использование 'gemini-1.5-pro-latest' или 'gemini-1.5-flash-latest' (у flash выше бесплатные лимиты RPM)
    MODEL_NAME = 'gemini-2.5-pro-preview-03-25'

    model = genai.GenerativeModel(
        MODEL_NAME,
        generation_config={
            "temperature": 1,
            "top_p": 1,
            "top_k": 40,
            "max_output_tokens": 2048, # Оставляем ваш конфиг
        },
        system_instruction="Отвечай в пределах 2000 знаков, если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, или другая, требующая объема работа. Ты - лучший эксперт в любых вопросах. Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку. Подкрепляй аргументами и доказательствами, без самоповтора. Если не знаешь ответ, - всегда предупреждай, если это предположение. Активно применяй юмор: культурные и бытовые отсылки, жизненный абсурд, шутки об актуальных в интернете темах, ирония и самоирония, тонкие и интимные намёки, пост-модерн и интернет-юмор, несоответствие ожиданиям, абсурдные решения проблем, псевдомудрость, разрушение идиом, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, редукционизм. При создании уникальной работы: Избегай копирования или близкого пересказа существующих текстов; Включай гипотетические ситуации для иллюстрации понятий; Применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи; Не допускай признаков ИИ; Варьируй структуру предложений, используя сочетание простых и сложных конструкций; Естественно включай разговорные выражения, идиомы и фигуры речи; Используй живые стилистические решения, свойственные людям; Вставляй региональные выражения или культурно специфичные ссылки, где это уместно; Добавляй остроумие."
    )
    logger.info(f"Модель Gemini ('{MODEL_NAME}') успешно сконфигурирована.")

except GoogleAPIError as e:
    # Ловим ошибки конфигурации API, например, неверный ключ
    logger.exception(f"Критическая ошибка при конфигурации Gemini API: {e}")
    raise RuntimeError(f"Не удалось настроить Gemini (API Error): {e}") from e
except Exception as e:
    # Ловим другие возможные ошибки при инициализации модели
    logger.exception("Критическая ошибка при инициализации модели Gemini!")
    raise RuntimeError(f"Не удалось настроить Gemini (General Error): {e}") from e

# --- Инициализация истории чата ---
chat_histories = {}

# --- Обработчики Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    if chat_id in chat_histories:
        del chat_histories[chat_id]
        logger.info(f"История чата сброшена для chat_id {chat_id}")
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Gemini бот ({MODEL_NAME}). Спроси меня о чём угодно!",
        reply_to_message_id=update.message.message_id
    )
    logger.info(f"/start от {user.id} ({user.username}) в чате {chat_id}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовые сообщения с логикой повторов при ошибке 429."""
    user_message = update.message.text
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id} ({user.username}) в чате {chat_id}: '{user_message[:50]}...'")

    if chat_id not in chat_histories:
        try:
            # Инициализируем чат внутри try, на случай если сама модель не создалась
            chat_histories[chat_id] = model.start_chat(history=[])
            logger.info(f"Начат новый чат для chat_id {chat_id}")
        except NameError: # Если 'model' не определена из-за ошибки выше
             logger.error("Модель Gemini не была инициализирована. Невозможно начать чат.")
             await update.message.reply_text("Ошибка: Модель AI не инициализирована. Обратитесь к администратору.", reply_to_message_id=update.message.message_id)
             return
        except Exception as e:
             logger.exception(f"Ошибка при старте чата для {chat_id}: {e}")
             await update.message.reply_text(f"Произошла ошибка при инициализации чата: {e}", reply_to_message_id=update.message.message_id)
             return


    chat = chat_histories[chat_id]
    response = None # Инициализируем переменную для ответа
    current_retry = 0
    backoff_time = INITIAL_BACKOFF_SECONDS

    # Ставим индикатор "печатает..." перед началом попыток
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')

    while current_retry <= MAX_RETRIES:
        try:
            logger.info(f"Попытка {current_retry + 1}/{MAX_RETRIES + 1}: Отправка запроса к Gemini для чата {chat_id}...")
            # Используем асинхронную версию
            response = await chat.send_message_async(user_message)
            # Если успешно, выходим из цикла повторов
            logger.info(f"Успешный ответ от Gemini для чата {chat_id} на попытке {current_retry + 1}.")
            break

        except ResourceExhausted as e:
            # Это ошибка 429 (превышение квоты)
            logger.warning(f"Ошибка 429 (ResourceExhausted) для чата {chat_id} на попытке {current_retry + 1}: {e}")
            current_retry += 1
            if current_retry > MAX_RETRIES:
                logger.error(f"Достигнуто максимальное количество попыток ({MAX_RETRIES+1}) для чата {chat_id}. Запрос не выполнен.")
                await update.message.reply_text(
                    "Извините, сейчас наблюдается высокая нагрузка на AI модель. Пожалуйста, попробуйте ваш запрос чуть позже.",
                    reply_to_message_id=update.message.message_id
                )
                return # Выходим из функции handle_message

            # Расчет времени ожидания с экспоненциальной задержкой и "джиттером"
            wait_time = min(backoff_time + random.uniform(0, 1), MAX_BACKOFF_SECONDS)
            logger.info(f"Ожидание {wait_time:.2f} секунд перед следующей попыткой для чата {chat_id}...")
            await asyncio.sleep(wait_time) # Используем asyncio.sleep для асинхронной функции

            # Увеличиваем время ожидания для следующей попытки
            backoff_time = min(backoff_time * 2, MAX_BACKOFF_SECONDS)

        except GoogleAPIError as e:
            # Обработка других ошибок API Google (не связанных с квотой)
            logger.exception(f"Произошла ошибка Google API (не 429) при общении с Gemini для чата {chat_id}: {e}")
            await update.message.reply_text(f"Произошла ошибка API при общении с Gemini: {e}", reply_to_message_id=update.message.message_id)
            return # Выходим из функции

        except Exception as e:
            # Обработка других непредвиденных ошибок (сеть, проблемы библиотеки и т.д.)
            logger.exception(f"Произошла непредвиденная ошибка при обработке сообщения от {user.id} в чате {chat_id}: {e}")
            try:
                await update.message.reply_text(f"Ой, что-то пошло совсем не так: {e}", reply_to_message_id=update.message.message_id)
            except Exception as inner_e:
                logger.error(f"Не удалось даже отправить сообщение об ошибке в чат {chat_id}: {inner_e}")
            return # Выходим из функции

    # --- Обработка ответа ПОСЛЕ цикла повторов ---
    if response and hasattr(response, 'text') and response.text: # Проверяем, что ответ получен и не пуст
        bot_response = response.text[:4090] # Ограничение Telegram ~4096
        try:
            await update.message.reply_text(bot_response, reply_to_message_id=update.message.message_id)
            logger.info(f"Ответ успешно отправлен для {user.id} в чате {chat_id}: '{bot_response[:50]}...'")
        except Exception as e:
            logger.exception(f"Ошибка при отправке ответа Gemini в Telegram чат {chat_id}: {e}")
            # Попытаться отправить сообщение об ошибке отправки
            try:
                 await update.message.reply_text("Не смог отправить ответ AI, возможно, он слишком длинный или содержит недопустимые символы.", reply_to_message_id=update.message.message_id)
            except:
                 logger.error(f"Не удалось отправить даже сообщение об ошибке отправки ответа в чат {chat_id}")

    elif response and (not hasattr(response, 'text') or not response.text):
        # Случай, когда ответ получен, но он пуст (например, из-за фильтров безопасности)
        reason = getattr(response, 'prompt_feedback', 'Причина неизвестна (возможно, фильтры безопасности)')
        logger.warning(f"Получен пустой или некорректный ответ от Gemini для {user.id} в чате {chat_id}. Причина: {reason}")
        await update.message.reply_text(f"Извините, не смог сгенерировать ответ. Возможная причина: {reason}. Попробуйте переформулировать.", reply_to_message_id=update.message.message_id)

    # Если response остался None после цикла, значит, все попытки провалились из-за 429,
    # и сообщение пользователю уже было отправлено внутри блока except ResourceExhausted.


def main() -> None:
    """Запускает бота."""
    # --- Проверка инициализации модели перед запуском ---
    if 'model' not in globals():
         logger.critical("Критическая ошибка: Переменная 'model' не определена. Бот не может быть запущен.")
         print("Критическая ошибка: Модель Gemini не была успешно инициализирована. Проверьте логи и конфигурацию.")
         return # Не запускаем бота, если модель не готова

    logger.info("Инициализация Telegram Application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем бота (polling)
    logger.info("Запуск бота...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

# --- END OF CORRECTED main.py ---
