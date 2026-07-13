"""Host CPU/RAM metrics for the GUI sidebar load indicator."""

import os
import time
from pathlib import Path


def _cpu_count() -> int:
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def _read_cpu_times() -> tuple[int, int]:
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = [int(x) for x in line.split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        total = sum(parts)
        return idle, total
    except (OSError, ValueError, IndexError):
        return 0, 0


def _cpu_percent(sample_ms: int = 120) -> float:
    idle1, total1 = _read_cpu_times()
    time.sleep(sample_ms / 1000)
    idle2, total2 = _read_cpu_times()
    delta_total = total2 - total1
    delta_idle = idle2 - idle1
    if delta_total <= 0:
        return 0.0
    return max(0.0, min(100.0, (1 - delta_idle / delta_total) * 100))


def _mem_stats() -> tuple[int, int, float]:
    total_kb = available_kb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    available_kb = int(line.split()[1])
        if total_kb <= 0:
            return 0, 0, 0.0
        used_kb = total_kb - available_kb
        percent = max(0.0, min(100.0, (used_kb / total_kb) * 100))
        return used_kb // 1024, total_kb // 1024, percent
    except (OSError, ValueError):
        return 0, 0, 0.0


def _load_dots(overload_percent: float) -> int:
    """Map 0–100% overload to 0–5 filled dots."""
    if overload_percent <= 0:
        return 0
    return min(5, max(1, int((overload_percent + 19) // 20)))


def get_system_stats(*, sample_cpu: bool = True) -> dict:
    cpu = round(_cpu_percent(), 1) if sample_cpu else 0.0
    ram_used_mb, ram_total_mb, ram_percent = _mem_stats()
    overload = max(cpu, ram_percent)
    dots = _load_dots(overload)
    return {
        "cpu_percent": cpu,
        "ram_used_mb": ram_used_mb,
        "ram_total_mb": ram_total_mb,
        "ram_percent": round(ram_percent, 1),
        "load_dots": dots,
        "load_dots_max": 5,
        "overload_percent": round(overload, 1),
    }


def format_ram_label(used_mb: int, total_mb: int) -> str:
    if total_mb <= 0:
        return "— Ram"
    if total_mb >= 1024:
        used_gb = used_mb / 1024
        total_gb = total_mb / 1024
        if used_gb >= 10 or total_gb >= 10:
            return f"{used_gb:.0f}GB / {total_gb:.0f}GB Ram"
        return f"{used_gb:.1f}GB / {total_gb:.1f}GB Ram"
    return f"{used_mb}MB / {total_mb}MB Ram"
