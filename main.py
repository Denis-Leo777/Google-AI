# --- START OF CORRECTED main.py ---

import logging
import os
import asyncio
import google.generativeai as genai
from telegram import Update
from telegram.constants import ParseMode # Импортируем ParseMode для форматирования
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

# --- Настройка Gemini ---
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    # Используем твою модель, но убедись, что она доступна тебе
    MODEL_NAME = 'gemini-1.5-pro-latest' # ИЛИ 'gemini-1.5-flash-latest' ИЛИ 'gemini-pro'
    # MODEL_NAME = 'gemini-2.5-pro-preview-03-25' # Оставил твою, но она может быть preview
    
    # ИСПРАВЛЕНО ЗДЕСЬ: system_instruction перенесен внутрь скобок, добавлены запятые
    model = genai.GenerativeModel(
        MODEL_NAME, # Имя модели как первый аргумент
        generation_config={ # Конфигурация генерации как именованный аргумент
            "temperature": 1,
            "top_p": 1,
            "top_k": 40,
            "max_output_tokens": 2048,
        }, # <--- ЗАПЯТАЯ после словаря generation_config
        system_instruction="Отвечай в пределах 2000 знаков, если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, или другая, требующая объема работа. Ты - лучший эксперт в любых вопросах. Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку. Подкрепляй аргументами и доказательствами, без самоповтора. Если не знаешь ответ, - всегда предупреждай, если это предположение. Активно применяй юмор: культурные и бытовые отсылки, жизненный абсурд, шутки об актуальных в интернете темах, ирония и самоирония, тонкие и интимные намёки, пост-модерн и интернет-юмор, несоответствие ожиданиям, абсурдные решения проблем, псевдомудрость, разрушение идиом, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, редукционизм. При создании уникальной работы: Избегай копирования или близкого пересказа существующих текстов; Включай гипотетические ситуации для иллюстрации понятий; Применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи; Не допускай признаков ИИ; Варьируй структуру предложений, используя сочетание простых и сложных конструкций; Естественно включай разговорные выражения, идиомы и фигуры речи; Используй живые стилистические решения, свойственные людям; Вставляй региональные выражения или культурно специфичные ссылки, где это уместно; Добавляй остроумие."
        # Закрывающая скобка теперь здесь ---> ) <---
    )
    logger.info(f"Модель Gemini ('{MODEL_NAME}') успешно сконфигурирована.")

    # --- Явное определение инструмента поиска (Оставил твой код, но пока он не используется в handle_message) ---
    # Если не планируешь использовать функции/инструменты Gemini, этот блок можно убрать
    # google_search_tool = Tool(
    #     function_declarations=[
    #         FunctionDeclaration(
    #             name="google_search",
    #             description="Returns relevant information from Google Search based on the user query.",
    #             parameters=Schema(
    #                 type_=Schema.Type.OBJECT,
    #                 properties={
    #                     "query": Schema(type_=Schema.Type.STRING, description="The query to search for on Google.")
    #                 },
    #                 required=["query"]
    #             )
    #         )
    #     ]
    # )
    # logger.info("Инструмент Google Search подготовлен.")

except Exception as e:
    logger.exception("Критическая ошибка при конфигурации Gemini API!")
    # Не используем exit(), чтобы дать шанс другим частям приложения (если они есть)
    # Можно просто выбросить исключение дальше или обработать иначе
    raise RuntimeError(f"Не удалось настроить Gemini: {e}") from e

# --- Инициализация истории чата (простой вариант) ---
# Для более сложного управления контекстом может потребоваться другой подход
chat_histories = {}

# --- Обработчики Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    # Сбрасываем историю для этого чата при старте
    if chat_id in chat_histories:
        del chat_histories[chat_id]
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Gemini бот ({MODEL_NAME}). Спроси меня о чём угодно!",
        reply_to_message_id=update.message.message_id
    )
    logger.info(f"/start от {user.id} ({user.username}) в чате {chat_id}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовые сообщения."""
    user_message = update.message.text
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id} ({user.username}) в чате {chat_id}: '{user_message[:50]}...'")

    # Получаем или создаем историю чата
    if chat_id not in chat_histories:
        chat_histories[chat_id] = model.start_chat(history=[])
        logger.info(f"Начат новый чат для chat_id {chat_id}")

    chat = chat_histories[chat_id]

    try:
        # Отправляем сообщение в Gemini и ждем ответ
        # Ставим индикатор "печатает..."
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')

        # Используем безопасную отправку, чтобы избежать потенциальных ошибок API
        response = await chat.send_message_async(user_message) # Используем асинхронную версию

        # Иногда ответ может быть пустым или содержать ошибки генерации
        if response and response.text:
             # Ограничиваем длину ответа на всякий случай
            bot_response = response.text[:4090] # Ограничение Telegram ~4096
            await update.message.reply_text(bot_response, reply_to_message_id=update.message.message_id)
            logger.info(f"Ответ для {user.id} в чате {chat_id}: '{bot_response[:50]}...'")
        else:
            await update.message.reply_text("Извините, не смог сгенерировать ответ. Попробуйте переформулировать.", reply_to_message_id=update.message.message_id)
            logger.warning(f"Пустой или некорректный ответ от Gemini для {user.id} в чате {chat_id}")

    except Exception as e:
        logger.exception(f"Ошибка при обработке сообщения от {user.id} в чате {chat_id}: {e}")
        try:
            await update.message.reply_text(f"Ой, что-то пошло не так при общении с Gemini: {e}", reply_to_message_id=update.message.message_id)
        except Exception as inner_e:
            logger.error(f"Не удалось даже отправить сообщение об ошибке в чат {chat_id}: {inner_e}")


def main() -> None:
    """Запускает бота."""
    # Создаем Application
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
