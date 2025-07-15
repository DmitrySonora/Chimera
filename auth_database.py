import psycopg2
import psycopg2.extras
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
from dotenv import load_dotenv
import logging

# Загружаем конфигурацию
load_dotenv('.env.ltm')

logger = logging.getLogger("auth_database")

# ========================================
# УТИЛИТЫ ДЛЯ РАБОТЫ С TIMEZONE
# ========================================

def utc_now():
    """Получение текущего времени в UTC с timezone информацией"""
    return datetime.now(timezone.utc)

def make_aware(dt):
    """Преобразование naive datetime в aware (UTC)"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def make_naive(dt):
    """Преобразование aware datetime в naive (UTC)"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

def safe_datetime_compare(dt1, dt2):
    """Безопасное сравнение datetime объектов"""
    if dt1 is None or dt2 is None:
        return dt1, dt2
    
    # Приводим оба к aware в UTC
    dt1_aware = make_aware(dt1) if dt1.tzinfo is None else dt1.astimezone(timezone.utc)
    dt2_aware = make_aware(dt2) if dt2.tzinfo is None else dt2.astimezone(timezone.utc)
    
    return dt1_aware, dt2_aware

# ========================================
# ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ
# ========================================

def get_connection():
    """Получение подключения к PostgreSQL"""
    try:
        conn = psycopg2.connect(
            host=os.getenv('POSTGRES_HOST', 'localhost'),
            port=os.getenv('POSTGRES_PORT', '5432'),
            database=os.getenv('POSTGRES_DB', 'himeralite_ltm'),
            user=os.getenv('POSTGRES_USER', 'himeralite_user'),
            password=os.getenv('POSTGRES_PASSWORD'),
            cursor_factory=psycopg2.extras.RealDictCursor
        )
        return conn
    except psycopg2.Error as e:
        logger.error(f"Ошибка подключения к PostgreSQL: {e}")
        raise

def mask_password(password: str) -> str:
    """Маскирует пароль для логов: test123 -> te***23"""
    if not password:
        return ""
    if len(password) <= 4:
        return "*" * len(password)
    return password[:2] + "*" * (len(password) - 4) + password[-2:]

def log_auth_event(user_id: int, action: str, password: Optional[str] = None, details: Optional[str] = None):
    """Логирование событий авторизации"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        password_masked = mask_password(password) if password else None
        
        cursor.execute('''
            INSERT INTO auth_log (user_id, action, password_masked, details, created_at)
            VALUES (%s, %s, %s, %s, %s)
        ''', (user_id, action, password_masked, details, utc_now()))
        
        conn.commit()
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка логирования авторизации: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

# ========================================
# УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ
# ========================================

def ensure_user_exists_auth(user_id: int):
    """Создает пользователя в БД, если его нет (для авторизации)"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT user_id FROM users WHERE user_id = %s', (user_id,))
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO users (user_id, is_authorized, created_at)
                VALUES (%s, FALSE, %s)
            ''', (user_id, utc_now()))
            conn.commit()
            logger.info(f"Создан новый пользователь для авторизации: {user_id}")
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка создания пользователя {user_id}: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

def check_user_auth_status(user_id: int) -> Dict[str, Any]:
    """Проверка статуса авторизации пользователя"""
    ensure_user_exists_auth(user_id)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT is_authorized, authorized_until, blocked_until, failed_attempts, warned_expiry
            FROM users WHERE user_id = %s
        ''', (user_id,))
        
        row = cursor.fetchone()
        
        if not row:
            return {'authorized': False, 'blocked': False, 'expired': False}
        
        is_authorized = row['is_authorized']
        authorized_until = row['authorized_until']
        blocked_until = row['blocked_until']
        failed_attempts = row['failed_attempts']
        warned_expiry = row['warned_expiry']
        
        now = utc_now()
        
        # Проверка блокировки с безопасным сравнением
        if blocked_until:
            blocked_until_aware, now_aware = safe_datetime_compare(blocked_until, now)
            if blocked_until_aware > now_aware:
                return {
                    'authorized': False,
                    'blocked': True,
                    'blocked_until': blocked_until.isoformat(),
                    'failed_attempts': failed_attempts
                }
        
        # Проверка истечения авторизации с безопасным сравнением
        if is_authorized and authorized_until:
            authorized_until_aware, now_aware = safe_datetime_compare(authorized_until, now)
            if authorized_until_aware <= now_aware:
                # Авторизация истекла - деактивируем
                cursor.execute('''
                    UPDATE users SET is_authorized = FALSE, warned_expiry = FALSE
                    WHERE user_id = %s
                ''', (user_id,))
                conn.commit()
                
                log_auth_event(user_id, 'auto_expired', details=f'Авторизация истекла: {authorized_until}')
                
                return {'authorized': False, 'blocked': False, 'expired': True}
            
            return {
                'authorized': True,
                'blocked': False,
                'authorized_until': authorized_until.isoformat(),
                'warned_expiry': warned_expiry
            }
        
        return {'authorized': False, 'blocked': False, 'expired': False}
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка проверки статуса авторизации: {e}")
        return {'authorized': False, 'blocked': False, 'expired': False}
    finally:
        cursor.close()
        conn.close()

