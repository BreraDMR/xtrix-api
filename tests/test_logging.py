import logging
import pytest
from pathlib import Path
from app.api.core.logger import logger, get_logger
def test_logger_exists():
    assert logger is not None
    assert isinstance(logger, logging.Logger)
def test_logger_name():
    assert logger.name == "xtrix"
def test_get_logger():
    test_logger = get_logger("test_module")
    assert test_logger is not None
    assert test_logger.name == "xtrix.test_module"
def test_logger_levels():
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")
    assert True
def test_log_files_exist():
    log_dir = Path("logs")
    assert log_dir.exists() or log_dir.is_dir()
def test_logger_handlers():
    assert len(logger.handlers) > 0
