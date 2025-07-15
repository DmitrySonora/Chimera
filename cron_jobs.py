"""
Cron задачи для проактивной системы - простая реализация без внешних планировщиков
"""
import logging
import asyncio
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("cron_jobs")

# Глобальные переменные
PROACTIVE_ENGINE = None
background_tasks = []

def set_proactive_engine(engine):
    """Установка движка проактивных инициаций"""
    global PROACTIVE_ENGINE
    PROACTIVE_ENGINE = engine
    logger.info("✅ Proactive engine установлен в cron_jobs")

async def run_proactive_scheduler():
    """Запуск планирования инициаций (каждые 30 минут)"""
    
    if PROACTIVE_ENGINE:
        try:
            logger.info("Starting proactive scheduler...")
            await PROACTIVE_ENGINE.check_and_schedule_initiations()
            logger.info("✅ Proactive scheduler completed")
        except Exception as e:
            logger.error(f"❌ Proactive scheduler error: {e}")
            import traceback
            traceback.print_exc()
    else:
        logger.warning("⚠️ PROACTIVE_ENGINE не установлен в scheduler")

async def run_proactive_sender():
    """Запуск отправки инициаций (каждые 10 минут)"""
    
    if PROACTIVE_ENGINE:
        try:
            logger.info("Starting proactive sender...")
            
            # Проверяем наличие telegram_app
            if PROACTIVE_ENGINE.telegram_app is None:
                logger.error("❌ telegram_app не установлен в движке")
                return
            
            # Отправляем запланированные инициации
            await PROACTIVE_ENGINE.send_scheduled_initiations()
            logger.info("✅ Proactive sender completed")
            
        except Exception as e:
            logger.error(f"❌ Proactive sender error: {e}")
            import traceback
            traceback.print_exc()
    else:
        logger.warning("⚠️ PROACTIVE_ENGINE не установлен в sender")

async def cleanup_old_initiations():
    """Очистка старых инициаций (раз в день)"""
    
    from ltm_database import get_connection
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Отменяем инициации старше 48 часов
        cursor.execute("""
            UPDATE initiation_schedule
            SET status = 'cancelled'
            WHERE status = 'pending'
            AND scheduled_at < NOW() - INTERVAL '48 hours'
        """)
        
        cancelled_count = cursor.rowcount
        
        # Удаляем логи старше 30 дней
        cursor.execute("""
            DELETE FROM initiation_logs
            WHERE created_at < NOW() - INTERVAL '30 days'
        """)
        
        deleted_count = cursor.rowcount
        
        conn.commit()
        
        logger.info(f"Cleanup: cancelled {cancelled_count} old initiations, deleted {deleted_count} old logs")
        
    except Exception as e:
        logger.error(f"Error in cleanup: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

async def collect_proactivity_metrics():
    """Сбор метрик проактивности (раз в день)"""
    
    from ltm_database import get_connection
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Собираем основные метрики
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT user_id) as active_users,
                COUNT(*) as total_initiations,
                AVG(CASE WHEN user_responded THEN 1 ELSE 0 END) as response_rate,
                AVG(user_response_length) as avg_response_length,
                AVG(user_response_sentiment) as avg_sentiment
            FROM initiation_logs
            WHERE created_at > NOW() - INTERVAL '24 hours'
        """)
        
        metrics = cursor.fetchone()
        
        if metrics and metrics['active_users'] is not None:
            response_rate = metrics['response_rate'] if metrics['response_rate'] else 0
            avg_length = metrics['avg_response_length'] if metrics['avg_response_length'] else 0
            avg_sentiment = metrics['avg_sentiment'] if metrics['avg_sentiment'] else 0
            
            logger.info(
                f"Daily metrics: "
                f"Active users: {metrics['active_users']}, "
                f"Initiations: {metrics['total_initiations']}, "
                f"Response rate: {response_rate:.2%}, "
                f"Avg response length: {avg_length:.1f}, "
                f"Avg sentiment: {avg_sentiment:.2f}"
            )
        
    except Exception as e:
        logger.error(f"Error collecting metrics: {e}")
    finally:
        cursor.close()
        conn.close()

# Фоновые задачи
async def scheduler_loop():
    """Цикл планировщика - каждые 30 минут"""
    await asyncio.sleep(60)  # Первый запуск через 1 минуту
    while True:
        try:
            await run_proactive_scheduler()
        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}")
        await asyncio.sleep(1800)  # 30 минут

async def sender_loop():
    """Цикл отправщика - каждые 10 минут"""
    await asyncio.sleep(120)  # Первый запуск через 2 минуты
    while True:
        try:
            await run_proactive_sender()
        except Exception as e:
            logger.error(f"Error in sender loop: {e}")
        await asyncio.sleep(600)  # 10 минут

async def cleanup_loop():
    """Цикл очистки - раз в день"""
    while True:
        await asyncio.sleep(86400)  # 24 часа
        try:
            await cleanup_old_initiations()
            await collect_proactivity_metrics()
        except Exception as e:
            logger.error(f"Error in cleanup loop: {e}")

def start_proactive_cron():
    """Запуск фоновых задач"""
    global background_tasks
    
    logger.info("Starting proactive background tasks...")
    
    # Создаем задачи
    background_tasks = [
        asyncio.create_task(scheduler_loop()),
        asyncio.create_task(sender_loop()),
        asyncio.create_task(cleanup_loop())
    ]
    
    logger.info("✅ Proactive background tasks started")

def stop_proactive_cron():
    """Остановка фоновых задач"""
    global background_tasks
    
    logger.info("Stopping proactive background tasks...")
    
    for task in background_tasks:
        task.cancel()
    
    background_tasks = []
    logger.info("✅ Proactive background tasks stopped")