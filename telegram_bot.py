# ========================================
# ИМПОРТЫ
# ========================================

import logging
import re
import random
import json
from datetime import datetime, timedelta, timezone
from telegram.constants import ChatAction
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import asyncio
import concurrent.futures

# Импорты конфигурации и API
from config import (
    TELEGRAM_TOKEN, SYSTEM_PROMPT, DAILY_MESSAGE_LIMIT, ADMIN_USER_IDS,
    HISTORY_LIMIT, AUTH_TIMEOUT, AVAILABLE_DURATIONS, USE_JSON_OUTPUT, JSON_FALLBACK_ENABLED,
    should_auto_save, is_excluded_from_autosave, MAX_AUTO_SAVES_PER_DAY, AUTO_SAVE_IMPORTANCE,
    INJECTION_PROMPT, 
    REDIS_HOST, REDIS_PORT, REDIS_DB,
    INJECTION_MAX_TOKENS, INJECTION_CACHE_TTL,
    MAX_INJECTIONS_PER_DIALOGUE, INJECTION_ENTROPY_THRESHOLD,
    INJECTION_LATENCY_BUDGET_MS, ENABLE_PERSONAL_ANCHORS,
    INJECTION_AB_TEST_ENABLED, INJECTION_AB_TEST_PERCENTAGE
)
from deepseek_api import ask_deepseek
from emotion_model import get_emotion

# Импорты для авторизации
from auth_database import (
    check_user_auth_status, check_daily_limit, increment_message_count,
    check_bruteforce_protection, process_password_attempt,
    list_passwords, add_password, deactivate_password,
    get_password_stats, get_auth_log,
    get_blocked_users, unblock_user, cleanup_old_limits, cleanup_expired_users,
    update_user_warning_flag, logout_user, get_users_stats, utc_now
)

# Импорты долговременной памяти
from ltm_database import (
    init_ltm, ensure_user_exists, add_message_to_history, 
    add_message_to_history_with_cleanup, build_enhanced_context_with_ltm_smart, 
    save_conversation_to_ltm,
    search_relevant_memories, get_user_ltm_stats, get_recent_memories,
    cleanup_old_memories, get_system_config, get_recent_history,
    auto_save_conversation, check_auto_save_limit, cleanup_old_auto_saves
)

import redis
from config import REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
)

import numpy as np
from injection_system import (
    AdaptiveInjectionSystem, 
    InjectionConfig,
    calculate_dialogue_entropy
)

# Проактивные инициации
from proactive_initiation import ProactiveInitiationEngine
from proactive_commands import (
    writeme_command, dontwrite_command, writeme_pause_command,
    get_proactivity_status, setup_proactivity_for_new_users
)
import cron_jobs



# ========================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ========================================

# Статистика JSON использования
json_stats = {
    'success': 0,
    'failures': 0,
    'fallbacks': 0
}

# Глобальные переменные для проактивной системы
PROACTIVE_ENGINE_ENABLED = False
proactive_engine = None



# ========================================
# ЛОГИРОВАНИЕ
# ========================================

logging.basicConfig(
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    level=logging.INFO,
    filename="himera.log"
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").propagate = False



# ========================================
# ЗАГЛУШКА ДЛЯ ФОТО
# ========================================

PHOTO_REPLIES = [
   "Ты показал мне живые эмоции? Удивительно! В моей галерее все портреты — это датасеты для обучения нейросетей.",
]



# ========================================
# СОСТОЯНИЯ ПОЛЬЗОВАТЕЛЕЙ
# ========================================

user_states = {}

# ========================================
# ИНИЦИАЛИЗАЦИЯ СИСТЕМЫ ИНЪЕКЦИЙ
# ========================================

try:
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True
    )
    redis_client.ping()
    logger.info("✅ Redis подключен успешно")
    
    injection_config = InjectionConfig(
        max_tokens=INJECTION_MAX_TOKENS,
        cache_ttl=INJECTION_CACHE_TTL,
        max_injections_per_dialogue=MAX_INJECTIONS_PER_DIALOGUE,
        entropy_threshold=INJECTION_ENTROPY_THRESHOLD,
        latency_budget_ms=INJECTION_LATENCY_BUDGET_MS
    )
    
    injection_system = AdaptiveInjectionSystem(redis_client, injection_config)
    logger.info("✅ Система адаптивных инъекций инициализирована")
    
    INJECTION_SYSTEM_ENABLED = True
    
except Exception as e:
    logger.error(f"❌ Ошибка инициализации системы инъекций: {e}")
    logger.warning("⚠️ Продолжаем работу с базовыми инъекциями")
    INJECTION_SYSTEM_ENABLED = False
    injection_system = None
    redis_client = None

def get_user_state(user_id):
    """Получение состояния пользователя"""
    if user_id not in user_states:
        user_states[user_id] = {
            'mode': 'auto',  # expert/writer/talk/auto
            'auth_state': 'unknown',  # authorized/unauthorized/waiting_password
            'waiting_password_since': None,
            'temp_data': {}
        }
    return user_states[user_id]

def update_user_state(user_id, **kwargs):
    """Обновление состояния пользователя"""
    state = get_user_state(user_id)
    state.update(kwargs)

