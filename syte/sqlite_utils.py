"""Shared SQLite pragmas for Syte databases."""

from __future__ import annotations

import aiosqlite

_wal_paths: set[str] = set()


async def configure_sqlite(db: aiosqlite.Connection, *, db_path: str | None = None) -> None:
    """Enable WAL mode once per database file for better read/write concurrency."""
    path_key = db_path or str(getattr(db, "database", "") or "")
    if path_key and path_key in _wal_paths:
        return
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA busy_timeout=5000")
    if path_key:
        _wal_paths.add(path_key)
