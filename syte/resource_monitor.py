"""Resource monitoring helpers for service-level dashboards.

This module groups resource usage into the three buckets the GUI should show:

- websites: all running project processes
- caddy: the reverse proxy / TLS service
- main gui: the Syte web app process itself

The helpers avoid external dependencies so the monitor can run on a minimal
Ubuntu install. They rely on /proc and the PID files already managed by
``syte.process_manager``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from syte.database import list_projects
from syte.process_manager import PID_DIR, is_running, pid_file

CLK_TCK = os.sysconf(os.sysconf_names["SC_CLK_TCK"]) if hasattr(os, "sysconf") else 100
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


@dataclass(slots=True)
class ProcessUsage:
    pid: int
    name: str
    cpu_percent: float
    memory_mb: float


@dataclass(slots=True)
class ServiceUsage:
    name: str
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    instances: int = 0
    pids: list[int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_mb": round(self.memory_mb, 1),
            "instances": self.instances,
            "pids": list(self.pids or []),
        }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _total_cpu_jiffies() -> int:
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            line = fh.readline().strip()
        parts = [int(part) for part in line.split()[1:]]
        return sum(parts)
    except (OSError, ValueError, IndexError):
        return 0


def _process_snapshot(pid: int) -> tuple[int, int, str]:
    stat_path = Path("/proc") / str(pid) / "stat"
    status_path = Path("/proc") / str(pid) / "status"
    comm_path = Path("/proc") / str(pid) / "comm"

    try:
        stat = _read_text(stat_path)
        if not stat:
            raise OSError
        # /proc/<pid>/stat is a single line where the comm field may contain spaces.
        # The final fields are stable, so we split from the right side.
        start = stat.rfind(") ")
        if start == -1:
            raise ValueError("invalid stat format")
        tail = stat[start + 2 :].split()
        utime = int(tail[11])
        stime = int(tail[12])
        rss_pages = int(tail[21])
        name = _read_text(comm_path).strip() or tail[0]
        return utime + stime, rss_pages, name
    except (OSError, ValueError, IndexError):
        # Fall back to a lighter status read so the caller can still surface a
        # partially useful record if the process exits between samples.
        try:
            status = _read_text(status_path)
            rss_kb = 0
            for line in status.splitlines():
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
            return 0, max(0, rss_kb * 1024 // PAGE_SIZE), name  # type: ignore[name-defined]
        except Exception:
            return 0, 0, f"pid-{pid}"


def _cpu_percent_from_samples(
    start_proc: int,
    start_total: int,
    end_proc: int,
    end_total: int,
) -> float:
    delta_total = end_total - start_total
    delta_proc = end_proc - start_proc
    if delta_total <= 0 or delta_proc <= 0:
        return 0.0
    return max(0.0, min(100.0, (delta_proc / delta_total) * 100.0))


async def sample_process_usage(pid: int, *, sample_ms: int = 120) -> ProcessUsage | None:
    """Return an instantaneous CPU/memory sample for a single process."""

    if pid <= 0:
        return None

    start_total = _total_cpu_jiffies()
    start_proc, start_rss, name = _process_snapshot(pid)
    if start_total <= 0:
        return ProcessUsage(pid=pid, name=name, cpu_percent=0.0, memory_mb=round(start_rss * PAGE_SIZE / 1024 / 1024, 1))

    await asyncio.sleep(sample_ms / 1000)
    end_total = _total_cpu_jiffies()
    end_proc, end_rss, end_name = _process_snapshot(pid)
    usage = ProcessUsage(
        pid=pid,
        name=end_name or name,
        cpu_percent=_cpu_percent_from_samples(start_proc, start_total, end_proc, end_total),
        memory_mb=round(end_rss * PAGE_SIZE / 1024 / 1024, 1),
    )
    return usage


async def _find_caddy_pids() -> list[int]:
    pids: list[int] = []
    proc_dir = Path("/proc")
    for child in proc_dir.iterdir():
        if not child.name.isdigit():
            continue
        pid = int(child.name)
        cmdline = _read_text(child / "cmdline").replace("\x00", " ").strip().lower()
        comm = _read_text(child / "comm").strip().lower()
        if "caddy" in comm or "caddy" in cmdline:
            pids.append(pid)
    return pids


async def _service_from_pids(name: str, pids: Iterable[int], *, sample_ms: int = 120) -> ServiceUsage:
    total_cpu = 0.0
    total_memory = 0.0
    seen_pids: list[int] = []
    for pid in pids:
        usage = await sample_process_usage(pid, sample_ms=sample_ms)
        if not usage:
            continue
        seen_pids.append(pid)
        total_cpu += usage.cpu_percent
        total_memory += usage.memory_mb
    return ServiceUsage(name=name, cpu_percent=total_cpu, memory_mb=total_memory, instances=len(seen_pids), pids=seen_pids)


async def get_resource_monitor_snapshot(*, sample_ms: int = 120) -> dict[str, Any]:
    """Collect a service-oriented resource snapshot for the dashboard/API."""

    projects = await list_projects()
    website_pids: list[int] = []
    website_names: list[str] = []
    for project in projects:
        project_id = str(project.get("id") or "").strip()
        if not project_id or not is_running(project_id, str(project.get("deploy_type") or "shell")):
            continue
        pf = pid_file(project_id)
        try:
            pid = int(pf.read_text().strip())
        except (OSError, ValueError):
            continue
        website_pids.append(pid)
        website_names.append(str(project.get("name") or project_id))

    websites = await _service_from_pids("websites", website_pids, sample_ms=sample_ms)
    caddy = await _service_from_pids("caddy", await _find_caddy_pids(), sample_ms=sample_ms)
    main_gui = await _service_from_pids("main gui", [os.getpid()], sample_ms=sample_ms)

    return {
        "ok": True,
        "sample_ms": sample_ms,
        "services": [
            {
                **websites.to_dict(),
                "service_type": "websites",
                "label": "Websites",
                "children": website_names,
            },
            {
                **caddy.to_dict(),
                "service_type": "caddy",
                "label": "Caddy",
            },
            {
                **main_gui.to_dict(),
                "service_type": "main_gui",
                "label": "Main GUI",
            },
        ],
    }