async def ask_deepseek_with_typing(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                 messages, mode="auto", use_json=None):
    """
    Async обертка для ask_deepseek с периодическим показом typing индикатора
    """
    typing_task = None
    typing_active = True
    
    async def send_typing_periodically():
        """Фоновая задача для периодической отправки typing"""
        while typing_active:
            try:
                await update.message.reply_chat_action(ChatAction.TYPING)
                logger.debug(f"Отправлен typing для пользователя {update.message.from_user.id}")
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                logger.debug("Typing задача отменена")
                break
            except Exception as e:
                logger.warning(f"Ошибка при отправке typing: {e}")
                await asyncio.sleep(4)
    
    try:
        # Запускаем фоновую задачу typing
        typing_task = asyncio.create_task(send_typing_periodically())
        logger.info(f"Запущен typing индикатор для пользователя {update.message.from_user.id}")
        
        # Выполняем синхронный API вызов в отдельном потоке
        loop = asyncio.get_event_loop()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = loop.run_in_executor(executor, ask_deepseek, messages, mode, use_json)
            response = await future
            
        logger.info(f"DeepSeek API вызов завершен для пользователя {update.message.from_user.id}")
        return response
        
    except Exception as e:
        logger.error(f"Ошибка в ask_deepseek_with_typing: {str(e)}")
        raise
        
    finally:
        # Останавливаем typing индикатор
        typing_active = False
        
        if typing_task and not typing_task.done():
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
        
        logger.debug(f"Typing индикатор остановлен для пользователя {update.message.from_user.id}")



# ========================================
# ФУНКЦИИ АВТОРИЗАЦИИ
# ========================================

def format_time_remaining(seconds):
    """Форматирование оставшегося времени"""
    if seconds <= 0:
        return "0 секунд"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours} ч")
    if minutes > 0:
        parts.append(f"{minutes} мин")
    if secs > 0 and hours == 0:
        parts.append(f"{secs} сек")
    
    return " ".join(parts)

