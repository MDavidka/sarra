from syte.process_manager import validate_shell_command
from unittest.mock import patch

def test_validate_shell_command_empty():
    assert validate_shell_command("") == "No start command configured."
    assert validate_shell_command("   ") == "No start command configured."
    assert validate_shell_command(None) == "No start command configured."

@patch("shutil.which")
def test_validate_shell_command_npm_not_installed(mock_which):
    mock_which.return_value = None
    res = validate_shell_command("npm run start")
    assert "npm is not installed on this server." in res

@patch("shutil.which")
def test_validate_shell_command_npm_installed(mock_which):
    mock_which.return_value = "/usr/bin/npm"
    assert validate_shell_command("npm run start") is None

@patch("shutil.which")
def test_validate_shell_command_regular_command(mock_which):
    # which shouldn't be called if it's not a known package manager,
    # but we can return None to be safe.
    mock_which.return_value = None
    assert validate_shell_command("python app.py") is None

def test_validate_shell_command_valid_commands_with_installed_tools():
    with patch("shutil.which", return_value="/usr/bin/tool"):
        assert validate_shell_command("npm start") is None
