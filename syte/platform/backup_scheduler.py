"""Background scheduler for enabled platform backup schedules."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from syte.platform.backup_manager import execute_backup
from syte.platform.store import enabled_backups, get, update

logger = logging.getLogger("syte.platform.backup_scheduler")


def _field_matches(value: int, field: str, minimum: int, maximum: int) -> bool:
    if field == "*":
        return True
    for token in field.split(","):
        token = token.strip()
        if token.startswith("*/") and token[2:].isdigit():
            step = max(1, int(token[2:]))
            if (value - minimum) % step == 0:
                return True
        elif token.isdigit() and minimum <= int(token) <= maximum and value == int(token):
            return True
    return False


def cron_matches(expression: str, now: datetime) -> bool:
    fields = (expression or "").split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    return (
        _field_matches(now.minute, minute, 0, 59)
        and _field_matches(now.hour, hour, 0, 23)
        and _field_matches(now.day, day, 1, 31)
        and _field_matches(now.month, month, 1, 12)
        and _field_matches((now.weekday() + 1) % 7, weekday, 0, 6)
    )


async def run_due_backups(now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    count = 0
    for backup in await enabled_backups():
        if not cron_matches(str(backup.get("frequency") or ""), current):
            continue
        last_run = str(backup.get("last_run_at") or "")
        if last_run[:16] == current.isoformat()[:16]:
            continue
        database = await get("platform_databases", str(backup["database_uuid"]), include_secrets=True)
        if database is None:
            await update("platform_backups", backup["uuid"], {"last_run_at": current.isoformat()})
            continue
        storage = None
        if backup.get("s3_storage_uuid"):
            storage = await get("platform_s3_storages", str(backup["s3_storage_uuid"]), include_secrets=True)
        try:
            await execute_backup(backup, database, storage)
        except Exception:  # noqa: BLE001 - one bad schedule must not stop the loop
            logger.exception("Backup schedule %s failed", backup.get("uuid"))
        count += 1
    return count


async def backup_scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await run_due_backups()
        except Exception:  # noqa: BLE001 - scheduler must stay alive
            logger.exception("Backup scheduler iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue
