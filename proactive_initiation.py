"""
Основной модуль системы проактивных инициаций
"""

import random
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import numpy as np
import logging
import json
import pytz

from config import (
    PROACTIVITY_DEFAULT_STATE,
    MIN_INITIATIONS_PER_DAY, MAX_INITIATIONS_PER_DAY,
    MIN_INITIATION_INTERVAL_HOURS, MAX_INITIATION_INTERVAL_HOURS,
    INITIATION_ACTIVE_HOURS_START, INITIATION_ACTIVE_HOURS_END,
    INITIATION_MIN_QUALITY_SCORE, INITIATION_SIMILARITY_THRESHOLD,
    SILENCE_AFTER_IGNORED_COUNT, SILENCE_MIN_RESPONSE_LENGTH,
    SILENCE_INACTIVITY_DAYS, INITIATION_TYPE_WEIGHTS
)

logger = logging.getLogger("proactive_initiation")

class InitiationType(Enum):
    CONTINUATION = "continuation"
    INSIGHT = "insight" 
    SUPPORTIVE = "supportive"

class InitiationStatus(Enum):
    PENDING = "pending"
    SENT = "sent"
    CANCELLED = "cancelled"
    FAILED = "failed"

@dataclass
class InitiationCandidate:
    """Кандидат на инициацию"""
    user_id: int
    initiation_type: InitiationType
    priority_score: float  # 0.0-1.0
    source_memories: List[Dict]
    context_data: Dict
    estimated_quality: float  # 0.0-1.0
    emotion_context: Optional[str] = None

