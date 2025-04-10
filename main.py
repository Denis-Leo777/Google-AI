# --- START OF REALLY x17 FULL CORRECTED main.py (USING google-genai CLIENT) ---

import logging
import os
import asyncio
# Используем правильную библиотеку
import google.genai as genai
import time
import random

# Импорт типов из google.genai.types (как в документации)
try:
    # Импортируем классы, используемые в коде и примерах
    from google.genai import types as genai_types
    # Явно импортируем нужные классы для удобства
    from google.genai.types import Tool, GenerateContentConfig, GoogleSearch, Content, Part
    # Добавим типы для обработки ошибок/ответов, если они понадобятся
    from google.generativeai.types import generation_types # Используем псевдоним genai, но типы могут быть здесь
    BlockedPromptException = generation_types.BlockedPromptException
    StopCandidateException = generation_types.StopCandidateException
    FinishReason = generation_types.FinishReason
    HarmCategory = generation_types.HarmCategory # Используем этот, если он основной
    HarmProbability = generation_types.HarmProbability
    #BlockReason = generation_types.BlockReason # Если он есть

    print("INFO: Успешно импортированы типы из google.genai.types и google.generativeai.types")

except ImportError as e:
    print(f"!!! НЕ УДАЛОСЬ импортировать типы из google.genai.types / google.generativeai.types: {e}. Используем заглушки.")
    # Создаем заглушки, чтобы код не падал
    class DummyTypes: pass; Tool = DummyTypes; GenerateContentConfig = DummyTypes; GoogleSearch = DummyTypes; Content = dict; Part = dict
    class DummyGenAITypes:
        class DummyEnum:
            FINISH_REASON_UNSPECIFIED = 0; STOP = 1; MAX_TOKENS = 2; SAFETY = 3; RECITATION = 4; OTHER = 5
        class DummyHarmEnum:
             HARM_CATEGORY_UNSPECIFIED = 0; HARM_CATEGORY_DEROGATORY = 1; HARM_CATEGORY_TOXICITY = 2; HARM_CATEGORY_VIOLENCE = 3; HARM_CATEGORY_SEXUAL = 4; HARM_CATEGORY_MEDICAL = 5; HARM_CATEGORY_DANGEROUS = 6; HARM_CATEGORY_HARASSMENT = 7; HARM_CATEGORY_HATE_SPEECH = 8; HARM_CATEGORY_SEXUALLY_EXPLICIT = 9; HARM_CATEGORY_DANGEROUS_CONTENT = 10
        class DummyHarmProb:
             HARM_PROBABILITY_UNSPECIFIED = 0; NEGLIGIBLE = 1; LOW = 2; MEDIUM = 3; HIGH = 4
        FinishReason = DummyEnum()
        HarmCategory = DummyHarmEnum()
        HarmProbability = DummyHarmProb()
    FinishReason = DummyGenAITypes.FinishReason
    HarmCategory = DummyGenAITypes.HarmCategory
    HarmProbability = DummyGenAITypes.HarmProbability
    BlockedPromptException = ValueError # Заглушка для исключения
    StopCandidateException = Exception # Заглушка для исключения


from typing import Optional, Dict, Union, Any, Tuple, List
import urllib.parse

# Конфигурация логов
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Печать версии
try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")

# Исключения (Импортируем стандартные API ошибки)
try:
    from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition, InvalidArgument
    logger.info("Исключения google.api_core.exceptions успешно импортированы.")
except ImportError:
    logger.warning("!!! НЕ УДАЛОСЬ импортировать google.api_core.exceptions. Используем базовый Exception.")
    ResourceExhausted=Exception; GoogleAPIError=Exception; FailedPrecondition=Exception; InvalidArgument=ValueError

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Protobuf Struct (Может не использоваться, но оставим на всякий случай)
try:
    from google.protobuf.struct_pb2 import Struct
    logger.info("google.protobuf.struct_pb2.Struct успешно импортирован.")
except ImportError:
    logger.warning("!!! НЕ УДАЛОСЬ импортировать google.protobuf. Используем dict вместо Struct.")
    Struct = dict

# Токены
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Проверка токенов ---
if not TELEGRAM_BOT_TOKEN: exit("Telegram токен не найден")
if not GOOGLE_API_KEY:
    logger.critical("Ключ Google API (GOOGLE_API_KEY) не найден в переменных окружения!")
    exit("Google API ключ не найден")
