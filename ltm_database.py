import psycopg2
import psycopg2.extras
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
from dotenv import load_dotenv
from config import HISTORY_LIMIT, HISTORY_STORAGE_LIMIT, LTM_CLEANUP_DAYS
import logging
import re  # для анализа текста

# Загружаем конфигурацию для долговременной памяти
load_dotenv('.env.ltm')

logger = logging.getLogger("ltm_database")

# Глобальные переменные для системы инъекций (будут установлены из telegram_bot.py)
INJECTION_SYSTEM_ENABLED = False
injection_system = None

def set_injection_system(enabled, system):
    """Установка системы инъекций из главного модуля"""
    global INJECTION_SYSTEM_ENABLED, injection_system
    INJECTION_SYSTEM_ENABLED = enabled
    injection_system = system



# ========================================
# КОНФИГУРАЦИЯ ПОДКЛЮЧЕНИЯ
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
            cursor_factory=psycopg2.extras.RealDictCursor  # Возвращает результаты как словари
        )
        return conn
    except psycopg2.Error as e:
        logger.error(f"Ошибка подключения к PostgreSQL: {e}")
        raise

def test_connection():
    """Тестирование подключения к базе данных"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT version();')
        version = cursor.fetchone()
        cursor.close()
        conn.close()
        logger.info(f"Подключение к PostgreSQL успешно: {version['version'][:50]}...")
        return True
    except Exception as e:
        logger.error(f"Ошибка тестирования подключения: {e}")
        return False



# ========================================
# УТИЛИТЫ ВРЕМЕНИ
# ========================================

def utc_now():
    """Получение текущего времени в UTC с timezone информацией"""
    return datetime.now(timezone.utc)



# ========================================
# РАБОТА С ПОЛЬЗОВАТЕЛЯМИ
# ========================================

def ensure_user_exists(user_id: int, username: str = None, first_name: str = None):
    """Создает пользователя в PostgreSQL, если его нет"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT user_id FROM users WHERE user_id = %s', (user_id,))
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, is_authorized, created_at)
                VALUES (%s, %s, %s, FALSE, %s)
            ''', (user_id, username, first_name, utc_now()))
            conn.commit()
            logger.info(f"Создан новый пользователь: {user_id}")
        
        cursor.close()
        conn.close()
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка создания пользователя {user_id}: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        raise

def add_message_to_history(user_id: int, role: str, content: str, 
                          emotion_primary: str = None, emotion_confidence: float = None,
                          bot_mode: str = 'auto'):
    """Добавление сообщения в историю PostgreSQL"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO history (user_id, role, content, emotion_primary, emotion_confidence, bot_mode, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (user_id, role, content, emotion_primary, emotion_confidence, bot_mode, utc_now()))
        
        message_id = cursor.fetchone()['id']
        conn.commit()
        cursor.close()
        conn.close()
        
        return message_id
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка добавления сообщения в историю: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        raise

def get_recent_history(user_id: int, limit: int = None) -> List[Dict[str, Any]]:
    if limit is None:
        limit = HISTORY_LIMIT
    """Получение недавней истории сообщений пользователя"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT role, content, emotion_primary, emotion_confidence, bot_mode
            FROM history
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        ''', (user_id, limit))
        
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        # Возвращаем в том же формате, что и старая функция get_history
        messages = []
        for row in reversed(rows):  # Обращаем порядок для хронологической последовательности
            msg = {"role": row['role'], "content": row['content']}
            if row['emotion_primary']:
                msg["emotion_primary"] = row['emotion_primary']
            if row['emotion_confidence']:
                msg["emotion_confidence"] = row['emotion_confidence']
            messages.append(msg)
        
        return messages
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка получения истории пользователя {user_id}: {e}")
        cursor.close()
        conn.close()
        return []



# ========================================
# ОСНОВНЫЕ ФУНКЦИИ ДОЛГОВРЕМЕННОЙ ПАМЯТИ
# ========================================

