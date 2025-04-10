# --- START OF REALLY x8 FULL CORRECTED main.py ---

import logging
import os
import asyncio
# ПРАВИЛЬНЫЙ ИМПОРТ для пакета google-genai
import google.genai as genai
import time
import random
# Попробуем импортировать types из google.genai
try:
    from google.genai import types as genai_types
    logger.info("Успешно импортирован types из google.genai") # Логгер тут еще не готов, но идея понятна
except ImportError:
    print("!!! НЕ УДАЛОСЬ импортировать types из google.genai.")
    class DummyTypes: pass
    genai_types = DummyTypes()
except NameError: # Если logger еще не определен
    print("!!! НЕ УДАЛОСЬ импортировать types из google.genai (logger не готов).")
    class DummyTypes: pass
    genai_types = DummyTypes()


from typing import Optional, Dict, Union, Any, Tuple, List
import urllib.parse

# --- Конфигурация логов ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__) # Определяем logger здесь

# Печать версии
try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")

# Исключения
try: from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
except ImportError: logger.warning("google.api_core.exceptions не найдены."); ResourceExhausted=Exception; GoogleAPIError=Exception; FailedPrecondition=Exception

# Библиотека Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Gemini типы для Struct
try: from google.protobuf.struct_pb2 import Struct
except ImportError: logger.warning("google.protobuf не найден. Struct не будет доступен."); Struct = dict

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

# --- Определение ВСТРОЕННОГО инструмента Google Search ---
google_search_tool = None
search_tool_type_used = None
try:
    if hasattr(genai_types, 'GoogleSearch'):
         google_search_config = genai_types.GoogleSearch()
         google_search_tool = genai_types.Tool(google_search=google_search_config)
         search_tool_type_used = "GoogleSearch (v2.0+)"
         logger.info(f"Инструмент '{search_tool_type_used}' определен.")
    elif hasattr(genai_types, 'GoogleSearchRetrieval'):
         google_search_retrieval_config = genai_types.GoogleSearchRetrieval()
         google_search_tool = genai_types.Tool(google_search_retrieval=google_search_retrieval_config)
         search_tool_type_used = "GoogleSearchRetrieval (v1.5)"
         logger.info(f"Инструмент '{search_tool_type_used}' определен.")
    else: logger.error("!!! Классы GoogleSearch/GoogleSearchRetrieval НЕ НАЙДЕНЫ.")
except AttributeError as e: logger.error(f"!!! Ошибка атрибута при поиске инструмента: {e}")
except Exception as e: logger.exception(f"!!! Ошибка при определении инструмента поиска: {e}")

# --- Загрузка и Настройка Моделей Gemini ---
LOADED_MODELS: Dict[str, genai.GenerativeModel] = {}
try:
    genai.configure(api_key=GOOGLE_API_KEY)
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
        if 'imagen' in model_id.lower(): logger.warning(f"'{alias}' пропущена."); continue
        current_tools = [google_search_tool] if google_search_tool else None
        try:
            model = genai.GenerativeModel(
                model_id,
                generation_config={"temperature": 0.8 if 'Flash' in alias else 1, "top_p": 1, "top_k": 40, "max_output_tokens": 2048},
                system_instruction=system_instruction_text,
                tools=current_tools
            )
            LOADED_MODELS[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Search: {'Enabled ('+search_tool_type_used+')' if current_tools else 'Disabled'}] загружена.")
        except Exception as e: logger.error(f"!!! ОШИБКА загрузки '{alias}': {e}")
    if not LOADED_MODELS: raise RuntimeError("Нет текстовых моделей!")
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS:
        try: DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS)); logger.warning(f"Дефолт: {DEFAULT_MODEL_ALIAS}")
        except StopIteration: raise RuntimeError("Нет моделей.")
except Exception as e: logger.exception("Крит. ошибка инициализации!"); exit(f"Крит. ошибка: {e}")

# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, Any] = {}

