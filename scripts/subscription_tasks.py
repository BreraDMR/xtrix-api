import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.api.core.subscription_tasks import run_all_subscription_tasks
from app.api.core.logger import logger
def main():
    try:
        logger.info("Starting subscription tasks...")
        run_all_subscription_tasks()
        logger.info("Subscription tasks completed successfully")
        return 0
    except Exception as e:
        logger.error(f"Error running subscription tasks: {e}", exc_info=True)
        return 1
if __name__ == "__main__":
    exit(main())
