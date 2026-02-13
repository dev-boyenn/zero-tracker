from __future__ import annotations

import re
from dataclasses import dataclass

LOG_LINE_RE = re.compile(
    r"^\[(?P<clock>\d{2}:\d{2}:\d{2})\] "
    r"\[(?P<thread>.+?)/(?P<level>[A-Z]+)\]:"
    r"(?: \[(?P<source>[^\]]+)\])?"
    r" ?(?P<body>.*)$"
)


@dataclass(slots=True)
class ParsedLogLine:
    raw_line: str
    clock_time: str | None
    thread_name: str | None
    level: str | None
    source: str | None
    body: str
    is_chat: bool
    chat_message: str | None


def normalize_chat_message(message: str) -> str:
    cleaned = message.strip()
    if cleaned.startswith("\\n"):
        cleaned = cleaned[2:].strip()
    return cleaned


def parse_log_line(raw_line: str) -> ParsedLogLine:
    match = LOG_LINE_RE.match(raw_line)
    if not match:
        return ParsedLogLine(
            raw_line=raw_line,
            clock_time=None,
            thread_name=None,
            level=None,
            source=None,
            body=raw_line,
            is_chat=False,
            chat_message=None,
        )

    body = match.group("body") or ""
    source = match.group("source")
    is_chat = source == "CHAT"
    chat_message = normalize_chat_message(body) if is_chat else None

    return ParsedLogLine(
        raw_line=raw_line,
        clock_time=match.group("clock"),
        thread_name=match.group("thread"),
        level=match.group("level"),
        source=source,
        body=body,
        is_chat=is_chat,
        chat_message=chat_message,
    )
