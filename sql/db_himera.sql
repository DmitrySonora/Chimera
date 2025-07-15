-- ========================================
-- СХЕМА БАЗЫ ДАННЫХ ХИМЕРА
-- PostgreSQL
-- ========================================

-- Включаем расширения PostgreSQL
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ========================================
-- 1. ПОЛЬЗОВАТЕЛИ
-- ========================================

CREATE TABLE users (
    user_id BIGINT PRIMARY KEY,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    
    -- Авторизация
    is_authorized BOOLEAN DEFAULT FALSE,
    authorized_until TIMESTAMP WITH TIME ZONE,
    password_used VARCHAR(255),
    last_auth TIMESTAMP WITH TIME ZONE,
    
    -- Защита от bruteforce
    failed_attempts INTEGER DEFAULT 0,
    blocked_until TIMESTAMP WITH TIME ZONE,
    warned_expiry BOOLEAN DEFAULT FALSE,
    
    -- Метаданные
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Настройки пользователя
    preferred_mode VARCHAR(20) DEFAULT 'auto', -- expert/writer/talk/auto
    timezone VARCHAR(50) DEFAULT 'UTC',
    
    -- Статистика
    total_messages INTEGER DEFAULT 0,
    total_ltm_saves INTEGER DEFAULT 0
);

-- Индексы для пользователей
CREATE INDEX idx_users_authorized_until ON users(authorized_until);
CREATE INDEX idx_users_blocked_until ON users(blocked_until);
CREATE INDEX idx_users_is_authorized ON users(is_authorized);

-- ========================================
-- 2. СТИЛЕВЫЕ ПРОФИЛИ ПОЛЬЗОВАТЕЛЕЙ
-- ========================================

