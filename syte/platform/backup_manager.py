"""Effectful managed-database backup and restore operations.

Backups are created with the engine-specific dump command from
``database_catalog``. Local artifacts are kept under Syte's data directory;
S3-compatible upload is optional and only attempted when configured and the
boto3 dependency is available. Credentials are never included in log messages.
"""
from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syte.config import settings
from syte.platform.database_catalog import dump_command, dump_extension, restore_command
from syte.platform.database_runtime import container_name
from syte.platform.store import find, get, insert, update
from syte.platform.types import new_uuid, utcnow
from syte.workspace import run_cmd

BACKUP_ROOT = settings.data_dir / "backups"


def _run(args: list[str]) -> tuple[bool, str]:
    code, out = run_cmd(args)
    return code == 0, out.strip()


async def _run_async(args: list[str]) -> tuple[bool, str]:
    return await asyncio.to_thread(_run, args)


def _artifact_name(db: dict[str, Any]) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{db.get('name', 'database')}-{stamp}.{dump_extension(db)}"


async def _upload_s3(local_path: Path, storage: dict[str, Any], key: str) -> tuple[bool, str]:
    try:
        import boto3  # type: ignore
    except ImportError:
        return False, "S3 upload requires the optional boto3 package on the Syte host."
    try:
        client = boto3.client(
            "s3",
            endpoint_url=str(storage["endpoint"]),
            region_name=str(storage.get("region") or "us-east-1"),
            aws_access_key_id=str(storage["access_key"]),
            aws_secret_access_key=str(storage["secret_key"]),
            config=__import__("botocore.config", fromlist=["Config"]).Config(
                s3={"addressing_style": "path" if storage.get("use_path_style") else "virtual"}
            ),
        )
        await asyncio.to_thread(client.upload_file, str(local_path), str(storage["bucket"]), key)
        return True, "uploaded"
    except Exception as exc:  # noqa: BLE001 - surface safe high-level error
        return False, f"S3 upload failed: {type(exc).__name__}"


async def execute_backup(backup: dict[str, Any], database: dict[str, Any], storage: dict[str, Any] | None = None) -> dict[str, Any]:
    if not database.get("database_type"):
        raise ValueError("Database type is missing.")
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    filename = _artifact_name(database)
    local_path = BACKUP_ROOT / filename
    execution = await insert(
        "platform_backup_executions",
        {
            "uuid": new_uuid(),
            "backup_uuid": backup["uuid"],
            "status": "running",
            "filename": filename,
            "started_at": utcnow(),
            "created_at": utcnow(),
        },
    )
    container_path = f"/tmp/{filename}"
    command = dump_command(database, output_path=container_path, dump_all=bool(backup.get("dump_all")))
    ok, output = await _run_async(["docker", "exec", container_name(database), "sh", "-lc", command])
    if ok:
        ok, output = await _run_async(["docker", "cp", f"{container_name(database)}:{container_path}", str(local_path)])
    if not ok:
        await update("platform_backup_executions", execution["uuid"], {"status": "failed", "message": "Database dump failed.", "finished_at": utcnow()})
        return {"ok": False, "execution": await get("platform_backup_executions", execution["uuid"]), "message": "Database dump failed."}
    size = local_path.stat().st_size if local_path.exists() else 0
    s3_key = ""
    upload_status = ""
    if storage:
        s3_key = f"syte/{database.get('uuid', 'database')}/{filename}"
        uploaded, upload_status = await _upload_s3(local_path, storage, s3_key)
        if not uploaded:
            await update("platform_backup_executions", execution["uuid"], {"status": "failed", "message": upload_status, "size": size, "finished_at": utcnow()})
            return {"ok": False, "execution": await get("platform_backup_executions", execution["uuid"]), "message": upload_status}
    if not bool(backup.get("save_locally", True)):
        local_path.unlink(missing_ok=True)
    retention = int(backup.get("retention_amount_locally") or 7)
    if retention > 0:
        siblings = sorted(BACKUP_ROOT.glob(f"{database.get('name', 'database')}-*.{dump_extension(database)}"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in siblings[retention:]:
            stale.unlink(missing_ok=True)
    result = await update(
        "platform_backup_executions",
        execution["uuid"],
        {"status": "finished", "message": "Backup completed.", "size": size, "s3_key": s3_key, "upload_status": upload_status or "not_configured", "finished_at": utcnow()},
    )
    await update("platform_backups", backup["uuid"], {"last_run_at": utcnow()})
    return {"ok": True, "execution": result}


async def restore_backup(backup_execution: dict[str, Any], database: dict[str, Any]) -> tuple[bool, str]:
    filename = str(backup_execution.get("filename") or "")
    local_path = BACKUP_ROOT / filename
    if not local_path.is_file():
        return False, "Backup artifact is not available locally."
    container_path = f"/tmp/{filename}"
    ok, output = await _run_async(["docker", "cp", str(local_path), f"{container_name(database)}:{container_path}"])
    if not ok:
        return False, "Could not copy the backup into the database container."
    command = restore_command(database, input_path=container_path)
    ok, output = await _run_async(["docker", "exec", container_name(database), "sh", "-lc", command])
    return ok, "Restore completed." if ok else "Restore command failed."


async def backup_executions(backup_uuid: str) -> list[dict[str, Any]]:
    return await find("platform_backup_executions", {"backup_uuid": backup_uuid}, order_by="created_at DESC")