def save_to_long_term_memory(user_id: int, user_message: str, bot_response: str,
                            importance_score: int = 5, memory_type: str = 'user_saved',
                            dialogue_context: str = None, style_markers: Dict = None,
                            contextual_tags: List[str] = None) -> int:
    """Сохранение диалога в долговременную память"""
    
    if importance_score < 1 or importance_score > 10:
        raise ValueError("importance_score должен быть от 1 до 10")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Подготавливаем данные
        style_markers_json = json.dumps(style_markers) if style_markers else None
        tags_array = contextual_tags if contextual_tags else []
        
        cursor.execute('''
            INSERT INTO long_term_memory 
            (user_id, user_message, bot_response, importance_score, memory_type,
             dialogue_context, style_markers, contextual_tags, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (
            user_id, user_message, bot_response, importance_score, memory_type,
            dialogue_context, style_markers_json, tags_array, utc_now()
        ))
        
        memory_id = cursor.fetchone()['id']
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"Сохранено воспоминание {memory_id} для пользователя {user_id} с важностью {importance_score}")
        return memory_id
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка сохранения в долговременную память: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        raise

def search_relevant_memories(user_id: int, query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Поиск релевантных воспоминаний для использования в контексте"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT * FROM search_memories(%s, %s, %s)
        ''', (user_id, query, limit))
        
        memories = cursor.fetchall()
        cursor.close()
        conn.close()
        
        # Обновляем счетчик доступа для найденных воспоминаний
        if memories:
            memory_ids = [mem['memory_id'] for mem in memories]
            update_memory_access_count(memory_ids)
        
        return [dict(mem) for mem in memories]
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка поиска воспоминаний: {e}")
        cursor.close()
        conn.close()
        return []

def update_memory_access_count(memory_ids: List[int]):
    """Обновление счетчика обращений к воспоминаниям"""
    if not memory_ids:
        return
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            UPDATE long_term_memory 
            SET access_count = access_count + 1, last_accessed = %s
            WHERE id = ANY(%s)
        ''', (utc_now(), memory_ids))
        
        conn.commit()
        cursor.close()
        conn.close()
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка обновления счетчика обращений: {e}")
        conn.rollback()
        cursor.close()
        conn.close()

def get_user_ltm_stats(user_id: int) -> Dict[str, Any]:
    """Получение статистики долговременной памяти пользователя"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM ltm_stats WHERE user_id = %s', (user_id,))
        stats = cursor.fetchone()
        cursor.close()
        conn.close()
        
        return dict(stats) if stats else {
            'user_id': user_id,
            'total_memories': 0,
            'avg_importance': 0,
            'anchor_chunks': 0,  # В Лайт версии всегда 0
            'user_favorites': 0,
            'last_memory_date': None
        }
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка получения статистики LTM: {e}")
        cursor.close()
        conn.close()
        return {}

def get_recent_memories(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Получение последних воспоминаний пользователя"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT id, user_message, bot_response, importance_score, memory_type, created_at
            FROM long_term_memory
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        ''', (user_id, limit))
        
        memories = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return [dict(mem) for mem in memories]
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка получения недавних воспоминаний: {e}")
        cursor.close()
        conn.close()
        return []



# ========================================
# ФУНКЦИИ АВТОСОХРАНЕНИЯ
# ========================================