else:
    logger.info("Переменная окружения GOOGLE_API_KEY найдена.")

# --- СОЗДАНИЕ КЛИЕНТА GENAI --- ИЗМЕНЕНО: Используем genai.Client
try:
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    logger.info("Клиент google.genai.Client успешно создан.")
    # Проверим доступность моделей (опционально, но полезно для отладки)
    # try:
    #     available_models_list = [m.name for m in gemini_client.models.list()]
    #     logger.info(f"Доступные модели через API: {available_models_list}")
    # except Exception as e_list:
    #     logger.warning(f"Не удалось получить список моделей через API: {e_list}")

except Exception as e:
    logger.exception("!!! КРИТИЧЕСКАЯ ОШИБКА при создании google.genai.Client!")
    exit("Ошибка создания клиента Gemini.")


# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ --- (Оставляем твои имена)
# В этом подходе мы не "загружаем" модели заранее, а просто храним их имена
AVAILABLE_MODELS = {
    '⚡ Flash 2.0': 'models/gemini-2.0-flash-001',
    '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25',
}
if not AVAILABLE_MODELS:
     exit("Нет определенных моделей в AVAILABLE_MODELS!")
DEFAULT_MODEL_ALIAS = '⚡ Flash 2.0'
if DEFAULT_MODEL_ALIAS not in AVAILABLE_MODELS:
     DEFAULT_MODEL_ALIAS = next(iter(AVAILABLE_MODELS))
     logger.warning(f"Дефолтная модель не найдена, установлена первая: {DEFAULT_MODEL_ALIAS}")

# --- КОНФИГУРАЦИЯ ИНСТРУМЕНТА ПОИСКА --- ИЗМЕНЕНО: Используем Tool и GoogleSearch
google_search_tool = None
search_tool_type_used = "GoogleSearch (for 2.0+)"
try:
    # Создаем инструмент поиска согласно документации для моделей 2.0
    google_search_tool = Tool(google_search=GoogleSearch())
    logger.info(f"Инструмент поиска '{search_tool_type_used}' успешно сконфигурирован.")
except NameError:
     logger.error("!!! Класс 'Tool' или 'GoogleSearch' не найден (ошибка импорта типов). Поиск будет недоступен.")
     google_search_tool = None
     search_tool_type_used = "N/A (import error)"
except Exception as e:
    logger.exception(f"!!! Ошибка при создании инструмента поиска: {e}")
    google_search_tool = None
    search_tool_type_used = "N/A (creation error)"


# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
# ИЗМЕНЕНО: Храним историю в формате [{'role': 'user'/'model', 'parts': [{'text': ...}]}]
chat_histories: Dict[int, List[Dict[str, Any]]] = {}

# --- СИСТЕМНЫЙ ПРОМПТ --- (Определяем один раз)
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
# Создаем объект Content для системной инструкции, если библиотека его поддерживает
system_instruction_content = None
try:
     # Формат может отличаться, пробуем стандартный
     system_instruction_content = Content(parts=[Part(text=system_instruction_text)], role="system") # Role 'system' может не поддерживаться всеми моделями/API
     logger.info("Системная инструкция создана как объект Content.")
except Exception as e_sys:
     logger.warning(f"Не удалось создать Content для системной инструкции ({e_sys}). Будет использоваться как текст.")
     system_instruction_content = system_instruction_text # Используем как строку