# ========================================
# ЛИМИТЫ СООБЩЕНИЙ
# ========================================

def check_daily_limit(user_id: int) -> Dict[str, Any]:
    """Проверка суточного лимита сообщений"""
    ensure_user_exists_auth(user_id)
    
    today = utc_now().date()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT count FROM message_limits 
            WHERE user_id = %s AND date = %s
        ''', (user_id, today))
        
        row = cursor.fetchone()
        current_count = row['count'] if row else 0
        
        # Получаем лимит из системной конфигурации
        cursor.execute("SELECT value FROM system_config WHERE key = 'daily_message_limit'")
        limit_row = cursor.fetchone()
        daily_limit = int(limit_row['value']) if limit_row else 10
        
        return {
            'count': current_count,
            'limit': daily_limit,
            'remaining': max(0, daily_limit - current_count),
            'exceeded': current_count >= daily_limit
        }
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка проверки лимита: {e}")
        return {'count': 0, 'limit': 10, 'remaining': 10, 'exceeded': False}
    finally:
        cursor.close()
        conn.close()

def increment_message_count(user_id: int) -> int:
    """Увеличивает счетчик сообщений на 1, возвращает новое значение"""
    today = utc_now().date()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Используем UPSERT для PostgreSQL
        cursor.execute('''
            INSERT INTO message_limits (user_id, date, count, created_at)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (user_id, date) 
            DO UPDATE SET count = message_limits.count + 1, updated_at = %s
            RETURNING count
        ''', (user_id, today, utc_now(), utc_now()))
        
        new_count = cursor.fetchone()['count']
        conn.commit()
        
        return new_count
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка увеличения счетчика сообщений: {e}")
        conn.rollback()
        return 0
    finally:
        cursor.close()
        conn.close()

# ========================================
# ЗАЩИТА ОТ BRUTEFORCE
# ========================================

def check_bruteforce_protection(user_id: int) -> Dict[str, Any]:
    """Проверка защиты от bruteforce атак"""
    auth_status = check_user_auth_status(user_id)
    
    if auth_status.get('blocked'):
        # Безопасное извлечение blocked_until
        blocked_until_str = auth_status['blocked_until']
        try:
            # Парсим datetime из ISO строки
            blocked_until = datetime.fromisoformat(blocked_until_str.replace('Z', '+00:00'))
            now = utc_now()
            
            # Безопасное сравнение
            blocked_until_aware, now_aware = safe_datetime_compare(blocked_until, now)
            remaining = blocked_until_aware - now_aware
            
            return {
                'blocked': True,
                'remaining_seconds': max(0, int(remaining.total_seconds())),
                'failed_attempts': auth_status['failed_attempts']
            }
        except (ValueError, TypeError) as e:
            logger.error(f"Ошибка парсинга blocked_until: {e}")
            return {'blocked': False}
    
    return {'blocked': False}

def process_password_attempt(user_id: int, password: str) -> Dict[str, Any]:
    """Обработка попытки ввода пароля"""
    
    # Получаем настройки из системной конфигурации
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT key, value FROM system_config WHERE key IN ('max_password_attempts', 'bruteforce_timeout_seconds')")
        config_rows = cursor.fetchall()
        config = {row['key']: int(row['value']) for row in config_rows}
        
        max_attempts = config.get('max_password_attempts', 5)
        bruteforce_timeout = config.get('bruteforce_timeout_seconds', 900)
        
        # Убеждаемся что пользователь существует
        ensure_user_exists_auth(user_id)
        
        # Проверяем существование, активность и срок действия пароля
        cursor.execute('''
            SELECT duration_days, expires_at 
            FROM passwords 
            WHERE password_text = %s AND is_active = TRUE
        ''', (password,))
        password_row = cursor.fetchone()
        
        if password_row:
            # Проверяем, не истек ли пароль
            expires_at = password_row['expires_at']
            if expires_at and expires_at <= utc_now():
                # Пароль истек - автоматически деактивируем
                cursor.execute('UPDATE passwords SET is_active = FALSE WHERE password_text = %s', (password,))
                conn.commit()
                
                log_auth_event(user_id, 'password_expired', password, f'Пароль истек: {expires_at}')
                
                return {
                    'success': False,
                    'blocked': False,
                    'expired': True,
                    'remaining_attempts': 0
                }
            
            # Пароль правильный и не истек
            duration_days = password_row['duration_days']
            authorized_until = utc_now() + timedelta(days=duration_days)
            
            # Обновляем пользователя
            cursor.execute('''
                UPDATE users SET 
                    is_authorized = TRUE,
                    authorized_until = %s,
                    password_used = %s,
                    last_auth = %s,
                    failed_attempts = 0,
                    blocked_until = NULL,
                    warned_expiry = FALSE
                WHERE user_id = %s
            ''', (authorized_until, password, utc_now(), user_id))
            
            # Увеличиваем счетчик использований пароля
            cursor.execute('UPDATE passwords SET times_used = times_used + 1 WHERE password_text = %s', (password,))
            
            conn.commit()
            
            log_auth_event(user_id, 'password_success', password, f'Авторизован на {duration_days} дней')
            
            return {
                'success': True,
                'duration_days': duration_days,
                'authorized_until': authorized_until.isoformat()
            }
        else:
            # Пароль неправильный или истекший
            # Получаем текущие попытки
            cursor.execute('SELECT failed_attempts FROM users WHERE user_id = %s', (user_id,))
            row = cursor.fetchone()
            current_attempts = row['failed_attempts'] if row and row['failed_attempts'] else 0
            new_attempts = current_attempts + 1
            
            if new_attempts >= max_attempts:
                # Блокируем пользователя
                blocked_until = utc_now() + timedelta(seconds=bruteforce_timeout)
                cursor.execute('''
                    UPDATE users SET 
                        failed_attempts = %s,
                        blocked_until = %s
                    WHERE user_id = %s
                ''', (new_attempts, blocked_until, user_id))
                
                conn.commit()
                
                log_auth_event(user_id, 'blocked', password, f'Заблокирован на {bruteforce_timeout} секунд')
                
                return {
                    'success': False,
                    'blocked': True,
                    'remaining_attempts': 0,
                    'blocked_seconds': bruteforce_timeout
                }
            else:
                # Увеличиваем счетчик попыток
                cursor.execute('UPDATE users SET failed_attempts = %s WHERE user_id = %s', (new_attempts, user_id))
                
                conn.commit()
                
                log_auth_event(user_id, 'password_fail', password, f'Попытка {new_attempts}/{max_attempts}')
                
                return {
                    'success': False,
                    'blocked': False,
                    'remaining_attempts': max_attempts - new_attempts
                }
                
    except psycopg2.Error as e:
        logger.error(f"Ошибка обработки пароля: {e}")
        conn.rollback()
        return {'success': False, 'blocked': False, 'remaining_attempts': 0}
    finally:
        cursor.close()
        conn.close()

# ========================================
# УПРАВЛЕНИЕ ПАРОЛЯМИ
# ========================================

def add_password(password: str, description: str, duration_days: int) -> bool:
    """Добавление временного пароля"""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        created_at = utc_now()
        expires_at = created_at + timedelta(days=duration_days)
        
        cursor.execute('''
            INSERT INTO passwords (password_text, description, duration_days, created_at, expires_at)
            VALUES (%s, %s, %s, %s, %s)
        ''', (password, description, duration_days, created_at, expires_at))
        conn.commit()
        logger.info(f"Добавлен пароль '{mask_password(password)}' на {duration_days} дней, истекает {expires_at.strftime('%d.%m.%Y')}")
        return True
        
    except psycopg2.IntegrityError:
        logger.warning(f"Пароль '{mask_password(password)}' уже существует")
        conn.rollback()
        return False
    except psycopg2.Error as e:
        logger.error(f"Ошибка добавления пароля: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()

def deactivate_password(password: str) -> bool:
    """Деактивация пароля"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('UPDATE passwords SET is_active = FALSE WHERE password_text = %s', (password,))
        success = cursor.rowcount > 0
        
        conn.commit()
        
        if success:
            log_auth_event(0, 'password_deactivated', password, 'Деактивирован администратором')
            logger.info(f"Пароль '{mask_password(password)}' деактивирован")
        
        return success
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка деактивации пароля: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()

def deactivate_expired_passwords():
    """Автоматическая деактивация истекших паролей"""
    now = utc_now()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Находим истекшие активные пароли
        cursor.execute('''
            SELECT password_text, expires_at 
            FROM passwords 
            WHERE is_active = TRUE AND expires_at IS NOT NULL AND expires_at <= %s
        ''', (now,))
        
        expired_passwords = cursor.fetchall()
        
        if expired_passwords:
            # Деактивируем их
            cursor.execute('''
                UPDATE passwords 
                SET is_active = FALSE 
                WHERE is_active = TRUE AND expires_at IS NOT NULL AND expires_at <= %s
            ''', (now,))
            
            deactivated_count = cursor.rowcount
            conn.commit()
            
            # Логируем каждый истекший пароль
            for pwd in expired_passwords:
                log_auth_event(0, 'password_auto_expired', pwd['password_text'], 
                             f'Автодеактивация: истек {pwd["expires_at"]}')
            
            logger.info(f"Автоматически деактивировано {deactivated_count} истекших паролей")
            return deactivated_count
        
        return 0
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка деактивации истекших паролей: {e}")
        conn.rollback()
        return 0
    finally:
        cursor.close()
        conn.close()

def list_passwords(show_full: bool = False) -> List[Dict[str, Any]]:
    """Список всех паролей с информацией"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT password_text, description, is_active, created_at, duration_days, times_used, expires_at
            FROM passwords ORDER BY created_at DESC
        ''')
        
        passwords = []
        for row in cursor.fetchall():
            passwords.append({
                'password': row['password_text'] if show_full else mask_password(row['password_text']),
                'description': row['description'],
                'is_active': bool(row['is_active']),
                'created_at': row['created_at'].isoformat(),
                'duration_days': row['duration_days'],
                'times_used': row['times_used'],
                'expires_at': row['expires_at'].isoformat() if row['expires_at'] else None
            })
        
        return passwords
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка получения списка паролей: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def get_password_stats() -> Dict[str, Any]:
    """Статистика по паролям"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT COUNT(*) FROM passwords WHERE is_active = TRUE')
        active_count = cursor.fetchone()['count']
        
        cursor.execute('SELECT COUNT(*) FROM passwords WHERE is_active = FALSE')
        inactive_count = cursor.fetchone()['count']
        
        cursor.execute('SELECT SUM(times_used) FROM passwords')
        total_uses = cursor.fetchone()['sum'] or 0
        
        cursor.execute('''
            SELECT duration_days, COUNT(*) as count
            FROM passwords WHERE is_active = TRUE 
            GROUP BY duration_days ORDER BY duration_days
        ''')
        
        by_duration = {row['duration_days']: row['count'] for row in cursor.fetchall()}
        
        return {
            'active_passwords': active_count,
            'inactive_passwords': inactive_count,
            'total_uses': total_uses,
            'by_duration': by_duration
        }
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка получения статистики паролей: {e}")
        return {'active_passwords': 0, 'inactive_passwords': 0, 'total_uses': 0, 'by_duration': {}}
    finally:
        cursor.close()
        conn.close()

# ========================================
# ЛОГИ И СТАТИСТИКА
# ========================================

def get_auth_log(user_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Просмотр логов авторизации"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        if user_id:
            cursor.execute('''
                SELECT user_id, action, password_masked, details, created_at
                FROM auth_log WHERE user_id = %s
                ORDER BY created_at DESC LIMIT %s
            ''', (user_id, limit))
        else:
            cursor.execute('''
                SELECT user_id, action, password_masked, details, created_at
                FROM auth_log ORDER BY created_at DESC LIMIT %s
            ''', (limit,))
        
        logs = []
        for row in cursor.fetchall():
            logs.append({
                'user_id': row['user_id'],
                'action': row['action'],
                'password_masked': row['password_masked'],
                'details': row['details'],
                'timestamp': row['created_at'].isoformat()
            })
        
        return logs
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка получения логов авторизации: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def get_blocked_users() -> List[Dict[str, Any]]:
    """Список заблокированных пользователей"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        now = utc_now()
        cursor.execute('''
            SELECT user_id, blocked_until, failed_attempts
            FROM users 
            WHERE blocked_until IS NOT NULL AND blocked_until > %s
            ORDER BY blocked_until DESC
        ''', (now,))
        
        blocked = []
        for row in cursor.fetchall():
            blocked_until = row['blocked_until']
            # Безопасное вычисление оставшегося времени
            blocked_until_aware, now_aware = safe_datetime_compare(blocked_until, now)
            remaining = blocked_until_aware - now_aware
            
            blocked.append({
                'user_id': row['user_id'],
                'blocked_until': blocked_until.isoformat(),
                'failed_attempts': row['failed_attempts'],
                'remaining_seconds': max(0, int(remaining.total_seconds()))
            })
        
        return blocked
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка получения заблокированных пользователей: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def unblock_user(user_id: int) -> bool:
    """Разблокировка пользователя вручную"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            UPDATE users SET blocked_until = NULL, failed_attempts = 0
            WHERE user_id = %s AND blocked_until IS NOT NULL
        ''', (user_id,))
        
        success = cursor.rowcount > 0
        conn.commit()
        
        if success:
            log_auth_event(user_id, 'unblocked', details='Разблокирован администратором')
            logger.info(f"Пользователь {user_id} разблокирован")
        
        return success
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка разблокировки пользователя: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()

# ========================================
# ОЧИСТКА И ОБСЛУЖИВАНИЕ
# ========================================

def cleanup_old_limits(days_keep: Optional[int] = None):
    """Очистка старых записей лимитов"""
    if days_keep is None:
        # Получаем из конфигурации
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_config WHERE key = 'ltm_auto_cleanup_days'")
        row = cursor.fetchone()
        days_keep = int(row['value']) if row else 7
        cursor.close()
        conn.close()
    
    cutoff_date = (utc_now() - timedelta(days=days_keep)).date()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM message_limits WHERE date < %s', (cutoff_date,))
        deleted_count = cursor.rowcount
        conn.commit()
        
        logger.info(f"Удалено {deleted_count} старых записей лимитов")
        return deleted_count
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка очистки лимитов: {e}")
        conn.rollback()
        return 0
    finally:
        cursor.close()
        conn.close()

def cleanup_expired_users():
    """Очистка просроченных авторизаций"""
    now = utc_now()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Находим просроченных пользователей для логирования
        cursor.execute('''
            SELECT user_id, authorized_until FROM users 
            WHERE is_authorized = TRUE AND authorized_until <= %s
        ''', (now,))
        
        expired_users = cursor.fetchall()
        
        # Деактивируем их
        cursor.execute('''
            UPDATE users SET is_authorized = FALSE, warned_expiry = FALSE
            WHERE is_authorized = TRUE AND authorized_until <= %s
        ''', (now,))
        
        conn.commit()
        
        # Логируем деактивацию
        for user in expired_users:
            log_auth_event(user['user_id'], 'auto_expired', details=f'Авторизация истекла: {user["authorized_until"]}')
        
        # Деактивируем истекшие пароли
        deactivate_expired_passwords()
        
        logger.info(f"Деактивировано {len(expired_users)} просроченных авторизаций")
        return len(expired_users)
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка очистки просроченных пользователей: {e}")
        conn.rollback()
        return 0
    finally:
        cursor.close()
        conn.close()

# ========================================
# ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ
# ========================================

def update_user_warning_flag(user_id: int) -> bool:
    """Обновление флага предупреждения об истечении"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('UPDATE users SET warned_expiry = TRUE WHERE user_id = %s', (user_id,))
        success = cursor.rowcount > 0
        conn.commit()
        return success
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка обновления флага предупреждения: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()

