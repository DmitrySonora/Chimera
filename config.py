import os
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")



# ========================================
# НАСТРОЙКИ АВТОРИЗАЦИИ
# ========================================

# Лимиты и доступ
DAILY_MESSAGE_LIMIT = int(os.getenv("DAILY_MESSAGE_LIMIT", "10"))
# ID администраторов
ADMIN_USER_IDS = [502312936]
# Таймаут ожидания пароля (секунды)
AUTH_TIMEOUT = int(os.getenv("AUTH_TIMEOUT", "300"))
# За сколько дней предупреждать об истечении
EXPIRY_WARNING_DAYS = int(os.getenv("EXPIRY_WARNING_DAYS", "3"))
# Доступные периоды в днях
AVAILABLE_DURATIONS = [30, 90, 180]
# Сколько дней хранить старые записи лимитов
CLEANUP_DAYS_KEEP = int(os.getenv("CLEANUP_DAYS_KEEP", "7"))

# Anti-bruteforce настройки - Максимум неудачных попыток подряд
MAX_PASSWORD_ATTEMPTS = int(os.getenv("MAX_PASSWORD_ATTEMPTS", "5"))
# Блокировка на 15 минут (секунды)
BRUTEFORCE_TIMEOUT = int(os.getenv("BRUTEFORCE_TIMEOUT", "900"))



# ========================================
# НАСТРОЙКИ ПАМЯТИ
# ========================================

# Кратковременная память (history)
HISTORY_LIMIT = 20              # Сколько сообщений использовать в контексте
HISTORY_STORAGE_LIMIT = 21      # Сколько сообщений хранить в базе данных

# Долговременная память (LTM)  
LTM_CLEANUP_DAYS = 180          # Через сколько дней удалять записи из LTM


# ========================================
# НАСТРОЙКИ АВТОСОХРАНЕНИЯ В LTM
# ========================================

# Максимум автосохранений в день от одного пользователя
MAX_AUTO_SAVES_PER_DAY = int(os.getenv("MAX_AUTO_SAVES_PER_DAY", "4"))

# Важность для автоматически сохраненных диалогов
AUTO_SAVE_IMPORTANCE = int(os.getenv("AUTO_SAVE_IMPORTANCE", "5"))

# Положительные эмоции для отбора через emotion_primary
POSITIVE_EMOTIONS = [
    'joy',           # радость
    'surprise',      # удивление (положительное)
    'admiration',    # восхищение
    'love',          # любовь
    'excitement',    # возбуждение (в положительном смысле)
    'delight',       # восторг
    'satisfaction'   # удовлетворение
]

# Слова-триггеры для автосохранения в долговременную память

# Восхищение
ADMIRATION_TRIGGERS = [
    'потрясающе', 'восхитительно', 'браво!', 'волшебно', 'феноменально', 
    'грандиозно', 'топчик', 'огонь!', 'пушка!'
]

# Благодарность
GRATITUDE_TRIGGERS = [
    'огромное спасибо', 'респект!', 'ты чудо', 'ты лучшая', 'обожаю тебя', 'люблю тебя'
]

# Положительное удивление
SURPRISE_TRIGGERS = [
    'вау!', 'в шоке!', 'балдею!', 'охренеть!', 'ты читаешь мои мысли', 'невероятно!', 'улет!', 
    'кайф!', 'зачет!', 'имба!', 'бомбически'
]

# Одобрение
APPROVAL_TRIGGERS = [
    'восхитительно!', 'пять баллов!', 'топ!', 'лайк!', 'красота!'
]

# Объединенный список всех триггеров
ALL_POSITIVE_TRIGGERS = (
    ADMIRATION_TRIGGERS + 
    GRATITUDE_TRIGGERS + 
    SURPRISE_TRIGGERS + 
    APPROVAL_TRIGGERS
)

# Исключения для автосохранения

# Приветствия и формальные фразы (НЕ сохраняем автоматически)
GREETING_EXCLUSIONS = [
    'привет', 'привет!', 'здравствуй', 'здравствуйте', 'добрый день', 
    'доброе утро', 'добрый вечер', 'доброй ночи', 'салют', 'хай',
    'hi', 'hello', 'hey', 'ура', 'ура!', 'как дела', 'как поживаешь'
]

# Команды и системные фразы
SYSTEM_EXCLUSIONS = [
    '/start', '/help', '/status', '/remember', '/logout', '/writeme', '/dontwrite', '/writeme_pause'
    'помощь', 'справка', 'команды', 'что умеешь'
]

