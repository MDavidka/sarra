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

def test_start_project_shell_false(mocker):
    """Test that start_project uses shell=False and shlex.split for subprocess.Popen."""
    mock_popen = mocker.patch("syte.process_manager.subprocess.Popen")
    mock_popen.return_value.poll.return_value = None
    mock_popen.return_value.pid = 12345

    mocker.patch("syte.process_manager.is_running", return_value=False)
    mocker.patch("syte.process_manager.validate_shell_command", return_value=None)
    mocker.patch("syte.process_manager.ensure_runtime_for_command", return_value=(True, ""))
    mocker.patch("syte.process_manager.ensure_workspace", return_value=Path("/tmp/ws"))
    mocker.patch("syte.process_manager.read_env_vars", return_value={})
    mocker.patch("syte.process_manager.pid_file", return_value=mocker.MagicMock())
    mocker.patch("syte.process_manager.time.sleep")

    start_command = "npm run dev --port 3000"

    process_manager.start_project(
        project_id="test-project",
        port=3000,
        start_command=start_command,
        env_vars_raw=""
    )

    # Assert Popen was called with expected arguments
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args

    # Check that the first argument is a list from shlex.split
    assert args[0] == ["npm", "run", "dev", "--port", "3000"]

    # Check that shell=False
    assert kwargs.get("shell") is False


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
