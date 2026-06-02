from __future__ import annotations

import mimetypes
import re
from pathlib import Path

INVALID_CS_NAME_CHARS = set('\\/:*?"<>|')

MIME_MAP = {
    "pdf": "application/pdf",
    "eml": "message/rfc822",
    "msg": "application/vnd.ms-outlook",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "zip": "application/zip",
    "xml": "application/xml",
    "html": "text/html",
}


def normalize_ws_name(name: str) -> str:
    """Normalize workspace names for comparison.

    Example: ``SPLIC-00042`` and ``SPLIC - 00042`` become the same key.
    """
    return re.sub(r"\s*-\s*", "-", (name or "").strip())


def clean_cell_value(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


def has_invalid_name_chars(value: str) -> bool:
    return any(char in INVALID_CS_NAME_CHARS for char in value or "")


def get_mime(path: str, hint: str = "") -> str:
    hint_normalized = (hint or "").strip().lower().lstrip(".")
    if hint_normalized in MIME_MAP:
        return MIME_MAP[hint_normalized]
    ext = Path(path or "").suffix.lstrip(".").lower()
    if ext in MIME_MAP:
        return MIME_MAP[ext]
    guessed, _ = mimetypes.guess_type(path or "")
    return guessed or "application/octet-stream"


def supported_mime_hints() -> set[str]:
    return set(MIME_MAP.keys())