def check_auto_save_limit(user_id: int) -> Dict[str, Any]:
    """Проверка лимита автосохранений в день"""
    from config import MAX_AUTO_SAVES_PER_DAY
    
    today = utc_now().date()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Создаем таблицу auto_save_limits если не существует
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auto_save_limits (
                user_id BIGINT,
                date DATE,
                count INTEGER DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                PRIMARY KEY (user_id, date),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        
        cursor.execute('''
            SELECT count FROM auto_save_limits 
            WHERE user_id = %s AND date = %s
        ''', (user_id, today))
        
        row = cursor.fetchone()
        current_count = row['count'] if row else 0
        
        result = {
            'count': current_count,
            'limit': MAX_AUTO_SAVES_PER_DAY,
            'remaining': max(0, MAX_AUTO_SAVES_PER_DAY - current_count),
            'exceeded': current_count >= MAX_AUTO_SAVES_PER_DAY
        }
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return result
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка проверки лимита автосохранений: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        return {'count': 0, 'limit': MAX_AUTO_SAVES_PER_DAY, 'remaining': MAX_AUTO_SAVES_PER_DAY, 'exceeded': False}

def increment_auto_save_count(user_id: int) -> int:
    """Увеличивает счетчик автосохранений на 1, возвращает новое значение"""
    today = utc_now().date()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Используем UPSERT для PostgreSQL
        cursor.execute('''
            INSERT INTO auto_save_limits (user_id, date, count, created_at, updated_at)
            VALUES (%s, %s, 1, %s, %s)
            ON CONFLICT (user_id, date) 
            DO UPDATE SET count = auto_save_limits.count + 1, updated_at = %s
            RETURNING count
        ''', (user_id, today, utc_now(), utc_now(), utc_now()))
        
        new_count = cursor.fetchone()['count']
        conn.commit()
        cursor.close()
        conn.close()
        
        return new_count
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка увеличения счетчика автосохранений: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        return 0

def auto_save_conversation(user_id: int, user_message: str, bot_response: str, 
                          emotion_primary: str = None) -> Optional[int]:
    """
    Автоматическое сохранение диалога в LTM с проверкой лимитов
    Возвращает memory_id если сохранено, None если лимит превышен
    """
    from config import AUTO_SAVE_IMPORTANCE
    
    # Дополнительная защита: Проверяем авторизацию
    from auth_database import check_user_auth_status

    auth_status = check_user_auth_status(user_id)
    if not auth_status.get('authorized'):
        logger.debug(f"Автосохранение пропущено - пользователь {user_id} не авторизирован")
        return None
    
    # Проверяем лимит автосохранений
    limit_check = check_auto_save_limit(user_id)
    if limit_check['exceeded']:
        logger.debug(f"Автосохранение для пользователя {user_id} пропущено - превышен лимит")
        return None
    
    try:
        # Автоматический анализ стилистических маркеров и тегов
        style_markers = extract_style_markers(user_message + " " + bot_response)
        contextual_tags = extract_contextual_tags(user_message, bot_response)
        
        # Добавляем информацию об эмоции
        if emotion_primary:
            contextual_tags.append(f"emotion_{emotion_primary}")
        
        # Сохраняем в долговременную память
        memory_id = save_to_long_term_memory(
            user_id=user_id,
            user_message=user_message,
            bot_response=bot_response,
            importance_score=AUTO_SAVE_IMPORTANCE,
            memory_type='auto_saved',
            style_markers=style_markers,
            contextual_tags=contextual_tags
        )
        
        # Увеличиваем счетчик автосохранений
        increment_auto_save_count(user_id)
        
        logger.info(f"Автосохранение {memory_id} для пользователя {user_id}, эмоция: {emotion_primary}")
        return memory_id
        
    except Exception as e:
        logger.error(f"Ошибка автосохранения для пользователя {user_id}: {e}")
        return None



# ========================================
# АНАЛИЗ И АВТОМАТИЧЕСКОЕ ИЗВЛЕЧЕНИЕ ТЕГОВ
# ========================================

def extract_style_markers(text: str) -> Dict[str, Any]:
    """Автоматическое извлечение стилистических маркеров из текста"""
    markers = {
        "магреализм": False,
        "балканизмы": [],
        "символические_объекты": [],
        "эмоциональные_элементы": []
    }
    
    # Простые правила для определения стилистических элементов
    text_lower = text.lower()
    
    # Маркеры магического реализма
    magic_realism_indicators = [
        "вороны", "птицы разносят", "слухи", "невидимая", "мистический", 
        "странный", "необъяснимый", "как будто", "словно", "тень", "призрак"
    ]
    
    if any(indicator in text_lower for indicator in magic_realism_indicators):
        markers["магреализм"] = True
    
    # Балканизмы (топонимы, имена, реалии)
    balkan_elements = [
        "зоровица", "рахиль", "исаак", "ашкеназка", "еврейский квартал", 
        "православный", "мечеть", "базар", "каштаны", "церковь"
    ]
    
    for element in balkan_elements:
        if element in text_lower:
            markers["балканизмы"].append(element)
    
    # Символические объекты
    symbolic_objects = [
        "брошь", "гранат", "масляная лампа", "свитки", "пергаменты", 
        "архивы", "библиотека", "книги", "кольцо", "амулет"
    ]
    
    for obj in symbolic_objects:
        if obj in text_lower:
            markers["символические_объекты"].append(obj)
    
    return markers

def extract_contextual_tags(user_message: str, bot_response: str) -> List[str]:
    """Автоматическое извлечение контекстных тегов для поиска"""
    tags = []
    
    # Объединяем тексты для анализа
    combined_text = (user_message + " " + bot_response).lower()
    
    # Основные категории тегов
    tag_categories = {
        "места": ["церковь", "библиотека", "квартал", "площадь", "село", "город"],
        "персонажи": ["рахиль", "исаак", "ашкеназка", "хранитель", "старец"],
        "объекты": ["брошь", "лампа", "свитки", "книги", "архивы"],
        "эмоции": ["тревога", "печаль", "радость", "страх", "удивление"],
        "стиль": ["магреализм", "балканы", "мистика", "символизм"]
    }
    
    for category, keywords in tag_categories.items():
        found_keywords = [kw for kw in keywords if kw in combined_text]
        tags.extend(found_keywords)
    
    return list(set(tags))  # Удаляем дубликаты



# ========================================
# ИНТЕГРАЦИЯ С СУЩЕСТВУЮЩЕЙ СИСТЕМОЙ
# ========================================

def build_enhanced_context_with_ltm(user_id: int, user_message: str, history_limit: int = 25) -> List[Dict[str, str]]:
    """Построение контекста с долговременной памятью (БЕЗ анкор-чанков)"""
    
    # Получаем обычную историю
    history = get_recent_history(user_id, limit=history_limit)
    
    # Получаем эмоциональный контекст
    emotions = [
        msg.get('emotion_primary') for msg in history
        if msg['role'] == 'user' and msg.get('emotion_primary')
    ]
    
    emotion_context = ', '.join(emotions[-3:]) if emotions else 'neutral'
    
    # Ищем персональные воспоминания
    personal_memories = search_relevant_memories(user_id, user_message, limit=3)
    
    # Строим системные сообщения
    from config import SYSTEM_PROMPT, INJECTION_PROMPT
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"ЭМОЦИОНАЛЬНЫЙ КОНТЕКСТ: последние эмоции пользователя — {emotion_context}."}
    ]
    
    # Добавляем память в контекст (только персональные воспоминания)
    if personal_memories:
        memory_context = "ДОЛГОВРЕМЕННАЯ ПАМЯТЬ:\n"
        
        # ПЕРСОНАЛЬНЫЕ ВОСПОМИНАНИЯ
        memory_context += f"\n👤 ПЕРСОНАЛЬНЫЕ ВОСПОМИНАНИЯ (этого пользователя):\n"
        for i, memory in enumerate(personal_memories, 1):
            memory_context += f"\nПример {i} (важность: {memory['importance_score']}):\n"
            memory_context += f"Запрос: {memory['user_message'][:200]}...\n"
            memory_context += f"Ответ: {memory['bot_response'][:300]}...\n"
        
        memory_context += "\nИспользуй эти примеры для понимания предпочтений пользователя и стиля общения."
        
        messages.append({"role": "system", "content": memory_context})
    
    # Добавляем инъекции
    messages.append({"role": "system", "content": INJECTION_PROMPT})
    
    # Добавляем историю с периодическими инъекциями
    step = 6
    
    for i, msg in enumerate(history, 1):
        if i % step == 0:
            messages.append({"role": "system", "content": INJECTION_PROMPT})
        messages.append(msg)
    
    return messages

