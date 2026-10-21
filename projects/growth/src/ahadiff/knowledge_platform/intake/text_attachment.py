"""Bounded UTF-8 text-file upload validation; never reads a client filesystem path."""

from __future__ import annotations

import base64
import binascii
import hashlib
import unicodedata
from typing import Any

from .contracts import IntakeError

MAX_TEXT_ATTACHMENT_BYTES = 100_000
MAX_TEXT_ATTACHMENT_BASE64 = 4 * ((MAX_TEXT_ATTACHMENT_BYTES + 2) // 3)


def decode_text_attachment(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return exact decoded text plus private original bytes for the temporary bundle."""
    allowed = {
        "expected_preview_revision",
        "filename",
        "content_type",
        "content_base64",
        "private_note",
    }
    if set(payload) - allowed:
        raise IntakeError("ATTACHMENT_INVALID", "文本文件请求含不支持的字段。", status_code=422)
    filename = payload.get("filename")
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > 120
        or filename != filename.strip()
        or filename.startswith(".")
        or ".." in filename
        or any(char in filename for char in "/\\:")
        or any(unicodedata.category(char).startswith("C") for char in filename)
    ):
        raise IntakeError(
            "ATTACHMENT_INVALID_NAME", "只接受不含路径的文本文件名。", status_code=422
        )
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content_type = payload.get("content_type")
    if (
        extension not in {"txt", "md"}
        or content_type not in {"text/plain", "text/markdown"}
        or (extension == "txt" and content_type != "text/plain")
    ):
        raise IntakeError(
            "ATTACHMENT_UNSUPPORTED",
            "仅支持 text/plain 的 .txt 或文本 Markdown .md 文件。",
            status_code=422,
        )
    encoded = payload.get("content_base64")
    if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_TEXT_ATTACHMENT_BASE64:
        raise IntakeError(
            "ATTACHMENT_TOO_LARGE", "文本文件须为 1 至 100,000 字节。", status_code=422
        )
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise IntakeError("ATTACHMENT_INVALID", "文件字节编码无效。", status_code=422) from exc
    if not content or len(content) > MAX_TEXT_ATTACHMENT_BYTES:
        raise IntakeError(
            "ATTACHMENT_TOO_LARGE", "文本文件须为 1 至 100,000 字节。", status_code=422
        )
    if base64.b64encode(content).decode("ascii") != encoded:
        raise IntakeError("ATTACHMENT_INVALID", "文件字节编码必须是规范 Base64。", status_code=422)
    forbidden = (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
        b"\x1f\x8b",
        b"7z\xbc\xaf\x27\x1c",
        b"Rar!",
        b"%PDF-",
        b"\x7fELF",
        b"MZ",
    )
    if content.startswith(forbidden) or any(
        byte < 32 and byte not in (9, 10, 13) or byte == 127 for byte in content
    ):
        raise IntakeError(
            "ATTACHMENT_UNSUPPORTED", "不接受归档、二进制文件或含控制字节的内容。", status_code=422
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntakeError(
            "ATTACHMENT_INVALID_UTF8",
            "文件必须是有效 UTF-8 文本，不会猜测或转换编码。",
            status_code=422,
        ) from exc
    if not text.strip().strip("\ufeff").strip():
        raise IntakeError("ATTACHMENT_INVALID", "文本文件不能只有空白。", status_code=422)
    return text, {
        "filename": filename,
        "content_type": content_type,
        "byte_count": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": encoded,
    }


def attachment_metadata(value: dict[str, Any]) -> dict[str, Any]:
    """Only non-executable file metadata is exposed in the authenticated preview."""
    return {key: value[key] for key in ("filename", "content_type", "byte_count", "sha256")}
