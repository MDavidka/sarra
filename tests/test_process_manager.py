"""Tests for process manager."""

import pytest
from pathlib import Path
from syte import process_manager

def test_is_running_invalid_pid_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that is_running handles ValueError gracefully when parsing an invalid PID."""
    # Override PID_DIR
    monkeypatch.setattr(process_manager, "PID_DIR", tmp_path)

    project_id = "test-project-1"
    pid_file = process_manager.pid_file(project_id)

    # Write invalid content
    pid_file.write_text("not_a_number")

    # It should return False and unlink the file
    assert process_manager.is_running(project_id, deploy_type="shell") is False
    assert not pid_file.exists()

def test_is_running_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that is_running handles OSError gracefully when trying to read."""
    monkeypatch.setattr(process_manager, "PID_DIR", tmp_path)

    project_id = "test-project-2"
    pid_file = process_manager.pid_file(project_id)

    # Write valid content but then mock the read operation to fail
    pid_file.write_text("12345")

    def mock_read_text(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "read_text", mock_read_text)

    # It should return False and unlink the file
    assert process_manager.is_running(project_id, deploy_type="shell") is False
    assert not pid_file.exists()
