import pytest
from syte.workspace import read_env_vars

def test_read_env_vars_valid_json_string():
    """Test read_env_vars with a valid JSON string."""
    raw = '{"key1": "value1", "key2": "value2"}'
    result = read_env_vars(raw)
    assert result == {"key1": "value1", "key2": "value2"}

def test_read_env_vars_dictionary():
    """Test read_env_vars with a dictionary."""
    raw = {"key1": "value1", "key2": "value2"}
    result = read_env_vars(raw)
    assert result == {"key1": "value1", "key2": "value2"}

def test_read_env_vars_empty_string():
    """Test read_env_vars with an empty string."""
    raw = ""
    result = read_env_vars(raw)
    assert result == {}

def test_read_env_vars_none():
    """Test read_env_vars with None, which could happen if raw parameter allows it or by mistake."""
    raw = None
    result = read_env_vars(raw)
    assert result == {}

def test_read_env_vars_malformed_json():
    """Test read_env_vars with malformed JSON string (e.g. missing quote/brace)."""
    raw = '{"key1": "value1", "key2": "value2"'  # Missing closing brace
    result = read_env_vars(raw)
    assert result == {}

    raw = 'not a json string'
    result = read_env_vars(raw)
    assert result == {}

    raw = '{"invalid_key": invalid_value}'
    result = read_env_vars(raw)
    assert result == {}

@pytest.mark.asyncio
async def test_execute_command_injection_prevention(mocker):
    """Test that execute_command uses shlex.split and shell=False, preventing command injection."""
    from syte.workspace_api import execute_command
    from syte.database import get_project

    mock_project = {
        "id": "test_project",
        "env_vars": "{}"
    }

    mocker.patch("syte.workspace_api.get_project", return_value=mock_project)

    # We mock out _resolve_workspace_path to return a valid Path-like object
    mock_path = mocker.Mock()
    mock_path.is_dir.return_value = True
    mocker.patch("syte.workspace_api._resolve_workspace_path", return_value=mock_path)

    # We mock _append_command_log and record_workspace_activity to avoid side effects
    mocker.patch("syte.workspace_api._append_command_log")
    mocker.patch("syte.agent_activity.record_workspace_activity")

    # Mock subprocess.run
    mock_subprocess_run = mocker.patch("subprocess.run")
    mock_subprocess_run.return_value.returncode = 0
    mock_subprocess_run.return_value.stdout = "output"
    mock_subprocess_run.return_value.stderr = ""

    # Command with injection attempt
    injected_cmd = "ls -la; echo 'injected' && cat /etc/passwd"

    code, output = await execute_command("test_project", injected_cmd)

    assert code == 0

    # Verify subprocess.run was called with split arguments and shell=False
    mock_subprocess_run.assert_called_once()
    args, kwargs = mock_subprocess_run.call_args

    assert kwargs.get("shell") is False
    # shlex.split should split this into: ['ls', '-la;', 'echo', 'injected', '&&', 'cat', '/etc/passwd']
    assert args[0] == ["ls", "-la;", "echo", "injected", "&&", "cat", "/etc/passwd"]
