import json
import logging
import re
from datetime import datetime, timezone

SECRET_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+/=]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(x-goog-api-key['\":=\s]+)[A-Za-z0-9._\-+/=]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(api_key['\":=\s]+)[A-Za-z0-9._\-+/=]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(api_secret['\":=\s]+)[A-Za-z0-9._\-+/=]+", re.IGNORECASE), r"\1***REDACTED***"),
]


def redact_secrets(text: str) -> str:
    """Strip bearer tokens and API keys before emitting log messages."""
    redacted = text
    for pattern, repl in SECRET_PATTERNS:
        redacted = pattern.sub(repl, redacted)
    return redacted


class StructuredFormatter(logging.Formatter):
    """Lightweight JSON structured formatter with automatic secret redaction."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_secrets(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger once with structured redacted output."""
    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers:
            handler.setFormatter(StructuredFormatter())
        root.setLevel(level)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root.addHandler(handler)
    root.setLevel(level)