def save_conversation_to_ltm(user_id: int, user_message: str, bot_response: str, 
                           importance_score: int = 5, auto_analyze: bool = True) -> int:
    """
    Удобная функция для сохранения диалога с автоматическим анализом
    Используется командой /remember
    """
    # Дополнительная защита: Проверяем авторизацию на уровне БД-функции
    from auth_database import check_user_auth_status

    auth_status = check_user_auth_status(user_id)
    if not auth_status.get('authorized'):
        logger.warning(f"SECURITY: Попытка сохранения в LTM неавторизированным пользователем {user_id}")
        raise PermissionError(f"Пользователь {user_id} не авторизирован для сохранения в долговременную память")
    
    # Автоматический анализ, если включен
    style_markers = None
    contextual_tags = None
    
    if auto_analyze:
        style_markers = extract_style_markers(user_message + " " + bot_response)
        contextual_tags = extract_contextual_tags(user_message, bot_response)
    
    # Сохраняем в долговременную память
    memory_id = save_to_long_term_memory(
        user_id=user_id,
        user_message=user_message,
        bot_response=bot_response,
        importance_score=importance_score,
        memory_type='user_saved',
        style_markers=style_markers,
        contextual_tags=contextual_tags
    )
    
    return memory_id



# ========================================
# СЛУЖЕБНЫЕ ФУНКЦИИ
# ========================================

