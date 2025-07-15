import torch
from transformers import pipeline

# Инициализация пайплайна с оптимизациями для Apple Silicon
emotion_classifier = pipeline(
    task="text-classification",
    model="./eq_models/rubert-tiny2-cedr-emotion-detection",  # Локальный путь
    device="mps" if torch.backends.mps.is_available() else None,  # Автовыбор устройства
    torch_dtype=torch.float16,  # Оптимизация памяти
    top_k=None,  # Возвращает все эмоции с их вероятностями
    batch_size=4  # Оптимально для M1/M2/M3/M4
)

# Проверка устройства
if torch.backends.mps.is_available():
    print("✅ Модель работает на Apple Metal (MPS)")
else:
    print("⚠️ MPS не доступен, используется CPU")

def get_emotion(text):
    """
    Анализирует эмоцию в русском тексте.
    Возвращает tuple: (основная эмоция, confidence)
    """
    if not text.strip():
        return "neutral", 1.0  # На пустой/пробельный текст — по умолчанию
    
    try:
        # Используем единственный инициализированный пайплайн
        result = emotion_classifier(text)[0]
        best = max(result, key=lambda x: x['score'])
        return best['label'], float(best['score'])
    except Exception as e:
        print(f"⚠️ Ошибка анализа эмоции: {str(e)}")
        return "error", 0.0  # Возвращаем значение при ошибке

# Пример запуска (можно удалить после теста):
if __name__ == "__main__":
    test_text = "Я сегодня очень рад тебя видеть!"
    emotion, conf = get_emotion(test_text)
    print(f"Эмоция: {emotion}, уверенность: {conf:.2f}")
    
    # Тест пустой строки
    empty_text = "   "
    emotion, conf = get_emotion(empty_text)
    print(f"Пустая строка: {emotion}, уверенность: {conf:.2f}")