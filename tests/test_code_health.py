"""Regression checks for source-level code-health guardrails."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_code_health_guard_detects_no_current_python_source_conflicts():
    script = (ROOT / "scripts/check_code_health.py").read_text(encoding="utf-8")

    assert "unresolved merge marker" in script
    assert "duplicate definition" in script
    assert "ast.parse" in script
    assert "SOURCE_ROOTS" in script


def test_root_ignore_rules_cover_pytest_cache():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".pytest_cache/" in ignore


def test_active_runtime_has_no_unused_next_frontend_or_service_unit():
    assert (ROOT / "systemd/syte.service").is_file()
    assert not (ROOT / "systemd/syte-next.service").exists()
    assert not (ROOT / "frontend").exists()
    assert not (ROOT / "syte/static/sycord-api-docs.html").exists()
    assert not (ROOT / "syte/static/tokens.css").exists()