def cleanup_old_memories(days_old: int = 365) -> int:
    """Очистка старых неиспользуемых воспоминаний"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT cleanup_old_ltm(%s)', (days_old,))
        deleted_count = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"Удалено {deleted_count} старых воспоминаний")
        return deleted_count
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка очистки старых воспоминаний: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        return 0

def get_system_config(key: str, default: Any = None) -> Any:
    """Получение значения из системной конфигурации"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT value FROM system_config WHERE key = %s', (key,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            # Попытка преобразования в правильный тип
            value = result['value']
            try:
                # Если это число
                return int(value)
            except ValueError:
                try:
                    return float(value)
                except ValueError:
                    # Если это строка
                    return value
        
        return default
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка получения конфигурации {key}: {e}")
        cursor.close()
        conn.close()
        return default

def cleanup_old_auto_saves(days_keep: int = 30) -> int:
    """Очистка старых записей автосохранений"""
    cutoff_date = (utc_now() - timedelta(days=days_keep)).date()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM auto_save_limits WHERE date < %s', (cutoff_date,))
        deleted_count = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"Удалено {deleted_count} старых записей автосохранений")
        return deleted_count
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка очистки автосохранений: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        return 0



# ========================================
# ИНИЦИАЛИЗАЦИЯ И ТЕСТИРОВАНИЕ
# ========================================

def init_ltm():
    """Инициализация модуля долговременной памяти"""
    try:
        if test_connection():
            logger.info("Модуль долговременной памяти инициализирован успешно")
            return True
        else:
            logger.error("Не удалось инициализировать модуль долговременной памяти")
            return False
    except Exception as e:
        logger.error(f"Ошибка инициализации LTM: {e}")
        return False

if __name__ == "__main__":
    # Тестирование модуля
    print("🧪 Тестирование модуля долговременной памяти (Лайт версия)...")
    
    if init_ltm():
        print("✅ Инициализация успешна")
        
        # Тест создания пользователя
        test_user_id = 987654321
        ensure_user_exists(test_user_id, "test_ltm_user", "LTM Test")
        print(f"✅ Пользователь {test_user_id} создан")
        
        # Тест проверки лимита автосохранений
        limit_check = check_auto_save_limit(test_user_id)
        print(f"✅ Лимит автосохранений: {limit_check}")
        
        print("🎉 Все тесты прошли успешно!")
        
    else:
        print("❌ Инициализация провалена")
        
       
        
# ========================================
# УМНЫЕ ИНЪЕКЦИИ
# ========================================

def analyze_response_for_injection(response: str) -> bool:
    """Простая проверка - нужна ли срочная инъекция"""
    
    # Проверяем нарушения формата
    has_lists = bool(re.search(r'^\s*[-*•]\s+', response, re.MULTILINE))
    has_numbered = bool(re.search(r'^\s*\d+[\.\)]\s+', response, re.MULTILINE))
    
    # Проверяем формальность
    formal_words = ['рекомендую', 'предлагаю', 'следует', 'необходимо']
    formal_count = sum(1 for word in formal_words if word in response.lower())
    
    return has_lists or has_numbered or formal_count > 2

