"""Утилиты для логирования и аудио."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from phoneagent.config import get_settings
from phoneagent.utils.audio import (
    SUPPORTED_FORMATS,
    is_valid_format,
    pcm_to_ulaw,
    pcm_to_wav,
    ulaw_to_pcm,
    wav_to_pcm,
)


# ── Logging ─────────────────────────────────────────────


def add_app_name(_: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Добавляет имя приложения во все логи."""
    event_dict["app"] = "phoneagent"
    event_dict["level"] = method_name.upper()
    return event_dict


def configure_logging() -> None:
    """Настройка structlog и stdlib logging."""
    settings = get_settings()
    level = getattr(logging, settings.log_level)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        add_app_name,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processor=structlog.dev.ConsoleRenderer(colors=True)
        if sys.stderr.isatty()
        else structlog.processors.JSONRenderer(),
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Возвращает настроенный логгер."""
    return structlog.get_logger(name or "phoneagent")


__all__ = [
    "configure_logging",
    "get_logger",
    "SUPPORTED_FORMATS",
    "is_valid_format",
    "pcm_to_ulaw",
    "pcm_to_wav",
    "ulaw_to_pcm",
    "wav_to_pcm",
]