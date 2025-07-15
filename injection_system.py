# injection_system.py
"""
Оптимизированная система адаптивных инъекций для Химеры
"""

import redis
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import time
import logging
import random

logger = logging.getLogger("injection_system")

# ========================================
# КОНФИГУРАЦИЯ
# ========================================

class InjectionPriority(Enum):
    """Приоритеты для разрешения конфликтов"""
    CORE = 100      # Ядро характера
    MODE = 80       # Режим работы (expert/writer/talk)
    EMOTION = 60    # Эмоциональный контекст
    PERSONAL = 40   # Персональные якоря

@dataclass
class InjectionConfig:
    """Конфигурация системы инъекций"""
    max_tokens: int = 65  # Максимум токенов на инъекцию
    cache_ttl: int = 3600  # TTL кэша в секундах
    max_injections_per_dialogue: int = 2  # Максимум инъекций на диалог
    entropy_threshold: float = 0.7  # Порог энтропии для инъекций
    latency_budget_ms: int = 120  # Бюджет задержки

# ========================================
# КЭШИРОВАНИЕ
# ========================================

class InjectionCache:
    """Кэш для шаблонов инъекций"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.prefix = "himera:injections:"
        
    def get_cached_injection(self, cache_key: str) -> Optional[str]:
        """Получить кэшированную инъекцию"""
        try:
            cached = self.redis.get(f"{self.prefix}{cache_key}")
            if cached:
                return cached.decode('utf-8')
        except Exception as e:
            logger.error(f"Cache read error: {e}")
        return None
    
    def cache_injection(self, cache_key: str, injection: str, ttl: int = 3600, user_activity: int = 1):
        """Сохранить инъекцию в кэш с динамической TTL"""
        try:
            # Динамическая TTL на основе активности пользователя
            dynamic_ttl = int(ttl * (1 + np.log(max(1, user_activity))))
            self.redis.setex(
                f"{self.prefix}{cache_key}", 
                dynamic_ttl, 
                injection.encode('utf-8')
            )
        except Exception as e:
            logger.error(f"Cache write error: {e}")

# ========================================
# ВЕКТОРНЫЕ ЯКОРЯ
# ========================================

class VectorAnchorSystem:
    """Система векторных якорей для компактного представления стиля"""
    
    # Предопределенные векторные шаблоны (предобученные)
    STYLE_VECTORS = {
        'playful': np.array([0.8, 0.2, 0.6, 0.9]),  # игривость, серьезность, ирония, магреализм
        'analytical': np.array([0.3, 0.9, 0.4, 0.2]),
        'mystical': np.array([0.5, 0.4, 0.3, 0.95]),
        'ironic': np.array([0.6, 0.3, 0.95, 0.4]),
        'neutral': np.array([0.5, 0.5, 0.5, 0.5])  # Фолбэк для новых пользователей
    }
    
    @staticmethod
    def encode_user_style(user_history: List[Dict]) -> np.ndarray:
        """Кодирование стиля пользователя в вектор"""
        # Фолбэк для пустой истории
        if not user_history:
            return VectorAnchorSystem.STYLE_VECTORS['neutral']
            
        # Анализируем последние 10 взаимодействий
        style_scores = np.zeros(4)
        
        for msg in user_history[-10:]:
            if msg.get('bot_mode') == 'talk':
                style_scores += VectorAnchorSystem.STYLE_VECTORS['playful'] * 0.1
            elif msg.get('bot_mode') == 'expert':
                style_scores += VectorAnchorSystem.STYLE_VECTORS['analytical'] * 0.1
            elif msg.get('bot_mode') == 'writer':
                style_scores += VectorAnchorSystem.STYLE_VECTORS['mystical'] * 0.1
                
        return np.clip(style_scores, 0, 1)
    
    @staticmethod
    def calculate_style_volatility(user_history: List[Dict]) -> float:
        """Расчет коэффициента волатильности стиля (0-1)"""
        if len(user_history) < 5:
            return 0.5  # Средняя волатильность для новых пользователей
            
        # Извлекаем векторы из последних 5 сообщений пользователя
        vectors = []
        for msg in user_history[-10:]:
            if msg['role'] == 'user':
                # Создаем мини-историю для каждого сообщения
                mini_history = [msg]
                vector = VectorAnchorSystem.encode_user_style(mini_history)
                vectors.append(vector)
                
        if len(vectors) < 2:
            return 0.5
            
        # Рассчитываем попарные косинусные расстояния
        distances = []
        for i in range(len(vectors) - 1):
            # Косинусное расстояние = 1 - косинусное сходство
            cos_sim = np.dot(vectors[i], vectors[i+1]) / (np.linalg.norm(vectors[i]) * np.linalg.norm(vectors[i+1]) + 1e-8)
            distance = 1 - cos_sim
            distances.append(distance)
            
        # Возвращаем нормализованную дисперсию расстояний
        if distances:
            volatility = np.std(distances) * 2  # Умножаем на 2 для расширения диапазона
            return np.clip(volatility, 0, 1)
        
        return 0.5
    
    @staticmethod
    def vector_to_micro_prompt(vector: np.ndarray) -> str:
        """Преобразование вектора в микро-промпт (20 токенов)"""
        playful, serious, ironic, magical = vector
        
        # Выбираем доминирующие черты
        traits = []
        if playful > 0.7:
            traits.append("игривость")
        if ironic > 0.7:
            traits.append("ирония")
        if magical > 0.8:
            traits.append("магреализм")
            
        if not traits:
            return "Баланс всех качеств."
        
        return f"Акцент: {', '.join(traits[:2])}."
    
    @staticmethod
    def get_ltm_anchor(user_id: int, ltm_memories: List[Dict]) -> Optional[str]:
        """Извлечение стилевого якоря из долговременной памяти"""
        if not ltm_memories:
            return None
            
        # Анализируем стилевые маркеры из LTM
        style_keywords = []
        for memory in ltm_memories[:3]:  # Топ-3 релевантных
            if memory.get('style_markers'):
                markers = memory['style_markers']
                if markers.get('магреализм'):
                    style_keywords.append("мистика")
                if markers.get('балканизмы'):
                    style_keywords.extend(markers['балканизмы'][:1])
                    
        if style_keywords:
            return f"Помни: {', '.join(style_keywords[:2])}."
        
        return None

# ========================================
# ОСНОВНАЯ СИСТЕМА
# ========================================

class AdaptiveInjectionSystem:
    """Главный класс системы адаптивных инъекций"""
    
    def __init__(self, redis_client: redis.Redis, config: InjectionConfig):
        self.config = config
        self.cache = InjectionCache(redis_client)
        self.vector_system = VectorAnchorSystem()
        self.redis = redis_client  # Для персистентности счетчиков
        
        # Базовые шаблоны (компрессированные)
        self.CORE_TEMPLATES = {
            'base': "Ты — Химера.",  # 15 токенов
            'format_violation': "Химера пишет сплошным текстом.",  # 20 токенов
            'character_drift': "Вернись к своей сути.",  # 15 токенов
        }
        
        # Режимные усилители (20 токенов каждый)
        self.MODE_BOOSTERS = {
            'talk': "Твое оружие - живая речь.",
            'expert': "Анализ с блеском эрудиции и поэзии.",
            'writer': "Говори изнутри текста."
        }
        
        # Эмоциональные модификаторы (15 токенов)
        self.EMOTION_MODS = {
            'joy': "Игривость уместна.",
            'sadness': "Чуткость без слащавости.",
            'surprise': "Удивление питает остроумие.",
            'neutral': "Баланс глубины и легкости."
        }
    
    def should_inject(self, user_id: int, dialogue_entropy: float) -> bool:
        """Определение необходимости инъекции"""
        # Проверяем счетчик инъекций (персистентный)
        user_count = self._get_injection_count(user_id)
        if user_count >= self.config.max_injections_per_dialogue:
            return False
            
        # Проверяем энтропию диалога
        if dialogue_entropy < self.config.entropy_threshold:
            return False
            
        return True
    
    def _get_injection_count(self, user_id: int) -> int:
        """Получение счетчика инъекций из Redis"""
        try:
            count = self.redis.get(f"himera:injection_count:{user_id}")
            return int(count) if count else 0
        except:
            return 0
    
    def _increment_injection_count(self, user_id: int):
        """Увеличение счетчика инъекций в Redis"""
        try:
            key = f"himera:injection_count:{user_id}"
            self.redis.incr(key)
            self.redis.expire(key, 86400)  # TTL 24 часа
        except Exception as e:
            logger.error(f"Error incrementing injection count: {e}")
    
    def generate_injection(
        self, 
        user_id: int,
        mode: str,
        emotion: str,
        user_history: List[Dict],
        violation_type: Optional[str] = None,
        is_authorized: bool = False,
        ltm_memories: Optional[List[Dict]] = None
    ) -> Tuple[str, int]:
        """
        Генерация адаптивной инъекции с динамической инвалидацией кэша
        Возвращает: (injection_text, latency_ms)
        """
        start_time = time.time()
        
        # 1. Проверяем кэш для неавторизованных
        cache_key = self._generate_cache_key(user_id, mode, emotion, violation_type)
        
        # Проверяем волатильность стиля
        volatility = self.vector_system.calculate_style_volatility(user_history)
        
        # Триггеры инвалидации
        should_ignore_cache = False
        
        # Триггер 1: Изменение эмоции (проверяем последнюю кэшированную)
        last_emotion_key = f"himera:last_emotion:{user_id}"
        last_emotion = self.redis.get(last_emotion_key)
        if last_emotion and last_emotion != emotion:
            should_ignore_cache = True
            logger.debug(f"Emotion changed for user {user_id}: {last_emotion} -> {emotion}")
            
        # Триггер 2: Высокая волатильность
        if volatility > 0.4:
            should_ignore_cache = True
            logger.debug(f"High volatility for user {user_id}: {volatility:.2f}")
            
        # Триггер 3: Расчет энтропии для критических случаев
        if violation_type or (len(user_history) > 3 and calculate_dialogue_entropy(user_history) > 0.8):
            should_ignore_cache = True
            logger.debug(f"Critical injection needed for user {user_id}")
        
        cached = self.cache.get_cached_injection(cache_key)
        
        # Авторизованные всегда получают персонализированные инъекции
        # Неавторизованные получают кэш, если нет триггеров инвалидации
        if cached and not is_authorized and not should_ignore_cache:
            latency = int((time.time() - start_time) * 1000)
            return cached, latency
        
        # 2. Выбираем компоненты по приоритету
        components = []
        tokens_used = 0
        
        # Ядро (приоритет 1)
        if violation_type:
            core = self.CORE_TEMPLATES.get(violation_type, self.CORE_TEMPLATES['base'])
        else:
            core = self.CORE_TEMPLATES['base']
        components.append((InjectionPriority.CORE, core))
        tokens_used += 15
        
        # Режим (приоритет 2)
        if tokens_used + 20 <= self.config.max_tokens:
            mode_boost = self.MODE_BOOSTERS.get(mode, "")
            if mode_boost:
                components.append((InjectionPriority.MODE, mode_boost))
                tokens_used += 20
        
        # Эмоция (приоритет 3)
        if tokens_used + 15 <= self.config.max_tokens:
            emotion_mod = self.EMOTION_MODS.get(emotion, self.EMOTION_MODS['neutral'])
            components.append((InjectionPriority.EMOTION, emotion_mod))
            tokens_used += 15
        
        # Персональный якорь для авторизованных пользователей (приоритет 4)
        if is_authorized and tokens_used + 20 <= self.config.max_tokens:
            # Сначала пробуем LTM якорь
            ltm_anchor = None
            if ltm_memories:
                ltm_anchor = self.vector_system.get_ltm_anchor(user_id, ltm_memories)
                
            if ltm_anchor:
                # Используем якорь из долговременной памяти
                components.append((InjectionPriority.PERSONAL, ltm_anchor))
                tokens_used += 20
            else:
                # Фолбэк на векторный анализ истории
                user_vector = self.vector_system.encode_user_style(user_history)
                personal_anchor = self.vector_system.vector_to_micro_prompt(user_vector)
                components.append((InjectionPriority.PERSONAL, personal_anchor))
                tokens_used += 20
        
        # 3. Сортируем по приоритету и собираем
        components.sort(key=lambda x: x[0].value, reverse=True)
        injection = " ".join([text for _, text in components])
        
        # 4. Применяем форматирование DeepSeek
        injection = f"## НАПОМИНАНИЕ ## {injection}"
        
        # 5. Кэшируем результат только для неавторизованных с динамическим TTL
        if not is_authorized:
            # Динамический TTL на основе волатильности
            # volatility 0.1 -> TTL = 3600 * 0.37 = 1332s (22 мин)
            # volatility 0.5 -> TTL = 3600 * 0.65 = 2340s (39 мин) 
            # volatility 0.9 -> TTL = 3600 * 0.93 = 3348s (56 мин)
            dynamic_ttl_factor = 0.3 + 0.7 * (1 - volatility)  # Инвертируем: низкая волатильность = долгий кэш
            dynamic_ttl = int(self.config.cache_ttl * dynamic_ttl_factor)
            
            user_activity = self._get_user_activity(user_id)
            self.cache.cache_injection(cache_key, injection, dynamic_ttl, user_activity)
            
            # Сохраняем последнюю эмоцию для отслеживания изменений
            self.redis.setex(f"himera:last_emotion:{user_id}", 86400, emotion)  # TTL 24 часа
            
            logger.debug(f"Cached injection for user {user_id} with TTL {dynamic_ttl}s (volatility: {volatility:.2f})")
        
        # 6. Обновляем счетчик
        self._increment_injection_count(user_id)
        
        latency = int((time.time() - start_time) * 1000)
        
        # Логируем если превысили бюджет
        if latency > self.config.latency_budget_ms:
            logger.warning(f"Injection latency {latency}ms exceeded budget for user {user_id}")
        
        return injection, latency
    
    def _get_user_activity(self, user_id: int) -> int:
        """Получение уровня активности пользователя для динамической TTL"""
        try:
            # Простая метрика: количество сообщений пользователя за последние 7 дней
            # В реальности можно использовать user_activity_stats из БД
            activity_key = f"himera:user_activity:{user_id}"
            activity = self.redis.get(activity_key)
            return int(activity) if activity else 1
        except:
            return 1
    
    def _generate_cache_key(self, user_id: int, mode: str, emotion: str, violation: Optional[str]) -> str:
        """Генерация ключа кэша"""
        components = [str(user_id % 100), mode, emotion]  # user_id % 100 для группировки
        if violation:
            components.append(violation)
        
        key_string = ":".join(components)
        return hashlib.md5(key_string.encode()).hexdigest()[:16]
    
    def reset_user_counter(self, user_id: int):
        """Сброс счетчика инъекций для пользователя"""
        try:
            self.redis.delete(f"himera:injection_count:{user_id}")
        except Exception as e:
            logger.error(f"Error resetting injection counter: {e}")
    
    def get_stats(self) -> Dict:
        """Получение статистики системы"""
        try:
            # Подсчет активных пользователей через Redis keys
            pattern = "himera:injection_count:*"
            active_users = len(self.redis.keys(pattern))
            
            # Подсчет общего количества инъекций
            total_injections = 0
            for key in self.redis.keys(pattern):
                count = self.redis.get(key)
                if count:
                    total_injections += int(count)
            
            # Подсчет кэшированных инъекций
            cache_pattern = f"{self.cache.prefix}*"
            cached_injections = len(self.redis.keys(cache_pattern))
            
            return {
                'active_users': active_users,
                'total_injections': total_injections,
                'cached_injections': cached_injections,
                'cache_info': self.redis.info('memory')
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {'active_users': 0, 'total_injections': 0}
    
    def background_cache_recalibration(self, percentage: float = 0.05):
        """Фоновая рекалибрация кэша (для cron задач)"""
        try:
            cache_pattern = f"{self.cache.prefix}*"
            all_keys = self.redis.keys(cache_pattern)
            
            if not all_keys:
                return 0
                
            # Выбираем случайные ключи для инвалидации
            num_to_invalidate = max(1, int(len(all_keys) * percentage))
            keys_to_invalidate = random.sample(all_keys, num_to_invalidate)
            
            # Удаляем выбранные ключи
            for key in keys_to_invalidate:
                self.redis.delete(key)
                
            logger.info(f"Background recalibration: invalidated {len(keys_to_invalidate)} cache keys")
            return len(keys_to_invalidate)
            
        except Exception as e:
            logger.error(f"Error in background recalibration: {e}")
            return 0

# ========================================
# ИНТЕГРАЦИЯ С ОСНОВНЫМ БОТОМ
# ========================================

def calculate_dialogue_entropy(messages: List[Dict]) -> float:
    """
    Расчет энтропии диалога (0-1)
    Высокая энтропия = хаотичный/несвязный диалог
    """
    if len(messages) < 3:
        return 0.0
    
    # Анализируем изменения режимов
    mode_changes = 0
    prev_mode = messages[0].get('bot_mode', 'auto')
    
    for msg in messages[1:]:
        curr_mode = msg.get('bot_mode', 'auto')
        if curr_mode != prev_mode:
            mode_changes += 1
        prev_mode = curr_mode
    
    # Анализируем длину сообщений
    lengths = [len(msg.get('content', '')) for msg in messages if msg['role'] == 'user']
    if lengths:
        length_variance = np.var(lengths) / (np.mean(lengths) + 1)
    else:
        length_variance = 0
    
    # Комбинированная метрика
    entropy = min(1.0, (mode_changes / len(messages)) * 0.5 + min(1.0, length_variance / 1000) * 0.5)
    
    return entropy

# Пример использования в telegram_bot.py:
"""
# В начале файла
redis_client = redis.Redis(host='localhost', port=6380, db=0)
injection_config = InjectionConfig()
injection_system = AdaptiveInjectionSystem(redis_client, injection_config)

# В handle_message после определения режима:
if injection_system.should_inject(user_id, dialogue_entropy):
    # Получаем LTM воспоминания (если пользователь авторизован)
    ltm_memories = None
    if auth_status.get('authorized'):
        ltm_memories = search_relevant_memories(user_id, user_message, limit=3)
    
    injection, latency = injection_system.generate_injection(
        user_id=user_id,
        mode=mode,
        emotion=emotion_label,
        user_history=recent_history,
        violation_type=violation_type,
        is_authorized=auth_status.get('authorized', False),
        ltm_memories=ltm_memories
    )
    
    # Добавляем инъекцию в контекст
    messages.append({"role": "system", "content": injection})
    logger.info(f"Applied injection for user {user_id}, latency: {latency}ms")

# Фоновая рекалибрация кэша (можно вызывать периодически)
# injection_system.background_cache_recalibration(0.05)
"""