class ProactiveInitiationEngine:
    """Главный класс системы проактивных инициаций"""
    
    def __init__(self, postgres_conn, redis_client, telegram_app=None):
        self.db = postgres_conn
        self.redis = redis_client
        self.telegram_app = telegram_app
        
    async def check_and_schedule_initiations(self):
        """Основная функция планирования инициаций (запускается по cron)"""
        logger.info("Starting proactive initiation scheduling...")
        
        # Получаем пользователей с включенной проактивностью
        eligible_users = self._get_eligible_users()
        logger.info(f"Found {len(eligible_users)} eligible users")
        
        scheduled_count = 0
        
        for user_id in eligible_users:
            try:
                # Проверяем можно ли инициировать для этого пользователя
                if await self._should_schedule_initiation(user_id):
                    # Анализируем возможности для инициации
                    candidates = await self._analyze_initiation_opportunities(user_id)
                    
                    if candidates:
                        # Выбираем лучшего кандидата
                        best_candidate = self._select_best_candidate(candidates)
                        
                        # Планируем отправку
                        scheduled_time = await self._calculate_optimal_time(user_id)
                        await self._schedule_initiation(best_candidate, scheduled_time)
                        scheduled_count += 1
                        
                        logger.info(f"Scheduled {best_candidate.initiation_type.value} initiation for user {user_id}")
                        
            except Exception as e:
                logger.error(f"Error scheduling for user {user_id}: {e}")
                
        logger.info(f"Scheduled {scheduled_count} initiations total")
    
    async def send_scheduled_initiations(self):
        """Отправка запланированных инициаций (запускается каждые 10 минут)"""
        
        # Получаем инициации готовые к отправке
        pending_initiations = self._get_pending_initiations()
        logger.info(f"Found {len(pending_initiations)} pending initiations")
        
        for initiation in pending_initiations:
            try:
                # Генерируем сообщение
                message = await self._generate_initiation_message(initiation)
                
                # Отправляем через Telegram Bot
                success = await self._send_initiation_message(initiation['user_id'], message)
                
                if success:
                    # Обновляем статус и логируем
                    self._mark_initiation_sent(initiation['id'], message)
                    logger.info(f"Sent initiation {initiation['id']} to user {initiation['user_id']}")
                else:
                    self._mark_initiation_failed(initiation['id'], "Failed to send via Telegram")
                    
            except Exception as e:
                logger.error(f"Failed to send initiation {initiation['id']}: {e}")
                self._mark_initiation_failed(initiation['id'], str(e))

    def _get_eligible_users(self) -> List[int]:
        """Получение пользователей готовых для инициаций"""
        
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT ups.user_id 
                FROM user_proactivity_settings ups
                JOIN users u ON ups.user_id = u.user_id
                WHERE ups.is_enabled = TRUE 
                AND (ups.paused_until IS NULL OR ups.paused_until < NOW())
                AND u.is_authorized = TRUE
                AND ups.ab_test_group = 'A'
            """)
            
            return [row['user_id'] for row in cursor.fetchall()]
            
        finally:
            cursor.close()
            conn.close()
    
    async def _should_schedule_initiation(self, user_id: int) -> bool:
        """Проверка можно ли планировать инициацию для пользователя"""
        
        # Проверяем лимиты инициаций на сегодня
        today_count = self._get_today_initiation_count(user_id)
        if today_count >= MAX_INITIATIONS_PER_DAY:
            return False
        
        # Проверяем последнюю активность пользователя
        last_activity = self._get_last_user_activity(user_id)
        if not last_activity or (datetime.now(timezone.utc) - last_activity).days > SILENCE_INACTIVITY_DAYS:
            logger.debug(f"User {user_id} inactive for too long")
            return False
        
        # Проверяем Adaptive Silence
        if await self._is_in_silence_mode(user_id):
            logger.debug(f"User {user_id} is in silence mode")
            return False
        
        # Проверяем минимальный интервал между инициациями
        last_initiation = self._get_last_initiation_time(user_id)
        if last_initiation:
            hours_passed = (datetime.now(timezone.utc) - last_initiation).total_seconds() / 3600
            if hours_passed < MIN_INITIATION_INTERVAL_HOURS:
                return False
        
        return True
    
    async def _analyze_initiation_opportunities(self, user_id: int) -> List[InitiationCandidate]:
        """Анализ возможностей для инициации"""
        
        candidates = []
        
        # Получаем LTM память и эмоциональный контекст
        from ltm_database import get_connection, get_recent_history
        
        # Получаем воспоминания за последние 7 дней
        recent_memories = self._get_recent_ltm_memories(user_id, days=7)
        
        # Получаем эмоциональную историю
        recent_history = get_recent_history(user_id, limit=50)
        emotion_trajectory = self._analyze_emotion_trajectory(recent_history)
        
        # Ищем кандидатов для каждого типа
        if random.random() < INITIATION_TYPE_WEIGHTS["continuation"]:
            continuation_candidates = self._find_continuation_opportunities(
                user_id, recent_memories, recent_history
            )
            candidates.extend(continuation_candidates)
        
        if random.random() < INITIATION_TYPE_WEIGHTS["insight"]:
            insight_candidates = self._find_insight_opportunities(
                user_id, recent_memories, recent_history
            )
            candidates.extend(insight_candidates)
        
        if random.random() < INITIATION_TYPE_WEIGHTS["supportive"]:
            supportive_candidates = self._find_supportive_opportunities(
                user_id, recent_memories, emotion_trajectory
            )
            candidates.extend(supportive_candidates)
        
        # Фильтруем по качеству
        quality_candidates = [c for c in candidates if c.estimated_quality >= INITIATION_MIN_QUALITY_SCORE]
        
        # Добавляем эмоциональный контекст
        for candidate in quality_candidates:
            candidate.emotion_context = emotion_trajectory.get('current_emotion', 'neutral')
        
        return quality_candidates
    
    def _find_continuation_opportunities(self, user_id: int, memories: List[Dict], 
                                      history: List[Dict]) -> List[InitiationCandidate]:
        """Поиск незавершенных тем для продолжения"""
        
        candidates = []
        
        for memory in memories[:10]:  # Анализируем последние 10 воспоминаний
            # Проверяем наличие открытых вопросов
            user_message = memory.get('user_message', '')
            bot_response = memory.get('bot_response', '')
            
            # Признаки незавершенности
            is_open_ended = (
                '?' in user_message or
                any(phrase in user_message.lower() for phrase in [
                    'как думаешь', 'что если', 'может быть', 'интересно'
                ]) or
                any(phrase in bot_response.lower() for phrase in [
                    'продолжим', 'вернемся к этому', 'подумаем об этом'
                ])
            )
            
            if is_open_ended:
                # Оцениваем качество на основе важности и давности
                days_ago = (datetime.now(timezone.utc) - memory['created_at']).days
                quality = memory.get('importance_score', 5) / 10.0
                
                # Снижаем качество для старых воспоминаний
                if days_ago > 3:
                    quality *= 0.7
                
                # Повышаем качество для автосохраненных
                if memory.get('memory_type') == 'auto_saved':
                    quality *= 1.2
                
                quality = min(1.0, quality)
                
                if quality >= INITIATION_MIN_QUALITY_SCORE:
                    candidate = InitiationCandidate(
                        user_id=user_id,
                        initiation_type=InitiationType.CONTINUATION,
                        priority_score=quality,
                        source_memories=[memory],
                        context_data={
                            'main_topic': self._extract_main_topic(user_message),
                            'last_question': user_message,
                            'days_ago': days_ago
                        },
                        estimated_quality=quality
                    )
                    candidates.append(candidate)
        
        return candidates
    
    def _find_insight_opportunities(self, user_id: int, memories: List[Dict],
                                  history: List[Dict]) -> List[InitiationCandidate]:
        """Поиск связей между темами из разных дней"""
        
        candidates = []
        
        # Группируем память по дням
        memory_by_days = {}
        for memory in memories:
            day = memory['created_at'].date()
            if day not in memory_by_days:
                memory_by_days[day] = []
            memory_by_days[day].append(memory)
        
        # Ищем семантические связи между днями
        days = sorted(memory_by_days.keys())
        
        for i in range(len(days)):
            for j in range(i + 1, len(days)):
                day1, day2 = days[i], days[j]
                
                # Пропускаем если дни слишком близко
                if (day2 - day1).days < 2:
                    continue
                
                # Анализируем память каждого дня
                for mem1 in memory_by_days[day1]:
                    for mem2 in memory_by_days[day2]:
                        # Используем простое сравнение ключевых слов
                        similarity = self._calculate_semantic_similarity(
                            mem1['user_message'] + ' ' + mem1['bot_response'],
                            mem2['user_message'] + ' ' + mem2['bot_response']
                        )
                        
                        if similarity > INITIATION_SIMILARITY_THRESHOLD:
                            quality = similarity * 0.9  # Немного снижаем для insight
                            
                            candidate = InitiationCandidate(
                                user_id=user_id,
                                initiation_type=InitiationType.INSIGHT,
                                priority_score=similarity,
                                source_memories=[mem1, mem2],
                                context_data={
                                    'connection_type': 'temporal',
                                    'days_between': (day2 - day1).days,
                                    'shared_concepts': self._extract_shared_concepts(mem1, mem2)
                                },
                                estimated_quality=quality
                            )
                            candidates.append(candidate)
        
        return candidates[:3]  # Максимум 3 insight кандидата
    
    def _find_supportive_opportunities(self, user_id: int, memories: List[Dict],
                                     emotion_trajectory: Dict) -> List[InitiationCandidate]:
        """Поиск возможностей для эмоциональной поддержки"""
        
        candidates = []
        
        # Анализируем текущее эмоциональное состояние
        current_emotion = emotion_trajectory.get('current_emotion', 'neutral')
        emotion_trend = emotion_trajectory.get('trend', 'stable')
        needs_support = emotion_trajectory.get('needs_support', False)
        
        if needs_support:
            # Находим релевантные воспоминания
            relevant_memories = []
            
            for memory in memories:
                # Ищем воспоминания с похожей эмоцией или поддерживающим контекстом
                memory_tags = memory.get('contextual_tags', [])
                
                if (current_emotion in memory_tags or
                    any(tag in ['поддержка', 'утешение', 'вдохновение'] for tag in memory_tags)):
                    relevant_memories.append(memory)
            
            if relevant_memories or emotion_trend == 'declining':
                quality = 0.8  # Поддержка всегда важна
                
                # Повышаем приоритет при явной необходимости
                if emotion_trend == 'declining':
                    quality = 0.9
                
                candidate = InitiationCandidate(
                    user_id=user_id,
                    initiation_type=InitiationType.SUPPORTIVE,
                    priority_score=quality,
                    source_memories=relevant_memories[:2],  # Максимум 2 воспоминания
                    context_data={
                        'emotional_context': current_emotion,
                        'emotion_trend': emotion_trend,
                        'support_type': 'empathetic' if current_emotion in ['sadness', 'fear'] else 'encouraging'
                    },
                    estimated_quality=quality
                )
                candidates.append(candidate)
        
        return candidates
    
    def _select_best_candidate(self, candidates: List[InitiationCandidate]) -> InitiationCandidate:
        """Выбор лучшего кандидата с учетом всех факторов"""
        
        if not candidates:
            return None
        
        # Взвешенная оценка
        for candidate in candidates:
            # Базовая оценка = качество * приоритет
            score = candidate.estimated_quality * candidate.priority_score
            
            # Бонус за тип (разнообразие)
            last_type = self._get_last_initiation_type(candidate.user_id)
            if last_type and last_type != candidate.initiation_type.value:
                score *= 1.2  # Поощряем разнообразие
            
            # Бонус за свежесть воспоминаний
            if candidate.source_memories:
                avg_days = np.mean([
                    (datetime.now(timezone.utc) - mem['created_at']).days 
                    for mem in candidate.source_memories
                ])
                if avg_days <= 3:
                    score *= 1.1
            
            candidate._final_score = score
        
        # Выбираем кандидата с максимальной оценкой
        return max(candidates, key=lambda c: c._final_score)
    
    async def _calculate_optimal_time(self, user_id: int) -> datetime:
        """Расчет оптимального времени для отправки"""
        
        # Получаем часовой пояс пользователя
        user_timezone = self._get_user_timezone(user_id)
        
        # Получаем историю активности
        activity_hours = self._get_user_activity_hours(user_id)
        
        # Базовый случай - случайное время в рабочие часы
        if not activity_hours:
            hour = random.randint(INITIATION_ACTIVE_HOURS_START + 2, INITIATION_ACTIVE_HOURS_END - 2)
        else:
            # Выбираем час из активных с вероятностью
            weights = [activity_hours.get(h, 0.1) for h in range(24)]
            
            # Обнуляем ночные часы
            for h in range(0, INITIATION_ACTIVE_HOURS_START):
                weights[h] = 0
            for h in range(INITIATION_ACTIVE_HOURS_END, 24):
                weights[h] = 0
            
            # Выбираем взвешенно случайный час
            hour = random.choices(range(24), weights=weights)[0]
        
        # Добавляем случайное отклонение ±90 минут
        minute = random.randint(0, 59)
        deviation_minutes = random.randint(-90, 90)
        
        # Создаем время в часовом поясе пользователя
        now = datetime.now(pytz.timezone(user_timezone))
        target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        target_time += timedelta(minutes=deviation_minutes)
        
        # Если время уже прошло сегодня, переносим на завтра
        if target_time <= now:
            target_time += timedelta(days=1)
        
        # Конвертируем в UTC для хранения
        return target_time.astimezone(timezone.utc)
    
    async def _schedule_initiation(self, candidate: InitiationCandidate, scheduled_time: datetime):
        """Сохранение запланированной инициации в БД"""
        
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            scheduled_time = scheduled_time.astimezone(timezone.utc)
            # Подготавливаем данные
            memory_ids = [m['id'] for m in candidate.source_memories] if candidate.source_memories else []
            
            cursor.execute("""
                INSERT INTO initiation_schedule 
                (user_id, scheduled_at, initiation_type, source_memory_ids, 
                 context_data, emotion_context, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                RETURNING id
            """, (
                candidate.user_id,
                scheduled_time,
                candidate.initiation_type.value,
                memory_ids,
                json.dumps(candidate.context_data),
                candidate.emotion_context
            ))
            
            initiation_id = cursor.fetchone()['id']
            conn.commit()
            
            logger.info(f"Scheduled initiation {initiation_id} for {scheduled_time}")
            
        finally:
            cursor.close()
            conn.close()
    
    async def _generate_initiation_message(self, initiation: Dict) -> str:
        """Генерация текста инициации с учетом всех систем"""
        
        # Импортируем необходимые модули
        from deepseek_api import ask_deepseek
        from ltm_database import get_recent_history
        from injection_system import AdaptiveInjectionSystem, VectorAnchorSystem
        
        # Получаем контекст
        user_history = get_recent_history(initiation['user_id'], limit=15)
        
        # ИСПРАВЛЕНИЕ: Правильно обрабатываем context_data
        if isinstance(initiation['context_data'], str):
            import json
            context_data = json.loads(initiation['context_data'])
        else:
            context_data = initiation['context_data']
        
        # Получаем воспоминания-источники
        source_memory_ids = initiation.get('source_memory_ids', [])
        if source_memory_ids is None:
            source_memory_ids = []
        source_memories = self._get_memories_by_ids(source_memory_ids)
        
        # Строим промпт в зависимости от типа
        if initiation['initiation_type'] == 'continuation':
            system_prompt = self._build_continuation_prompt(context_data, source_memories)
        elif initiation['initiation_type'] == 'insight':
            system_prompt = self._build_insight_prompt(context_data, source_memories)
        elif initiation['initiation_type'] == 'supportive':
            system_prompt = self._build_supportive_prompt(context_data, source_memories)
        else:
            system_prompt = "Начни дружеский диалог, вспомнив прошлые беседы."
        
        # Формируем сообщения для DeepSeek
        messages = [
            {"role": "system", "content": "Ты - Химера. Инициируешь диалог с пользователем, с которым у тебя уже есть история общения. Будь естественной, не упоминай что ты 'вспомнила' или 'анализировала'."},
            {"role": "system", "content": system_prompt}
        ]
        
        # Добавляем персонализацию через векторные якоря если доступно
        if hasattr(self, 'injection_system') and self.injection_system:
            user_vector = VectorAnchorSystem.encode_user_style(user_history)
            personal_style = VectorAnchorSystem.vector_to_micro_prompt(user_vector)
            messages.append({"role": "system", "content": f"Стиль общения: {personal_style}"})
        
        # Добавляем эмоциональный контекст
        if initiation.get('emotion_context'):
            messages.append({
                "role": "system", 
                "content": f"Эмоциональный контекст пользователя: {initiation['emotion_context']}"
            })
        
        # Генерируем через DeepSeek - ИСПРАВЛЕНИЕ: ask_deepseek синхронная
        response = ask_deepseek(messages, mode="talk", use_json=False)
        
        return response.strip()
        
        
        
    
    def _build_continuation_prompt(self, context: Dict, memories: List[Dict]) -> str:
        """Построение промпта для continuation инициации"""
        
        main_topic = context.get('main_topic', 'тема')
        days_ago = context.get('days_ago', 1)
        last_question = context.get('last_question', '')
        
        # Извлекаем ключевые моменты из воспоминаний
        if memories:
            memory = memories[0]
            user_msg = memory['user_message'][:200]
            bot_resp = memory['bot_response'][:200]
        else:
            user_msg = last_question
            bot_resp = ""
        
        time_ref = "вчера" if days_ago == 1 else f"{days_ago} дня назад" if days_ago < 5 else "на днях"
        
        prompt = f"""
        Пользователь {time_ref} интересовался темой: {main_topic}
        
        Его сообщение: "{user_msg}"
        
        Начни новый диалог, естественно возвращаясь к этой теме. 
        Предложи неожиданный взгляд, новую мысль или развитие идеи.
        Будь конкретной и увлекательной. Не более 2-3 предложений.
        
        НЕ говори "помнишь", "мы обсуждали" - просто начни с интересной мысли по теме.
        """
        
        return prompt
    
    def _build_insight_prompt(self, context: Dict, memories: List[Dict]) -> str:
        """Построение промпта для insight инициации"""
        
        shared_concepts = context.get('shared_concepts', [])
        days_between = context.get('days_between', 3)
        
        if len(memories) >= 2:
            topic1 = self._extract_main_topic(memories[0]['user_message'])
            topic2 = self._extract_main_topic(memories[1]['user_message'])
            
            prompt = f"""
            В разных беседах пользователь касался тем:
            1. {topic1}
            2. {topic2}
            
            Между ними есть интересная связь через: {', '.join(shared_concepts[:2]) if shared_concepts else 'общую идею'}
            
            Начни диалог, предложив неожиданную связь или параллель между этими темами.
            Будь проницательной и оригинальной, в духе магического реализма.
            
            Начни сразу с интригующей мысли, без вступлений.
            """
        else:
            prompt = "Предложи неожиданную мысль или связь между темами прошлых бесед."
        
        return prompt
    
    def _build_supportive_prompt(self, context: Dict, memories: List[Dict]) -> str:
        """Построение промпта для supportive инициации"""
        
        emotion = context.get('emotional_context', 'neutral')
        support_type = context.get('support_type', 'encouraging')
        
        emotion_map = {
            'sadness': 'грусть',
            'fear': 'тревога',
            'anger': 'раздражение',
            'neutral': 'задумчивость'
        }
        
        emotion_ru = emotion_map.get(emotion, 'настроение')
        
        if support_type == 'empathetic':
            prompt = f"""
            Пользователь в последнее время испытывает {emotion_ru}.
            
            Начни теплый, поддерживающий диалог. Будь чуткой, но не навязчивой.
            Можешь предложить отвлечься на что-то вдохновляющее или просто выслушать.
            
            Избегай банальностей вроде "все будет хорошо". Будь искренней.
            """
        else:
            prompt = f"""
            Начни вдохновляющий диалог, который поднимет настроение.
            Можешь поделиться интересной мыслью, предложить творческую идею или рассказать что-то удивительное.
            
            Будь позитивной, но естественной. Никакой искусственной бодрости.
            """
        
        return prompt
    
    async def _send_initiation_message(self, user_id: int, message: str) -> bool:
        """Отправка инициации через Telegram Bot"""
        
        try:
            if self.telegram_app and self.telegram_app.bot:
                await self.telegram_app.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode=None
                )
                return True
            else:
                logger.error("Telegram app not initialized")
                return False
                
        except Exception as e:
            logger.error(f"Failed to send Telegram message to {user_id}: {e}")
            return False
    
    # === Вспомогательные методы ===
    
    def _get_pending_initiations(self) -> List[Dict]:
        """Получение инициаций готовых к отправке"""
        
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT * FROM initiation_schedule
                WHERE status = 'pending' 
                AND scheduled_at <= NOW()
                ORDER BY scheduled_at
                LIMIT 50
            """)
            
            return [dict(row) for row in cursor.fetchall()]
            
        finally:
            cursor.close()
            conn.close()
            
            
            
    
    def _mark_initiation_sent(self, initiation_id: int, message: str):
        """Отметка инициации как отправленной"""
        
        from ltm_database import get_connection, add_message_to_history
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            # Получаем user_id из initiation_schedule
            cursor.execute("""
                SELECT user_id, initiation_type 
                FROM initiation_schedule 
                WHERE id = %s
            """, (initiation_id,))
            
            result = cursor.fetchone()
            if not result:
                logger.error(f"Initiation {initiation_id} not found")
                return
                
            user_id = result['user_id']
            initiation_type = result['initiation_type']
            
            # Обновляем статус
            cursor.execute("""
                UPDATE initiation_schedule 
                SET status = 'sent', sent_at = NOW()
                WHERE id = %s
            """, (initiation_id,))
            
            # Логируем в initiation_logs
            cursor.execute("""
                INSERT INTO initiation_logs 
                (user_id, initiation_id, message_content, initiation_type, created_at)
                VALUES (%s, %s, %s, %s, NOW())
            """, (user_id, initiation_id, message, initiation_type))
            
            conn.commit()
            
            # КРИТИЧЕСКИ ВАЖНО: Записываем в history!
            logger.info(f"Добавляем проактивное сообщение в history для user {user_id}")
            add_message_to_history(
                user_id=user_id,
                role="assistant",
                content=message,
                bot_mode="proactive"  # Специальная метка для проактивных сообщений
            )
            
            logger.info(f"✅ Проактивное сообщение записано в history")
            
        except Exception as e:
            logger.error(f"Error marking initiation as sent: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()
            
            
            
    
    def _mark_initiation_failed(self, initiation_id: int, error: str):
        """Отметка инициации как неудачной"""
        
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE initiation_schedule 
                SET status = 'failed', error_message = %s
                WHERE id = %s
            """, (error, initiation_id))
            
            conn.commit()
            
        finally:
            cursor.close()
            conn.close()
    
    def _get_today_initiation_count(self, user_id: int) -> int:
        """Подсчет инициаций за сегодня"""
        
        from ltm_database import get_connection, utc_now
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            # Используем сравнение дат через выражение
            today_start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_start = today_start + timedelta(days=1)
            
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM initiation_schedule
                WHERE user_id = %s 
                AND scheduled_at >= %s
                AND scheduled_at < %s
                AND status IN ('pending', 'sent')
            """, (user_id, today_start, tomorrow_start))
            
            return cursor.fetchone()['count']
            
        finally:
            cursor.close()
            conn.close()
    
    def _get_last_user_activity(self, user_id: int) -> Optional[datetime]:
        """Получение времени последней активности пользователя"""
        
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT MAX(created_at) as last_activity
                FROM history
                WHERE user_id = %s AND role = 'user'
            """, (user_id,))
            
            result = cursor.fetchone()
            return result['last_activity'] if result else None
            
        finally:
            cursor.close()
            conn.close()
    
    async def _is_in_silence_mode(self, user_id: int) -> bool:
        """Проверка режима молчания (Adaptive Silence)"""
    
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
    
        try:
            # Проверяем последние инициации
            cursor.execute("""
                SELECT COUNT(*) as ignored_count
                FROM initiation_logs
                WHERE user_id = %s 
                AND created_at > NOW() - INTERVAL '7 days'
                AND user_responded = FALSE
            """, (user_id,))
        
            ignored_count = cursor.fetchone()['ignored_count']
        
            if ignored_count >= SILENCE_AFTER_IGNORED_COUNT:
                return True
        
            # Проверяем короткие ответы
            cursor.execute("""
                SELECT AVG(user_response_length) as avg_length
                FROM initiation_logs
                WHERE user_id = %s 
                AND created_at > NOW() - INTERVAL '7 days'
                AND user_responded = TRUE
            """, (user_id,))
        
            result = cursor.fetchone()
            if result and result['avg_length']:
                if result['avg_length'] < SILENCE_MIN_RESPONSE_LENGTH:
                    return True
        
            # Проверяем признаки стресса
            cursor.execute("""
                SELECT emotion_primary
                FROM history
                WHERE user_id = %s 
                AND role = 'user'
                AND created_at > NOW() - INTERVAL '24 hours'
                ORDER BY created_at DESC
                LIMIT 5
            """, (user_id,))
        
            recent_emotions = [row['emotion_primary'] for row in cursor.fetchall()]
            stress_emotions = ['sadness', 'anger', 'fear']
            stress_count = sum(1 for e in recent_emotions if e in stress_emotions)
        
            if stress_count >= 3:
                return True
        
            return False
        
        finally:
            cursor.close()
            conn.close()
    
    def _get_last_initiation_time(self, user_id: int) -> Optional[datetime]:
        """Получение времени последней инициации"""
        
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT MAX(scheduled_at) as last_time
                FROM initiation_schedule
                WHERE user_id = %s AND status IN ('sent', 'pending')
            """, (user_id,))
            
            result = cursor.fetchone()
            return result['last_time'] if result else None
            
        finally:
            cursor.close()
            conn.close()
    
    def _get_recent_ltm_memories(self, user_id: int, days: int = 7) -> List[Dict]:
        """Получение воспоминаний из LTM за последние N дней"""
        
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT * FROM long_term_memory
                WHERE user_id = %s 
                AND created_at > NOW() - INTERVAL '%s days'
                ORDER BY importance_score DESC, created_at DESC
            """, (user_id, days))
            
            return [dict(row) for row in cursor.fetchall()]
            
        finally:
            cursor.close()
            conn.close()
    
    def _analyze_emotion_trajectory(self, history: List[Dict]) -> Dict:
        """Анализ эмоциональной траектории пользователя"""
        
        if not history:
            return {'current_emotion': 'neutral', 'trend': 'stable', 'needs_support': False}
        
        # Собираем эмоции пользователя
        user_emotions = []
        for msg in history:
            if msg['role'] == 'user' and msg.get('emotion_primary'):
                user_emotions.append({
                    'emotion': msg['emotion_primary'],
                    'confidence': msg.get('emotion_confidence', 0.5)
                })
        
        if not user_emotions:
            return {'current_emotion': 'neutral', 'trend': 'stable', 'needs_support': False}
        
        # Текущая эмоция
        current_emotion = user_emotions[0]['emotion']
        
        # Анализ тренда (последние 10 сообщений)
        recent_emotions = user_emotions[:10]
        
        negative_emotions = ['sadness', 'anger', 'fear', 'disgust']
        positive_emotions = ['joy', 'surprise', 'love']
        
        negative_count = sum(1 for e in recent_emotions if e['emotion'] in negative_emotions)
        positive_count = sum(1 for e in recent_emotions if e['emotion'] in positive_emotions)
        
        # Определяем тренд
        if negative_count > positive_count * 2:
            trend = 'declining'
            needs_support = True
        elif positive_count > negative_count * 2:
            trend = 'improving'
            needs_support = False
        else:
            trend = 'stable'
            needs_support = negative_count >= 3
        
        return {
            'current_emotion': current_emotion,
            'trend': trend,
            'needs_support': needs_support,
            'recent_emotions': [e['emotion'] for e in recent_emotions[:5]]
        }
    
    def _extract_main_topic(self, text: str) -> str:
        """Извлечение основной темы из текста"""
        
        # Простая эвристика - берем существительные и ключевые слова
        import re
        
        # Удаляем знаки препинания
        text = re.sub(r'[^\w\s]', ' ', text.lower())
        
        # Ключевые слова для разных тем
        topics = {
            'литература': ['книга', 'рассказ', 'роман', 'писатель', 'павич', 'борхес'],
            'философия': ['смысл', 'жизнь', 'время', 'бытие', 'сознание'],
            'творчество': ['писать', 'создать', 'идея', 'проект', 'творить'],
            'эмоции': ['чувство', 'радость', 'грусть', 'любовь', 'страх'],
            'история': ['прошлое', 'история', 'память', 'воспоминание'],
            'магия': ['магия', 'мистика', 'тайна', 'символ', 'знак']
        }
        
        # Ищем совпадения
        for topic, keywords in topics.items():
            if any(keyword in text for keyword in keywords):
                return topic
        
        # Если не нашли - берем первые значимые слова
        words = text.split()
        significant_words = [w for w in words if len(w) > 4][:3]
        
        return ' '.join(significant_words) if significant_words else 'разговор'
    
    def _calculate_semantic_similarity(self, text1: str, text2: str) -> float:
        """Простой расчет семантической схожести текстов"""
        
        # Токенизация
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        # Удаляем стоп-слова
        stop_words = {'и', 'в', 'на', 'с', 'по', 'для', 'что', 'как', 'это', 'то', 'а', 'но'}
        words1 = words1 - stop_words
        words2 = words2 - stop_words
        
        # Коэффициент Жаккара
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1.intersection(words2))
        union = len(words1.union(words2))
        
        return intersection / union if union > 0 else 0.0
    
    def _extract_shared_concepts(self, mem1: Dict, mem2: Dict) -> List[str]:
        """Извлечение общих концепций из двух воспоминаний"""
        
        text1 = mem1['user_message'] + ' ' + mem1['bot_response']
        text2 = mem2['user_message'] + ' ' + mem2['bot_response']
        
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        # Находим общие значимые слова
        common = words1.intersection(words2)
        
        # Фильтруем
        significant = [w for w in common if len(w) > 4]
        
        return significant[:5]
    
    def _get_memories_by_ids(self, memory_ids: List[int]) -> List[Dict]:
        """Получение воспоминаний по ID"""
        
        if not memory_ids:
            return []
        
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT * FROM long_term_memory
                WHERE id = ANY(%s)
            """, (memory_ids,))
            
            return [dict(row) for row in cursor.fetchall()]
            
        finally:
            cursor.close()
            conn.close()
    
    def _get_last_initiation_type(self, user_id: int) -> Optional[str]:
        """Получение типа последней инициации"""
        
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT initiation_type
                FROM initiation_schedule
                WHERE user_id = %s AND status = 'sent'
                ORDER BY sent_at DESC
                LIMIT 1
            """, (user_id,))
            
            result = cursor.fetchone()
            return result['initiation_type'] if result else None
            
        finally:
            cursor.close()
            conn.close()
    
    def _get_user_timezone(self, user_id: int) -> str:
        """Получение часового пояса пользователя"""
        
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
            return result['timezone'] if result else 'Europe/Amsterdam'  # Default
            
        finally:
            cursor.close()
            conn.close()
    
    def _get_user_activity_hours(self, user_id: int) -> Dict[int, float]:
        """Анализ часов активности пользователя"""
    
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
    
        try:
            # Получаем активность за последние 30 дней
            cursor.execute("""
                SELECT EXTRACT(HOUR FROM h.created_at AT TIME ZONE COALESCE(ups.timezone, 'Europe/Amsterdam')) as hour,
                       COUNT(*) as message_count
                FROM history h
                LEFT JOIN user_proactivity_settings ups ON h.user_id = ups.user_id
                    WHERE h.user_id = %s 
                AND h.role = 'user'
                AND h.created_at > NOW() - INTERVAL '30 days'
                GROUP BY hour
            """, (user_id,))
        
            activity = {}
            total = 0
        
            for row in cursor.fetchall():
                hour = int(row['hour'])
                count = row['message_count']
                activity[hour] = count
                total += count
        
            # Нормализуем в вероятности
            if total > 0:
                for hour in range(24):
                    activity[hour] = activity.get(hour, 0) / total
        
            return activity
        
        finally:
            cursor.close()
            conn.close()

    # === Методы для обработки ответов ===
    
    def process_user_response(self, user_id: int, message: str, emotion: str = None, 
                            emotion_confidence: float = None) -> bool:
        """Обработка ответа пользователя на инициацию"""
        
        from ltm_database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            # Ищем последнюю отправленную инициацию
            cursor.execute("""
                SELECT il.id, il.initiation_id, il.created_at
                FROM initiation_logs il
                WHERE il.user_id = %s 
                AND il.user_responded = FALSE
                AND il.created_at > NOW() - INTERVAL '24 hours'
                ORDER BY il.created_at DESC
                LIMIT 1
            """, (user_id,))
            
            log_entry = cursor.fetchone()
            
            if not log_entry:
                return False  # Нет недавних инициаций
            
            # Рассчитываем время ответа
            response_time = (datetime.now(timezone.utc) - log_entry['created_at']).total_seconds() / 60
            
            # Анализируем sentiment (простая эвристика)
            sentiment = self._analyze_response_sentiment(message, emotion)
            
            # Обновляем лог
            cursor.execute("""
                UPDATE initiation_logs
                SET user_response = %s,
                    user_response_emotion = %s,
                    user_response_sentiment = %s,
                    user_response_length = %s,
                    user_responded = TRUE,
                    response_time_minutes = %s
                WHERE id = %s
            """, (
                message[:1000],  # Ограничиваем длину
                emotion,
                sentiment,
                len(message),
                int(response_time),
                log_entry['id']
            ))
            
            # Обновляем статус в schedule
            if log_entry['initiation_id']:
                cursor.execute("""
                    UPDATE initiation_schedule
                    SET user_response_received = TRUE
                    WHERE id = %s
                """, (log_entry['initiation_id'],))
            
            conn.commit()
            
            logger.info(f"Processed response from user {user_id} to initiation {log_entry['id']}")
            return True
            
        except Exception as e:
            logger.error(f"Error processing user response: {e}")
            conn.rollback()
            return False
            
        finally:
            cursor.close()
            conn.close()
    
    def _analyze_response_sentiment(self, message: str, emotion: str = None) -> float:
        """Анализ эмоционального тона ответа"""
        
        # Базовый sentiment по эмоции
        emotion_sentiment = {
            'joy': 0.8,
            'love': 0.9,
            'surprise': 0.6,
            'neutral': 0.0,
            'sadness': -0.6,
            'anger': -0.8,
            'fear': -0.7,
            'disgust': -0.9
        }
        
        base_sentiment = emotion_sentiment.get(emotion, 0.0) if emotion else 0.0
        
        # Корректировка по тексту
        positive_words = ['спасибо', 'интересно', 'здорово', 'отлично', 'да', 'конечно', 'хорошо']
        negative_words = ['нет', 'не надо', 'отстань', 'устал', 'занят', 'потом', 'неинтересно']
        
        text_lower = message.lower()
        
        positive_count = sum(1 for word in positive_words if word in text_lower)
        negative_count = sum(1 for word in negative_words if word in text_lower)
        
        text_sentiment = (positive_count - negative_count) * 0.2
        
        # Финальный sentiment
        final_sentiment = base_sentiment * 0.7 + text_sentiment * 0.3
        
        return max(-1.0, min(1.0, final_sentiment))