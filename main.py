# --- START OF CORRECTED main.py (Adjusted for extremely low limits) ---

import logging
import os
import asyncio
import google.generativeai as genai
import time
import random
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError
from telegram import Update
from telegram.constants import ParseMode, ChatAction # Добавляем ChatAction
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
# (оставляем как есть)
if not TELEGRAM_BOT_TOKEN:
    logger.critical("ОШИБКА: Не найден TELEGRAM_BOT_TOKEN! Проверьте переменные окружения.")
    exit("Telegram токен не найден")
if not GOOGLE_API_KEY:
    logger.critical("ОШИБКА: Не найден GOOGLE_API_KEY! Проверьте переменные окружения.")
    exit("Google API ключ не найден")

# --- Параметры для повторных попыток (Адаптированы под низкие лимиты) ---
MAX_RETRIES = 1  # Меньше попыток, если первая же неудачна
INITIAL_BACKOFF_SECONDS = 15 # <-- Увеличиваем начальную задержку (1 запрос раз в 15 сек > 5 RPM)
MAX_BACKOFF_SECONDS = 30 # Максимум можно уменьшить

# --- Настройка Gemini ---
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    # Оставляем вашу модель, помня о лимитах 5 RPM / 25 RPD
    MODEL_NAME = 'gemini-2.5-pro-preview-03-25'

    model = genai.GenerativeModel(
        MODEL_NAME,
        generation_config={ # Ваш конфиг
            "temperature": 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048,
        },
        system_instruction="Отвечай в пределах 2000 знаков, если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, или другая, требующая объема работа. Ты - лучший эксперт в любых вопросах. Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку. Подкрепляй аргументами и доказательствами, без самоповтора. Если не знаешь ответ, - всегда предупреждай, если это предположение. Активно применяй юмор: культурные и бытовые отсылки, жизненный абсурд, шутки об актуальных в интернете темах, ирония и самоирония, тонкие и интимные намёки, пост-модерн и интернет-юмор, несоответствие ожиданиям, абсурдные решения проблем, псевдомудрость, разрушение идиом, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, редукционизм. При создании уникальной работы: - Избегай копирования или близкого пересказа существующих текстов; - Включай гипотетические ситуации для иллюстрации понятий; - Применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи; - Не допускай признаков ИИ; - Варьируй структуру предложений, используя сочетание простых и сложных конструкций; - Естественно включай разговорные выражения, идиомы и фигуры речи; - Используй живые стилистические решения, свойственные людям; - Вставляй региональные выражения или культурно специфичные ссылки, где это уместно; - Добавляй остроумие." # Сократил для примера
    )
    logger.info(f"Модель Gemini ('{MODEL_NAME}') [Free Tier: 5 RPM, 25 RPD] успешно сконфигурирована.")

except GoogleAPIError as e:
    logger.exception(f"Критическая ошибка при конфигурации Gemini API: {e}")
    raise RuntimeError(f"Не удалось настроить Gemini (API Error): {e}") from e
except Exception as e:
    logger.exception("Критическая ошибка при инициализации модели Gemini!")
    raise RuntimeError(f"Не удалось настроить Gemini (General Error): {e}") from e

# --- Инициализация истории чата ---
chat_histories = {}

