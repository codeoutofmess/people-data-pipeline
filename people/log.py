"""Structured logging: every log line is one JSON object, so tools can search and filter it."""

import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Extra key-value pairs, passed as logger.info("...", extra={"fields": {...}}).
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry, default=str)


def setup(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()  # writes to stderr, keeping stdout for command output
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
