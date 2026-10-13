"""Shared identifiers and validation for research records."""

from datetime import datetime, timezone


class ResearchError(ValueError):
    pass


def required(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ResearchError(f"{name}を空にできません。")
    return value.strip()


def identifier(value, prefix):
    text = str(value).upper()
    if not text.startswith(prefix) or not text[1:].isascii() or not text[1:].isdigit():
        raise ResearchError(f"IDは{prefix}001の形式で指定してください。")
    number = int(text[1:])
    if not 0 < number < 2**63:
        raise ResearchError("IDが有効な範囲にありません。")
    return number


def label(number, prefix):
    return f"{prefix}{number:03d}"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_text(text):
    # Keep words and punctuation unchanged; only normalize extraction whitespace.
    return " ".join(text.split())
