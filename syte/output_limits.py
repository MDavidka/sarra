"""Caps for subprocess / log payloads kept in process memory."""

from __future__ import annotations

import asyncio
from typing import BinaryIO, Callable, TextIO

# Soft cap for strings returned to callers / kept in RAM (DAV-127).
MAX_CAPTURED_OUTPUT_BYTES = 2 * 1024 * 1024  # 2 MiB
TRUNCATION_MARKER = "\n… [output truncated]\n"


def truncate_output(text: str, max_bytes: int = MAX_CAPTURED_OUTPUT_BYTES) -> str:
    """Return ``text`` unchanged when small; otherwise keep head+tail under ``max_bytes``."""
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    marker = TRUNCATION_MARKER.encode("utf-8")
    budget = max_bytes - len(marker)
    if budget <= 0:
        return TRUNCATION_MARKER.strip()
    head = budget // 2
    tail = budget - head
    return (
        encoded[:head].decode("utf-8", errors="replace")
        + TRUNCATION_MARKER
        + encoded[-tail:].decode("utf-8", errors="replace")
    )


def read_text_stream_limited(
    stream: TextIO,
    *,
    max_bytes: int = MAX_CAPTURED_OUTPUT_BYTES,
    on_line: Callable[[str], None] | None = None,
) -> tuple[str, bool]:
    """Read a text stream, optionally forwarding every line, while capping RAM capture.

    Returns ``(captured_text, truncated)``. Lines after the cap are still passed to
    ``on_line`` (e.g. for disk logging) but are not appended to the returned string.
    """
    chunks: list[str] = []
    captured = 0
    truncated = False
    for line in stream:
        if on_line is not None:
            on_line(line)
        if truncated:
            continue
        line_bytes = len(line.encode("utf-8", errors="replace"))
        if captured + line_bytes > max_bytes:
            remaining = max_bytes - captured
            if remaining > 0:
                piece = line.encode("utf-8", errors="replace")[:remaining]
                chunks.append(piece.decode("utf-8", errors="replace"))
            chunks.append(TRUNCATION_MARKER)
            truncated = True
            continue
        chunks.append(line)
        captured += line_bytes
    return "".join(chunks).strip(), truncated


def read_binary_stream_limited(
    stream: BinaryIO,
    *,
    max_bytes: int = MAX_CAPTURED_OUTPUT_BYTES,
    chunk_size: int = 65_536,
) -> tuple[bytes, bool]:
    """Read a binary pipe up to ``max_bytes``, then drain the remainder."""
    buf = bytearray()
    truncated = False
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        if truncated:
            continue
        if len(buf) + len(chunk) > max_bytes:
            buf.extend(chunk[: max(0, max_bytes - len(buf))])
            truncated = True
            # Drain so the child does not block on a full pipe.
            while stream.read(chunk_size):
                pass
            break
        buf.extend(chunk)
    return bytes(buf), truncated


async def read_async_stream_limited(
    stream: asyncio.StreamReader | None,
    *,
    max_bytes: int = MAX_CAPTURED_OUTPUT_BYTES,
    chunk_size: int = 65_536,
) -> tuple[bytes, bool]:
    """Async counterpart of :func:`read_binary_stream_limited`."""
    if stream is None:
        return b"", False
    buf = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(chunk_size)
        if not chunk:
            break
        if truncated:
            continue
        if len(buf) + len(chunk) > max_bytes:
            buf.extend(chunk[: max(0, max_bytes - len(buf))])
            truncated = True
            while await stream.read(chunk_size):
                pass
            break
        buf.extend(chunk)
    return bytes(buf), truncated
