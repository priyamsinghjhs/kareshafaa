"""Logging set-up. KARESHAFAA_LOG_FORMAT=json writes one JSON object per line (for log collectors); the default is
plain text. Never log secrets, passwords, one-time codes or patient identifiers."""
from __future__ import annotations

import json
import logging
import os
import time


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
             "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        for k in ("request_id", "path", "status", "ms", "user_role"):
            v = getattr(record, k, None)
            if v is not None:
                d[k] = v
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False, default=str)


def configure_logging(fmt: str | None = None) -> None:
    fmt = (fmt or os.environ.get("KARESHAFAA_LOG_FORMAT", "text")).lower()
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)