-- Таблица для хранения стилевых профилей пользователей
CREATE TABLE user_style_profiles (
    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    style_vector JSONB NOT NULL DEFAULT '{"playful": 0.5, "serious": 0.5, "ironic": 0.5, "magical": 0.5}',
    dominant_mode VARCHAR(20) DEFAULT 'auto',
    interaction_count INTEGER DEFAULT 0,
    last_analysis TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Индекс для быстрого поиска
CREATE INDEX idx_user_style_updated ON user_style_profiles(updated_at DESC);

-- ========================================
-- 3. СТАТИСТИКА АКТИВНОСТИ ПОЛЬЗОВАТЕЛЕЙ
-- ========================================

-- Таблица для отслеживания активности пользователей
CREATE TABLE user_activity_stats (
    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    total_messages INTEGER DEFAULT 0,
    daily_average FLOAT DEFAULT 0,
    last_active TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ========================================
-- 4. ПРОАКТИВНОСТЬ ПОЛЬЗОВАТЕЛЕЙ
-- ========================================

-- Настройки проактивности пользователей
CREATE TABLE user_proactivity_settings (
    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    is_enabled BOOLEAN DEFAULT FALSE,
    enabled_at TIMESTAMP WITH TIME ZONE,
    paused_until TIMESTAMP WITH TIME ZONE,
    pause_reason VARCHAR(50),
    ab_test_group CHAR(1),
    timezone VARCHAR(50) DEFAULT 'UTC',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_proactivity_enabled ON user_proactivity_settings(is_enabled, paused_until);
CREATE INDEX idx_proactivity_ab_group ON user_proactivity_settings(ab_test_group);

-- Планирование инициаций
CREATE TABLE initiation_schedule (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
    scheduled_at TIMESTAMP WITH TIME ZONE NOT NULL,
    initiation_type VARCHAR(30),
    source_memory_ids INTEGER[],
    context_data JSONB,
    emotion_context VARCHAR(50),
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    sent_at TIMESTAMP WITH TIME ZONE,
    user_response_received BOOLEAN DEFAULT FALSE,
    error_message TEXT
);

CREATE INDEX idx_initiation_schedule_pending ON initiation_schedule(user_id, scheduled_at, status);
CREATE INDEX idx_initiation_schedule_user ON initiation_schedule(user_id, scheduled_at);

-- Логи инициаций
CREATE TABLE initiation_logs (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
    initiation_id INTEGER REFERENCES initiation_schedule(id),
    message_content TEXT NOT NULL,
    initiation_type VARCHAR(30),
    source_memory_context TEXT,
    user_response TEXT,
    user_response_emotion VARCHAR(50),
    user_response_sentiment FLOAT,
    user_response_length INTEGER,
    user_responded BOOLEAN DEFAULT FALSE,
    response_time_minutes INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_initiation_logs_user ON initiation_logs(user_id, created_at);
CREATE INDEX idx_initiation_logs_response ON initiation_logs(user_id, user_responded);

-- ========================================
-- 5. ПАРОЛИ
-- ========================================

CREATE TABLE passwords (
    id SERIAL PRIMARY KEY,
    password_text VARCHAR(255) UNIQUE NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    duration_days INTEGER NOT NULL,
    times_used INTEGER DEFAULT 0,
    
    -- Дополнительные поля
    created_by BIGINT REFERENCES users(user_id),
    expires_at TIMESTAMP WITH TIME ZONE, -- для автоматической деактивации
    max_uses INTEGER -- ограничение использований
);

CREATE INDEX idx_passwords_active ON passwords(is_active);
CREATE INDEX idx_passwords_expires_at ON passwords(expires_at);

-- ========================================
-- 6. ЛИМИТЫ СООБЩЕНИЙ
-- ========================================

CREATE TABLE message_limits (
    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
    date DATE NOT NULL,
    count INTEGER DEFAULT 0,
    
    -- Метаданные
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    PRIMARY KEY (user_id, date)
);

CREATE INDEX idx_message_limits_date ON message_limits(date);

-- ========================================
-- 7. ИСТОРИЯ СООБЩЕНИЙ
-- ========================================

CREATE TABLE history (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
    
    -- Контент
    role VARCHAR(20) NOT NULL, -- user/assistant/system
    content TEXT NOT NULL,
    
    -- Эмоциональный анализ
    emotion_primary VARCHAR(50),
    emotion_confidence REAL,
    emotion_raw_data JSONB, -- полный ответ модели эмоций
    
    -- Режим работы
    bot_mode VARCHAR(20), -- expert/writer/talk/auto
    
    -- Метаданные
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    message_length INTEGER GENERATED ALWAYS AS (LENGTH(content)) STORED,
    
    -- Контекстные данные
    session_id UUID DEFAULT uuid_generate_v4(), -- для группировки диалогов
    parent_message_id INTEGER REFERENCES history(id), -- для связи реплик
    
    -- Качественные метрики
    user_rating INTEGER CHECK (user_rating >= 1 AND user_rating <= 10),
    flagged_for_ltm BOOLEAN DEFAULT FALSE
);

-- Индексы для истории
CREATE INDEX idx_history_user_id ON history(user_id);
CREATE INDEX idx_history_created_at ON history(created_at);
CREATE INDEX idx_history_role ON history(role);
CREATE INDEX idx_history_session_id ON history(session_id);
CREATE INDEX idx_history_flagged_ltm ON history(flagged_for_ltm);
CREATE INDEX idx_history_user_rating ON history(user_rating);

-- ========================================
-- 8. ДОЛГОВРЕМЕННАЯ ПАМЯТЬ
-- ========================================

CREATE TABLE long_term_memory (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
    
    -- Основной контент
    user_message TEXT NOT NULL,
    bot_response TEXT NOT NULL,
    dialogue_context TEXT, -- предшествующий контекст
    
    -- Метаданные важности
    importance_score INTEGER CHECK (importance_score >= 1 AND importance_score <= 10) DEFAULT 5,
    memory_type VARCHAR(50) DEFAULT 'user_saved', -- user_saved, auto_saved, user_favorite
    
    -- Стилистические маркеры
    style_markers JSONB, -- [магреализм, балканизмы, символические_объекты]
    emotional_markers JSONB, -- данные из rubert-tiny2
    
    -- Связи
    source_history_id INTEGER REFERENCES history(id),
    source_session_id UUID,
    
    -- Технические поля для будущего
    embedding_vector TEXT, -- для семантического поиска
    contextual_tags TEXT[], -- массив ключевых слов
    
    -- Метаданные
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_by VARCHAR(20) DEFAULT 'user', -- user/auto/system
    last_accessed TIMESTAMP WITH TIME ZONE,
    access_count INTEGER DEFAULT 0,
    
    -- Качественные метрики
    effectiveness_rating REAL, -- насколько помогло в генерации
    user_feedback TEXT
);

-- Индексы для долговременной памяти
CREATE INDEX idx_ltm_user_id ON long_term_memory(user_id);
CREATE INDEX idx_ltm_importance ON long_term_memory(importance_score);
CREATE INDEX idx_ltm_memory_type ON long_term_memory(memory_type);
CREATE INDEX idx_ltm_created_at ON long_term_memory(created_at);
CREATE INDEX idx_ltm_last_accessed ON long_term_memory(last_accessed);
CREATE INDEX idx_ltm_style_markers ON long_term_memory USING GIN(style_markers);
CREATE INDEX idx_ltm_contextual_tags ON long_term_memory USING GIN(contextual_tags);

-- ========================================
-- 9. ЛИМИТЫ АВТОСОХРАНЕНИЯ
-- ========================================

CREATE TABLE auto_save_limits (
    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
    date DATE NOT NULL,
    count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (user_id, date)
);

CREATE INDEX idx_auto_save_limits_date ON auto_save_limits(date);

-- ========================================
-- 10. ЛОГИ АВТОРИЗАЦИИ
-- ========================================

CREATE TABLE auth_log (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    
    action VARCHAR(50) NOT NULL,
    password_masked VARCHAR(255),
    details TEXT,
    
    -- Технические данные
    ip_address INET,
    user_agent TEXT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_auth_log_user_id ON auth_log(user_id);
CREATE INDEX idx_auth_log_created_at ON auth_log(created_at);
CREATE INDEX idx_auth_log_action ON auth_log(action);

-- ========================================
-- 11. СИСТЕМНЫЕ ТАБЛИЦЫ
-- ========================================

-- Версия схемы БД
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    description TEXT,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

INSERT INTO schema_version (version, description) VALUES 
(3, 'Химера: PostgreSQL + долговременная память + стилевые профили + проактивные инициации');

-- Конфигурация системы
CREATE TABLE system_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    description TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Базовые настройки
INSERT INTO system_config (key, value, description) VALUES
('daily_message_limit', '10', 'Лимит бесплатных сообщений в день'),
('ltm_max_records_per_user', '1000', 'Максимум записей долговременной памяти на пользователя'),
('ltm_auto_cleanup_days', '365', 'Автоочистка неиспользуемых записей LTM'),
('auth_timeout_seconds', '300', 'Таймаут ожидания пароля'),
('bruteforce_timeout_seconds', '900', 'Блокировка при bruteforce'),
('max_password_attempts', '5', 'Максимум попыток ввода пароля'),
('max_auto_saves_per_day', '4', 'Максимум автосохранений в день'),
('auto_save_importance', '6', 'Важность автосохраненных диалогов'),
('proactive_enabled_globally', 'true', 'Глобальное включение проактивных инициаций'),
('proactive_min_interval_hours', '6', 'Минимальный интервал между инициациями (часы)'),
('proactive_max_per_day', '2', 'Максимум инициаций в день на пользователя');

-- ========================================
-- 12. ТРИГГЕРЫ И АВТОМАТИЗАЦИЯ
-- ========================================

-- Триггер для обновления updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Триггер для обновления updated_at стилевых профилей
CREATE OR REPLACE FUNCTION update_user_style_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Функция для обновления статистики активности
CREATE OR REPLACE FUNCTION update_user_activity(p_user_id BIGINT)
RETURNS VOID AS $$
BEGIN
    INSERT INTO user_activity_stats (user_id, total_messages, last_active)
    VALUES (p_user_id, 1, NOW())
    ON CONFLICT (user_id) DO UPDATE
    SET total_messages = user_activity_stats.total_messages + 1,
        last_active = NOW(),
        daily_average = user_activity_stats.total_messages / 
            GREATEST(1, EXTRACT(EPOCH FROM (NOW() - user_activity_stats.created_at)) / 86400);
END;
$$ LANGUAGE plpgsql;

-- Применяем триггеры к нужным таблицам
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_message_limits_updated_at BEFORE UPDATE ON message_limits
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_auto_save_limits_updated_at BEFORE UPDATE ON auto_save_limits
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_user_style_profiles_timestamp
BEFORE UPDATE ON user_style_profiles
FOR EACH ROW
EXECUTE FUNCTION update_user_style_timestamp();

CREATE TRIGGER update_user_proactivity_settings_updated_at 
BEFORE UPDATE ON user_proactivity_settings 
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Функции для подсчета статистики пользователей
CREATE OR REPLACE FUNCTION update_user_stats_history()
RETURNS TRIGGER AS $$
BEGIN
    -- Увеличиваем счетчик только для пользовательских сообщений
    IF NEW.role = 'user' THEN
        UPDATE users SET total_messages = total_messages + 1 
        WHERE user_id = NEW.user_id;
        
        -- Обновляем статистику активности
        PERFORM update_user_activity(NEW.user_id);
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION update_user_stats_ltm()
RETURNS TRIGGER AS $$
BEGIN
    -- Увеличиваем счетчик сохранений в LTM
    UPDATE users SET total_ltm_saves = total_ltm_saves + 1 
    WHERE user_id = NEW.user_id;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Создаем триггеры
CREATE TRIGGER update_stats_on_history 
    AFTER INSERT ON history
    FOR EACH ROW 
    EXECUTE FUNCTION update_user_stats_history();

CREATE TRIGGER update_stats_on_ltm 
    AFTER INSERT ON long_term_memory
    FOR EACH ROW 
    EXECUTE FUNCTION update_user_stats_ltm();

-- ========================================
-- 13. ВЬЮХИ ДЛЯ АНАЛИТИКИ
-- ========================================

-- Активные пользователи
CREATE VIEW active_users AS
SELECT 
    user_id,
    username,
    is_authorized,
    authorized_until,
    total_messages,
    total_ltm_saves,
    last_auth,
    CASE 
        WHEN authorized_until > NOW() THEN 'authorized'
        WHEN blocked_until > NOW() THEN 'blocked'
        ELSE 'unauthorized'
    END as status
FROM users
WHERE total_messages > 0;

-- Статистика долговременной памяти
CREATE VIEW ltm_stats AS
SELECT 
    u.user_id,
    u.username,
    COUNT(ltm.id) as total_memories,
    COALESCE(AVG(ltm.importance_score), 0) as avg_importance,
    0 as anchor_chunks, -- В Лайт версии всегда 0
    COUNT(CASE WHEN ltm.memory_type = 'user_favorite' THEN 1 END) as user_favorites,
    MAX(ltm.created_at) as last_memory_date
FROM users u
LEFT JOIN long_term_memory ltm ON u.user_id = ltm.user_id
GROUP BY u.user_id, u.username;

-- Расширенная статистика пользователей с стилевыми профилями
CREATE VIEW user_complete_stats AS
SELECT 
    u.user_id,
    u.username,
    u.total_messages,
    u.total_ltm_saves,
    u.is_authorized,
    u.preferred_mode,
    usp.style_vector,
    usp.dominant_mode,
    usp.interaction_count,
    uas.daily_average,
    uas.last_active,
    ups.is_enabled as proactivity_enabled,
    ups.ab_test_group,
    CASE 
        WHEN u.authorized_until > NOW() THEN 'authorized'
        WHEN u.blocked_until > NOW() THEN 'blocked'
        ELSE 'unauthorized'
    END as status
FROM users u
LEFT JOIN user_style_profiles usp ON u.user_id = usp.user_id
LEFT JOIN user_activity_stats uas ON u.user_id = uas.user_id
LEFT JOIN user_proactivity_settings ups ON u.user_id = ups.user_id
WHERE u.total_messages > 0;

-- Статистика проактивных инициаций
CREATE VIEW proactivity_stats AS
SELECT 
    u.user_id,
    u.username,
    ups.is_enabled,
    ups.ab_test_group,
    COUNT(il.id) as total_initiations,
    COUNT(CASE WHEN il.user_responded THEN 1 END) as responded_initiations,
    CASE 
        WHEN COUNT(il.id) > 0 THEN 
            COUNT(CASE WHEN il.user_responded THEN 1 END)::FLOAT / COUNT(il.id)::FLOAT 
        ELSE 0 
    END as response_rate,
    AVG(il.response_time_minutes) as avg_response_time,
    MAX(il.created_at) as last_initiation
FROM users u
LEFT JOIN user_proactivity_settings ups ON u.user_id = ups.user_id
LEFT JOIN initiation_logs il ON u.user_id = il.user_id
WHERE u.total_messages > 0
GROUP BY u.user_id, u.username, ups.is_enabled, ups.ab_test_group;

-- ========================================
-- 14. ФУНКЦИИ ДЛЯ РАБОТЫ С LTM
-- ========================================

-- Функция поиска релевантных воспоминаний
CREATE OR REPLACE FUNCTION search_memories(
    p_user_id BIGINT,
    p_query TEXT,
    p_limit INTEGER DEFAULT 3
)
RETURNS TABLE(
    memory_id INTEGER,
    user_message TEXT,
    bot_response TEXT,
    importance_score INTEGER,
    similarity_score REAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        ltm.id,
        ltm.user_message,
        ltm.bot_response,
        ltm.importance_score,
        -- Простая оценка релевантности
        (CASE 
            WHEN ltm.user_message ILIKE '%' || p_query || '%' THEN 1.0
            WHEN ltm.bot_response ILIKE '%' || p_query || '%' THEN 0.8
            ELSE 0.5
        END)::REAL as similarity
    FROM long_term_memory ltm
    WHERE ltm.user_id = p_user_id
        AND ltm.importance_score >= 5
        AND (
            ltm.user_message ILIKE '%' || p_query || '%' OR
            ltm.bot_response ILIKE '%' || p_query || '%' OR
            ltm.contextual_tags && ARRAY[LOWER(p_query)]
        )
    ORDER BY ltm.importance_score DESC, similarity DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- Очистка старых записей LTM
CREATE OR REPLACE FUNCTION cleanup_old_ltm(days_old INTEGER DEFAULT 365)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM long_term_memory 
    WHERE created_at < NOW() - INTERVAL '1 day' * days_old
        AND (last_accessed IS NULL OR last_accessed < NOW() - INTERVAL '1 day' * days_old/2)
        AND importance_score < 7;
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ========================================
-- 15. ФУНКЦИИ ДЛЯ ПРОАКТИВНОСТИ
-- ========================================

-- Функция для поиска пользователей готовых к инициации
CREATE OR REPLACE FUNCTION get_users_ready_for_initiation()
RETURNS TABLE(
    user_id BIGINT,
    last_message_ago_hours INTEGER,
    memory_count INTEGER,
    avg_importance REAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        u.user_id,
        EXTRACT(EPOCH FROM (NOW() - uas.last_active))::INTEGER / 3600 as hours_since_last,
        COALESCE(ltm_counts.memory_count, 0)::INTEGER,
        COALESCE(ltm_counts.avg_importance, 0)::REAL
    FROM users u
    JOIN user_proactivity_settings ups ON u.user_id = ups.user_id
    JOIN user_activity_stats uas ON u.user_id = uas.user_id
    LEFT JOIN (
        SELECT 
            user_id, 
            COUNT(*) as memory_count,
            AVG(importance_score) as avg_importance
        FROM long_term_memory 
        WHERE created_at > NOW() - INTERVAL '30 days'
        GROUP BY user_id
    ) ltm_counts ON u.user_id = ltm_counts.user_id
    WHERE ups.is_enabled = TRUE
        AND (ups.paused_until IS NULL OR ups.paused_until < NOW())
        AND uas.last_active < NOW() - INTERVAL '6 hours'
        AND uas.last_active > NOW() - INTERVAL '7 days'
        AND u.is_authorized = TRUE
        AND u.authorized_until > NOW()
    ORDER BY ltm_counts.memory_count DESC, hours_since_last DESC;
END;
$$ LANGUAGE plpgsql;

-- Функция планирования инициации
CREATE OR REPLACE FUNCTION schedule_initiation(
    p_user_id BIGINT,
    p_scheduled_at TIMESTAMP WITH TIME ZONE,
    p_initiation_type VARCHAR(30),
    p_source_memory_ids INTEGER[],
    p_emotion_context VARCHAR(50) DEFAULT NULL
)
RETURNS INTEGER AS $$
DECLARE
    new_initiation_id INTEGER;
BEGIN
    INSERT INTO initiation_schedule (
        user_id, 
        scheduled_at, 
        initiation_type, 
        source_memory_ids, 
        emotion_context,
        context_data
    ) VALUES (
        p_user_id,
        p_scheduled_at,
        p_initiation_type,
        p_source_memory_ids,
        p_emotion_context,
        jsonb_build_object('created_by', 'system', 'scheduling_reason', 'auto_detected')
    )
    RETURNING id INTO new_initiation_id;
    
    RETURN new_initiation_id;
END;
$$ LANGUAGE plpgsql;

-- ========================================
-- 16. КОММЕНТАРИИ К СХЕМЕ
-- ========================================

COMMENT ON TABLE users IS 'Пользователи системы с расширенными метаданными';
COMMENT ON TABLE user_style_profiles IS 'Стилевые профили пользователей для адаптивных инъекций';
COMMENT ON TABLE user_activity_stats IS 'Статистика активности пользователей';
COMMENT ON TABLE user_proactivity_settings IS 'Настройки проактивных инициаций пользователей';
COMMENT ON TABLE initiation_schedule IS 'Планировщик проактивных инициаций';
COMMENT ON TABLE initiation_logs IS 'Логи отправленных инициаций и ответов пользователей';
COMMENT ON TABLE long_term_memory IS 'Долговременная память - значимые диалоги пользователей';
COMMENT ON TABLE history IS 'История всех сообщений с эмоциональным анализом';
COMMENT ON TABLE auto_save_limits IS 'Лимиты автосохранения в долговременную память';
COMMENT ON FUNCTION search_memories IS 'Поиск релевантных воспоминаний для контекста генерации';
COMMENT ON FUNCTION update_user_activity IS 'Обновление статистики активности пользователя';
COMMENT ON FUNCTION get_users_ready_for_initiation IS 'Поиск пользователей готовых к проактивной инициации';
COMMENT ON FUNCTION schedule_initiation IS 'Планирование проактивной инициации для пользователя';

COMMENT ON COLUMN long_term_memory.importance_score IS 'Важность записи от 1 до 10';
COMMENT ON COLUMN long_term_memory.memory_type IS 'Тип воспоминания: user_saved, auto_saved, user_favorite';
COMMENT ON COLUMN long_term_memory.style_markers IS 'JSON с маркерами стиля: магреализм, балканизмы и т.д.';
COMMENT ON COLUMN long_term_memory.embedding_vector IS 'Векторное представление для семантического поиска (будущее)';
COMMENT ON COLUMN user_style_profiles.style_vector IS 'JSON с 4D-вектором стиля: playful, serious, ironic, magical';
COMMENT ON COLUMN user_proactivity_settings.ab_test_group IS 'Группа A/B тестирования проактивности';
COMMENT ON COLUMN initiation_schedule.source_memory_ids IS 'Массив ID воспоминаний для контекста инициации';

-- ========================================
-- 17. ФИНАЛЬНАЯ ПРОВЕРКА
-- ========================================

-- Показываем созданные объекты
SELECT 'ТАБЛИЦЫ:' as object_type, table_name as name
FROM information_schema.tables 
WHERE table_schema = 'public' AND table_name NOT LIKE 'anchor_%'
UNION ALL
SELECT 'ВЬЮХИ:', table_name
FROM information_schema.views 
WHERE table_schema = 'public'
UNION ALL
SELECT 'ФУНКЦИИ:', routine_name
FROM information_schema.routines 
WHERE routine_schema = 'public' AND routine_type = 'FUNCTION'
ORDER BY object_type, name;

-- ========================================
-- ГОТОВО! Схема Химера создана.
-- ========================================

SELECT 'Химера - Полная база данных готова к работе!' as status;