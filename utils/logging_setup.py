"""Structured logging with request-scoped context (request_id, user_id, chat_id)."""
from __future__ import annotations

import contextvars
import logging
import uuid
from typing import Optional

_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)
_user_id_var: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "user_id", default=None
)
_chat_id_var: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "chat_id", default=None
)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get() or "-"
        record.user_id = _user_id_var.get() if _user_id_var.get() is not None else "-"
        record.chat_id = _chat_id_var.get() if _chat_id_var.get() is not None else "-"
        return True


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] req=%(request_id)s user=%(user_id)s "
            "chat=%(chat_id)s %(name)s: %(message)s"
        )
    )
    handler.addFilter(_ContextFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:8]
    _request_id_var.set(rid)
    return rid


def set_user(user_id: Optional[int]) -> None:
    _user_id_var.set(user_id)


def set_chat(chat_id: Optional[int]) -> None:
    _chat_id_var.set(chat_id)


def get_request_id() -> Optional[str]:
    return _request_id_var.get()
