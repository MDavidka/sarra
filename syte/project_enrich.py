"""Shared project enrichment for GUI and token API consumers."""

from __future__ import annotations

from syte.ssl_status import project_ssl_summary


def enrich_ssl(project: dict) -> dict:
    """Return ssl summary block for a project record."""
    return project_ssl_summary(project)
