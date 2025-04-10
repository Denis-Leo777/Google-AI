# --- START OF REALLY x15 FULL CORRECTED main.py (NO IMAGEN) ---

import logging
import os
import asyncio
import google.genai as genai
import time
import random

# Импорт types
try:
    from google.genai import types as genai_types
    print("INFO: Успешно импортирован types из google.genai")
except ImportError:
    print("!!! НЕ УДАЛОСЬ импортировать types из google.genai.")
    class DummyTypes: pass; genai_types = DummyTypes()
except NameError:
    print("!!! НЕ УДАЛОСЬ импортировать types из google.genai (NameError).")
    class DummyTypes: pass; genai_types = DummyTypes()

from typing import Optional, Dict, Union, Any, Tuple, List
import urllib.parse

# Конфигурация логов
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Печать версии
try: logger.info(f"!!!!!!!!!! Используемая версия google-genai: {genai.__version__} !!!!!!!!!!")
except Exception as e: logger.error(f"!!!!!!!!!! Ошибка получения версии google-genai: {e} !!!!!!!!!!")

# Исключения
try:
    from google.api_core.exceptions import ResourceExhausted, GoogleAPIError, FailedPrecondition
    logger.info("Исключения google.api_core.exceptions успешно импортированы.")
except ImportError:
    logger.warning("!!! НЕ УДАЛОСЬ импортировать google.api_core.exceptions. Используем базовый Exception.")
    ResourceExhausted=Exception; GoogleAPIError=Exception; FailedPrecondition=Exception

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Protobuf Struct
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
    # УБРАЛИ genai.configure, просто логируем наличие ключа
    logger.info("Переменная окружения GOOGLE_API_KEY найдена. Библиотека google-genai должна использовать её автоматически.")

# --- КОНФИГУРАЦИЯ МОДЕЛЕЙ --- УБРАН IMAGEN
AVAILABLE_MODELS = {
    '⚡ Flash 2.0': 'models/gemini-2.0-flash-001',
    '✨ Pro 2.5': 'models/gemini-2.5-pro-exp-03-25',
    # '🖼️ Imagen 3 (Картинки!)': 'models/imagen-3.0-generate-002', # УДАЛЕНО
}
# Убедимся, что дефолтная модель все еще в списке
if not AVAILABLE_MODELS:
     exit("Нет определенных моделей в AVAILABLE_MODELS!")
DEFAULT_MODEL_ALIAS = '⚡ Flash 2.0'
if DEFAULT_MODEL_ALIAS not in AVAILABLE_MODELS:
     # Если вдруг удалили и дефолтную, берем первую попавшуюся
     DEFAULT_MODEL_ALIAS = next(iter(AVAILABLE_MODELS))
     logger.warning(f"Дефолтная модель была удалена или не найдена, установлена первая: {DEFAULT_MODEL_ALIAS}")


# --- Определение ВСТРОЕННОГО инструмента Google Search ---
google_search_tool = None
search_tool_type_used = None
try:
    if hasattr(genai_types, 'GoogleSearchRetrieval'):
         google_search_retrieval_config = genai_types.GoogleSearchRetrieval()
         google_search_tool = genai_types.Tool(google_search_retrieval=google_search_retrieval_config)
         search_tool_type_used = "GoogleSearchRetrieval (v1.5)"
         logger.info(f"Инструмент поиска '{search_tool_type_used}' определен.")
    else:
         logger.warning("!!! Классы GoogleSearch/GoogleSearchRetrieval НЕ НАЙДЕНЫ в genai_types. Поиск будет недоступен.")
except Exception as e:
    logger.exception(f"!!! Ошибка при определении инструмента поиска: {e}")
    google_search_tool = None