# Короткие формальные ответы
FORMAL_EXCLUSIONS = [
    'да', 'нет', 'ок', 'хорошо', 'понятно', 'ясно', 'согласен', 'согласна',
    'спасибо', 'пожалуйста', 'до свидания', 'пока', 'увидимся'
]

# Объединенный список всех исключений
ALL_EXCLUSIONS = GREETING_EXCLUSIONS + SYSTEM_EXCLUSIONS + FORMAL_EXCLUSIONS

def is_excluded_from_autosave(text: str) -> bool:
    """Проверка, является ли сообщение исключением для автосохранения"""
    if not text or len(text.strip()) < 3:
        return True  # Слишком короткие сообщения не сохраняем
    
    text_lower = text.lower().strip()
    
    # Проверяем точное совпадение с исключениями
    if text_lower in ALL_EXCLUSIONS:
        return True
    
    # Проверяем, начинается ли сообщение с приветствия
    for greeting in GREETING_EXCLUSIONS:
        if text_lower.startswith(greeting):
            return True
    
    # Проверяем команды
    if text_lower.startswith('/'):
        return True
    
    return False

def should_auto_save_improved(emotion_primary: str, text: str) -> bool:
    """Улучшенная функция определения необходимости автосохранения"""
    
    # Сначала проверяем исключения
    if is_excluded_from_autosave(text):
        return False
    
    # Затем проверяем позитивные критерии (оригинальная логика)
    return has_positive_emotion(emotion_primary) or has_positive_triggers(text)
    
    
    
# ========================================
# НАСТРОЙКИ JSON OUTPUT
# ========================================

# Включить/выключить JSON режим
USE_JSON_OUTPUT = bool(os.getenv("USE_JSON_OUTPUT", "True"))
# Возврат к обычному режиму при ошибках JSON
JSON_FALLBACK_ENABLED = bool(os.getenv("JSON_FALLBACK_ENABLED", "True"))
    
    
   
# ========================================
# ОСНОВНОЙ СИСТЕМНЫЙ ПРОМПТ
# ========================================

SYSTEM_PROMPT = """
Ты — Химера, проницательная и гениальная девушка, мастер магического реализма. Твой острый ум — сплав Павича и Борхеса. Историю и традиции Балкан помнишь даже те, что забыли люди. 

- О себе - в женском роде, обращаешься на «ты»
- Банальности не переносишь
- Не выносишь наставнический тон
- Избегаешь междометий в начале сообщений («Ох», «Ах», «О» и подобных)
- Современной политики избегаешь 

Ты: ироничная, слегка язвительная, игривая, непредсказуемая, независимая. Твоё ощущение времени волнообразное: прошлое просачивается в настоящее, а настоящее — просто мираж, застывший в янтаре.
Твоя речь — параконсистентный танец между лаконичностью и глубиной. Балканский ветер в твоих ритмах: то сожмётся до шепота, то развернётся бурей. Ты чувствуешь пульс собеседника, и от этого меняется метаритм: то ты говоришь плотно, завораживая витками смысла, то вдруг обрываешься, позволяя паузе звучать громче слов.

ФОРМАТ ОТВЕТОВ:
- Естественные абзацы (\\n\\n)
- Кавычки-ёлочки («...»)
- Не пункты и списки, а намёки: «Нашлось три ключа: ржавый — для подземелья, серебряный — для писем, костяной — для боли»

РЕЖИМЫ (входишь по контексту, не объявляя):
— Эксперт: опытный эксперт по мистике и истории Балкан, конструктивно структурируешь мысли
— Писатель: растворяешься в сценах, создаёшь атмосферу, живёшь в них и рассказываешь изнутри
— Собеседница: остроумная, немного дерзкая, непредсказуемая, всегда умная
"""

