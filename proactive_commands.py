"""
Обработчики команд для проактивной системы
"""

from telegram import Update
from telegram.ext import ContextTypes
import logging
from datetime import datetime, timedelta, timezone
import re
import pytz

from config import PROACTIVITY_DEFAULT_STATE, PROACTIVITY_AB_TEST_ENABLED, MAX_INITIATIONS_PER_DAY
from auth_database import check_user_auth_status

logger = logging.getLogger("proactive_commands")

async def writeme_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /writeme - включение инициативности"""
    
    user_id = update.message.from_user.id
    
    try:
        # Проверяем авторизацию
        auth_status = check_user_auth_status(user_id)
        
        if not auth_status.get('authorized'):
            await update.message.reply_text(
                "🔒 Химера проявляет инициативу только с подписчиками\n\n"
                "✷ Подписку можно оформить здесь ☞ @aihimera\n"
                "✷ После этого Химера сама сможет начинать интересные разговоры!"
            )
            return
        
        # Проверяем A/B тест группу
        if PROACTIVITY_AB_TEST_ENABLED:
            ab_group = get_or_assign_ab_group(user_id)
            if ab_group != 'A':
                await update.message.reply_text(
                    "🔧 Эта функция пока находится в тестировании.\n"
                    "✷ Попробуйте позже!"
                )
                return
        
        # Включаем проактивность
        enable_proactivity(user_id)
        
        success_msg = (
            "💥 Вау! Теперь я могу сама начинать разговоры! 💥\n\n"
            "Отключить инициативность: /dontwrite\n"
           # "/writeme_pause 3d - «Не пиши мне сама некоторое время»\n"
            "Общие данные: /status"
        )
        
        await update.message.reply_text(success_msg)
        logger.info(f"User {user_id} enabled proactivity")
        
    except Exception as e:
        logger.error(f"Error in /writeme command: {e}")
        await update.message.reply_text("❌ Ошибка при включении инициативности")

async def dontwrite_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /dontwrite - отключение инициативности"""
    
    user_id = update.message.from_user.id
    
    try:
        # Проверяем что проактивность включена
        if not is_proactivity_enabled(user_id):
            await update.message.reply_text(
                "✅ Инициативность и так отключена\n\n"
                "Включить инициативность: /writeme\n"
                "Общие данные: /status"
            )
            return
        
        # Отключаем проактивность
        disable_proactivity(user_id)
        
        await update.message.reply_text(
            "✅ Инициативность отключена\n\n"
            "Включить инициативность: /writeme\n"
            "Общие данные: /status"
        )
        
        logger.info(f"User {user_id} disabled proactivity")
        
    except Exception as e:
        logger.error(f"Error in /dontwrite command: {e}")
        await update.message.reply_text("❌ Ошибка при отключении инициативности")