def build_enhanced_context_with_ltm_smart(user_id: int, user_message: str, mode: str = 'auto', history_limit: int = 20) -> List[Dict[str, str]]:
    """Построение контекста с умными инъекциями"""
    from auth_database import check_user_auth_status
    
    # Получаем обычную историю
    history = get_recent_history(user_id, limit=history_limit)
    
    # Получаем эмоциональный контекст
    emotions = [
        msg.get('emotion_primary') for msg in history
        if msg['role'] == 'user' and msg.get('emotion_primary')
    ]
    
    emotion_context = ', '.join(emotions[-3:]) if emotions else 'neutral'
    
    # Ищем персональные воспоминания
    personal_memories = search_relevant_memories(user_id, user_message, limit=3)
    
    # Строим системные сообщения
    from config import SYSTEM_PROMPT, INJECTION_PROMPT
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"ЭМОЦИОНАЛЬНЫЙ КОНТЕКСТ: последние эмоции пользователя — {emotion_context}."}
    ]
    
    # Добавляем память в контекст
    if personal_memories:
        memory_context = "ДОЛГОВРЕМЕННАЯ ПАМЯТЬ:\n"
        memory_context += f"\n👤 ПЕРСОНАЛЬНЫЕ ВОСПОМИНАНИЯ (этого пользователя):\n"
        for i, memory in enumerate(personal_memories, 1):
            memory_context += f"\nПример {i} (важность: {memory['importance_score']}):\n"
            memory_context += f"Запрос: {memory['user_message'][:200]}...\n"
            memory_context += f"Ответ: {memory['bot_response'][:300]}...\n"
        
        memory_context += "\nИспользуй эти примеры для понимания предпочтений пользователя и стиля общения."
        messages.append({"role": "system", "content": memory_context})
    
    # Добавляем инъекции
    messages.append({"role": "system", "content": INJECTION_PROMPT})
    
    # УМНАЯ ЛОГИКА ИНЪЕКЦИЙ
    # Добавляем глобальные переменные (если их нет в области видимости)
    global INJECTION_SYSTEM_ENABLED, injection_system
    
    if 'INJECTION_SYSTEM_ENABLED' in globals() and INJECTION_SYSTEM_ENABLED and injection_system:
        # Используем новую систему адаптивных инъекций
        from injection_system import calculate_dialogue_entropy
        entropy = calculate_dialogue_entropy(history)
        logger.info(f"Dialogue entropy for user {user_id}: {entropy:.2f}")
        
        # Получаем статус авторизации
        auth_status = check_user_auth_status(user_id)
        
        # Определяем тип нарушения для критических инъекций
        last_assistant_msg = None
        for msg in reversed(history):
            if msg['role'] == 'assistant':
                last_assistant_msg = msg['content']
                break
        
        violation_type = None
        if last_assistant_msg and analyze_response_for_injection(last_assistant_msg):
            if re.search(r'^\s*[-*•]\s+', last_assistant_msg, re.MULTILINE):
                violation_type = 'format_violation'
            else:
                violation_type = 'character_drift'
        
        # Проверяем необходимость инъекции
        if violation_type or injection_system.should_inject(user_id, entropy):
            try:
                # Определяем эмоцию для инъекции
                injection_emotion = emotions[-1] if emotions else 'neutral'
                
                # Получаем LTM воспоминания для авторизованных
                ltm_memories = None
                is_authorized = auth_status.get('authorized', False)
                if is_authorized:
                    ltm_memories = personal_memories  # Используем уже полученные воспоминания
                
                # Генерируем инъекцию
                injection_text, latency = injection_system.generate_injection(
                    user_id=user_id,
                    mode=mode,
                    emotion=injection_emotion,
                    user_history=history,
                    violation_type=violation_type,
                    is_authorized=is_authorized,
                    ltm_memories=ltm_memories
                )
                
                messages.append({"role": "system", "content": injection_text})
                logger.info(f"🎯 Адаптивная инъекция применена: latency={latency}ms, auth={is_authorized}")
                
            except Exception as e:
                logger.error(f"Ошибка генерации адаптивной инъекции: {e}")
                # Fallback на базовую инъекцию
                messages.append({"role": "system", "content": INJECTION_PROMPT})
        
        # Добавляем историю
        messages.extend(history)
                
    else:
        # Fallback на старую систему
        injection_intervals = {
            'talk': 7,
            'writer': 10, 
            'expert': 8,
            'auto': 7
        }
        
        step = injection_intervals.get(mode, 6)
        assistant_message_count = 0
        
        for i, msg in enumerate(history, 1):
            if msg['role'] == 'assistant':
                assistant_message_count += 1
                
                if analyze_response_for_injection(msg['content']):
                    messages.append({"role": "system", "content": INJECTION_PROMPT})
                    logger.info(f"🚨 Критическая инъекция: нарушение формата")
                
                elif assistant_message_count % step == 0:
                    if mode == 'writer' and len(msg['content']) > 800:
                        logger.info(f"🛡️ Пропуск инъекции: защита творческого потока")
                    else:
                        messages.append({"role": "system", "content": INJECTION_PROMPT})
                        logger.info(f"⏰ Плановая инъекция: {assistant_message_count} сообщений в режиме {mode}")
            
            messages.append(msg)
    
    return messages
  
  

