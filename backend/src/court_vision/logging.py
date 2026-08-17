"""Структурные логи. JSON в любом окружении: грепается и парсится одинаково везде.

Логи uvicorn проходят через тот же конвейер: иначе половина строк сервиса была бы
JSON-ом, а половина — человекочитаемым текстом, и сборщик логов ронял бы вторую половину.
"""

import logging
from typing import Final

import structlog

_SHARED_PROCESSORS: Final = (
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
)

_UVICORN_LOGGERS: Final = ("uvicorn", "uvicorn.access", "uvicorn.error")


def configure_logging(level: int = logging.INFO) -> None:
    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=list(_SHARED_PROCESSORS),
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
