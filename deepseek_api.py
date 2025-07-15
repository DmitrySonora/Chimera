import requests
import logging

from config import (
    DEEPSEEK_API_URL,
    DEEPSEEK_API_KEY,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_EXPERT,
    SYSTEM_PROMPT_WRITER,
    SYSTEM_PROMPT_TALK,
    SYSTEM_PROMPT_JSON,
    SYSTEM_PROMPT_EXPERT_JSON,
    SYSTEM_PROMPT_WRITER_JSON,
    SYSTEM_PROMPT_TALK_JSON,
    DEEPSEEK_MODEL,
    # Параметры по умолчанию (auto)
    TEMPERATURE,
    MAX_TOKENS,
    TOP_P,
    FREQUENCY_PENALTY,
    PRESENCE_PENALTY,
    # Параметры для expert
    TEMPERATURE_EXPERT,
    MAX_TOKENS_EXPERT,
    TOP_P_EXPERT,
    FREQUENCY_PENALTY_EXPERT,
    PRESENCE_PENALTY_EXPERT,
    # Параметры для writer
    TEMPERATURE_WRITER,
    MAX_TOKENS_WRITER,
    TOP_P_WRITER,
    FREQUENCY_PENALTY_WRITER,
    PRESENCE_PENALTY_WRITER,
     # Параметры для talk
    TEMPERATURE_TALK,
    MAX_TOKENS_TALK,
    TOP_P_TALK,
    FREQUENCY_PENALTY_TALK,
    PRESENCE_PENALTY_TALK,
)

logger = logging.getLogger("deepseek_api")

def ask_deepseek(messages, mode="auto", use_json=None):
    """
    Отправляет запрос к DeepSeek API и возвращает сгенерированный ответ.
    Поддерживает разные режимы ответа: expert, writer, talk, auto.
    
    Args:
        messages: список сообщений для API
        mode: режим работы (auto/expert/talk/writer)
        use_json: использовать JSON output (True/False/None для использования настройки по умолчанию)
    """

    # Если use_json не указан явно, используем настройку из конфига
    if use_json is None:
        use_json = USE_JSON_OUTPUT
    
    # Выбор параметров в зависимости от режима
    if mode == "talk":
        temperature = TEMPERATURE_TALK
        max_tokens = MAX_TOKENS_TALK
        top_p = TOP_P_TALK
        frequency_penalty = FREQUENCY_PENALTY_TALK
        presence_penalty = PRESENCE_PENALTY_TALK

    elif mode == "writer":
        temperature = TEMPERATURE_WRITER
        max_tokens = MAX_TOKENS_WRITER
        top_p = TOP_P_WRITER
        frequency_penalty = FREQUENCY_PENALTY_WRITER
        presence_penalty = PRESENCE_PENALTY_WRITER
        
    elif mode == "expert":
        temperature = TEMPERATURE_EXPERT
        max_tokens = MAX_TOKENS_EXPERT
        top_p = TOP_P_EXPERT
        frequency_penalty = FREQUENCY_PENALTY_EXPERT
        presence_penalty = PRESENCE_PENALTY_EXPERT

    else:
        temperature = TEMPERATURE
        max_tokens = MAX_TOKENS
        top_p = TOP_P
        frequency_penalty = FREQUENCY_PENALTY
        presence_penalty = PRESENCE_PENALTY

    # Если используем JSON режим, заменяем системные промпты на JSON версии
    if use_json:
        messages_copy = []
        for msg in messages:
            if msg['role'] == 'system':
                # Определяем какой промпт заменить
                if msg['content'] == SYSTEM_PROMPT:
                    messages_copy.append({"role": "system", "content": SYSTEM_PROMPT_JSON})
                elif msg['content'] == SYSTEM_PROMPT_EXPERT:
                    messages_copy.append({"role": "system", "content": SYSTEM_PROMPT_EXPERT_JSON})
                elif msg['content'] == SYSTEM_PROMPT_WRITER:
                    messages_copy.append({"role": "system", "content": SYSTEM_PROMPT_WRITER_JSON})
                elif msg['content'] == SYSTEM_PROMPT_TALK:
                    messages_copy.append({"role": "system", "content": SYSTEM_PROMPT_TALK_JSON})
                else:
                    # Оставляем как есть (например, INJECTION_PROMPT)
                    messages_copy.append(msg)
            else:
                messages_copy.append(msg)
        messages = messages_copy
        
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": top_p,
        "frequency_penalty": frequency_penalty,
        "presence_penalty": presence_penalty,
        "stream": False
    }

    # Добавляем response_format для JSON режима
    if use_json:
        payload["response_format"] = {"type": "json_object"}
        logger.info("Используется JSON output режим")
        
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        snippet = str(messages)[:200]  # Для логирования
        logger.info(f"Запрос к DeepSeek (режим: {mode}): {snippet}")
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        if "choices" in data and len(data["choices"]) > 0:
            answer = data["choices"][0]["message"]["content"]
            logger.info(f"Ответ DeepSeek: {answer[:100]}")
            return answer.strip()
        else:
            logger.error(f"Пустой ответ DeepSeek: {data}")
            return "Ошибка: Пустой ответ DeepSeek API"
    except requests.exceptions.Timeout:
        logger.error("Таймаут запроса к DeepSeek API")
        return "Ошибка: Превышено время ожидания ответа DeepSeek API"
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка соединения с DeepSeek API: {str(e)}")
        return "Ошибка: Не удалось связаться с DeepSeek API"
    except Exception as e:
        logger.error(f"Непредвиденная ошибка DeepSeek API: {str(e)}")
        return "Ошибка: Внутренняя ошибка при обращении к DeepSeek API"
