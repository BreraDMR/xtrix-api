import sys
import os
import argparse
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.api.core.notification_tasks import run_all_notification_tasks
from app.api.core.logger import logger
def main():
    parser = argparse.ArgumentParser(description='Run notification tasks')
    parser.add_argument(
        '--channel',
        type=str,
        default='email',
        choices=['email', 'sms', 'whatsapp', 'log'],
        help='Notification channel (default: email)'
    )
    args = parser.parse_args()
    try:
        logger.info(f"Starting notification tasks (channel: {args.channel})...")
        results = run_all_notification_tasks(channel=args.channel)
        logger.info(f"Notification tasks completed: {results}")
        return 0
    except Exception as e:
        logger.error(f"Error running notification tasks: {e}", exc_info=True)
        return 1
if __name__ == "__main__":
    exit(main())
