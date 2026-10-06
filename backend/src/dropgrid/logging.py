import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Do not serialize exception objects or config: these can contain credentials.
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Telegram HTTP endpoints include bot tokens. Avoid third-party request logging.
    for name in ("httpx", "httpcore", "aiogram", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