async def check_auth_and_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Проверка авторизации и лимитов пользователя.
    Возвращает: (can_proceed, response_message)
    """
    user_id = update.message.from_user.id
    state = get_user_state(user_id)
    
    # 1. Проверяем блокировку от bruteforce
    bruteforce_check = check_bruteforce_protection(user_id)
    if bruteforce_check['blocked']:
        remaining_time = format_time_remaining(bruteforce_check['remaining_seconds'])
        return False, f"🚫 Доступ временно заблокирован из-за множественных попыток. Попробуйте через {remaining_time}."
    
    # 2. Проверяем статус авторизации
    auth_status = check_user_auth_status(user_id)
    
    if auth_status.get('authorized'):
        # Пользователь авторизован - разрешаем доступ
        state['auth_state'] = 'authorized'
        
        # Проверяем предупреждение об истечении
        if not auth_status.get('warned_expiry'):
            auth_until = datetime.fromisoformat(auth_status['authorized_until'])
            days_left = (auth_until - utc_now()).days
            
            if days_left <= 2 and days_left > 0:
                # Отправляем предупреждение
                if update_user_warning_flag(user_id):
                    warning_msg = f"\n\n⚠️ Осталось дней: {days_left}. Обратитесь за новым паролем.\n\n Химера сейчас ответит..."
                    await update.message.reply_text(warning_msg)
        
        return True, None
    
    # 3. Пользователь не авторизован - проверяем лимиты
    limit_check = check_daily_limit(user_id)
    
    if not limit_check['exceeded']:
        # Лимит не исчерпан - разрешаем и увеличиваем счетчик
        increment_message_count(user_id)
        state['auth_state'] = 'unauthorized'
        
        # Показываем оставшиеся сообщения
        remaining = limit_check['remaining'] - 1
        if remaining <= 3:  # Предупреждаем когда остается мало
            info_msg = f"⚠️ Осталось сообщений: {remaining}"
            if remaining <= 3:
                info_msg += "\n\n✷ Потом введите пароль от подписки и общайтесь без ограничений!\n✷  Подписка и мануал здесь ☞ @aihimera\n\nХимера сейчас ответит..."
            await update.message.reply_text(info_msg)
        
        return True, None
    
    # 4. Лимит исчерпан - запрашиваем пароль
    state['auth_state'] = 'waiting_password'
    state['waiting_password_since'] = utc_now()
    
    limit_msg = (
        f"🚫 Лимит исчерпан ({DAILY_MESSAGE_LIMIT} сообщений в день)\n\n"
        f"Введите 🔑 пароль от подписки и общайтесь безлимитно!\n"
        f"Подписка и мануал здесь ☞ @aihimera"
    )
    
    return False, limit_msg

async def handle_password_input(update: Update, context: ContextTypes.DEFAULT_TYPE, password: str):
    """Обработка ввода пароля"""
    user_id = update.message.from_user.id
    state = get_user_state(user_id)
    
    # Обрабатываем попытку ввода пароля
    try:
        logger.info(f"Попытка обработки пароля для пользователя {user_id}")
        result = process_password_attempt(user_id, password)
        logger.info(f"Результат обработки пароля: {result}")
    except Exception as e:
        logger.error(f"ОШИБКА в process_password_attempt: {str(e)}")
        await update.message.reply_text(f"❌ Ошибка обработки пароля: {str(e)}")
        return False
    
    if result['success']:
        # Пароль правильный
        state['auth_state'] = 'authorized'
        state['waiting_password_since'] = None
        
        success_msg = (
            f"✅ Ура! Добро пожаловать в компанию Химеры на {result['duration_days']} дней.\n"
            f"✷ Полный доступ до {datetime.fromisoformat(result['authorized_until']).strftime('%d.%m.%Y %H:%M')}."
        )
        await update.message.reply_text(success_msg)
        return True
        
    elif result.get('blocked'):
        # Пользователь заблокирован
        state['auth_state'] = 'unauthorized'
        state['waiting_password_since'] = None
        
        blocked_time = format_time_remaining(result['blocked_seconds'])
        blocked_msg = (
            f"🚫 Слишком много неудачных попыток ввода пароля.\n"
            f"✷ Доступ заблокирован на {blocked_time}.\n"
            f"✷ После разблокировки вы сможете снова использовать бесплатные сообщения."
        )
        await update.message.reply_text(blocked_msg)
        return False
        
    else:
        # Пароль неправильный
        fail_msg = f"❌ Неверный пароль. Попробуйте еще раз. (Осталось попыток: {result['remaining_attempts']})"
        await update.message.reply_text(fail_msg)
        return False









# ========================================
# ХИМЕРА !!! ФУНКЦИИ ОПРЕДЕЛЕНИЯ РЕЖИМА
# ========================================

def detect_mode(text: str, user_id: int) -> str:
    """Умное определение режима работы с учётом контекста Химеры"""
    state = get_user_state(user_id)
    t = text.strip().lower()
    
    # Явное переключение режима пользователем
    if t in ["анализируем", "режим эксперта"]:
        state['mode'] = "expert"
        return "expert"
    if t in ["пишем", "режим писателя"]:
        state['mode'] = "writer"
        return "writer"
    if t in ["поболтаем", "режим беседы"]:
        state['mode'] = "talk"
        return "talk"
    if t == "авто":
        state['mode'] = "auto"
    
    # Приоритет сохранённого режима
    if state.get('mode') in ['expert', 'writer', 'talk']:
        return state['mode']
    
    # Контекстно-зависимое автоопределение
    if is_expert_query(t):
        return "expert"
    if is_writer_query(t):
        return "writer"
    if is_talk_query(t):
        return "talk"
    
    return "auto"

def is_expert_query(text: str) -> bool:
    """Определение аналитических запросов с защитой от ложных срабатываний"""
    expert_triggers = {
        "разбери": ["структур", "композици", "символ"],
        "объясни": ["значени", "подтекст", "метафор"],
        "анализ": ["текст", "стиль", "персонаж"],
        "как улучшить": ["сцен", "диалог", "описан"],
        "критика": ["правк", "слабое место", "ошибк"]
    }
    return any(
        trigger in text and any(context in text for context in contexts)
        for trigger, contexts in expert_triggers.items()
    )

def is_writer_query(text: str) -> bool:
    """Выявление творческих запросов с литературной спецификой"""
    writer_patterns = [
        r"напиши (сцену|фрагмент|диалог|описание) .+",
        r"продолжи (историю|текст|сюжет) .+",
        r"описать .+ (в стиле|как у Павича|в духе Борхеса)",
        r"создай (персонажа|образ) .+",
        r"развитие сюжета .+"
    ]
    return any(re.search(pattern, text) for pattern in writer_patterns)

def is_talk_query(text: str) -> bool:
    """Определение неформальных запросов"""
    talk_indicators = [
        "как твои дела", "что ты думаешь", "твое мнение", 
        "расскажи о себе", "как настроение",
        "а если бы", "представь что", "вообрази",
        "это потрясающе", "как интересно", "удивительно",
        "поспорим", "угадай", "шутк", "загадк"
    ]
    return (
        any(indicator in text for indicator in talk_indicators) or
        text.startswith(("а ", "но ", "и "))
    )
    
    
    
    
    
    
    
    
    
# ========================================
# ФУНКЦИИ ОБРАБОТКИ ТЕКСТА
# ========================================

def clean_bot_response(text):
    """Очистка ответа бота от форматирования"""
    # Убираем эмодзи
    text = re.sub(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        "", text
    )
    
    # Сохраняем дефисы внутри слов (например: "что-то")
    text = re.sub(r'(\w)-(\w)', r'\1-\2', text)
    
    # Удаляем только опасные символы форматирования
    text = re.sub(r'[\]\[*_`~<>#=]', ' ', text)
    
    # Убираем множественные пробелы
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()

def detect_format_violation(text):
    """Проверка нарушений форматирования"""
    if re.search(r'[\]\[*_`~<>#=]', text):
        return True
    if re.search(r'^\s*[\d\w]+[\.\)\-]\s+', text, re.MULTILINE):
        return True
    if re.search(r'^\s*[-*]\s+', text, re.MULTILINE):
        return True
    return False



# ========================================
# КОМАНДЫ АССИСТЕНТА
# ========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    try:
        user_id = update.message.from_user.id
        username = update.message.from_user.username
        first_name = update.message.from_user.first_name
        
        # Создаем пользователя в PostgreSQL
        ensure_user_exists(user_id, username, first_name)
        
        # Сбрасываем счетчики инъекций для нового диалога
        if INJECTION_SYSTEM_ENABLED and injection_system:
            injection_system.reset_user_counter(user_id)
        
        welcome_msg = (
            f"Привет! Я — Химера, искусственный интеллект магического реализма!\n\n"
            f"✷ Это демо-доступ на {DAILY_MESSAGE_LIMIT} сообщений в день\n"
            f"✷ Когда лимит закончится, введите 🔑 пароль от подписки и общайтесь безлимитно\n"
            f"✷ Подписка и мануал здесь ☞ @aihimera\n\n"
            f"✨Теперь можете общаться — Химера ждёт!✨"
        )
        
        await update.message.reply_text(welcome_msg)
        logger.info(f"Команда /start от пользователя {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка при выполнении /start: {str(e)}")
        await update.message.reply_text("Ошибка при запуске бота.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /status"""
    try:
        user_id = update.message.from_user.id
        
        # Проверяем авторизацию
        auth_status = check_user_auth_status(user_id)
        
        if auth_status.get('authorized'):
            auth_until = datetime.fromisoformat(auth_status['authorized_until'])
            days_left = (auth_until - utc_now()).days
            
            status_msg = (
                f"✷ Подписка до: {auth_until.strftime('%d.%m.%Y')} (ещё {days_left} дн.)\n"
               # f"/logout — выйти из аккаунта"
            )
        else:
            # Проверяем лимиты
            limit_check = check_daily_limit(user_id)
            
            status_msg = (
                f"Демо-доступ. У вас {DAILY_MESSAGE_LIMIT} сообщений в день\n"
                f"✷ Использовано: {limit_check['count']}/{limit_check['limit']}, осталось: {limit_check['remaining']}\n"
                f"✷ Введите 🔑 пароль от подписки для снятия ограничений\n"
                f"✷ Подписка и мануал здесь ☞ @aihimera"
            )
        
        # Добавляем информацию об автосохранении для авторизованных
        if auth_status.get('authorized'):
            auto_save_limit = check_auto_save_limit(user_id)
            status_msg += f"✷ Сохранено в память: {auto_save_limit['count']} из {auto_save_limit['limit']} диалогов за сегодня"
            
            # Добавляем информацию о проактивности
            proactivity_status = get_proactivity_status(user_id)
            if proactivity_status:
                status_msg += f"\n\n{proactivity_status}"
        
        await update.message.reply_text(status_msg)
        
    except Exception as e:
        logger.error(f"Ошибка при выполнении /status: {str(e)}")
        await update.message.reply_text("Ошибка при получении статуса.")

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /logout"""
    try:
        user_id = update.message.from_user.id
        
        if logout_user(user_id):
            update_user_state(user_id, auth_state='unauthorized')
            logout_msg = "✅ Вы успешно вышли из аккаунта"
        else:
            logout_msg = "ℹ️ Вы не были авторизованы"
        
        await update.message.reply_text(logout_msg)
        
    except Exception as e:
        logger.error(f"Ошибка при выполнении /logout: {str(e)}")
        await update.message.reply_text("Ошибка при выходе из аккаунта.")

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /remember - сохранение диалога в долговременную память"""
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    first_name = update.message.from_user.first_name
    
    try:
        # Убеждаемся, что пользователь существует в PostgreSQL
        ensure_user_exists(user_id, username, first_name)
        
        # Проверяем ТОЛЬКО авторизацию (LTM только для подписчиков)
        auth_status = check_user_auth_status(user_id)
        
        if not auth_status.get('authorized'):
            await update.message.reply_text(
                "🔒 Долговременная память Химеры активируется только по подписке. \n"
                "✷ Подписку и мануал здесь ☞ @aihimera \n"
                "✷ С подпиской Химера будет помнить многое о вас!"
                )
            return

        # Проверяем предупреждение об истечении
        if not auth_status.get('warned_expiry'):
            auth_until = datetime.fromisoformat(auth_status['authorized_until'])
            days_left = (auth_until - utc_now()).days
            
            if days_left <= 2 and days_left > 0:
                if update_user_warning_flag(user_id):
                    warning_msg = f"⚠️ Осталось {days_left} д. Обратитесь за новым паролем: ☞ @aihimera"
                    await update.message.reply_text(warning_msg)
        
        # Парсим аргументы команды
        args = context.args
        importance_score = 5  # По умолчанию
        
        if args:
            try:
                importance_score = int(args[0])
                if importance_score < 1 or importance_score > 10:
                    await update.message.reply_text("❌ Важность должна быть от 1 до 10")
                    return
            except ValueError:
                await update.message.reply_text("❌ Важность должна быть числом от 1 до 10")
                return
        
        # Получаем последние сообщения из PostgreSQL
        recent_history = get_recent_history(user_id, limit=2)
        
        if len(recent_history) < 2:
            await update.message.reply_text("❌ Мало истории для сохранения. Сначала задайте вопрос и получите ответ.")
            return
        
        # Находим последнюю пару: вопрос пользователя + ответ бота
        user_message = None
        bot_response = None
        
        for i in range(len(recent_history) - 1, -1, -1):
            if recent_history[i]['role'] == 'assistant' and bot_response is None:
                bot_response = recent_history[i]['content']
            elif recent_history[i]['role'] == 'user' and user_message is None and bot_response is not None:
                user_message = recent_history[i]['content']
                break
        
        if not user_message or not bot_response:
            await update.message.reply_text("❌ Не найдена подходящая пара сообщений для сохранения")
            return
        
        # Сохраняем в долговременную память
        memory_id = save_conversation_to_ltm(
            user_id=user_id,
            user_message=user_message,
            bot_response=bot_response,
            importance_score=importance_score,
            auto_analyze=True
        )
        
        # Получаем статистику пользователя
        stats = get_user_ltm_stats(user_id)
        
        # Формируем ответ
        success_msg = (
            f"✅ Диалог сохранен в долговременную память!\n\n"
            f"✷ Важность: {importance_score}/10\n"
            f"✷ Всего воспоминаний: {stats['total_memories']}\n"
            f"✷ Теперь Химера будет использовать этот пример для улучшения ответов!"
        )
        
        await update.message.reply_text(success_msg)
        logger.info(f"Пользователь {user_id} сохранил воспоминание {memory_id} с важностью {importance_score}")
        
    except Exception as e:
        logger.error(f"Ошибка в команде /remember: {str(e)}")
        await update.message.reply_text("❌ Ошибка при сохранении в долговременную память")

