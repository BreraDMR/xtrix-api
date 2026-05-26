import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from app.api.core.config import settings
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
logger = logging.getLogger("xtrix")
logger.setLevel(logging.DEBUG if settings.environment == "development" else logging.INFO)
logger.propagate = False
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG if settings.environment == "development" else logging.INFO)
console_formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)
file_handler = RotatingFileHandler(
    LOG_DIR / "app.log",
    maxBytes=10 * 1024 * 1024,  
    backupCount=5,
    encoding="utf-8",
)
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)
error_handler = RotatingFileHandler(
    LOG_DIR / "errors.log",
    maxBytes=10 * 1024 * 1024,  
    backupCount=5,
    encoding="utf-8",
)
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(file_formatter)
logger.addHandler(error_handler)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("fastapi").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"xtrix.{name}")
