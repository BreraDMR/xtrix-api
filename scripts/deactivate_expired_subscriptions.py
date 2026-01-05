#!/usr/bin/env python3
"""
Скрипт для деактивации истекших подписок.
Можно запускать вручную или настроить как cron задачу.

Использование:
    python scripts/deactivate_expired_subscriptions.py
"""
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.session import SessionLocal
from app.api.core.subscription_tasks import deactivate_expired_subscriptions
from app.api.core.logger import logger


def main():
    """Главная функция для запуска деактивации подписок."""
    try:
        logger.info("Starting deactivation of expired subscriptions...")
        count = deactivate_expired_subscriptions()
        logger.info(f"Successfully deactivated {count} expired subscriptions")
        return 0
    except Exception as e:
        logger.error(f"Error deactivating expired subscriptions: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())