async def memory_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /memory_stats - статистика долговременной памяти пользователя"""
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    first_name = update.message.from_user.first_name
    
    try:
        ensure_user_exists(user_id, username, first_name)
        
        # Проверяем ТОЛЬКО авторизацию (LTM только для подписчиков)
        auth_status = check_user_auth_status(user_id)
        
        if not auth_status.get('authorized'):
            await update.message.reply_text(
                "🔒 Статистика долговременной памяти доступна подписчикам\n\n"
                "✷ Подписка и мануал здесь ☞ @aihimera"
            )
            return

        # Получаем статистику
        stats = get_user_ltm_stats(user_id)
        recent_memories = get_recent_memories(user_id, limit=5)
        
        if stats['total_memories'] == 0:
            msg = (
                "🌀 ДОЛГОВРЕМЕННАЯ ПАМЯТЬ\n\n"
                "✷ Пока нет сохраненных воспоминаний\n\n"
                "✷ Используйте команду /remember для сохранения интересных диалогов\n"
                "✷ Химера также автоматически сохраняет особо удачные беседы"
            )
        else:
            msg = (
                f"🌀 ДОЛГОВРЕМЕННАЯ ПАМЯТЬ\n\n"
                f"✷ Всего воспоминаний: {stats['total_memories']}\n"
                f"✷ Средняя важность: {stats['avg_importance']:.1f}/10\n"
                f"✷ Избранные: {stats['user_favorites']}\n"
            )
            
            if stats['last_memory_date']:
                last_date = stats['last_memory_date'].strftime('%d.%m.%Y')
                msg += f"📅 Последнее: {last_date}\n"
            
            # Информация об автосохранении (пользователь уже авторизирован)
            auto_save_limit = check_auto_save_limit(user_id)
            msg += f"\n💭 Автосохранений: {auto_save_limit['count']} из {auto_save_limit['limit']} на сегодня\n"
            
            if recent_memories:
                msg += f"\n📜 ПОСЛЕДНИЕ ВОСПОМИНАНИЯ:\n"
                for i, memory in enumerate(recent_memories[:3], 1):
                    created = memory['created_at'].strftime('%d.%m')
                    memory_type = "🤖" if memory['memory_type'] == 'auto_saved' else "👤"
                    msg += f"{i}. [{created}] {memory_type} ⭐{memory['importance_score']} - {memory['user_message'][:50]}...\n"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Ошибка в команде /memory_stats: {str(e)}")
        await update.message.reply_text("❌ Ошибка при получении статистики памяти")



# ========================================
# АДМИНИСТРАТИВНЫЕ КОМАНДЫ
# ========================================

async def admin_add_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin_add_password"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    try:
        args = context.args
        if len(args) < 3:
            await update.message.reply_text(
                "❌ Использование: /admin_add_password <пароль> <дни> <описание>\n"
                f"Доступные дни: {AVAILABLE_DURATIONS}"
            )
            return
        
        password = args[0]
        try:
            days = int(args[1])
        except ValueError:
            await update.message.reply_text("❌ Количество дней должно быть числом.")
            return
        
        description = " ".join(args[2:])
        
        if days not in AVAILABLE_DURATIONS:
            await update.message.reply_text(f"❌ Недопустимая продолжительность. Доступны: {AVAILABLE_DURATIONS}")
            return
        
        success = add_password(password, description, days)
        
        if success:
            await update.message.reply_text(
                f"✅ Пароль '{password}' добавлен на {days} дней.\n"
                f"📝 Описание: {description}"
            )
        else:
            await update.message.reply_text(f"❌ Пароль '{password}' уже существует.")
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении пароля: {str(e)}")
        await update.message.reply_text("❌ Ошибка при добавлении пароля.")

async def admin_list_passwords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin_list_passwords"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    try:
        show_full = len(context.args) > 0 and context.args[0] == "full"
        passwords = list_passwords(show_full=show_full)
        
        if not passwords:
            await update.message.reply_text("📝 Паролей не найдено.")
            return
        
        msg = f"📋 ПАРОЛИ ({len(passwords)} шт.):\n" + "="*30 + "\n"
        
        for i, p in enumerate(passwords, 1):
            status = "🟢" if p['is_active'] else "🔴"
            created = datetime.fromisoformat(p['created_at']).strftime("%d.%m")
            
            expires_info = ""
            if p['expires_at']:
                expires_date = datetime.fromisoformat(p['expires_at']).strftime("%d.%m")
                expires_info = f", истекает {expires_date}"
            
            msg += (
                f"{i}. {status} {p['password']}\n"
                f"   📝 {p['description']}\n"
                f"   📅 {p['duration_days']} дн, создан {created}{expires_info}, использован {p['times_used']}x\n\n"
            )
        
        if len(msg) > 4000:
            for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
                await update.message.reply_text(chunk)
        else:
            await update.message.reply_text(msg)
            
    except Exception as e:
        logger.error(f"Ошибка при получении списка паролей: {str(e)}")
        await update.message.reply_text("❌ Ошибка при получении списка паролей.")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin_stats"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    try:
        stats = get_password_stats()
        users_stats = get_users_stats()
        
        msg = (
            f"📊 СТАТИСТИКА\n"
            f"🔑 Активных паролей: {stats['active_passwords']}\n"
            f"✷ Деактивированных: {stats['inactive_passwords']}\n"
            f"✷ Всего использований: {stats['total_uses']}\n\n"
            f"✷ Всего пользователей: {users_stats['total_users']}\n"
            f"✅ Сейчас авторизовано: {users_stats['active_users']}\n"
            f"✷ Заблокировано: {users_stats['blocked_users']}\n\n"
            f"✷ По длительности:\n"
        )
        
        for days, count in stats['by_duration'].items():
            msg += f"   {days} дней: {count} паролей\n"
        
        msg += f"\n🤖 JSON статистика:\n"
        msg += f"   Успешных: {json_stats['success']}\n"
        msg += f"   Ошибок: {json_stats['failures']}\n"
        msg += f"   Fallback: {json_stats['fallbacks']}\n"
        msg += f"   JSON режим: {'✅ Включен' if USE_JSON_OUTPUT else '❌ Выключен'}\n"
        
        msg += f"\n💭 Автосохранение:\n"
        msg += f"   Лимит в день: {MAX_AUTO_SAVES_PER_DAY}\n"
        msg += f"   Важность: {AUTO_SAVE_IMPORTANCE}/10\n"
        
        # Статистика адаптивных инъекций
        if INJECTION_SYSTEM_ENABLED and injection_system:
            injection_stats = injection_system.get_stats()
            msg += f"\n\n🎯 Адаптивные инъекции:\n"
            msg += f"   Активных диалогов: {injection_stats['active_users']}\n"
            msg += f"   Всего инъекций: {injection_stats['total_injections']}\n"
            msg += f"   Кэшировано: {injection_stats.get('cached_injections', 0)}\n"
            msg += f"   Статус: ✅ Включены (с динамической инвалидацией)\n"
        else:
            msg += f"\n\n🎯 Адаптивные инъекции: ❌ Выключены\n"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {str(e)}")
        await update.message.reply_text("❌ Ошибка при получении статистики.")

async def admin_deactivate_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin_deactivate_password"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    try:
        args = context.args
        if len(args) < 1:
            await update.message.reply_text("❌ Использование: /admin_deactivate_password <пароль>")
            return
        
        password = args[0]
        
        if deactivate_password(password):
            await update.message.reply_text(f"✅ Пароль '{password}' деактивирован.")
        else:
            await update.message.reply_text(f"❌ Пароль '{password}' не найден.")
            
    except Exception as e:
        logger.error(f"Ошибка при деактивации пароля: {str(e)}")
        await update.message.reply_text("❌ Ошибка при деактивации пароля.")

async def admin_auth_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin_auth_log"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    try:
        target_user_id = None
        if context.args:
            try:
                target_user_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ ID пользователя должен быть числом.")
                return
        
        logs = get_auth_log(user_id=target_user_id, limit=20)
        
        if not logs:
            await update.message.reply_text("📝 Логов не найдено.")
            return
        
        msg = f"📜 ЛОГИ АВТОРИЗАЦИИ"
        if target_user_id:
            msg += f" (user {target_user_id})"
        msg += f" - последние {len(logs)}:\n" + "="*30 + "\n"
        
        for log in logs[:10]:
            timestamp = datetime.fromisoformat(log['timestamp']).strftime("%d.%m %H:%M")
            action_emoji = {
                'password_success': '✅',
                'password_fail': '❌',
                'auto_expired': '⏰',
                'blocked': '🚫',
                'unblocked': '🔓',
                'manual_logout': '👋'
            }.get(log['action'], '📝')
            
            msg += f"{action_emoji} {timestamp} | U{log['user_id']} | {log['action']}\n"
            if log['password_masked']:
                msg += f"   Пароль: {log['password_masked']}\n"
            if log['details']:
                msg += f"   {log['details']}\n"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Ошибка при получении логов: {str(e)}")
        await update.message.reply_text("❌ Ошибка при получении логов.")

async def admin_blocked_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin_blocked_users"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    try:
        blocked = get_blocked_users()
        
        if not blocked:
            await update.message.reply_text("✅ Заблокированных пользователей нет.")
            return
        
        msg = f"🚫 ЗАБЛОКИРОВАННЫЕ ({len(blocked)} чел.):\n" + "="*30 + "\n"
        
        for user in blocked:
            remaining_min = user['remaining_seconds'] // 60
            msg += (
                f"User {user['user_id']}:\n"
                f"  Осталось: {remaining_min} мин\n"
                f"  Попыток: {user['failed_attempts']}\n\n"
            )
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Ошибка при получении заблокированных: {str(e)}")
        await update.message.reply_text("❌ Ошибка при получении списка.")

async def admin_unblock_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin_unblock_user"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    try:
        args = context.args
        if len(args) < 1:
            await update.message.reply_text("❌ Использование: /admin_unblock_user <user_id>")
            return
        
        try:
            target_user_id = int(args[0])
        except ValueError:
            await update.message.reply_text("❌ ID пользователя должен быть числом.")
            return
        
        if unblock_user(target_user_id):
            await update.message.reply_text(f"✅ Пользователь {target_user_id} разблокирован.")
        else:
            await update.message.reply_text(f"❌ Пользователь {target_user_id} не заблокирован.")
            
    except Exception as e:
        logger.error(f"Ошибка при разблокировке: {str(e)}")
        await update.message.reply_text("❌ Ошибка при разблокировке.")



# ========================================
# ОБРАБОТЧИКИ СООБЩЕНИЙ
# ========================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фото"""
    can_proceed, auth_message = await check_auth_and_limits(update, context)
    
    if not can_proceed:
        await update.message.reply_text(auth_message)
        return
    
    await update.message.reply_text(random.choice(PHOTO_REPLIES))

