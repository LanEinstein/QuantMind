"""Structured logging configuration for QuantMind."""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler

import structlog


def configure_logging(log_dir: str = "logs", level: str = "INFO") -> None:
    """Configure structlog to output JSON to rotating log files + stdout.

    Sets up:
    - Daily rotating JSONL file at ``{log_dir}/quantmind.jsonl``
    - Stdout handler for Docker compatibility
    - structlog processors for structured JSON output

    Args:
        log_dir: Directory for log files. Created if missing.
        level: Logging level (DEBUG, INFO, WARNING, ERROR).
    """
    os.makedirs(log_dir, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)

    # Shared processors for structlog
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # Configure structlog to use stdlib logging
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Formatter that renders structlog events as JSON
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )

    # File handler: daily rotation, 30-day retention
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "quantmind.jsonl"),
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)

    # Stdout handler for Docker / terminal
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(log_level)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)
    root_logger.setLevel(log_level)
