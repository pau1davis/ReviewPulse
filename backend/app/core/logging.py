import logging
import sys

import structlog


def setup_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    """
    Configure structlog + stdlib logging so every log line is structured.

    In production (json_logs=True):  emits newline-delimited JSON.
    In development (json_logs=False): emits colourised, human-readable output.

    Set LOG_JSON=false and LOG_LEVEL=DEBUG in your local .env for dev mode.

    Every log call site can attach arbitrary key=value pairs:
        log.info("ingest.review_done", review_id=str(id), sentiment="positive")
    Those keys appear in the JSON output and are filterable in any log platform.
    """
    shared_processors: list = [
        # Pull context vars bound via structlog.contextvars.bind_contextvars()
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    if json_logs:
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=shared_processors,
        )
    else:
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=True),
            foreign_pre_chain=shared_processors,
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level.upper())

    # Quiet noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
