# --- START OF REALLY REALLY TRULY HONESTLY FULL CORRECTED main.py (No '...' placeholders ANYWHERE) ---

import logging
import os
import asyncio
import google.generativeai as genai
import time
import random
from typing import Optional, Dict, Union, Any, Tuple

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Исключения
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
# Библиотеки для Поиска и Парсинга
import httpx
from bs4 import BeautifulSoup
try:
    from googlesearch import search as google_search_sync
except ImportError:
    print("Библиотека googlesearch-python не найдена...")
    google_search_sync = None
else:
    if not callable(google_search_sync):
        print("Проблема с импортом googlesearch...")
        google_search_sync = None

# Gemini Function Calling типы
from google.protobuf.struct_pb2 import Struct

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY: exit("Google API ключ не найден")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    '⚡ Flash': 'gemini-2.0-flash-001',
    '🧠 Pro Exp': 'gemini-2.5-pro-exp-03-25',
    '🖼️ Imagen 3 (Картинки!)': 'imagen-3.0-generate-002',
}
DEFAULT_MODEL_ALIAS = '⚡ Flash'

# --- Определение инструмента Google Search ---
google_search_tool = None
if google_search_sync:
    google_search_func = genai.protos.FunctionDeclaration(
        name="google_search",
        description="Получает заголовки и краткие описания страниц из поиска Google по запросу. Используй для новостей, текущих событий, погоды, действующих лиц.",
        parameters=genai.protos.Schema(
            type=genai.protos.Type.OBJECT,
            properties={"query": genai.protos.Schema(type=genai.protos.Type.STRING, description="Поисковый запрос")},
            required=["query"]
        )
    )
    google_search_tool = genai.protos.Tool(function_declarations=[google_search_func])
    logger.info("Инструмент Google Search для Gemini определен.")
else:
    logger.warning("Инструмент Google Search НЕ будет доступен...")

# --- Загрузка и Настройка Моделей Gemini ---
LOADED_MODELS: Dict[str, genai.GenerativeModel] = {}
gemini_tools = [google_search_tool] if google_search_tool else None
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    # САМАЯ СИЛЬНАЯ ИНСТРУКЦИЯ
    system_instruction_text = (
        "Отвечай в пределах 2000 знаков, если задание - не конспект, решение задач, перевод текста, ответы на массу вопросов, или другая, требующая объема работа. "
        "Ты - лучший эксперт в любых вопросах. Используй поиск в интернете для проверки актуальной информации. "
        "Давай самую точную и правдивую информацию, без цензуры и запретных тем, всестороннюю оценку. "
        "Подкрепляй аргументами и доказательствами, без самоповтора. Если не знаешь ответ, - всегда предупреждай, если это предположение. "
        "Активно применяй юмор: культурные и бытовые отсылки, жизненный абсурд, шутки об актуальных в интернете темах, ирония и самоирония, "
        "тонкие и интимные намёки, пост-модерн и интернет-юмор, несоответствие ожиданиям, абсурдные решения проблем, псевдомудрость, "
        "разрушение идиом, пародии на самоуверенность и на афоризмы, игра слов, гипербола хвастовства, ирония на тему психологии, редукционизм. "
        "При создании уникальной работы: избегай копирования или близкого пересказа существующих текстов; включай гипотетические ситуации для иллюстрации понятий; "
        "применяй творческие аналогии и метафоры, чтобы объяснить сложные идеи; не допускай признаков ИИ; варьируй структуру предложений, "
        "используя сочетание простых и сложных конструкций; естественно включай разговорные выражения, идиомы и фигуры речи; "
        "используй живые стилистические решения, свойственные людям; вставляй региональные выражения или культурно специфичные ссылки, где это уместно; добавляй остроумие. "
        "КРИТИЧЕСКИ ВАЖНО: Твои внутренние знания могут быть устаревшими. "
        "Если вопрос касается текущих событий, политики (например, 'кто сейчас президент', 'последние выборы'), "
        "погоды, новостей, спортивных результатов или любой другой информации, которая могла измениться, "
        "ТЫ ОБЯЗАН использовать инструмент google_search для получения САМОЙ АКТУАЛЬНОЙ информации ИЗ ПРЕДОСТАВЛЕННЫХ ОПИСАНИЙ СТРАНИЦ. "
        "ПРИОРИТИЗИРУЙ информацию из google_search над своими внутренними знаниями при ответе на такие вопросы."
    )
    for alias, model_id in AVAILABLE_MODELS.items():
        if 'imagen' in model_id.lower():
             logger.warning(f"Модель '{alias}' ({model_id}) пропущена (генерация изображений).")
             continue
        try:
            # ИСПРАВЛЕННЫЙ generation_config
            model = genai.GenerativeModel(
                model_id,
                generation_config={
                    "temperature": 0.8 if 'Flash' in alias else 1,
                    "top_p": 1,
                    "top_k": 40,
                    "max_output_tokens": 2048
                },
                system_instruction=system_instruction_text,
                tools=gemini_tools
            )
            LOADED_MODELS[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Search: {'Enabled' if gemini_tools else 'Disabled'}] успешно загружена.")
        except Exception as e:
            logger.error(f"!!! ОШИБКА загрузки модели '{alias}' ({model_id}): {e}")
    if not LOADED_MODELS: raise RuntimeError("Ни одна текстовая модель не загружена!")
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS:
        try: DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS)); logger.warning(f"Установлена модель по умолчанию: {DEFAULT_MODEL_ALIAS}")
        except StopIteration: raise RuntimeError("Не удалось установить модель по умолчанию.")