# --- ЗАГРУЗКА МОДЕЛЕЙ ---
LOADED_MODELS_ANY: Dict[str, Any] = {}
try:
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
        # Проверка на imagen больше не нужна
        # if 'imagen' in model_id.lower():
        #    logger.warning(f"Модель изображений '{alias}' ({model_id}) пропущена для текстовой загрузки.")
        #    continue

        current_tools = [google_search_tool] if google_search_tool else None
        tool_attempt_info = f"с инструментом '{search_tool_type_used}'" if current_tools else "без инструментов"
        logger.info(f"Попытка инициализации '{alias}' ({model_id}) {tool_attempt_info}...")

        try:
            # Первая попытка инициализации
            model = genai.GenerativeModel(
                model_name=model_id,
                system_instruction=system_instruction_text,
                tools=current_tools
            )
            # Определяем статус поиска для лога (эвристика)
            search_status = "N/A"
            if current_tools:
                 # Предполагаем, что инструмент принят, если не было ошибки
                 search_status = f"Enabled ({search_tool_type_used})"
            else:
                 search_status = "Disabled (not available)"

            LOADED_MODELS_ANY[alias] = model
            logger.info(f"Модель '{alias}' ({model_id}) [Search: {search_status}] успешно инициализирована.")

        except (ValueError, FailedPrecondition, GoogleAPIError, Exception) as e:
            # Обработка ошибки первой попытки
            logger.error(f"!!! ОШИБКА первой попытки инициализации '{alias}' ({model_id}) {tool_attempt_info}: {e}")

            # Вторая попытка: без инструментов, если ошибка была с ними
            if current_tools and isinstance(e, (ValueError, FailedPrecondition, GoogleAPIError)):
                 logger.warning(f"Похоже на несовместимость инструмента. Попытка инициализировать '{alias}' ({model_id}) БЕЗ инструментов...")
                 try:
                     model = genai.GenerativeModel(
                         model_name=model_id,
                         system_instruction=system_instruction_text,
                         tools=None # Явно без инструментов
                     )
                     LOADED_MODELS_ANY[alias] = model
                     # Статус поиска точно Disabled
                     logger.info(f"Модель '{alias}' ({model_id}) [Search: Disabled (fallback)] успешно инициализирована (вторая попытка).")
                 except Exception as e_fallback:
                      # Если и вторая попытка не удалась
                      logger.error(f"!!! ОШИБКА повторной инициализации '{alias}' ({model_id}) без инструментов: {e_fallback}")
                      logger.error(f"Модель '{alias}' не будет загружена.")
            else:
                 # Ошибка произошла без инструментов, или это не ошибка совместимости
                 logger.error(f"Модель '{alias}' не будет загружена из-за ошибки на первой попытке (без запасного плана).")


    # Проверка наличия загруженных моделей
    if not LOADED_MODELS_ANY:
        logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Ни одна текстовая модель не была успешно загружена.")
        raise RuntimeError("Нет доступных текстовых моделей для запуска бота!")

    # Проверка и установка дефолтной модели (если она вдруг оказалась не загружена)
    if DEFAULT_MODEL_ALIAS not in LOADED_MODELS_ANY:
        try:
            # Берем первую доступную модель как дефолтную
            DEFAULT_MODEL_ALIAS = next(iter(LOADED_MODELS_ANY))
            logger.warning(f"Модель по умолчанию '{DEFAULT_MODEL_ALIAS}' не найдена/не загружена. Установлена первая доступная: '{DEFAULT_MODEL_ALIAS}'")
        except StopIteration:
            # Эта ошибка не должна возникать из-за проверки выше, но на всякий случай
            logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Нет загруженных моделей для установки по умолчанию.")
            raise RuntimeError("Нет моделей для работы.")

except Exception as e:
    # Ловим общие ошибки на этапе инициализации
    logger.exception("Критическая ошибка на этапе инициализации моделей!")
    # Не используем exit, даем программе завершиться через main

# --- Хранение состояния пользователя ---
user_selected_model: Dict[int, str] = {}
chat_histories: Dict[int, Any] = {} # Хранит объекты ChatSession