# --- Вспомогательная функция для извлечения текста --- (Адаптирована под client.models.generate_content)
def extract_response_text(response) -> Optional[str]:
    """Извлекает текст из ответа client.models.generate_content."""
    try:
        # Документация показывает доступ через response.text
        return response.text
    except ValueError as e_val:
        # Обработка потенциальной блокировки или пустого ответа
        logger.warning(f"ValueError при извлечении response.text: {e_val}")
        try: # Используем try-except для безопасного доступа к атрибутам
             # Ищем причину блокировки в candidates -> safety_ratings или finish_reason
             if response.candidates:
                 candidate = response.candidates[0]
                 finish_reason = getattr(candidate, 'finish_reason', None)
                 safety_ratings = getattr(candidate, 'safety_ratings', [])

                 # Собираем сообщение об ошибке
                 error_parts = []
                 if finish_reason and finish_reason not in (FinishReason.FINISH_REASON_UNSPECIFIED, FinishReason.STOP):
                      finish_reason_name = getattr(FinishReason, '_enum_map', {}).get(finish_reason, finish_reason) # Пытаемся получить имя
                      error_parts.append(f"Причина остановки: {finish_reason_name}")

                 relevant_ratings = [f"{getattr(HarmCategory, '_enum_map', {}).get(r.category, r.category)}: {getattr(HarmProbability, '_enum_map', {}).get(r.probability, r.probability)}"
                                     for r in safety_ratings if hasattr(r, 'probability') and r.probability not in (HarmProbability.HARM_PROBABILITY_UNSPECIFIED, HarmProbability.NEGLIGIBLE)]
                 if relevant_ratings:
                      error_parts.append(f"Фильтры безопасности: {', '.join(relevant_ratings)}")

                 if error_parts:
                      return f"⚠️ Не удалось получить ответ. {' '.join(error_parts)}."

             # Проверим и prompt_feedback на всякий случай
             prompt_feedback = getattr(response, 'prompt_feedback', None)
             if prompt_feedback and getattr(prompt_feedback, 'block_reason', None):
                 reason = getattr(prompt_feedback.block_reason, 'name', prompt_feedback.block_reason)
                 return f"⚠️ Не удалось получить ответ. Блокировка: {reason}."

             # Если причин не найдено
             logger.warning("Не удалось извлечь текст и не найдено явных причин блокировки/ошибки.")
             return None

        except (AttributeError, IndexError, Exception) as e_details:
             logger.warning(f"Ошибка при попытке получить детали ошибки из ответа: {e_details}")
             return None # Не удалось получить детали, возвращаем None

    except AttributeError:
        # Если у ответа нет .text, пробуем собрать из parts (как в примере из документации)
        logger.warning("Ответ не имеет атрибута .text. Попытка извлечь из parts.")
        try:
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                parts_text = "".join(p.text for p in response.candidates[0].content.parts if hasattr(p, 'text'))
                return parts_text.strip() if parts_text and parts_text.strip() else None
            else:
                logger.warning("Не найдено candidates или parts для извлечения текста.")
                return None
        except (AttributeError, IndexError, Exception) as e_inner:
            logger.error(f"Ошибка при сборке текста из parts: {e_inner}")
            return None
    except Exception as e:
        logger.exception(f"Неожиданная ошибка при извлечении текста ответа: {e}")
        return None