# --- Обработчики Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    if chat_id in chat_histories:
        del chat_histories[chat_id]
        logger.info(f"История чата сброшена для chat_id {chat_id}")
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Google AI бот ({MODEL_NAME}).\n"
        f"⚠️ **Помните:** Бесплатный лимит этой модели очень мал (5 запросов/минуту, 25 запросов/день). Бот может перестать отвечать.",
        reply_to_message_id=update.message.message_id
    )
    logger.info(f"/start от {user.id} ({user.username}) в чате {chat_id}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id} ({user.username}) в чате {chat_id}: '{user_message[:50]}...'")

    if chat_id not in chat_histories:
        try:
            chat_histories[chat_id] = model.start_chat(history=[])
            logger.info(f"Начат новый чат для chat_id {chat_id}")
        except NameError:
             logger.error("Модель Gemini не была инициализирована.")
             await update.message.reply_text("Ошибка: Модель AI не инициализирована.", reply_to_message_id=update.message.message_id)
             return
        except Exception as e:
             logger.exception(f"Ошибка при старте чата для {chat_id}: {e}")
             await update.message.reply_text(f"Произошла ошибка при инициализации чата: {e}", reply_to_message_id=update.message.message_id)
             return

    chat = chat_histories[chat_id]
    response = None
    current_retry = 0
    backoff_time = INITIAL_BACKOFF_SECONDS

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    message_to_user_about_delay = None # Сообщение о задержке

    while current_retry <= MAX_RETRIES:
        try:
            logger.info(f"Попытка {current_retry + 1}/{MAX_RETRIES + 1}: Отправка запроса к Gemini для чата {chat_id}...")
            response = await chat.send_message_async(user_message)
            # Если успешно и было сообщение о задержке - удалим его
            if message_to_user_about_delay:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message_to_user_about_delay.message_id)
                except Exception:
                    pass # Ничего страшного, если не удалилось
            logger.info(f"Успешный ответ от Gemini для чата {chat_id} на попытке {current_retry + 1}.")
            break # Успех, выходим из цикла

        except ResourceExhausted as e:
            logger.warning(f"Ошибка 429 (ResourceExhausted) для чата {chat_id} на попытке {current_retry + 1}: {e}")
            current_retry += 1
            if current_retry > MAX_RETRIES:
                logger.error(f"Достигнуто максимальное количество попыток ({MAX_RETRIES+1}) для чата {chat_id}. Запрос не выполнен.")
                await update.message.reply_text(
                    "😔 Извините, AI модель сейчас перегружена или достигнут дневной лимит запросов (25/день).\n"
                    "Пожалуйста, попробуйте значительно позже или завтра.",
                    reply_to_message_id=update.message.message_id
                )
                return # Выходим из функции

            # Расчет времени ожидания
            wait_time = min(backoff_time + random.uniform(0, 1), MAX_BACKOFF_SECONDS)
            logger.info(f"Ожидание {wait_time:.2f} секунд перед следующей попыткой для чата {chat_id}...")

            # Сообщим пользователю, если это первая задержка
            if current_retry == 1: # Только при первой ошибке 429 для этого сообщения
                 try:
                     message_to_user_about_delay = await update.message.reply_text(
                         f"⏳ Модель AI сейчас занята, пробую снова через ~{int(wait_time)} сек...",
                         reply_to_message_id=update.message.message_id
                     )
                 except Exception as send_err:
                      logger.error(f"Не удалось отправить сообщение о задержке: {send_err}")


            await asyncio.sleep(wait_time) # Асинхронная пауза
            backoff_time = min(backoff_time * 1.5, MAX_BACKOFF_SECONDS) # Увеличиваем не так агрессивно

        except GoogleAPIError as e:
            logger.exception(f"Произошла ошибка Google API (не 429) для чата {chat_id}: {e}")
            await update.message.reply_text(f"Произошла ошибка API при общении с Gemini: {e}", reply_to_message_id=update.message.message_id)
            return
        except Exception as e:
            logger.exception(f"Непредвиденная ошибка при обработке сообщения от {user.id} в чате {chat_id}: {e}")
            try:
                await update.message.reply_text(f"Ой, что-то пошло совсем не так: {e}", reply_to_message_id=update.message.message_id)
            except Exception as inner_e:
                logger.error(f"Не удалось отправить сообщение об ошибке в чат {chat_id}: {inner_e}")
            return

    # --- Обработка ответа ПОСЛЕ цикла ---
    if response and hasattr(response, 'text') and response.text:
        bot_response = response.text[:4090]
        try:
            await update.message.reply_text(bot_response, reply_to_message_id=update.message.message_id)
            logger.info(f"Ответ успешно отправлен для {user.id} в чате {chat_id}")
        except Exception as e:
            logger.exception(f"Ошибка при отправке ответа Gemini в Telegram чат {chat_id}: {e}")
            try:
                 await update.message.reply_text("Не смог отправить ответ AI (возможно, ошибка форматирования или длины).", reply_to_message_id=update.message.message_id)
            except:
                 logger.error(f"Не удалось отправить сообщение об ошибке отправки ответа в чат {chat_id}")

    elif response and (not hasattr(response, 'text') or not response.text):
        reason = getattr(response, 'prompt_feedback', 'Причина неизвестна (возможно, фильтры безопасности)')
        logger.warning(f"Получен пустой/некорректный ответ от Gemini для {user.id} в чате {chat_id}. Причина: {reason}")
        await update.message.reply_text(f"Извините, не смог сгенерировать ответ. Возможная причина: {reason}.", reply_to_message_id=update.message.message_id)
    # Если response is None, сообщение об ошибке уже было отправлено в блоке ResourceExhausted

def main() -> None:
    """Запускает бота."""
    if 'model' not in globals():
         logger.critical("Критическая ошибка: Модель 'model' не определена.")
         print("Критическая ошибка: Модель Gemini не инициализирована.")
         return

    logger.info("Инициализация Telegram Application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Запуск бота...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

# --- END OF CORRECTED main.py ---
