#!/usr/bin/env python3
"""
Скрипт для запуска финансовых задач:
- Архивация финансовых данных за прошлый месяц

Использование:
    python scripts/finance_tasks.py
"""
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.api.core.finance_tasks import check_and_run_monthly_archive
from app.api.core.logger import logger


def main():
    """Главная функция для запуска финансовых задач."""
    try:
        logger.info("Starting finance tasks...")
        count = check_and_run_monthly_archive()
        if count > 0:
            logger.info(f"Finance tasks completed successfully: {count} businesses archived")
        else:
            logger.info("Finance tasks completed: no archive needed")
        return 0
    except Exception as e:
        logger.error(f"Error running finance tasks: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())

