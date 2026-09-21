import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

request_id_context: ContextVar[str] = ContextVar("request_id", default="")
trace_id_context: ContextVar[str] = ContextVar("trace_id", default="")
tenant_id_context: ContextVar[str] = ContextVar("tenant_id", default="")
UTC_PLUS_8 = timezone(timedelta(hours=8))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC_PLUS_8).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
            "trace_id": trace_id_context.get(),
            "tenant_id": tenant_id_context.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ProjectLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("app.")


class DatePartitionedFileHandler(logging.Handler):
    def __init__(self, log_root: Path) -> None:
        super().__init__()
        self.log_root = log_root
        self._current_date: str | None = None
        self._file_handler: logging.FileHandler | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            record_date = datetime.fromtimestamp(record.created, tz=UTC_PLUS_8).date().isoformat()
            if record_date != self._current_date:
                if self._file_handler is not None:
                    self._file_handler.close()
                log_directory = self.log_root / record_date
                log_directory.mkdir(parents=True, exist_ok=True)
                self._file_handler = logging.FileHandler(
                    log_directory / "app.log",
                    encoding="utf-8",
                )
                self._file_handler.setFormatter(self.formatter)
                self._current_date = record_date
            if self._file_handler is not None:
                self._file_handler.emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._file_handler is not None:
            self._file_handler.close()
            self._file_handler = None
        super().close()


def configure_logging(log_level: str) -> None:
    formatter = JsonFormatter()
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    file_handler = DatePartitionedFileHandler(Path(__file__).resolve().parents[2] / "logs")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(ProjectLogFilter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(log_level.upper())