async def handle_image_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка изображений как документов"""
    if update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("image/"):
        await handle_photo(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главная функция обработки сообщений"""
    user_message = update.message.text.strip()
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    first_name = update.message.from_user.first_name
    
    logger.info(f"Получено сообщение от {user_id}: {user_message[:100]}")

    # Создаем пользователя в PostgreSQL
    ensure_user_exists(user_id, username, first_name)
    
    # Обновляем активность пользователя для динамической TTL
    if INJECTION_SYSTEM_ENABLED and redis_client:
        try:
            activity_key = f"himera:user_activity:{user_id}"
            redis_client.incr(activity_key)
            redis_client.expire(activity_key, 604800)  # TTL 7 дней
        except Exception as e:
            logger.debug(f"Failed to update user activity: {e}")

    state = get_user_state(user_id)

    try:
        # Проверка таймаута ожидания пароля
        if state['auth_state'] == 'waiting_password' and state['waiting_password_since']:
            waiting_time = (utc_now() - state['waiting_password_since']).total_seconds()
            if waiting_time > AUTH_TIMEOUT:
                update_user_state(user_id, auth_state='unauthorized', waiting_password_since=None)
                await update.message.reply_text(
                    f"✷ Время ожидания пароля истекло ({AUTH_TIMEOUT//60} мин).\n"
                    f"✷ Вы можете продолжить использовать бесплатные сообщения."
                )

        # Обработка ввода пароля
        if state['auth_state'] == 'waiting_password':
            await handle_password_input(update, context, user_message)
            return

        # Проверка авторизации и лимитов
        can_proceed, auth_message = await check_auth_and_limits(update, context)
        
        if not can_proceed:
            await update.message.reply_text(auth_message)
            return

        # Определяем режим работы
        mode = detect_mode(user_message, user_id)
        logger.info(f"Режим пользователя {user_id}: {mode}")

        # Анализируем эмоции и сохраняем сообщение в PostgreSQL
        emotion_label, emotion_confidence = get_emotion(user_message)
        add_message_to_history_with_cleanup(user_id, "user", user_message, emotion_label, emotion_confidence, mode)

        # Строим контекст с долговременной памятью
        messages = build_enhanced_context_with_ltm_smart(user_id, user_message, mode, history_limit=HISTORY_LIMIT)
        
        
        
        # === ОБРАБОТКА С JSON РЕЖИМОМ ===
        
        final_response = None
        
        if USE_JSON_OUTPUT:
            try:
                response = await ask_deepseek_with_typing(
                    update, context, messages, mode=mode, use_json=True
                )
                json_response = json.loads(response)
                clean_text = json_response.get("response", "")
                
                if not clean_text:
                    raise ValueError("Пустое поле response в JSON")
                
                if detect_format_violation(clean_text):
                    logger.warning(f"JSON содержит нарушения формата: {clean_text[:100]}")
                    clean_text = clean_bot_response(clean_text)
                
                json_stats['success'] += 1
                final_response = clean_text
                
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.error(f"Ошибка JSON парсинга: {str(e)}")
                json_stats['failures'] += 1
                
                if JSON_FALLBACK_ENABLED:
                    logger.info("Активирован fallback режим без JSON")
                    json_stats['fallbacks'] += 1
                    
                    fallback_response = await ask_deepseek_with_typing(
                        update, context, messages, mode=mode, use_json=False
                    )
                    final_response = clean_bot_response(fallback_response)
                else:
                    final_response = "Извините, произошла ошибка формирования ответа. Попробуйте еще раз."
        else:
            # JSON режим отключен
            response = await ask_deepseek_with_typing(
                update, context, messages, mode=mode, use_json=False
            )
            
            if detect_format_violation(response):
                logger.warning(f"Формат нарушен: {response[:100]}")
                add_message_to_history(user_id, "system", INJECTION_PROMPT)
            
            final_response = clean_bot_response(response)
        
        # Сохраняем ответ в PostgreSQL
        add_message_to_history_with_cleanup(user_id, "assistant", final_response, bot_mode=mode)
        
        
        
        # === АВТОСОХРАНЕНИЕ ===
        
        auto_save_notification = ""
        
        # Проверяем нужно ли автосохранение для ТЕКУЩЕГО диалога
        if should_auto_save(emotion_label, user_message):
            auth_status = check_user_auth_status(user_id)
            if auth_status.get('authorized'):
                # Сохраняем ТЕКУЩУЮ пару диалога
                auto_save_result = auto_save_conversation(
                    user_id=user_id, 
                    user_message=user_message,      # Текущее сообщение пользователя
                    bot_response=final_response,     # Только что сгенерированный ответ
                    emotion_primary=emotion_label
                )
                if auto_save_result:
                    logger.info(f"✅ Автосохранение {auto_save_result}: '{user_message[:30]}...' -> '{final_response[:30]}...'")
                    auto_save_notification = f"\n\n✨ Диалог сохранен в долговременную память ✨"
        
        
        
        # === ОБРАБОТКА ОТВЕТА НА ИНИЦИАЦИЮ ===
        
        if PROACTIVE_ENGINE_ENABLED and proactive_engine:
            # Проверяем, является ли это ответом на инициацию
            try:
                was_response = proactive_engine.process_user_response(
                    user_id=user_id,
                    message=user_message,
                    emotion=emotion_label,
                    emotion_confidence=emotion_confidence
                )
                
                if was_response:
                    logger.info(f"Processed response to proactive initiation from user {user_id}")
                    
            except Exception as e:
                logger.error(f"Error processing proactive response: {e}")
        
        # Отправляем финальный ответ с уведомлением об автосохранении
        await update.message.reply_text(final_response + auto_save_notification)

    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {str(e)}")
        await update.message.reply_text("Внутренняя ошибка бота. Попробуйте позже.")