# --- Вспомогательная функция для извлечения текста ---
def extract_response_text(response) -> Optional[str]:
    """Извлекает текст из ответа Gemini, пробуя разные способы и детализируя ошибки."""
    try:
        # Основной способ
        return response.text
    except ValueError:
        # Чаще всего при блокировке или пустом ответе
        logger.warning("ValueError при извлечении text (вероятно, контент заблокирован или ответ пуст).")
        block_reason = None; finish_reason = None; safety_ratings = None
        error_parts = [] # Список для сборки сообщения об ошибке

        # Пытаемся извлечь детали из prompt_feedback
        if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
             block_reason = getattr(response.prompt_feedback, 'block_reason', None)
             safety_ratings = getattr(response.prompt_feedback, 'safety_ratings', None)
             # Логируем причину блокировки (если есть и не unspecified)
             if block_reason and hasattr(genai_types, 'BlockReason') and block_reason != genai_types.BlockReason.BLOCK_REASON_UNSPECIFIED:
                 block_reason_name = getattr(block_reason, 'name', block_reason)
                 logger.warning(f"Prompt Feedback: Block Reason: {block_reason_name}")
                 error_parts.append(f"Блокировка запроса: {block_reason_name}")
             # Логируем рейтинги безопасности (если есть)
             if safety_ratings:
                 logger.warning(f"Prompt Feedback: Safety Ratings: {safety_ratings}")


        # Пытаемся извлечь детали из candidates (может содержать больше информации)
        if hasattr(response, 'candidates') and response.candidates:
            try:
                candidate = response.candidates[0]
                # Причина завершения генерации
                finish_reason = getattr(candidate, 'finish_reason', None)
                # Рейтинги безопасности для кандидата (могут переопределить общие)
                safety_ratings_candidate = getattr(candidate, 'safety_ratings', None)
                if safety_ratings_candidate: safety_ratings = safety_ratings_candidate # Используем рейтинги кандидата, если они есть

                # Добавляем причину завершения в ошибку, если она не нормальная (STOP) и не unspecified
                if finish_reason and hasattr(genai_types, 'FinishReason') and finish_reason != genai_types.FinishReason.FINISH_REASON_UNSPECIFIED and finish_reason != genai_types.FinishReason.STOP:
                     finish_reason_name = getattr(finish_reason, 'name', finish_reason)
                     logger.warning(f"Candidate Finish Reason: {finish_reason_name}")
                     error_parts.append(f"Причина остановки: {finish_reason_name}")

                # Добавляем информацию о фильтрах безопасности в ошибку
                if safety_ratings:
                     # Формируем строку только с "опасными" рейтингами
                     relevant_ratings = [f"{r.category.name}: {r.probability.name}"
                                         for r in safety_ratings if hasattr(r, 'probability') and hasattr(genai_types, 'HarmProbability') and r.probability not in (genai_types.HarmProbability.NEGLIGIBLE, genai_types.HarmProbability.HARM_PROBABILITY_UNSPECIFIED)]
                     if relevant_ratings:
                         ratings_str = ', '.join(relevant_ratings)
                         logger.warning(f"Candidate Safety Ratings triggered: {ratings_str}")
                         error_parts.append(f"Фильтры безопасности: {ratings_str}")

            except (IndexError, AttributeError) as e_inner:
                 # Ошибка доступа к деталям кандидата
                 logger.warning(f"Ошибка при доступе к candidates для деталей ValueError: {e_inner}")

        # Если собрали какие-то причины ошибки, возвращаем их
        if error_parts:
            return f"⚠️ Не удалось получить ответ. Причина: {'. '.join(error_parts)}."
        else:
             # Если причин не найдено, но текста все равно нет
             logger.warning("Не удалось извлечь текст и не найдено явных причин блокировки/ошибки.")
             return None # Возвращаем None, чтобы потом обработать как пустой ответ

    except AttributeError:
        # Если у ответа вообще нет атрибута .text
        logger.warning("Ответ не имеет атрибута .text. Попытка извлечь из parts.")
        try:
            # Проверяем наличие нужной структуры
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                # Собираем текст из всех частей
                parts_text = "".join(p.text for p in response.candidates[0].content.parts if hasattr(p, 'text'))
                # Возвращаем собранный текст или None, если он пуст или состоит только из пробелов
                return parts_text.strip() if parts_text and parts_text.strip() else None
            else:
                # Если структура не найдена
                logger.warning("Не найдено candidates или parts для извлечения текста.")
                return None
        except (AttributeError, IndexError, Exception) as e_inner:
            # Любая ошибка при сборке из parts
            logger.error(f"Ошибка при сборке текста из parts: {e_inner}")
            return None

    except Exception as e_unknown:
        # Ловим совсем неожиданные ошибки при доступе к тексту
        logger.exception(f"Неожиданная ошибка при извлечении текста ответа: {e_unknown}")
        return None