# ========================================
# СИСТЕМА ОЧИСТКИ ПАМЯТИ
# ========================================

def cleanup_old_ltm_by_age():
    """Очистка LTM записей старше LTM_CLEANUP_DAYS дней"""
    cutoff_date = utc_now() - timedelta(days=LTM_CLEANUP_DAYS)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            DELETE FROM long_term_memory 
            WHERE created_at < %s
        ''', (cutoff_date,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        
        logger.info(f"🗑️ LTM: Удалено {deleted_count} записей старше {LTM_CLEANUP_DAYS} дней")
        return deleted_count
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка очистки LTM: {e}")
        conn.rollback()
        return 0
    finally:
        cursor.close()
        conn.close()

def cleanup_history_ring_buffer():
    """Очистка History - кольцевой буфер, оставляем только последние HISTORY_STORAGE_LIMIT сообщений на пользователя"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Находим всех пользователей с превышением лимита
        cursor.execute('''
            SELECT user_id, COUNT(*) as total_messages
            FROM history 
            GROUP BY user_id 
            HAVING COUNT(*) > %s
        ''', (HISTORY_STORAGE_LIMIT,))
        
        users_to_cleanup = cursor.fetchall()
        total_deleted = 0
        
        for user_row in users_to_cleanup:
            user_id = user_row['user_id']
            total_messages = user_row['total_messages']
            
            # Удаляем старые сообщения, оставляем только последние HISTORY_STORAGE_LIMIT
            cursor.execute('''
                DELETE FROM history 
                WHERE user_id = %s 
                AND id NOT IN (
                    SELECT id FROM history 
                    WHERE user_id = %s 
                    ORDER BY created_at DESC 
                    LIMIT %s
                )
            ''', (user_id, user_id, HISTORY_STORAGE_LIMIT))
            
            deleted_for_user = cursor.rowcount
            total_deleted += deleted_for_user
            
            if deleted_for_user > 0:
                logger.info(f"👤 User {user_id}: удалено {deleted_for_user} старых сообщений (было {total_messages}, осталось {HISTORY_STORAGE_LIMIT})")
        
        conn.commit()
        
        logger.info(f"🗑️ HISTORY: Всего удалено {total_deleted} старых сообщений")
        return total_deleted
        
    except psycopg2.Error as e:
        logger.error(f"Ошибка очистки History: {e}")
        conn.rollback()
        return 0
    finally:
        cursor.close()
        conn.close()

def cleanup_memory_systems():
    """Комплексная очистка всех систем памяти"""
    logger.info("🧹 Запуск комплексной очистки памяти...")
    
    # 1. Очищаем долговременную память (по времени)
    ltm_deleted = cleanup_old_ltm_by_age()
    
    # 2. Очищаем кратковременную память (кольцевой буфер)
    history_deleted = cleanup_history_ring_buffer()
    
    # 3. Очищаем лимиты сообщений
    limits_deleted = cleanup_old_limits()
    
    # 4. Очищаем автосохранения
    auto_saves_deleted = cleanup_old_auto_saves()
    
    logger.info(f"✅ Очистка завершена: LTM={ltm_deleted}, History={history_deleted}, Limits={limits_deleted}, AutoSaves={auto_saves_deleted}")
    
    return {
        'ltm_deleted': ltm_deleted,
        'history_deleted': history_deleted, 
        'limits_deleted': limits_deleted,
        'auto_saves_deleted': auto_saves_deleted
    }

def add_message_to_history_with_cleanup(user_id: int, role: str, content: str, 
                          emotion_primary: str = None, emotion_confidence: float = None,
                          bot_mode: str = 'auto'):
    """Добавление сообщения в историю с периодической очисткой"""
    
    # Добавляем сообщение
    message_id = add_message_to_history(user_id, role, content, emotion_primary, emotion_confidence, bot_mode)
    
    # Периодически запускаем очистку (примерно раз в 100 сообщений)
    import random
    if random.randint(1, 100) == 1:
        try:
            cleanup_memory_systems()
        except Exception as e:
            logger.error(f"Ошибка фоновой очистки: {e}")
    
    return message_id    
    
    
    