# --- ОБРАБОТЧИКИ TELEGRAM ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    user = update.effective_user; chat_id = update.effective_chat.id
    if chat_id in user_selected_model: del user_selected_model[chat_id]
    if chat_id in chat_histories: del chat_histories[chat_id]
    logger.info(f"Обработка /start для пользователя {user.id} в чате {chat_id}. Состояние сброшено.")
    actual_default_model = DEFAULT_MODEL_ALIAS
    search_status = "включен (если поддерживается моделью)" if google_search_tool else "отключен (ошибка конфигурации)"
    await update.message.reply_html(
        rf"Привет, {user.mention_html()}! Бот Gemini на связи (через genai.Client)."
        f"\n\nТекущая модель: <b>{actual_default_model}</b>"
        f"\n🔍 Поиск Google: <b>{search_status}</b>."
        f"\n\n/model - выбрать модель."
        f"\n/start - сбросить чат."
        f"\n\nСпрашивай!",
        reply_to_message_id=update.message.message_id
    )

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /model."""
    chat_id = update.effective_chat.id; current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS); keyboard = []
    # Используем AVAILABLE_MODELS, т.к. модели не "загружены" заранее
    for alias in AVAILABLE_MODELS.keys():
        button_text = f"✅ {alias}" if alias == current_alias else alias
        keyboard.append([InlineKeyboardButton(button_text, callback_data=alias)])
    if not keyboard: await update.message.reply_text("Нет доступных моделей для выбора."); return
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Текущая модель: *{current_alias}*\n\nВыберите новую:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия на кнопку выбора модели."""
    query = update.callback_query; await query.answer(); selected_alias = query.data; chat_id = query.message.chat_id; user_id = query.from_user.id
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    # Проверяем, что выбранный alias есть в нашем словаре доступных моделей
    if selected_alias not in AVAILABLE_MODELS:
        logger.error(f"Пользователь {user_id} в чате {chat_id} выбрал недоступный alias: {selected_alias}")
        try: await query.edit_message_text(text="❌ Ошибка: Неизвестный выбор модели.")
        except Exception as e: logger.warning(f"Не удалось отредактировать сообщение об ошибке выбора модели для {chat_id}: {e}")
        return
    if selected_alias == current_alias:
        logger.info(f"Пользователь {user_id} в чате {chat_id} перевыбрал ту же модель: {selected_alias}")
        try: await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        except Exception as e: logger.warning(f"Не удалось убрать 'загрузку' с кнопки для {chat_id}: {e}")
        return
    # Устанавливаем выбранную модель (alias)
    user_selected_model[chat_id] = selected_alias; logger.info(f"Пользователь {user_id} в чате {chat_id} сменил модель с '{current_alias}' на '{selected_alias}'")
    reset_message = "";
    # Сбрасываем историю при смене модели
    if chat_id in chat_histories: del chat_histories[chat_id]; logger.info(f"История чата {chat_id} сброшена."); reset_message = "\n⚠️ История чата сброшена."
    keyboard = [];
    for alias in AVAILABLE_MODELS.keys(): # Снова используем AVAILABLE_MODELS
        button_text = f"✅ {alias}" if alias == selected_alias else alias
        keyboard.append([InlineKeyboardButton(button_text, callback_data=alias)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(text=f"✅ Модель: *{selected_alias}*!{reset_message}\n\nВыберите другую или начните чат:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение после смены модели для {chat_id}: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"Модель изменена на *{selected_alias}*!{reset_message}", parse_mode=ParseMode.MARKDOWN)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений."""
    if not update.message or not update.message.text: logger.warning("Получено пустое сообщение."); return
    user_message = update.message.text; user = update.effective_user; chat_id = update.effective_chat.id; message_id = update.message.message_id
    logger.info(f"Сообщение от {user.id} в чате {chat_id} ({len(user_message)}): '{user_message[:80].replace(chr(10), ' ')}...'")

    # Получаем alias выбранной модели и её ID (имя для API)
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    model_id = AVAILABLE_MODELS.get(selected_alias)
    if not model_id:
        logger.error(f"Критическая ошибка: Не найден ID модели для alias '{selected_alias}' (чат {chat_id})."); await update.message.reply_text("Ой! Ошибка конфигурации моделей.", reply_to_message_id=message_id); return

    final_text: Optional[str] = None; search_suggestions: List[str] = []; error_message: Optional[str] = None; start_time = time.monotonic()

    try:
        # --- Подготовка истории для API ---
        current_history = chat_histories.get(chat_id, [])
        # Формируем `contents` для API: история + новое сообщение пользователя
        # Добавляем системную инструкцию, если она есть и в формате Content
        api_contents = []
        # if system_instruction_content and isinstance(system_instruction_content, Content):
        #     api_contents.append(system_instruction_content) # Системную инструкцию лучше передавать в model.generate_content, если есть параметр
        api_contents.extend(current_history)
        api_contents.append({'role': 'user', 'parts': [{'text': user_message}]})

        logger.info(f"Отправка запроса к '{model_id}' для {chat_id}. История: {len(current_history)} сообщ.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # --- Конфигурация запроса (включая поиск) ---
        generation_config = None
        tools_config = [google_search_tool] if google_search_tool else None
        try:
             generation_config = GenerateContentConfig(
                  tools=tools_config,
                  # response_modalities=["TEXT"], # Как в примере, но может быть необязательно
                  # можно добавить другие параметры: temperature, top_p, top_k, max_output_tokens
             )
        except NameError:
             logger.error("Класс GenerateContentConfig не найден. Запрос будет без конфига.")
             generation_config = None # Не удалось создать конфиг


        # --- Вызов API ---
        # Используем client.models.generate_content
        # Обратите внимание: обработка ошибок может быть синхронной,
        # для полной асинхронности потребовался бы aiohttp/httpx и REST API напрямую.
        response = gemini_client.models.generate_content(
            model=model_id,
            contents=api_contents,
            generation_config=generation_config,
             # stream=False, # По умолчанию False
             # system_instruction=system_instruction_content # Передаем системную инструкцию сюда, если поддерживается
             # safety_settings=... # Можно добавить настройки безопасности
        )

        processing_time = time.monotonic() - start_time
        logger.info(f"Ответ от '{model_id}' для {chat_id} получен за {processing_time:.2f} сек.")

        # --- Обработка ответа ---
        final_text = extract_response_text(response)

        # Обновление истории, если ответ успешен
        if final_text and not final_text.startswith("⚠️"):
            # Добавляем сообщение пользователя (уже добавили в api_contents)
            # Добавляем ответ модели
            current_history.append({'role': 'model', 'parts': [{'text': final_text}]})
            chat_histories[chat_id] = current_history # Сохраняем обновленную историю
            logger.info(f"История чата {chat_id} обновлена, теперь {len(current_history)} сообщений.")
        elif final_text and final_text.startswith("⚠️"):
            error_message = final_text # Используем ошибку из extract_response_text
            final_text = None
            # Историю не обновляем, т.к. ответа модели не было
            logger.warning(f"Ответ для {chat_id} был ошибкой, история не обновлена.")
        else: # final_text is None
            if not error_message: # Если API не вернуло ошибку, но текст извлечь не удалось
                 error_message = "⚠️ Получен пустой или некорректный ответ от модели."
            # Историю не обновляем
            logger.warning(f"Не удалось извлечь текст для {chat_id}, история не обновлена.")


        # --- Извлечение поисковых предложений/источников ---
        if hasattr(response, 'candidates') and response.candidates:
             try:
                 candidate = response.candidates[0]
                 grounding_metadata = getattr(candidate, 'grounding_metadata', None)
                 if grounding_metadata:
                     # Ищем searchEntryPoint (для рендеринга поиска Google)
                     search_entry_point = getattr(grounding_metadata, 'search_entry_point', None)
                     if search_entry_point and hasattr(search_entry_point, 'rendered_content'):
                          # Можно логировать или использовать rendered_content, но для кнопок он не очень подходит
                          logger.info(f"Найден searchEntryPoint для {chat_id}.")
                          # Можно попробовать извлечь запросы из него, если они там есть

                     # Ищем webSearchQueries
                     web_queries = getattr(grounding_metadata, 'web_search_queries', [])
                     if web_queries:
                          search_suggestions = list(web_queries)
                          logger.info(f"Найдены webSearchQueries ({len(search_suggestions)}) для {chat_id}: {search_suggestions}")

                     # Ищем grounding_chunks/citation_metadata для ссылок
                     citation_metadata = getattr(candidate, 'citation_metadata', None) # Из другого примера
                     if citation_metadata and hasattr(citation_metadata, 'citation_sources'):
                         sources = getattr(citation_metadata, 'citation_sources', [])
                         urls = [source.uri for source in sources if hasattr(source, 'uri') and source.uri]
                         if urls:
                             # Добавляем URL источников к предложениям поиска, если они еще не там
                             for url in urls:
                                 if url not in search_suggestions: search_suggestions.append(url)
                             logger.info(f"Найдены источники цитирования ({len(urls)}) для {chat_id} и добавлены к предложениям.")

             except (AttributeError, IndexError):
                 pass # Не нашли метаданные

    # --- Обработка исключений API ---
    # BlockedPromptException и StopCandidateException теперь не нужны, т.к. generate_content их не кидает?
    # Вместо этого ошибки могут быть в самом ответе (finish_reason, safety_ratings)
    except InvalidArgument as e_arg:
         logger.error(f"Ошибка InvalidArgument при вызове API для '{model_id}' (чат {chat_id}): {e_arg}")
         error_message = f"❌ Ошибка в запросе к модели '{selected_alias}'. Возможно, неверный формат истории или параметров."
         # Сбросим историю на всякий случай
         if chat_id in chat_histories: del chat_histories[chat_id]; logger.info(f"История чата {chat_id} сброшена из-за InvalidArgument.")
    except ResourceExhausted as e_limit: logger.warning(f"Исчерпана квота API для '{model_id}' (чат {chat_id}): {e_limit}"); error_message = f"😔 Модель '{selected_alias}' устала (лимиты). /model или позже."
    except (GoogleAPIError, Exception) as e_other: logger.exception(f"Неожиданная ошибка при вызове API ('{model_id}') для {chat_id}: {e_other}"); error_message = f"😵 Ошибка ({type(e_other).__name__}) при общении с '{selected_alias}'. Попробуйте /start."

    # --- Отправка ответа или ошибки ---
    reply_markup = None
    if search_suggestions:
        keyboard = []
        # Создаем кнопки: для URL - ссылка, для запросов - поиск Google
        for suggestion in search_suggestions[:4]: # Лимит кнопок
             if suggestion.startswith('http://') or suggestion.startswith('https://'):
                 try: domain = urllib.parse.urlparse(suggestion).netloc or suggestion[:30]+".."
                 except Exception: domain = suggestion[:30]+".."
                 keyboard.append([InlineKeyboardButton(f"🔗 {domain}", url=suggestion)])
             else: # Считаем это поисковым запросом
                 try: encoded_suggestion = urllib.parse.quote_plus(suggestion); search_url = f"https://www.google.com/search?q={encoded_suggestion}"; keyboard.append([InlineKeyboardButton(f"🔍 {suggestion}", url=search_url)])
                 except Exception as e_enc: logger.error(f"Ошибка кодирования поискового запроса: {e_enc}")
        if keyboard: reply_markup = InlineKeyboardMarkup(keyboard); logger.info(f"Добавлена клавиатура с {len(keyboard)} ссылками/запросами для {chat_id}.")

    # Отправляем основной текст
    if final_text:
        max_length = 4096; bot_response = final_text
        if len(bot_response) > max_length: logger.warning(f"Ответ для {chat_id} ('{selected_alias}') слишком длинный ({len(bot_response)}), обрезаем."); bot_response = bot_response[:max_length - 3] + "..."
        try: await update.message.reply_text(bot_response, reply_to_message_id=message_id, reply_markup=reply_markup); logger.info(f"Успешно отправлен ответ ({len(bot_response)} симв.) для {chat_id}.")
        except Exception as e_send: logger.exception(f"Ошибка отправки ответа Telegram для {chat_id}: {e_send}");
    # Отправляем сообщение об ошибке
    elif error_message:
        logger.info(f"Отправка сообщения об ошибке для {chat_id}: {error_message}")
        try: await update.message.reply_text(error_message, reply_to_message_id=message_id)
        except Exception as e_send_err: logger.error(f"Не удалось отправить сообщение об ошибке Telegram для {chat_id}: {e_send_err}")
    # Если ни текста, ни ошибки
    else:
        logger.warning(f"Нет ни текста, ни ошибки для {chat_id} ('{selected_alias}').");
        try: await update.message.reply_text("Модель вернула пустой ответ без ошибок. Странно... 🤷", reply_to_message_id=message_id)
        except Exception as e_send_fallback: logger.error(f"Не удалось отправить стандартный ответ 'ничего не найдено' для {chat_id}: {e_send_fallback}")


# --- Точка входа ---
def main() -> None:
    """Инициализирует и запускает Telegram бота."""
    # Проверяем наличие клиента и ключей перед запуском
    if 'gemini_client' not in globals() or not gemini_client: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Клиент Gemini не создан."); return
    if not TELEGRAM_BOT_TOKEN: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Токен Telegram не найден."); return
    if not GOOGLE_API_KEY: logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Ключ Google API не найден."); return

    # Информируем о статусе поиска
    search_status = "включен" if google_search_tool else "ОТКЛЮЧЕН (ошибка конфигурации или импорта)"
    logger.info(f"Встроенный поиск Google ({search_tool_type_used}) глобально {search_status}.")

    logger.info("Инициализация приложения Telegram...")
    try:
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("model", select_model_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(CallbackQueryHandler(select_model_callback))
        logger.info("Запуск бота в режиме polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Exception as e: logger.exception("Критическая ошибка при инициализации или запуске Telegram!")

if __name__ == '__main__':
    # Проверяем, что клиент Gemini был создан
    if 'gemini_client' in globals() and gemini_client:
         logger.info("Клиент Gemini создан. Запускаем main().")
         main()
    else:
         logger.critical("Завершение работы, так как клиент Gemini не был создан.")

# --- END OF REALLY x17 FULL CORRECTED main.py (USING google-genai CLIENT) ---
