# ОСНОВА: main.py v13.6
# ИЗМЕНЯЕМАЯ ФУНКЦИЯ: generate_response
# ПРИЧИНА: Добавление корректной обработки ошибки RESOURCE_EXHAUSTED для более дружелюбного вывода.

async def generate_response(client: genai.Client, request_contents: list, context: ContextTypes.DEFAULT_TYPE, tools: list, system_instruction_override: str = None) -> types.GenerateContentResponse | str:
    chat_id = context.chat_data.get('id', 'Unknown')
    
    if system_instruction_override:
        final_system_instruction = system_instruction_override
    else:
        try:
            final_system_instruction = SYSTEM_INSTRUCTION.format(current_time=get_current_time_str())
        except KeyError:
            logger.warning("В system_prompt.md отсутствует плейсхолдер {current_time}. Дата не будет подставлена.")
            final_system_instruction = SYSTEM_INSTRUCTION

    config = types.GenerateContentConfig(
        safety_settings=SAFETY_SETTINGS, 
        tools=tools,
        system_instruction=types.Content(parts=[types.Part(text=final_system_instruction)]),
        temperature=1.0,
        thinking_config=types.ThinkingConfig(thinking_budget=24576)
    )
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await client.aio.models.generate_content(
                model=MODEL_NAME,
                contents=request_contents,
                config=config
            )
            if response and response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                logger.info(f"ChatID: {chat_id} | Ответ от Gemini API получен (попытка {attempt + 1}).")
                return response
            
            logger.warning(f"ChatID: {chat_id} | Получен пустой ответ от API (попытка {attempt + 1}/{max_retries}). Пауза перед повтором.")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)

        except genai_errors.APIError as e:
            logger.error(f"ChatID: {chat_id} | Ошибка Google API (попытка {attempt + 1}): {e}", exc_info=False)
            is_retryable = hasattr(e, 'http_status') and 500 <= e.http_status < 600
            
            if is_retryable and attempt < max_retries - 1:
                delay = 2 ** (attempt + 1)
                logger.warning(f"ChatID: {chat_id} | Обнаружена временная ошибка. Повторная попытка через {delay} сек.")
                await asyncio.sleep(delay)
                continue
            else:
                error_text = str(e).lower()
                # --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
                if "resource_exhausted" in error_text:
                     return "⏳ <b>Слишком много запросов!</b>\nПожалуйста, подождите минуту, я немного перегрузилась."
                # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                if "input token count" in error_text and "exceeds the maximum" in error_text:
                    return "🤯 <b>Слишком длинная история!</b>\nКажется, мы заболтались, и я уже не могу удержать в голове весь наш диалог. Пожалуйста, очистите историю командой /clear, чтобы начать заново."
                if "permission denied" in error_text:
                    return "❌ <b>Ошибка доступа к файлу.</b>\nВозможно, файл был удален с серверов Google (срок хранения 48 часов) или возникла другая проблема. Попробуйте отправить файл заново."
                return f"❌ <b>Ошибка Google API:</b>\n<code>{html.escape(str(e))}</code>"
        
        except Exception as e:
            logger.error(f"ChatID: {chat_id} | Неизвестная ошибка генерации (попытка {attempt + 1}): {e}", exc_info=True)
            return f"❌ <b>Произошла критическая внутренняя ошибка:</b>\n<code>{html.escape(str(e))}</code>"
    
    logger.error(f"ChatID: {chat_id} | Не удалось получить содержательный ответ от API после {max_retries} попыток.")
    return "Я не смогла сформировать ответ. Попробуйте переформулировать запрос или повторить попытку позже."