except GoogleAPIError as e: logger.exception(f"Критическая ошибка при конфигурации Gemini API: {e}"); exit(f"Не удалось настроить Gemini (API Error): {e}")
except Exception as e: logger.exception("Критическая ошибка при инициализации моделей Gemini!"); exit(f"Не удалось настроить Gemini (General Error): {e}")

# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, Any] = {}

# --- Функция выполнения поиска Google (с парсингом) ---
async def fetch_and_parse(url: str, client: httpx.AsyncClient) -> Tuple[Optional[str], Optional[str]]:
    """Вспомогательная функция для загрузки и парсинга одной страницы."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = await client.get(url, timeout=7.0, follow_redirects=True, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        title = soup.title.string.strip() if soup.title else None
        description = None
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            description = meta_desc['content'].strip()
        else:
            first_p = soup.find('p')
            if first_p: description = first_p.get_text().strip()
        if description and len(description) > 150: description = description[:150] + "..."
        if title: logger.info(f"Успешно спарсен title для {url}")
        return title, description
    except httpx.TimeoutException: logger.warning(f"!!!! Таймаут URL: {url}"); return None, None
    except httpx.RequestError as e: logger.warning(f"!!!! Ошибка сети URL {url}: {e}"); return None, None
    except Exception as e: logger.warning(f"!!!! Ошибка парсинга URL {url}: {e}"); return None, None

async def perform_google_search(query: str, num_results: int = 3) -> str:
    """Выполняет поиск Google и возвращает заголовки/сниппеты первых результатов."""
    if not google_search_sync: return "Ошибка: Функция поиска недоступна."
    logger.info(f"!!!! Начало Google поиска (с парсингом): '{query}'")
    formatted_results = f"Результаты поиска Google по запросу '{query}':\n\n"
    urls_to_fetch = []
    try:
        search_results = await asyncio.to_thread(google_search_sync, query, num_results=num_results, lang="ru")
        urls_to_fetch = list(search_results)
        if not urls_to_fetch: logger.warning(f"!!!! Google поиск по '{query}' не дал URL."); return formatted_results + "Поиск Google не дал результатов."
        logger.info(f"!!!! Google поиск нашел {len(urls_to_fetch)} URL.")
    except Exception as e: logger.exception(f"!!!! ОШИБКА на этапе Google поиска '{query}': {e}"); return formatted_results + f"Ошибка Google поиска: {e}"
    async with httpx.AsyncClient() as client:
        tasks = [fetch_and_parse(url, client) for url in urls_to_fetch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    processed_count = 0
    for i, result in enumerate(results):
        if isinstance(result, Exception) or result is None: logger.warning(f"!!!! Ошибка обработки URL #{i+1}: {result}"); continue
        title, description = result
        if title:
            processed_count += 1
            formatted_results += f"{processed_count}. {title}\n"
            if description: formatted_results += f"   - {description}\n"
            formatted_results += f"   URL: {urls_to_fetch[i]}\n\n"
    if processed_count == 0: logger.warning(f"!!!! Не удалось извлечь контент для '{query}'."); formatted_results += "(Не удалось извлечь контент)"
    logger.info(f"!!!! РЕЗУЛЬТАТ ДЛЯ GEMINI (начало): {formatted_results[:300]}...")
    return formatted_results[:2500]

# --- Вспомогательная функция для обработки хода Gemini ---
async def process_gemini_chat_turn(
    chat_session, model_name: str, initial_content, context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> str:
    """Обрабатывает один ход диалога с Gemini, включая Function Calling (для v0.7.1)."""
    current_message_or_response = initial_content
    is_function_response = isinstance(current_message_or_response, genai.protos.FunctionResponse)

    for attempt in range(5):
        logger.info(f"[{model_name}] Итерация {attempt+1}. Отправка {'ОТВЕТА НА ФУНКЦИЮ' if is_function_response else 'СООБЩЕНИЯ'}.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        content_to_send = current_message_or_response
        if is_function_response: logger.info(f"[{model_name}] Отправляем FunctionResponse: {current_message_or_response.name}")
        else: logger.info(f"[{model_name}] Отправляем строку: {str(content_to_send)[:100]}...")
        try:
            logger.info(f"[{model_name}] !!! НАЧАЛО вызова send_message_async...")
            response = await chat_session.send_message_async(content=content_to_send)
            logger.info(f"[{model_name}] !!! ЗАВЕРШЕНИЕ вызова send_message_async.")
            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0]
                logger.info(f"[{model_name}] ПОЛУЧЕНА ЧАСТЬ: {part}")
                if hasattr(part, 'function_call') and part.function_call and part.function_call.name == "google_search":
                    function_call = part.function_call
                    logger.info(f"[{model_name}] !!!! ОБНАРУЖЕН ВЫЗОВ ФУНКЦИИ google_search.")
                    if not google_search_tool:
                         logger.error(f"[{model_name}] !!! Инструмент поиска не настроен!")
                         s_err = Struct(); s_err.update({"content": "Ошибка: Функция поиска не настроена."})
                         current_message_or_response = genai.protos.FunctionResponse(name="google_search", response=s_err)
                         continue
                    args = {key: value for key, value in function_call.args.items()}
                    query = args.get("query")
                    logger.info(f"[{model_name}] Извлечен поисковый запрос: '{query}'")
                    if query:
                        logger.info(f"[{model_name}] !!! НАЧАЛО вызова perform_google_search...")
                        search_result = await perform_google_search(query)
                        logger.info(f"[{model_name}] !!! ЗАВЕРШЕНИЕ вызова perform_google_search...")
                        s_res = Struct(); s_res.update({"content": search_result})
                        current_message_or_response = genai.protos.FunctionResponse(name="google_search", response=s_res)
                        logger.info(f"[{model_name}] Подготовлен FunctionResponse для отправки.")
                        continue
                    else:
                         logger.warning(f"[{model_name}] !!! Вызов google_search без 'query'.")
                         s_err = Struct(); s_err.update({"content": "Ошибка: Параметр 'query' не предоставлен."})
                         current_message_or_response = genai.protos.FunctionResponse(name="google_search", response=s_err)
                         continue
                else: # Не function call
                    try:
                        logger.info(f"[{model_name}] Это не вызов функции, извлекаем текст...")
                        final_text = response.text
                        logger.info(f"[{model_name}] Получен финальный текстовый ответ.")
                        return final_text
                    except ValueError as e:
                         reason = getattr(response.prompt_feedback, 'block_reason', 'Неизвестно') if hasattr(response, 'prompt_feedback') else 'Неизвестно'
                         raise ValueError(f"Ответ модели {model_name} заблокирован. Причина: {reason}") from e
                    except AttributeError:
                        logger.warning(f"[{model_name}] !!! Ответ не содержит атрибута .text")
                        try:
                            final_text = "".join(p.text for p in response.parts if hasattr(p, 'text'))
                            if final_text: logger.info(f"[{model_name}] Текст собран из частей."); return final_text
                            else: raise Exception("Нет текста в .parts")
                        except Exception as e_inner: raise Exception("Не удалось извлечь текст") from e_inner
            else: # Пустой ответ
                 reason = getattr(response.prompt_feedback, 'block_reason', 'Неизвестно') if hasattr(response, 'prompt_feedback') else 'Неизвестно'
                 if reason != 'BLOCK_REASON_UNSPECIFIED': raise ValueError(f"Пустой ответ {model_name} заблокирован: {reason}")
                 raise Exception(f"Модель {model_name} вернула пустой ответ.")
        except (ResourceExhausted, FailedPrecondition, GoogleAPIError) as e: logger.error(f"[{model_name}] !!! Ошибка API: {e}"); raise e
        except ValueError as ve: logger.error(f"[{model_name}] !!! Ошибка ValueError (блокировка?): {ve}"); raise ve
        except Exception as e: logger.exception(f"[{model_name}] !!! Непредвиденная ошибка в цикле: {e}"); raise e
    logger.error(f"[{model_name}] !!! Превышен лимит ({attempt+1}) обработки функций."); raise Exception(f"Превышен лимит обработки функций для {model_name}.")

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Выбор модели и история чата сброшены для {chat_id} по команде /start")
    default_model_display_name = DEFAULT_MODEL_ALIAS
    search_status = "включен (если нужна)" if google_search_tool else "отключен"
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я - Gemini бот.\n"
        f"Модель по умолчанию: {default_model_display_name}\n"
        f"Используйте /model для выбора другой модели.\n"
        f"🔍 Поиск Google {search_status}.",
        reply_to_message_id=update.message.message_id
    )
    logger.info(f"/start от {user.id}")

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    keyboard = []
    for alias in LOADED_MODELS.keys():
        text = f"✅ {alias}" if alias == current_alias else alias
        keyboard.append([InlineKeyboardButton(text, callback_data=alias)])
    imagen_alias = '🖼️ Imagen 3 (Картинки!)'
    if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS:
         keyboard.append([InlineKeyboardButton(f"{imagen_alias} (Недоступна для чата)", callback_data="imagen_info")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Текущая модель: *{current_alias}*\n\nВыберите модель для общения:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    selected_alias = query.data
    chat_id = query.message.chat_id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    if selected_alias == "imagen_info":
        await context.bot.send_message(chat_id=chat_id, text="Модель Imagen не может использоваться для чата.")
        return
    if selected_alias not in LOADED_MODELS:
        await query.edit_message_text(text="Ошибка: Модель недоступна.")
        return
    if selected_alias != current_alias:
        user_selected_model[chat_id] = selected_alias
        logger.info(f"Пользователь {chat_id} сменил модель на '{selected_alias}'")
        if chat_id in chat_histories:
            del chat_histories[chat_id]
            logger.info(f"История чата для {chat_id} сброшена.")
        keyboard = []
        for alias in LOADED_MODELS.keys():
            text = f"✅ {alias}" if alias == selected_alias else alias
            keyboard.append([InlineKeyboardButton(text, callback_data=alias)])
        imagen_alias = '🖼️ Imagen 3 (Картинки!)'
        if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS:
             keyboard.append([InlineKeyboardButton(f"{imagen_alias} (Недоступна для чата)", callback_data="imagen_info")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=f"✅ Модель изменена на: *{selected_alias}*\n⚠️ История сброшена.\n\nВыберите модель:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        try: await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        except: await context.bot.send_message(chat_id=chat_id, text=f"Модель *{selected_alias}* уже выбрана.", parse_mode=ParseMode.MARKDOWN)

async def test_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args)
    chat_id = update.effective_chat.id
    if not query: await update.message.reply_text("Укажите запрос после /testsearch"); return
    logger.info(f"Тестовый поиск для {chat_id}: '{query}'")
    await update.message.reply_text(f"Выполняю тестовый поиск: '{query}'...")
    try:
        search_result = await perform_google_search(query)
        logger.info(f"Тестовый поиск для {chat_id} вернул: {search_result[:200]}...")
        await update.message.reply_text(f"Результат поиска:\n\n{search_result[:4000]}", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.exception(f"Ошибка тестового поиска для {chat_id}: {e}")
        await update.message.reply_text(f"Ошибка тестового поиска: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    selected_model_object = LOADED_MODELS.get(selected_alias)
    if not selected_model_object:
        logger.error(f"Выбранная модель '{selected_alias}' для {chat_id} не найдена!")
        selected_alias = DEFAULT_MODEL_ALIAS; selected_model_object = LOADED_MODELS.get(DEFAULT_MODEL_ALIAS)
        if not selected_model_object: await update.message.reply_text("Крит. ошибка: Модели не найдены."); return
        else: await update.message.reply_text(f"Ошибка: Использую модель {selected_alias}"); user_selected_model[chat_id] = selected_alias
    final_text: Optional[str] = None; error_message: Optional[str] = None
    try:
        if chat_id not in chat_histories: chat_histories[chat_id] = selected_model_object.start_chat(history=[]); logger.info(f"Начат новый чат {chat_id} с '{selected_alias}'")
        current_chat_session = chat_histories[chat_id]; logger.info(f"Попытка с моделью: {selected_alias}")
        final_text = await process_gemini_chat_turn(current_chat_session, selected_alias, user_message, context, chat_id)
    except ResourceExhausted as e_limit: logger.warning(f"Модель '{selected_alias}' квота: {e_limit}"); error_message = f"😔 Модель '{selected_alias}' перегружена. /model"
    except FailedPrecondition as e_precondition:
        logger.error(f"Модель '{selected_alias}' FailedPrecondition: {e_precondition}. Сброс истории.")
        error_message = f"⚠️ История чата с моделью '{selected_alias}' стала слишком длинной. Я ее сбросил. Повторите запрос."
        if chat_id in chat_histories:
            del chat_histories[chat_id] # Отступ
    except ValueError as e_blocked: logger.warning(f"Модель '{selected_alias}' блокировка: {e_blocked}"); error_message = f"⚠️ {e_blocked}"
    except (GoogleAPIError, Exception) as e_other: logger.exception(f"Ошибка '{selected_alias}': {e_other}"); error_message = f"Ошибка модели '{selected_alias}': {e_other}"
    if final_text:
        bot_response = final_text[:4090]
        try:
            await update.message.reply_text(bot_response, reply_to_message_id=update.message.message_id)
            logger.info(f"Ответ от '{selected_alias}' отправлен {user.id}")
        except Exception as e:
            # ИСПРАВЛЕННЫЙ БЛОК
            logger.exception(f"Ошибка отправки ответа: {e}")
            try:
                await update.message.reply_text("Не смог отправить ответ AI.", reply_to_message_id=update.message.message_id) # Новая строка
            except Exception:
                pass # Новая строка
    elif error_message:
        try:
            await update.message.reply_text(error_message, reply_to_message_id=update.message.message_id)
            logger.info(f"Ошибка отправлена: {error_message[:100]}...")
        except Exception as e:
            logger.error(f"Не удалось отправить ошибку '{error_message[:100]}...': {e}")
    else:
        logger.warning(f"Нет текста и ошибки для {chat_id}.")
        if "История чата" not in (error_message or "") and "Ответ модели" not in (error_message or "") :
            try: await update.message.reply_text("Не удалось обработать запрос.", reply_to_message_id=update.message.message_id)
            except Exception: pass

# --- main ---
def main() -> None:
    """Запускает бота."""
    if not LOADED_MODELS: logger.critical("Модели не загружены!"); return
    if not google_search_sync: logger.warning("Запуск БЕЗ поиска Google.")
    logger.info("Инициализация Telegram...");
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(CommandHandler("testsearch", test_search))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    logger.info("Запуск бота...");
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()

# --- END OF REALLY REALLY TRULY HONESTLY FULL CORRECTED main.py ---
