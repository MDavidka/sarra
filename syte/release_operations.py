"""Release-control persistence and policy helpers for the operator workspace.

The legacy project remains the runtime source of truth. This module adds a
backward-compatible operational layer around it: release environments,
protected production promotions, preview policy, resource budgets, recovery
points, project roles, and an append-only release timeline.
"""

from __future__ import annotations

import json
import tarfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from syte.config import settings
from syte.database import get_project
from syte.sqlite_utils import configure_sqlite

ENVIRONMENT_KINDS = ("production", "staging", "preview")
TEAM_ROLES = ("owner", "admin", "deployer", "viewer")
DEPLOYMENT_STRATEGIES = ("rolling", "blue_green", "canary")
BACKUP_SCHEDULES = ("daily", "weekly")

_DEFAULT_POLICY = {
    "deployment_strategy": "rolling",
    "canary_percent": 10,
    "preview_enabled": True,
    "preview_retention_days": 7,
    "resource_alert_percent": 85,
    "storage_limit_mb": 0,
    "backup_enabled": False,
    "backup_schedule": "daily",
    "backup_retention_days": 14,
    "last_restore_check_at": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def _connection():
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        yield db


def _environment_row(row: dict[str, Any]) -> dict[str, Any]:
    row["auto_deploy"] = bool(row.get("auto_deploy"))
    row["require_approval"] = bool(row.get("require_approval"))
    return row


def _policy_row(row: dict[str, Any]) -> dict[str, Any]:
    for field in ("preview_enabled", "backup_enabled"):
        row[field] = bool(row.get(field))
    return row


async def ensure_release_project(project_id: str) -> None:
    """Create safe defaults for an existing project without changing deployment."""

    project = await get_project(project_id)
    if not project:
        raise ValueError("Project not found")
    now = _now()
    branch = str(project.get("branch") or "main")
    domain = str(project.get("domain") or "")
    async with _connection() as db:
        await db.execute(
            """INSERT OR IGNORE INTO release_policies
            (project_id, deployment_strategy, canary_percent, preview_enabled,
             preview_retention_days, resource_alert_percent, storage_limit_mb,
             backup_enabled, backup_schedule, backup_retention_days, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "rolling", 10, 1, 7, 85, 0, 0, "daily", 14, now),
        )
        defaults = (
            ("Production", "production", branch, domain, int(bool(project.get("auto_deploy"))), 1),
            ("Staging", "staging", branch, "", 0, 0),
            ("Preview", "preview", branch, str(project.get("preview_domain") or ""), 0, 0),
        )
        for name, kind, env_branch, env_domain, auto_deploy, require_approval in defaults:
            await db.execute(
                """INSERT OR IGNORE INTO release_environments
                (id, project_id, name, kind, branch, domain, auto_deploy, require_approval, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex[:16], project_id, name, kind, env_branch, env_domain, auto_deploy, require_approval, now, now),
            )
        await db.commit()


async def list_environments(project_id: str) -> list[dict[str, Any]]:
    await ensure_release_project(project_id)
    async with _connection() as db:
        async with db.execute(
            """SELECT * FROM release_environments WHERE project_id = ?
            ORDER BY CASE kind WHEN 'production' THEN 1 WHEN 'staging' THEN 2 ELSE 3 END""",
            (project_id,),
        ) as cursor:
            return [_environment_row(dict(row)) for row in await cursor.fetchall()]


async def get_environment(project_id: str, environment_id: str) -> dict[str, Any] | None:
    await ensure_release_project(project_id)
    async with _connection() as db:
        async with db.execute(
            "SELECT * FROM release_environments WHERE project_id = ? AND id = ?",
            (project_id, environment_id),
        ) as cursor:
            row = await cursor.fetchone()
    return _environment_row(dict(row)) if row else None


async def update_environment(project_id: str, environment_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"name", "branch", "domain", "auto_deploy", "require_approval"}
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        return await get_environment(project_id, environment_id)
    values["updated_at"] = _now()
    if "auto_deploy" in values:
        values["auto_deploy"] = int(bool(values["auto_deploy"]))
    if "require_approval" in values:
        values["require_approval"] = int(bool(values["require_approval"]))
    async with _connection() as db:
        assignment = ", ".join(f"{column} = ?" for column in values)
        await db.execute(
            f"UPDATE release_environments SET {assignment} WHERE project_id = ? AND id = ?",
            [*values.values(), project_id, environment_id],
        )
        await db.commit()
    return await get_environment(project_id, environment_id)


async def get_policy(project_id: str) -> dict[str, Any]:
    await ensure_release_project(project_id)
    async with _connection() as db:
        async with db.execute("SELECT * FROM release_policies WHERE project_id = ?", (project_id,)) as cursor:
            row = await cursor.fetchone()
    return _policy_row(dict(row)) if row else {"project_id": project_id, **_DEFAULT_POLICY}


async def update_policy(project_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    await ensure_release_project(project_id)
    allowed = set(_DEFAULT_POLICY)
    values = {key: value for key, value in updates.items() if key in allowed}
    if values.get("deployment_strategy") not in {None, *DEPLOYMENT_STRATEGIES}:
        raise ValueError("Deployment strategy must be rolling, blue_green, or canary")
    if values.get("backup_schedule") not in {None, *BACKUP_SCHEDULES}:
        raise ValueError("Backup schedule must be daily or weekly")
    for name, lower, upper in (("canary_percent", 1, 100), ("preview_retention_days", 1, 90), ("resource_alert_percent", 50, 100), ("storage_limit_mb", 0, 10_000_000), ("backup_retention_days", 1, 3650)):
        if name in values:
            values[name] = int(values[name])
            if not lower <= values[name] <= upper:
                raise ValueError(f"{name} is outside the supported range")
    for field in ("preview_enabled", "backup_enabled"):
        if field in values:
            values[field] = int(bool(values[field]))
    if values:
        values["updated_at"] = _now()
        async with _connection() as db:
            assignment = ", ".join(f"{column} = ?" for column in values)
            await db.execute(f"UPDATE release_policies SET {assignment} WHERE project_id = ?", [*values.values(), project_id])
            await db.commit()
    return await get_policy(project_id)


async def record_event(
    project_id: str,
    event_type: str,
    title: str,
    *,
    detail: str = "",
    severity: str = "info",
    environment_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if severity not in {"info", "success", "warning", "danger"}:
        severity = "info"
    record = {
        "id": uuid.uuid4().hex[:16],
        "project_id": project_id,
        "environment_id": environment_id,
        "event_type": event_type,
        "severity": severity,
        "title": title,
        "detail": detail,
        "payload": json.dumps(payload or {}, separators=(",", ":")),
        "created_at": _now(),
    }
    async with _connection() as db:
        await db.execute(
            """INSERT INTO release_events
            (id, project_id, environment_id, event_type, severity, title, detail, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(record.values()),
        )
        await db.commit()
    record["payload"] = payload or {}
    return record


async def list_events(project_id: str, limit: int = 30) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 100))
    async with _connection() as db:
        async with db.execute(
            "SELECT * FROM release_events WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, safe_limit),
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        try:
            row["payload"] = json.loads(row.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            row["payload"] = {}
    return rows


async def list_team_members(project_id: str) -> list[dict[str, Any]]:
    async with _connection() as db:
        async with db.execute(
            "SELECT * FROM project_team_members WHERE project_id = ? ORDER BY CASE role WHEN 'owner' THEN 1 WHEN 'admin' THEN 2 WHEN 'deployer' THEN 3 ELSE 4 END, email",
            (project_id,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def upsert_team_member(project_id: str, email: str, display_name: str, role: str) -> dict[str, Any]:
    if role not in TEAM_ROLES:
        raise ValueError("Role must be owner, admin, deployer, or viewer")
    cleaned_email = email.strip().lower()
    if not cleaned_email or "@" not in cleaned_email:
        raise ValueError("Provide a valid member email")
    now = _now()
    async with _connection() as db:
        await db.execute(
            """INSERT INTO project_team_members (id, project_id, email, display_name, role, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, email) DO UPDATE SET
              display_name = excluded.display_name, role = excluded.role, updated_at = excluded.updated_at""",
            (uuid.uuid4().hex[:16], project_id, cleaned_email, display_name.strip(), role, now, now),
        )
        await db.commit()
        async with db.execute("SELECT * FROM project_team_members WHERE project_id = ? AND email = ?", (project_id, cleaned_email)) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else {"project_id": project_id, "email": cleaned_email, "role": role}


async def remove_team_member(project_id: str, member_id: str) -> bool:
    async with _connection() as db:
        cursor = await db.execute("DELETE FROM project_team_members WHERE project_id = ? AND id = ?", (project_id, member_id))
        await db.commit()
    return bool(cursor.rowcount)


async def request_approval(project_id: str, environment_id: str, requested_by: str, note: str = "") -> dict[str, Any]:
    environment = await get_environment(project_id, environment_id)
    if not environment:
        raise ValueError("Release environment not found")
    now = _now()
    record = {
        "id": uuid.uuid4().hex[:16],
        "project_id": project_id,
        "environment_id": environment_id,
        "requested_by": requested_by.strip(),
        "status": "pending",
        "approved_by": "",
        "note": note.strip(),
        "created_at": now,
        "decided_at": None,
    }
    async with _connection() as db:
        await db.execute(
            """INSERT INTO release_approvals
            (id, project_id, environment_id, requested_by, status, approved_by, note, created_at, decided_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(record.values()),
        )
        await db.commit()
    await record_event(project_id, "release.approval_requested", f"Approval requested for {environment['name']}", detail=record["note"], severity="warning", environment_id=environment_id)
    return record


async def list_approvals(project_id: str, limit: int = 30) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 100))
    async with _connection() as db:
        async with db.execute(
            """SELECT approvals.*, environments.name AS environment_name, environments.kind AS environment_kind
            FROM release_approvals AS approvals
            JOIN release_environments AS environments ON environments.id = approvals.environment_id
            WHERE approvals.project_id = ? ORDER BY approvals.created_at DESC LIMIT ?""",
            (project_id, safe_limit),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def decide_approval(project_id: str, approval_id: str, approved_by: str, approved: bool, note: str = "") -> dict[str, Any] | None:
    status = "approved" if approved else "rejected"
    now = _now()
    async with _connection() as db:
        await db.execute(
            """UPDATE release_approvals SET status = ?, approved_by = ?, note = ?, decided_at = ?
            WHERE project_id = ? AND id = ? AND status = 'pending'""",
            (status, approved_by.strip(), note.strip(), now, project_id, approval_id),
        )
        await db.commit()
        async with db.execute("SELECT * FROM release_approvals WHERE project_id = ? AND id = ?", (project_id, approval_id)) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    result = dict(row)
    await record_event(project_id, f"release.approval_{status}", f"Release approval {status}", detail=result.get("note") or "", severity="success" if approved else "warning", environment_id=result["environment_id"])
    return result


async def has_approved_release(project_id: str, environment_id: str) -> bool:
    async with _connection() as db:
        async with db.execute(
            """SELECT 1 FROM release_approvals WHERE project_id = ? AND environment_id = ? AND status = 'approved'
            ORDER BY decided_at DESC LIMIT 1""",
            (project_id, environment_id),
        ) as cursor:
            return bool(await cursor.fetchone())


async def consume_approved_release(project_id: str, environment_id: str) -> bool:
    """Consume the newest approval once a protected release is actually queued."""

    async with _connection() as db:
        async with db.execute(
            """SELECT id FROM release_approvals WHERE project_id = ? AND environment_id = ? AND status = 'approved'
            ORDER BY decided_at DESC LIMIT 1""",
            (project_id, environment_id),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return False
        await db.execute("UPDATE release_approvals SET status = 'consumed' WHERE id = ?", (str(row["id"]),))
        await db.commit()
    await record_event(project_id, "release.approval_consumed", "Protected release approval consumed", severity="info", environment_id=environment_id)
    return True


async def create_restore_point(project_id: str, label: str, source: str = "deployment", artifact_path: str = "") -> dict[str, Any]:
    record = {
        "id": uuid.uuid4().hex[:16],
        "project_id": project_id,
        "label": label.strip() or "Release restore point",
        "source": source.strip() or "deployment",
        "status": "available",
        "artifact_path": artifact_path,
        "created_at": _now(),
        "verified_at": None,
    }
    async with _connection() as db:
        await db.execute(
            """INSERT INTO release_restore_points (id, project_id, label, source, status, artifact_path, created_at, verified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(record.values()),
        )
        await db.commit()
    await record_event(project_id, "recovery.restore_point_created", "Recovery point recorded", detail=record["label"], severity="success")
    return record


async def create_workspace_backup(project_id: str, label: str = "") -> dict[str, Any]:
    """Create a compressed local snapshot of a project's persistent data directory.

    Application source stays protected by Git deployment and rollback records;
    this snapshot intentionally contains only the mutable `data` directory.
    """

    from syte.workspace import ensure_workspace, workspace_path

    await ensure_release_project(project_id)
    ensure_workspace(project_id)
    data_path = workspace_path(project_id) / "data"
    data_path.mkdir(parents=True, exist_ok=True)
    backup_dir = settings.data_dir / "release-backups" / project_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = backup_dir / f"{stamp}-{uuid.uuid4().hex[:8]}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(data_path, arcname="data", recursive=True)
    return await create_restore_point(
        project_id,
        label or f"Data snapshot · {stamp}",
        source="workspace-data",
        artifact_path=str(archive_path),
    )


async def verify_restore_point(project_id: str, restore_point_id: str) -> dict[str, Any] | None:
    now = _now()
    async with _connection() as db:
        async with db.execute("SELECT * FROM release_restore_points WHERE project_id = ? AND id = ?", (project_id, restore_point_id)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        point = dict(row)
        artifact_path = str(point.get("artifact_path") or "")
        if artifact_path and not Path(artifact_path).is_file():
            await db.execute("UPDATE release_restore_points SET status = 'missing' WHERE project_id = ? AND id = ?", (project_id, restore_point_id))
            await db.commit()
            point["status"] = "missing"
            return point
        await db.execute(
            "UPDATE release_restore_points SET status = 'verified', verified_at = ? WHERE project_id = ? AND id = ?",
            (now, project_id, restore_point_id),
        )
        await db.commit()
        point["status"] = "verified"
        point["verified_at"] = now
    await update_policy(project_id, {"last_restore_check_at": now})
    await record_event(project_id, "recovery.restore_point_verified", "Recovery point verified", severity="success")
    return point


async def list_restore_points(project_id: str, limit: int = 20) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 100))
    async with _connection() as db:
        async with db.execute(
            "SELECT * FROM release_restore_points WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, safe_limit),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def workspace(project_id: str) -> dict[str, Any]:
    await ensure_release_project(project_id)
    from syte.system_stats import get_system_stats

    metrics = get_system_stats(sample_cpu=False)
    return {
        "environments": await list_environments(project_id),
        "policy": await get_policy(project_id),
        "approvals": await list_approvals(project_id),
        "team": await list_team_members(project_id),
        "restore_points": await list_restore_points(project_id),
        "events": await list_events(project_id),
        "host_metrics": {
            "cpu_percent": metrics.get("cpu_percent", 0),
            "ram_percent": metrics.get("ram_percent", 0),
            "disk_percent": metrics.get("disk_percent", 0),
        },
    }