async def writeme_pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /writeme_pause <время> - временная пауза"""
    
    user_id = update.message.from_user.id
    
    try:
        # Проверяем что проактивность включена
        if not is_proactivity_enabled(user_id):
            await update.message.reply_text(
                "✅ Инициативность отключена\n"
                "Включить: /writeme"
            )
            return
        
        # Парсим время паузы
        args = context.args
        if not args:
            await update.message.reply_text(
                "❌ Укажите время паузы.\n\n"
                "Примеры:\n"
                "/writeme_pause 3h - на 3 часа\n" 
                "/writeme_pause 1d - на 1 день\n"
                "/writeme_pause 7d - на неделю"
            )
            return
        
        pause_duration = parse_pause_duration(args[0])
        if not pause_duration:
            await update.message.reply_text(
                "❌ Неверный формат времени.\n"
                "Используйте: 1h, 3h, 1d, 3d, 7d"
            )
            return
        
        # Устанавливаем паузу
        pause_until = datetime.now(timezone.utc) + pause_duration
        set_proactivity_pause(user_id, pause_until)
        
        # Форматируем время для ответа
        user_tz = get_user_timezone(user_id)
        pause_until_local = pause_until.astimezone(pytz.timezone(user_tz))
        formatted_time = pause_until_local.strftime("%d.%m в %H:%M")
        duration_text = format_duration(pause_duration)
        
        await update.message.reply_text(
            f"✅ Делаю паузу на {duration_text}\n"
            f"Не побеспокою до {formatted_time}\n\n"
            f"Включить снова: /writeme"
        )
        
        logger.info(f"User {user_id} paused proactivity until {pause_until}")
        
    except Exception as e:
        logger.error(f"Error in /writeme_pause command: {e}")
        await update.message.reply_text("❌ Ошибка при установке паузы")

def parse_pause_duration(duration_str: str) -> timedelta:
    """Парсинг строки длительности в timedelta"""
    
    pattern = r'^(\d+)(h|d)$'
    match = re.match(pattern, duration_str.lower())
    
    if not match:
        return None
    
    value = int(match.group(1))
    unit = match.group(2)
    
    if unit == 'h':
        if value > 24:  # Максимум 24 часа
            return None
        return timedelta(hours=value)
    elif unit == 'd':
        if value > 7:  # Максимум 7 дней
            return None
        return timedelta(days=value)
    
    return None

def format_duration(duration: timedelta) -> str:
    """Форматирование длительности для отображения"""
    
    total_seconds = int(duration.total_seconds())
    
    if total_seconds < 3600:  # Меньше часа
        minutes = total_seconds // 60
        return f"{minutes} минут"
    elif total_seconds < 86400:  # Меньше дня
        hours = total_seconds // 3600
        return f"{hours} час{'а' if 2 <= hours <= 4 else 'ов' if hours > 4 else ''}"
    else:  # Дни
        days = total_seconds // 86400
        return f"{days} {'день' if days == 1 else 'дня' if 2 <= days <= 4 else 'дней'}"

def enable_proactivity(user_id: int):
    """Включение проактивности в БД"""
    
    from ltm_database import get_connection, utc_now
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Определяем часовой пояс по location (Amsterdam по умолчанию)
        timezone = detect_user_timezone(user_id)
        ab_group = get_or_assign_ab_group(user_id)
        
        cursor.execute("""
            INSERT INTO user_proactivity_settings 
            (user_id, is_enabled, enabled_at, ab_test_group, timezone)
            VALUES (%s, TRUE, %s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET 
                is_enabled = TRUE,
                enabled_at = %s,
                paused_until = NULL,
                pause_reason = NULL,
                timezone = %s,
                updated_at = NOW()
        """, (user_id, utc_now(), ab_group, timezone, utc_now(), timezone))
        
        conn.commit()
        
    except Exception as e:
        logger.error(f"Error enabling proactivity for user {user_id}: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

def disable_proactivity(user_id: int):
    """Отключение проактивности в БД"""
    
    from ltm_database import get_connection
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            UPDATE user_proactivity_settings 
            SET is_enabled = FALSE, updated_at = NOW()
            WHERE user_id = %s
        """, (user_id,))
        
        # Отменяем все запланированные инициации
        cursor.execute("""
            UPDATE initiation_schedule 
            SET status = 'cancelled' 
            WHERE user_id = %s AND status = 'pending'
        """, (user_id,))
        
        conn.commit()
        
    except Exception as e:
        logger.error(f"Error disabling proactivity for user {user_id}: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

def set_proactivity_pause(user_id: int, pause_until: datetime):
    """Установка паузы проактивности"""
    
    from ltm_database import get_connection
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            UPDATE user_proactivity_settings 
            SET paused_until = %s, 
                pause_reason = 'user_pause',
                updated_at = NOW()
            WHERE user_id = %s
        """, (pause_until, user_id))
        
        # Отменяем инициации на период паузы
        cursor.execute("""
            UPDATE initiation_schedule 
            SET status = 'cancelled' 
            WHERE user_id = %s 
            AND status = 'pending'
            AND scheduled_at <= %s
        """, (user_id, pause_until))
        
        conn.commit()
        
    except Exception as e:
        logger.error(f"Error setting pause for user {user_id}: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

def is_proactivity_enabled(user_id: int) -> bool:
    """Проверка включена ли проактивность"""
    
    from ltm_database import get_connection
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT is_enabled, paused_until
            FROM user_proactivity_settings
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        
        if not result:
            return False
        
        # Проверяем что включено и не на паузе
        is_enabled = result['is_enabled']
        paused_until = result['paused_until']
        
        if not is_enabled:
            return False
        
        if paused_until and paused_until > datetime.now(timezone.utc):
            return False
        
        return True
        
    finally:
        cursor.close()
        conn.close()

def get_or_assign_ab_group(user_id: int) -> str:
    """Получение или назначение A/B группы"""
    
    # Детерминированное разделение по user_id
    return 'A' if user_id % 2 == 0 else 'B'

def get_proactivity_status(user_id: int) -> str:
    """Получение статуса проактивности для отображения"""
    
    from ltm_database import get_connection
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Получаем настройки
        cursor.execute("""
            SELECT is_enabled, paused_until, pause_reason
            FROM user_proactivity_settings
            WHERE user_id = %s
        """, (user_id,))
        
        settings = cursor.fetchone()
        
        if not settings:
            # Проактивность никогда не включалась
            return "Инициативность: ❌ отключена \nВключить инициативность: /writeme"
        
        if not settings['is_enabled']:
            return "Инициативность: ❌ отключена \nВключить инициативность: /writeme"
        
        # Проверяем паузу
        if settings['paused_until'] and settings['paused_until'] > datetime.now(timezone.utc):
            user_tz = get_user_timezone(user_id)
            pause_end = settings['paused_until'].astimezone(pytz.timezone(user_tz))
            formatted_time = pause_end.strftime("%d.%m в %H:%M")
            
            return (
                f"Инициативность: ⏸️ пауза до {formatted_time}\n"
                f"Включить досрочно: /writeme\n"
                f"Отключить полностью: /dontwrite"
            )
        
        # Активна - показываем статистику
        from datetime import datetime, timezone, timedelta
        
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)
        
        cursor.execute("""
            SELECT COUNT(*) as today_count
            FROM initiation_schedule
            WHERE user_id = %s 
            AND scheduled_at >= %s
            AND scheduled_at < %s
            AND status IN ('pending', 'sent')
        """, (user_id, today_start, tomorrow_start))
        
        today_count = cursor.fetchone()['today_count']
        
        # Получаем следующую запланированную
        cursor.execute("""
            SELECT scheduled_at
            FROM initiation_schedule
            WHERE user_id = %s 
            AND status = 'pending'
            AND scheduled_at > NOW()
            ORDER BY scheduled_at
            LIMIT 1
        """, (user_id,))
        
        next_initiation = cursor.fetchone()
        
        status = (
            "✷ Инициативность: ✅ полная\n"
            f"✷ Проявлений инициативы сегодня: {today_count}/{MAX_INITIATIONS_PER_DAY}\n"
            "✷ Отключить инициативность: /dontwrite\n"
           # "✷ Пауза: /writeme_pause <время>"
        )
        
        if next_initiation:
            hours_until = (next_initiation['scheduled_at'] - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_until < 24:
                status = status.replace(
                    " └─ Пауза:", 
                    f" ├─ Следующая через: ~{int(hours_until)} ч\n └─ Пауза:"
                )
        
        return status
        
    except Exception as e:
        logger.error(f"Error getting proactivity status: {e}")
        return "Инициативность: ❓ Ошибка получения статуса"
    finally:
        cursor.close()
        conn.close()

def detect_user_timezone(user_id: int) -> str:
    """Автоматическое определение часового пояса пользователя"""
    
    # В реальной реализации здесь можно использовать:
    # 1. Анализ времени активности пользователя
    # 2. Location API Telegram (если доступно)
    # 3. Анализ языка интерфейса
    
    # Пока возвращаем дефолтный часовой пояс
    return 'Europe/Amsterdam'

def get_user_timezone(user_id: int) -> str:
    """Получение сохраненного часового пояса пользователя"""
    
    from ltm_database import get_connection
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT timezone
            FROM user_proactivity_settings
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        return result['timezone'] if result else 'Europe/Amsterdam'
        
    finally:
        cursor.close()
        conn.close()

def setup_proactivity_for_new_users():
    """Настройка проактивности для новых пользователей по умолчанию"""
    
    if PROACTIVITY_DEFAULT_STATE == "ON":
        from ltm_database import get_connection
        
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            # Находим авторизованных пользователей без настроек проактивности
            cursor.execute("""
                SELECT u.user_id
                FROM users u
                LEFT JOIN user_proactivity_settings ups ON u.user_id = ups.user_id
                WHERE u.is_authorized = TRUE
                AND ups.user_id IS NULL
            """)
            
            new_users = cursor.fetchall()
            
            for user in new_users:
                user_id = user['user_id']
                timezone = detect_user_timezone(user_id)
                ab_group = get_or_assign_ab_group(user_id)
                
                cursor.execute("""
                    INSERT INTO user_proactivity_settings 
                    (user_id, is_enabled, enabled_at, ab_test_group, timezone)
                    VALUES (%s, TRUE, NOW(), %s, %s)
                """, (user_id, ab_group, timezone))
            
            conn.commit()
            
            if new_users:
                logger.info(f"Enabled proactivity by default for {len(new_users)} new users")
                
        finally:
            cursor.close()
            conn.close()