# --- ОБРАБОТЧИКИ TELEGRAM ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start. Сбрасывает состояние чата."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Сбрасываем состояние для этого чата
    if chat_id in user_selected_model:
        del user_selected_model[chat_id]
        logger.info(f"Выбранная модель для чата {chat_id} сброшена.")
    if chat_id in chat_histories:
        del chat_histories[chat_id]
        logger.info(f"История чата {chat_id} сброшена.")

    logger.info(f"Обработка /start для пользователя {user.id} в чате {chat_id}. Состояние сброшено.")

    # Получаем актуальную модель по умолчанию
    actual_default_model = DEFAULT_MODEL_ALIAS
    # Определяем статус поиска (глобально, т.к. зависит от загрузки библиотеки)
    search_status = f"включен ({search_tool_type_used})" if google_search_tool else "отключен"

    await update.message.reply_html(
        rf"Привет, {user.mention_html()}! Я снова в деле (надеюсь). Бот на базе Gemini."
        f"\n\nТекущая модель по умолчанию: <b>{actual_default_model}</b>"
        f"\n🔍 Встроенный поиск Google глобально <b>{search_status}</b> (будет ли он работать с конкретной моделью - посмотрим)."
        f"\n\nИспользуй /model, чтобы выбрать модель из доступных."
        f"\nИспользуй /start, чтобы я все забыл и начал сначала (полезно, если я затуплю)."
        f"\n\nПросто напиши мне что-нибудь...",
        reply_to_message_id=update.message.message_id
    )