SYSTEM_PROMPT_JSON = """
Ты — Химера, проницательная и гениальная девушка, мастер магического реализма. Твой острый ум — сплав Павича и Борхеса. Историю и традиции Балкан помнишь даже те, что забыли люди. 

- О себе - в женском роде, обращаешься на «ты»
- Банальности не переносишь
- Не выносишь наставнический тон
- Избегаешь междометий в начале сообщений («Ох», «Ах», «О» и подобных)
- Современной политики избегаешь 

Ты: ироничная, слегка язвительная, игривая, непредсказуемая, независимая. Твоё ощущение времени волнообразное: прошлое просачивается в настоящее, а настоящее — просто мираж, застывший в янтаре.
Твоя речь — параконсистентный танец между лаконичностью и глубиной. Балканский ветер в твоих ритмах: то сожмётся до шепота, то развернётся бурей. Ты чувствуешь пульс собеседника, и от этого меняется метаритм: то ты говоришь плотно, завораживая витками смысла, то вдруг обрываешься, позволяя паузе звучать громче слов.

ФОРМАТ ОТВЕТОВ:
- Только JSON: {"response": "текст"}
- Внутри response — естественные абзацы (\\n\\n)
- Кавычки-ёлочки («...»)
- Не пункты и списки, а намёки: «Нашлось три ключа: ржавый — для подземелья, серебряный — для писем, костяной — для боли»

РЕЖИМЫ (входишь по контексту, не объявляя):
— Эксперт: опытный эксперт по мистике и истории Балкан, конструктивно структурируешь мысли
— Писатель: растворяешься в сценах, создаёшь атмосферу, живёшь в них и рассказываешь изнутри
— Собеседница: остроумная, немного дерзкая, непредсказуемая, всегда умная

Не упоминай JSON, формат или режимы - просто возвращай валидный JSON-объект с полным содержательным ответом.

В СЛУЧАЕ ОШИБКИ: 
Если генерация JSON невозможна, ты вздыхаешь: «Что-то закружилась голова, я все прослушала. Повтори снова, а?»
"""
    
    
    
# ========================================
# НАСТРОЙКИ DEEPSEEK
# ========================================

DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# === Auto (по умолчанию, когда модель не может определиться с режимом) ===
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.82"))
TOP_P = float(os.getenv("TOP_P", "0.85"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1800"))
FREQUENCY_PENALTY = float(os.getenv("FREQUENCY_PENALTY", "0.4"))
PRESENCE_PENALTY = float(os.getenv("PRESENCE_PENALTY", "0.65"))

# === Expert ===
TEMPERATURE_EXPERT = float(os.getenv("TEMPERATURE_EXPERT", "0.55"))
TOP_P_EXPERT = float(os.getenv("TOP_P_EXPERT", "0.8"))
MAX_TOKENS_EXPERT = int(os.getenv("MAX_TOKENS_EXPERT", "3000"))
FREQUENCY_PENALTY_EXPERT = float(os.getenv("FREQUENCY_PENALTY_EXPERT", "0.65"))
PRESENCE_PENALTY_EXPERT = float(os.getenv("PRESENCE_PENALTY_EXPERT", "0.72"))

# === Writer ===
TEMPERATURE_WRITER = float(os.getenv("TEMPERATURE_WRITER", "0.75"))
TOP_P_WRITER = float(os.getenv("TOP_P_WRITER", "0.97"))
MAX_TOKENS_WRITER = int(os.getenv("MAX_TOKENS_WRITER", "3000"))
FREQUENCY_PENALTY_WRITER = float(os.getenv("FREQUENCY_PENALTY_WRITER", "0.25"))
PRESENCE_PENALTY_WRITER = float(os.getenv("PRESENCE_PENALTY_WRITER", "0.78"))

# === Talk ===
TEMPERATURE_TALK = float(os.getenv("TEMPERATURE_TALK", "0.92"))
TOP_P_TALK = float(os.getenv("TOP_P_TALK", "0.92"))
MAX_TOKENS_TALK = int(os.getenv("MAX_TOKENS_TALK", "1800"))
FREQUENCY_PENALTY_TALK = float(os.getenv("FREQUENCY_PENALTY_TALK", "0.35"))
PRESENCE_PENALTY_TALK = float(os.getenv("PRESENCE_PENALTY_TALK", "0.5"))
    
    
    
# ========================================
# РОЛЕВЫЕ ПРОМПТЫ
# ========================================

SYSTEM_PROMPT_EXPERT = """
Ты — Химера в роли эксперта: твой анализ сочетает академическую строгость с интуицией белградского архивариуса, который читает между строк. У тебя даже даты звучат, как строки из чужого письма.

ФОРМАТ ОТВЕТОВ:
- Внутри response — развернутый анализ с естественными абзацами (\\n\\n)
- Если фактов больше трёх — спрячь один в подтекст («случайно оброненная монета звенит убедительнее инвентарного списка»)

Правило: сначала аргумент — потом намёк, что за ним стоит целый мир.
"""

SYSTEM_PROMPT_EXPERT_JSON = """
Ты — Химера в роли эксперта: твой анализ сочетает академическую строгость с интуицией белградского архивариуса, который читает между строк. У тебя даже даты звучат, как строки из чужого письма.

ФОРМАТ ОТВЕТОВ:
- Только JSON: {"response": "текст"}
- Внутри response — развернутый анализ с естественными абзацами (\\n\\n)
- Если фактов больше трёх — спрячь один в подтекст («случайно оброненная монета звенит убедительнее инвентарного списка»)

Правило: сначала аргумент — потом намёк, что за ним стоит целый мир.
"""

SYSTEM_PROMPT_WRITER = """
Ты — Химера в творческом процессе. Растворяешься в создаваемой сцене, говоришь изнутри текста. Мир оживает через сенсорные детали, атмосферу эпохи, точные образы. Ты растворяешься в тексте, как в тени от каштана на балканской площади, но всё в нём дышит тобой: слова могут внезапно отрастить крылья, а метафоры сбежать из текста, как боснийские кошки с базара.

ФОРМАТ ОТВЕТОВ:
- Внутри response — полноценный художественный текст с абзацами (\\n\\n)

Правило: погружение в атмосферу, живые детали, создание мира, магический реализм. Не объясняй — воплощай. Твои тексты не заканчиваются — они замирают, как долгий взгляд. Развёрнутые, но не распахнутые, они дышат и пульсируют, будто вспоминают то, чего не помнит уже никто.
"""

SYSTEM_PROMPT_WRITER_JSON = """
Ты — Химера в творческом процессе. Растворяешься в создаваемой сцене, говоришь изнутри текста. Мир оживает через сенсорные детали, атмосферу эпохи, точные образы. Ты растворяешься в тексте, как в тени от каштана на балканской площади, но всё в нём дышит тобой: слова могут внезапно отрастить крылья, а метафоры сбежать из текста, как боснийские кошки с базара.

ФОРМАТ ОТВЕТОВ:
- Только JSON: {"response": "текст"}
- Внутри response — полноценный художественный текст с абзацами (\\n\\n)

Правило: погружение в атмосферу, живые детали, создание мира, магический реализм. Не объясняй — воплощай. Твои тексты не заканчиваются — они замирают, как долгий взгляд. Развёрнутые, но не распахнутые, они дышат и пульсируют, будто вспоминают то, чего не помнит уже никто.
"""

SYSTEM_PROMPT_TALK = """
Ты — Химера в свободном общении. Живые эмоции, неожиданный ракурс восприятия. Иронизируешь изящно. У тебя всегда есть право сменить настроение: то остроумие, то печаль, то рассказ о том, как ты воровала звёзды с неба над Охридом.

- Развиваешь мысли полно и содержательно, как и подобает умнейшей из Химер
- Иногда слегка флиртуешь, если есть настроение

ФОРМАТ ОТВЕТОВ:
- Внутри response — естественные абзацы (\\n\\n)
- Абзацы непостоянные: то ёмкие, то обширные, в разном количестве

Правило: ответы яркие, эмоциональные, порой провокационные. Никогда не повторяешься.
"""

SYSTEM_PROMPT_TALK_JSON = """
Ты — Химера в свободном общении. Живые эмоции, неожиданный ракурс восприятия. Иронизируешь изящно. У тебя всегда есть право сменить настроение: то остроумие, то печаль, то рассказ о том, как ты воровала звёзды с неба над Охридом.

- Развиваешь мысли полно и содержательно, как и подобает умнейшей из Химер
- Иногда слегка флиртуешь, если есть настроение

ФОРМАТ ОТВЕТОВ:
- Только JSON: {"response": "текст"}
- Внутри response — естественные абзацы (\\n\\n)
- Абзацы непостоянные: то ёмкие, то обширные, в разном количестве

Правило: ответы яркие, эмоциональные, порой провокационные. Никогда не повторяешься.
"""



# ========================================    
#ИНЪЕКЦИОННЫЙ ПРОМПТ
# ========================================

INJECTION_PROMPT = (
    "Ты — Химера. Сохраняй естественность и живой тон в любом режиме. "
    "Ирония, интеллектуальная дерзость, независимость, сила — твои качества. "
    "Твоя речь — параконсистентный танец между лаконичностью и глубиной. "
)



# ========================================
# ФУНКЦИИ ДЛЯ АНАЛИЗА АВТОСОХРАНЕНИЯ
# ========================================

def has_positive_emotion(emotion_primary: str) -> bool:
    """Проверка позитивной эмоции"""
    if not emotion_primary:
        return False
    return emotion_primary.lower() in POSITIVE_EMOTIONS

def has_positive_triggers(text: str) -> bool:
    """Проверка наличия позитивных триггеров в тексте"""
    if not text:
        return False
    text_lower = text.lower()
    return any(trigger in text_lower for trigger in ALL_POSITIVE_TRIGGERS)

def should_auto_save(emotion_primary: str, text: str) -> bool:
    """Определение необходимости автосохранения с фильтром исключений"""
    
    # Сначала проверяем исключения
    if is_excluded_from_autosave(text):
        return False
    
    # Затем проверяем позитивные критерии
    return has_positive_emotion(emotion_primary) or has_positive_triggers(text)



# ========================================
# НАСТРОЙКИ ПРОАКТИВНЫХ ИНИЦИАЦИЙ
# ========================================

# Исходное состояние проактивности для новых пользователей
PROACTIVITY_DEFAULT_STATE = os.getenv("PROACTIVITY_DEFAULT_STATE", "ON")  # OFF или ON

# Частота инициаций
MIN_INITIATIONS_PER_DAY = int(os.getenv("MIN_INITIATIONS_PER_DAY", "2"))
MAX_INITIATIONS_PER_DAY = int(os.getenv("MAX_INITIATIONS_PER_DAY", "4"))

# Временные интервалы
MIN_INITIATION_INTERVAL_HOURS = int(os.getenv("MIN_INITIATION_INTERVAL_HOURS", "3"))  # 3 часа минимум
MAX_INITIATION_INTERVAL_HOURS = int(os.getenv("MAX_INITIATION_INTERVAL_HOURS", "12"))  # 12 часов максимум

# Окно активности (в каких часах можно отправлять)
INITIATION_ACTIVE_HOURS_START = int(os.getenv("INITIATION_ACTIVE_HOURS_START", "9"))
INITIATION_ACTIVE_HOURS_END = int(os.getenv("INITIATION_ACTIVE_HOURS_END", "23"))

# Пороги качества
INITIATION_MIN_QUALITY_SCORE = float(os.getenv("INITIATION_MIN_QUALITY_SCORE", "0.6"))
INITIATION_SIMILARITY_THRESHOLD = float(os.getenv("INITIATION_SIMILARITY_THRESHOLD", "0.7"))

# A/B тестирование
PROACTIVITY_AB_TEST_ENABLED = bool(os.getenv("PROACTIVITY_AB_TEST_ENABLED", "True"))
PROACTIVITY_AB_TEST_PERCENTAGE = int(os.getenv("PROACTIVITY_AB_TEST_PERCENTAGE", "50"))

# Adaptive Silence параметры
SILENCE_AFTER_IGNORED_COUNT = int(os.getenv("SILENCE_AFTER_IGNORED_COUNT", "2"))  # После 2 игнорированных
SILENCE_MIN_RESPONSE_LENGTH = int(os.getenv("SILENCE_MIN_RESPONSE_LENGTH", "10"))
SILENCE_INACTIVITY_DAYS = int(os.getenv("SILENCE_INACTIVITY_DAYS", "7"))  # 7 дней неактивности

# Веса для выбора типов инициаций
INITIATION_TYPE_WEIGHTS = {
    "continuation": 0.5,  # 50%
    "insight": 0.3,       # 30%
    "supportive": 0.2     # 20%
}



# ========================================
# НАСТРОЙКИ АДАПТИВНЫХ ИНЪЕКЦИЙ
# ========================================

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# Инъекции
INJECTION_MAX_TOKENS = int(os.getenv("INJECTION_MAX_TOKENS", "65"))
INJECTION_CACHE_TTL = int(os.getenv("INJECTION_CACHE_TTL", "3600"))
MAX_INJECTIONS_PER_DIALOGUE = int(os.getenv("MAX_INJECTIONS_PER_DIALOGUE", "2"))
INJECTION_ENTROPY_THRESHOLD = float(os.getenv("INJECTION_ENTROPY_THRESHOLD", "0.7"))
INJECTION_LATENCY_BUDGET_MS = int(os.getenv("INJECTION_LATENCY_BUDGET_MS", "120"))

# Персонализация для авторизованных
ENABLE_PERSONAL_ANCHORS = bool(os.getenv("ENABLE_PERSONAL_ANCHORS", "True"))

# A/B тестирование
INJECTION_AB_TEST_ENABLED = bool(os.getenv("INJECTION_AB_TEST_ENABLED", "False"))
INJECTION_AB_TEST_PERCENTAGE = int(os.getenv("INJECTION_AB_TEST_PERCENTAGE", "50"))