# ========================================
# ГЛАВНАЯ ФУНКЦИЯ
# ========================================

def main():
    """Главная функция запуска бота"""
    
    # Инициализация долговременной памяти
    logger.info("Инициализация долговременной памяти...")
    
    # Передаем систему инъекций в ltm_database
    from ltm_database import set_injection_system
    set_injection_system(INJECTION_SYSTEM_ENABLED, injection_system)
    if not init_ltm():
        logger.error("КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать долговременную память!")
        logger.error("Проверьте подключение к PostgreSQL и файл .env.ltm")
        return
    else:
        logger.info("✅ Долговременная память готова к работе")
    
    # Тестирование функций авторизации
    logger.info("Проверка функций авторизации...")
    try:
        from auth_database import test_auth_functions
        if test_auth_functions():
            logger.info("✅ Авторизация PostgreSQL готова к работе")
        else:
            logger.warning("⚠️ Обнаружены проблемы с авторизацией, но продолжаем работу")
    except Exception as e:
        logger.error(f"Ошибка тестирования авторизации: {e}")
        logger.warning("⚠️ Продолжаем работу без тестирования авторизации")
    
    # Выполняем очистку при старте
    try:
        cleanup_old_limits()
        cleanup_expired_users()
        cleanup_old_memories()
        cleanup_old_auto_saves()
        logger.info("Выполнена очистка устаревших данных при старте")
    except Exception as e:
        logger.error(f"Ошибка при очистке данных: {e}")

    # Логируем настройки
    logger.info(f"JSON Output режим: {'ВКЛЮЧЕН' if USE_JSON_OUTPUT else 'ВЫКЛЮЧЕН'}")
    logger.info(f"JSON Fallback: {'ВКЛЮЧЕН' if JSON_FALLBACK_ENABLED else 'ВЫКЛЮЧЕН'}")
    logger.info(f"Автосохранение: лимит {MAX_AUTO_SAVES_PER_DAY}/день, важность {AUTO_SAVE_IMPORTANCE}")

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # ========================================
    # НАСТРОЙКА ПРОАКТИВНОЙ СИСТЕМЫ
    # ========================================
    
    async def setup_proactive_system(app):
        """Инициализация проактивной системы после запуска бота"""
        global PROACTIVE_ENGINE_ENABLED, proactive_engine
        
        logger.info("Настройка проактивной системы...")
        
        try:
            # Создаем движок с правильным telegram_app
            proactive_engine = ProactiveInitiationEngine(
                postgres_conn=None,
                redis_client=redis_client,
                telegram_app=app
            )
            
            PROACTIVE_ENGINE_ENABLED = True
            logger.info("✅ ProactiveInitiationEngine создан")
            
            # Передаем движок в cron_jobs
            cron_jobs.set_proactive_engine(proactive_engine)
            
            # Настраиваем пользователей
            setup_proactivity_for_new_users()
            
            # Запускаем фоновые задачи
            cron_jobs.start_proactive_cron()
            
            logger.info("🤖 Проактивная система полностью запущена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка настройки проактивной системы: {e}")
            import traceback
            traceback.print_exc()
            PROACTIVE_ENGINE_ENABLED = False
            proactive_engine = None
    
    # Устанавливаем callback для инициализации после запуска
    application.post_init = setup_proactive_system
    
    # Основные команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("logout", logout_command))
    
    # Команды долговременной памяти
    application.add_handler(CommandHandler("remember", remember_command))
    application.add_handler(CommandHandler("memory_stats", memory_stats_command))
    
    # Команды проактивности
    application.add_handler(CommandHandler("writeme", writeme_command))
    application.add_handler(CommandHandler("dontwrite", dontwrite_command))
    application.add_handler(CommandHandler("writeme_pause", writeme_pause_command))
    
    # Административные команды
    application.add_handler(CommandHandler("admin_add_password", admin_add_password))
    application.add_handler(CommandHandler("admin_list_passwords", admin_list_passwords))
    application.add_handler(CommandHandler("admin_stats", admin_stats))
    application.add_handler(CommandHandler("admin_deactivate_password", admin_deactivate_password))
    application.add_handler(CommandHandler("admin_auth_log", admin_auth_log))
    application.add_handler(CommandHandler("admin_blocked_users", admin_blocked_users))
    application.add_handler(CommandHandler("admin_unblock_user", admin_unblock_user))
    
    # Обработчики контента
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.IMAGE, handle_image_doc))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    logger.info("🚀 Химера Лайт запущена с автосохранением в долговременную память")
    



    
    try:
        application.run_polling(close_loop=False)   # цикл остаётся открыт
    finally:
        if PROACTIVE_ENGINE_ENABLED:
            cron_jobs.stop_proactive_cron()
            logger.info("✅ Проактивная система остановлена")
    
        import asyncio
        asyncio.get_event_loop().close()            # закрываем сами


    
    """
    try:
        application.run_polling()
    finally:
        # Останавливаем фоновые задачи при выходе
        if PROACTIVE_ENGINE_ENABLED:
            cron_jobs.stop_proactive_cron()
            logger.info("✅ Проактивная система остановлена")
    """
    

if __name__ == "__main__":
    main()