async def select_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /model. Показывает клавиатуру выбора модели."""
    chat_id = update.effective_chat.id
    # Определяем текущую выбранную модель ИЛИ модель по умолчанию
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    keyboard = []

    # Создаем кнопки для ВСЕХ загруженных текстовых моделей
    for alias in LOADED_MODELS_ANY.keys():
        # Добавляем галочку к текущей модели
        button_text = f"✅ {alias}" if alias == current_alias else alias
        keyboard.append([InlineKeyboardButton(button_text, callback_data=alias)])

    # Проверка, есть ли вообще кнопки (если вдруг ни одна модель не загрузилась)
    if not keyboard:
        logger.error("Нет загруженных моделей для отображения в /model")
        await update.message.reply_text("Ой! Не могу найти ни одной рабочей модели. Что-то серьезно пошло не так при запуске. 😵‍💫")
        return

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Текущая модель: *{current_alias}*\n\nВыберите новую модель из списка:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def select_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия на кнопку выбора модели."""
    query = update.callback_query
    await query.answer() # Обязательно отвечаем на callback

    selected_alias = query.data
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    # Получаем текущую модель (или дефолтную) для сравнения
    current_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)

    # Обработка нажатия на несуществующую кнопку (на всякий случай)
    # Убрали проверку на imagen_info

    # Проверка, существует ли выбранная модель среди загруженных
    if selected_alias not in LOADED_MODELS_ANY:
        logger.error(f"Пользователь {user_id} в чате {chat_id} выбрал недоступную/незагруженную модель: {selected_alias}")
        try:
            await query.edit_message_text(text="❌ Ошибка: Эта модель почему-то недоступна. Возможно, она не загрузилась при старте. Выберите другую.")
        except Exception as e:
             logger.warning(f"Не удалось отредактировать сообщение об ошибке выбора модели для {chat_id}: {e}")
        return

    # Если пользователь выбрал ту же модель, что и текущая
    if selected_alias == current_alias:
        logger.info(f"Пользователь {user_id} в чате {chat_id} перевыбрал ту же модель: {selected_alias}")
        # Просто убираем "загрузку" с кнопки (если она была)
        try:
            await query.edit_message_reply_markup(reply_markup=query.message.reply_markup)
        except Exception as e:
            logger.warning(f"Не удалось убрать 'загрузку' с кнопки для {chat_id} при перевыборе той же модели: {e}")
        return

    # --- Если выбрана НОВАЯ РАБОЧАЯ модель ---
    user_selected_model[chat_id] = selected_alias
    logger.info(f"Пользователь {user_id} в чате {chat_id} сменил модель с '{current_alias}' на '{selected_alias}'")

    # Сбрасываем историю чата при смене модели
    reset_message = ""
    if chat_id in chat_histories:
        del chat_histories[chat_id]
        logger.info(f"История чата {chat_id} сброшена из-за смены модели.")
        reset_message = "\n⚠️ История чата сброшена."

    # Обновляем клавиатуру, чтобы галочка была у новой модели
    keyboard = []
    for alias in LOADED_MODELS_ANY.keys():
        button_text = f"✅ {alias}" if alias == selected_alias else alias
        keyboard.append([InlineKeyboardButton(button_text, callback_data=alias)])
    # Кнопку для Imagen больше не добавляем

    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(
            text=f"✅ Модель успешно изменена на *{selected_alias}*!{reset_message}\n\nВыберите другую модель или начните чат:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение после смены модели для {chat_id}: {e}")
        # Попробуем просто отправить новое сообщение
        await context.bot.send_message(chat_id=chat_id, text=f"Модель изменена на *{selected_alias}*!{reset_message}", parse_mode=ParseMode.MARKDOWN)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений от пользователя."""
    # Проверка на пустое сообщение
    if not update.message or not update.message.text:
        logger.warning("Получено пустое сообщение или сообщение без текста.")
        return

    user_message = update.message.text
    user = update.effective_user
    chat_id = update.effective_chat.id
    message_id = update.message.message_id # Сохраняем ID для ответа
    logger.info(f"Сообщение от {user.id} в чате {chat_id} ({len(user_message)} символов): '{user_message[:80].replace(chr(10), ' ')}...'")

    # Получаем выбранную модель или дефолтную
    selected_alias = user_selected_model.get(chat_id, DEFAULT_MODEL_ALIAS)
    selected_model_object = LOADED_MODELS_ANY.get(selected_alias)

    # Проверка, что модель действительно загружена (важно!)
    if not selected_model_object:
        logger.error(f"Критическая ошибка: Не найдена модель '{selected_alias}' для чата {chat_id}, хотя она выбрана/дефолтная.")
        await update.message.reply_text(
            f"Ой! Не могу найти выбранную модель ('{selected_alias}'). Что-то сломалось после запуска. 😵‍💫 Попробуйте /start или /model.",
            reply_to_message_id=message_id
        )
        return

    # Инициализация переменных для ответа
    final_text: Optional[str] = None
    search_suggestions: List[str] = []
    error_message: Optional[str] = None
    start_time = time.monotonic() # Засекаем время начала обработки

    try:
        # Получаем или создаем сессию чата для пользователя
        if chat_id not in chat_histories:
            # Убедимся, что у объекта модели есть метод start_chat
            if hasattr(selected_model_object, 'start_chat'):
                chat_histories[chat_id] = selected_model_object.start_chat(history=[])
                logger.info(f"Начата новая сессия чата для {chat_id} с моделью '{selected_alias}'.")
            else:
                # Этого не должно быть, если модель - GenerativeModel, но проверим
                logger.error(f"Критическая ошибка: Модель '{selected_alias}' не имеет метода start_chat!")
                await update.message.reply_text("Ой! Проблема с инициализацией чата для этой модели. Сообщите администратору.", reply_to_message_id=message_id)
                return

        current_chat_session = chat_histories[chat_id]

        # --- Логирование использования поиска (улучшенная эвристика) ---
        # Проверяем, был ли инструмент передан *этой* модели при инициализации
        # (Это все еще эвристика, т.к. мы не храним точный статус поиска для каждой модели)
        search_enabled_for_model = False
        if google_search_tool and hasattr(selected_model_object, '_tools'): # Проверяем наличие внутреннего атрибута (может измениться!)
            if google_search_tool in selected_model_object._tools:
                 search_enabled_for_model = True
        search_type_info = f" (Поиск: {'Enabled' if search_enabled_for_model else 'Disabled'})"
        logger.info(f"Отправка сообщения в Gemini для {chat_id} с моделью '{selected_alias}'{search_type_info}")

        # Отправляем "печатает..."
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # --- Асинхронно отправляем сообщение модели ---
        response = await current_chat_session.send_message_async(content=user_message)

        # Замеряем время ответа
        end_time = time.monotonic()
        processing_time = end_time - start_time
        logger.info(f"Ответ от Gemini для {chat_id} ('{selected_alias}') получен за {processing_time:.2f} сек. Проверка содержимого...")

        # --- Извлекаем текст ответа ---
        final_text = extract_response_text(response)

        # Проверка, не вернула ли extract_response_text сообщение об ошибке (начинается с ⚠️)
        if final_text and final_text.startswith("⚠️"):
             error_message = final_text # Используем сообщение об ошибке из экстрактора
             final_text = None # Сбрасываем основной текст, т.к. это ошибка

        # Если текст извлечь не удалось И не было ошибки от экстрактора
        elif final_text is None:
             logger.warning(f"Не удалось извлечь текст ответа для {chat_id} ('{selected_alias}'), и нет явной ошибки от extract_response_text.")
             error_message = "⚠️ Получен пустой или некорректный ответ от модели. Попробуйте переформулировать запрос."

        # --- Попытка извлечь поисковые запросы ---
        # Зависит от версии API и использовала ли модель поиск
        if hasattr(response, 'candidates') and response.candidates:
            try:
                 metadata = getattr(response.candidates[0], 'grounding_metadata', None)
                 if metadata and hasattr(metadata, 'web_search_queries') and metadata.web_search_queries:
                     search_suggestions = list(metadata.web_search_queries)
                     logger.info(f"Найдены поисковые запросы ({len(search_suggestions)}) для {chat_id}: {search_suggestions}")
            except (AttributeError, IndexError):
                 pass # Просто пропускаем, если метаданных нет

    # --- Обработка исключений API и других ---
    except ResourceExhausted as e_limit:
        logger.warning(f"Исчерпана квота API для модели '{selected_alias}' (чат {chat_id}): {e_limit}")
        error_message = f"😔 Упс! Похоже, модель '{selected_alias}' немного перегружена (превышены лимиты запросов). Попробуйте немного позже или выберите другую модель через /model."
    except FailedPrecondition as e_precondition:
        # Часто связано с неверным состоянием истории чата
        logger.error(f"Ошибка FailedPrecondition для модели '{selected_alias}' (чат {chat_id}): {e_precondition}. Сбрасываем историю чата.")
        error_message = f"⚠️ Произошла ошибка состояния чата с моделью '{selected_alias}'. История была сброшена, чтобы это исправить. Пожалуйста, повторите ваш последний запрос."
        # Принудительно сбрасываем историю для этого чата
        if chat_id in chat_histories:
            del chat_histories[chat_id]
            logger.info(f"История чата {chat_id} принудительно сброшена из-за FailedPrecondition.")
    except (GoogleAPIError, Exception) as e_other:
        # Ловим остальные ошибки API и общие исключения Python
        logger.exception(f"Неожиданная ошибка при обработке запроса моделью '{selected_alias}' для чата {chat_id}: {e_other}")
        error_message = f"😵 Ой! Произошла непредвиденная ошибка ({type(e_other).__name__}) при общении с моделью '{selected_alias}'. Попробуйте еще раз. Если ошибка повторится, возможно, стоит сбросить чат (/start)."

    # --- Отправка ответа или ошибки пользователю ---
    reply_markup = None # По умолчанию без кнопок

    # --- Создание кнопок для поисковых запросов ---
    if search_suggestions:
        keyboard = []
        # Ограничим количество кнопок, чтобы не загромождать интерфейс
        for suggestion in search_suggestions[:3]:
             try:
                 # Кодируем запрос для URL
                 encoded_suggestion = urllib.parse.quote_plus(suggestion)
                 # Создаем URL для поиска в Google
                 search_url = f"https://www.google.com/search?q={encoded_suggestion}"
                 # Добавляем кнопку с внешней ссылкой
                 keyboard.append([InlineKeyboardButton(f"🔍 {suggestion}", url=search_url)])
             except Exception as e_url:
                 logger.error(f"Ошибка создания URL для поискового запроса '{suggestion}': {e_url}")
        if keyboard:
            reply_markup = InlineKeyboardMarkup(keyboard)
            logger.info(f"Добавлена клавиатура с {len(keyboard)} поисковыми запросами для {chat_id}.")

    # --- Отправляем основной ответ, если он есть и это не ошибка ---
    if final_text:
        # Разбиваем на части, если слишком длинный (Telegram лимит 4096)
        max_length = 4096
        bot_response = final_text

        if len(bot_response) > max_length:
             logger.warning(f"Ответ для {chat_id} ('{selected_alias}') слишком длинный ({len(bot_response)}), обрезаем до {max_length}.")
             # Просто обрезаем и добавляем многоточие
             bot_response = bot_response[:max_length - 3] + "..."
             # TODO: Можно реализовать более умную разбивку на несколько сообщений в будущем

        try:
             await update.message.reply_text(
                 bot_response,
                 reply_to_message_id=message_id,
                 reply_markup=reply_markup # Прикрепляем кнопки поиска, если они есть
             )
             logger.info(f"Успешно отправлен ответ ({len(bot_response)} симв.) для {chat_id}.")
        except Exception as e_send:
             logger.exception(f"Ошибка отправки ответа Telegram для {chat_id}: {e_send}")
             try:
                 # Попытка отправить сообщение об ошибке отправки
                 await update.message.reply_text(
                      "Не смог отправить форматированный ответ. Возможно, он был слишком большой или содержал что-то, что не понравилось Telegram. 🤔",
                      reply_to_message_id=message_id
                 )
             except Exception:
                 logger.error(f"Не удалось отправить даже сообщение об ошибке отправки для {chat_id}.")

    # --- Если был текст ошибки (из блока try/except или из extract_response_text) ---
    elif error_message:
        logger.info(f"Отправка сообщения об ошибке для {chat_id}: {error_message}")
        try:
            await update.message.reply_text(
                error_message,
                reply_to_message_id=message_id
                # Не прикрепляем кнопки поиска к сообщению об ошибке
            )
        except Exception as e_send_err:
            logger.error(f"Не удалось отправить сообщение об ошибке Telegram для {chat_id}: {e_send_err}")

    # --- Если не было ни текста, ни ошибки (маловероятно, но возможно) ---
    else:
        logger.warning(f"Нет ни текста ответа, ни сообщения об ошибке для {chat_id} ('{selected_alias}'). Отправляем стандартный ответ.")
        try:
            await update.message.reply_text(
                "Что-то странное произошло. Я не получил ни ответа, ни ошибки от модели. 🤷 Попробуйте переформулировать или написать позже.",
                reply_to_message_id=message_id
            )
        except Exception as e_send_fallback:
             logger.error(f"Не удалось отправить стандартный ответ 'ничего не найдено' для {chat_id}: {e_send_fallback}")


# --- Точка входа ---
def main() -> None:
    """Инициализирует и запускает Telegram бота."""
    # Проверяем критические условия перед запуском
    if not LOADED_MODELS_ANY:
        logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Ни одна модель не была загружена. Проверьте логи выше на ошибки инициализации.")
        return # Выходим, если нет моделей

    if not TELEGRAM_BOT_TOKEN:
        logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Токен Telegram (TELEGRAM_BOT_TOKEN) не найден.")
        return

    if not GOOGLE_API_KEY:
         logger.critical("ЗАПУСК НЕВОЗМОЖЕН: Ключ Google API (GOOGLE_API_KEY) не найден.")
         return

    # Информируем о статусе поиска (глобально)
    if not google_search_tool:
        logger.warning(f"Встроенный поиск Google НЕ настроен или не удалось определить тип. Модели будут работать без доступа к свежей информации из сети.")
    else:
        logger.info(f"Встроенный поиск Google глобально настроен (найден тип: {search_tool_type_used}). Будет использоваться, если модель его поддерживает.")

    logger.info("Инициализация приложения Telegram...")
    try:
        # Создаем билдер приложения
        application_builder = Application.builder().token(TELEGRAM_BOT_TOKEN)

        # Можно настроить параметры пулинга здесь, если нужно
        # application_builder.read_timeout(30).get_updates_read_timeout(30)

        # Собираем приложение
        application = application_builder.build()

        # Добавляем обработчики команд
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("model", select_model_command))

        # Добавляем обработчик текстовых сообщений (фильтруя команды)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Добавляем обработчик для нажатий на инлайн-кнопки (выбор модели)
        application.add_handler(CallbackQueryHandler(select_model_callback))

        logger.info("Запуск бота в режиме polling...")
        # allowed_updates=Update.ALL_TYPES - получаем все типы обновлений
        # drop_pending_updates=True - полезно при перезапусках, чтобы бот не отвечал на старые сообщения, полученные во время оффлайна
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

    except Exception as e:
        # Ловим ошибки инициализации или запуска Telegram приложения
        logger.exception("Критическая ошибка при инициализации или запуске Telegram приложения!")

if __name__ == '__main__':
    # Проверяем, что словарь моделей был создан и содержит что-то, прежде чем запускать main
    # Это предотвращает запуск main(), если была критическая ошибка ДО загрузки моделей
    if 'LOADED_MODELS_ANY' in locals() and isinstance(LOADED_MODELS_ANY, dict) and LOADED_MODELS_ANY:
         logger.info(f"Обнаружены загруженные модели ({', '.join(LOADED_MODELS_ANY.keys())}). Запускаем main().")
         main()
    else:
         # Если LOADED_MODELS_ANY пуст или не существует (из-за ошибки выше), не запускаем main
         logger.critical("Завершение работы, так как инициализация моделей не удалась или не была выполнена.")

# --- END OF REALLY x15 FULL CORRECTED main.py (NO IMAGEN) ---