def logout_user(user_id: int) -> bool:
    """Выход пользователя из системы"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('UPDATE users SET is_authorized = FALSE WHERE user_id = %s', (user_id,))
        success = cursor.rowcount > 0
        conn.commit()
        
        if success:
            log_auth_event(user_id, 'manual_logout', details='Пользователь вышел сам')
            logger.info(f"Пользователь {user_id} вышел из системы")
        
        return success
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка logout: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()

def get_users_stats() -> Dict[str, int]:
    """Получение статистики пользователей"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT COUNT(*) FROM users WHERE is_authorized = TRUE')
        active_users = cursor.fetchone()['count']
        
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM users')
        total_users = cursor.fetchone()['count']
        
        cursor.execute('SELECT COUNT(*) FROM users WHERE blocked_until > %s', (utc_now(),))
        blocked_users = cursor.fetchone()['count']
        
        return {
            'active_users': active_users,
            'total_users': total_users,
            'blocked_users': blocked_users
        }
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка получения статистики пользователей: {e}")
        return {'active_users': 0, 'total_users': 0, 'blocked_users': 0}
    finally:
        cursor.close()
        conn.close()

# ========================================
# ТЕСТИРОВАНИЕ
# ========================================

def test_auth_functions():
    """Тестирование функций авторизации"""
    logger.info("🧪 Тестирование функций авторизации PostgreSQL...")
    
    test_user_id = 999999999
    
    try:
        # Тест создания пользователя
        ensure_user_exists_auth(test_user_id)
        logger.info("✅ Создание пользователя")
        
        # Тест проверки статуса
        status = check_user_auth_status(test_user_id)
        logger.info(f"✅ Проверка статуса: {status}")
        
        # Тест лимитов
        limit_check = check_daily_limit(test_user_id)
        logger.info(f"✅ Проверка лимитов: {limit_check}")
        
        # Тест увеличения счетчика
        new_count = increment_message_count(test_user_id)
        logger.info(f"✅ Увеличение счетчика: {new_count}")
        
        # Тест статистики
        stats = get_users_stats()
        logger.info(f"✅ Статистика пользователей: {stats}")
        
        # Тест деактивации истекших паролей
        deactivated = deactivate_expired_passwords()
        logger.info(f"✅ Деактивация истекших паролей: {deactivated}")
        
        logger.info("🎉 Все тесты авторизации прошли успешно!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка тестирования: {e}")
        return False

if __name__ == "__main__":
    # Запуск тестирования
    test_auth_functions()