# --- Вспомогательная функция для извлечения текста ---
def extract_response_text(response) -> Optional[str]:
    """Извлекает текст из ответа Gemini, пробуя разные способы."""
    try: return response.text
    except ValueError:
        logger.warning("ValueError при извлечении text"); block_reason = getattr(response.prompt_feedback, 'block_reason', None) if hasattr(response, 'prompt_feedback') else None; block_reason_exists = hasattr(genai_types, 'BlockReason'); # ... (логирование причины) ...
        return None
    except AttributeError:
        logger.warning("Нет .text, пробуем parts.")
        try:
            if response.candidates and response.candidates[0].content.parts: parts_text = "".join(p.text for p in response.candidates[0].content.parts if hasattr(p, 'text')); return parts_text if parts_text else None
            else: logger.warning("Нет parts."); return None
        except Exception as e_inner: logger.error(f"Ошибка сборки из parts: {e_inner}"); return None

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Состояние для {chat_id} сброшено по /start")
    default_model_display_name = DEFAULT_MODEL_ALIAS
    search_status = f"включен ({search_tool_type_used})" if google_search_tool else "отключен"
    await update.message.reply_html( f"Привет, {user.mention_html()}! Я - Gemini бот.\nМодель по умолчанию: {default_model_display_name}\nИспользуйте /model для выбора.\n🔍 Поиск Google {search_status}.", reply_to_message_id=update.message.message_id)
    logger.info(f"/start от {user.id}")

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id; current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    keyboard = []; imagen_alias = '🖼️ Imagen 3 (Картинки!)'
    for alias in LOADED_MODELS.keys(): keyboard.append([InlineKeyboardButton(f"✅ {alias}" if alias == current_alias else alias, callback_data=alias)])
    if imagen_alias in AVAILABLE_MODELS and imagen_alias not in LOADED_MODELS: keyboard.append([InlineKeyboardButton(f"{imagen_alias} (Недоступна)", callback_data="imagen_info")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите модель:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

# ИСПРАВЛЕННАЯ select_model_callback
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
        # ИСПРАВЛЕННЫЙ БЛОК else
        try:
            await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        except Exception as e:
            logger.warning(f"Не удалось отредактировать разметку для {chat_id}: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Модель *{selected_alias}* уже выбрана.",
                parse_mode=ParseMode.MARKDOWN
            )

# handle_message (для встроенного поиска + предложения поиска)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id
    logger.info(f"Сообщение от {user.id}: '{user_message[:50]}...'")
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    selected_model_object = LOADED_MODELS.get(selected_alias)
    if not selected_model_object: logger.error(...); await update.message.reply_text("Крит. ошибка: Модель не найдена."); return
    final_text: Optional[str] = None; search_suggestions: List[str] = []; error_message: Optional[str] = None
    try:
        if chat_id not in chat_histories: chat_histories[chat_id] = selected_model_object.start_chat(history=[]); logger.info(...)
        current_chat_session = chat_histories[chat_id]; logger.info(f"Попытка с {selected_alias} (Встроенный поиск)")
        await context.bot.send_chat_action(...)
        response = await current_chat_session.send_message_async(content=user_message)
        logger.info(f"[{selected_alias}] Ответ получен. Проверка...")
        final_text = extract_response_text(response)
        if final_text is None: raise ValueError(f"Не удалось извлечь текст (причина: {getattr(response.prompt_feedback, 'block_reason', '?')})")
        if response.candidates and hasattr(response.candidates[0], 'grounding_metadata') and response.candidates[0].grounding_metadata:
             metadata = response.candidates[0].grounding_metadata
             if hasattr(metadata, 'web_search_queries') and metadata.web_search_queries: search_suggestions = list(metadata.web_search_queries); logger.info(f"[{selected_alias}] !!!! Предложения поиска: {search_suggestions}")
             else: logger.info(f"[{selected_alias}] meta без запросов.")
        else: logger.info(f"[{selected_alias}] НЕТ grounding_metadata.")
    except ResourceExhausted as e_limit: logger.warning(...); error_message = f"😔 '{selected_alias}' перегружена. /model"
    except FailedPrecondition as e_precondition:
        logger.error(f"Модель '{selected_alias}' FailedPrecondition: {e_precondition}. Сброс.")
        error_message = f"⚠️ История '{selected_alias}' сброшена. Повторите."
        if chat_id in chat_histories:
            del chat_histories[chat_id] # Отступ
    except ValueError as e_blocked: logger.warning(...); error_message = f"⚠️ {e_blocked}"
    except (GoogleAPIError, Exception) as e_other: logger.exception(...); error_message = f"Ошибка модели '{selected_alias}': {e_other}"
    # --- Отправка ответа ---
    reply_markup = None
    if search_suggestions:
        keyboard = []
        for suggestion in search_suggestions: search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(suggestion)}"; keyboard.append([InlineKeyboardButton(f"🔎 {suggestion}", url=search_url)])
        if keyboard: reply_markup = InlineKeyboardMarkup(keyboard); logger.info(...)
    if final_text:
        bot_response = final_text[:4090]
        try: await update.message.reply_text(bot_response, reply_to_message_id=update.message.message_id, reply_markup=reply_markup); logger.info(...)
        except Exception as e:
            logger.exception(f"Ошибка отправки ответа: {e}")
            try: await update.message.reply_text("Не смог отправить ответ AI.", reply_to_message_id=update.message.message_id)
            except Exception: pass
    elif error_message:
        try: await update.message.reply_text(error_message, reply_to_message_id=update.message.message_id); logger.info(...)
        except Exception as e: logger.error(...)
    else:
        logger.warning(f"Нет финального текста и сообщения об ошибке для {chat_id}.")
        if "История чата" not in (error_message or "") and "Ответ модели" not in (error_message or ""):
             try: await update.message.reply_text("Не удалось обработать запрос (неизвестная ошибка).", reply_to_message_id=update.message.message_id)
             except Exception: pass

# --- main ---
def main() -> None:
    """Запускает бота."""
    if not LOADED_MODELS: logger.critical("Модели не загружены!"); return
    if not google_search_tool: logger.warning(f"Встроенный поиск НЕ настроен (тип: {search_tool_type_used}).")
    else: logger.info(f"Встроенный поиск настроен (тип: {search_tool_type_used}).")
    logger.info("Инициализация Telegram...");
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    # Убрали /testsearch
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("model", select_model_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(select_model_callback))
    logger.info("Запуск бота...");
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()

# --- END OF REALLY x8 FULL CORRECTED main.py ---
