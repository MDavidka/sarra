import pytest
from syte.workspace import read_env_vars, slugify, workspace_path, ensure_workspace

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


def test_slugify():
    """Test slugify function handles various strings correctly."""
    assert slugify("My Project") == "my-project"
    assert slugify("  Spaces   And   Caps  ") == "spaces-and-caps"
    assert slugify("Special !@# Characters $%^") == "special-characters"
    assert slugify("---Leading and Trailing---") == "leading-and-trailing"
    assert slugify("123 Numbers 456") == "123-numbers-456"
    assert slugify("") == "project"
    assert slugify("!@#$%^&*") == "project"
    assert slugify("a" * 100) == "a" * 100


def test_workspace_path(mocker, tmp_path):
    """Test workspace_path uses the configured resolved_workspaces_dir."""
    mocker.patch("syte.workspace.settings", resolved_workspaces_dir=tmp_path)

    project_id = "test-project-123"
    path = workspace_path(project_id)

    assert path == tmp_path / project_id


def test_ensure_workspace(mocker, tmp_path):
    """Test ensure_workspace creates the workspace, data, and app directories."""
    mocker.patch("syte.workspace.settings", resolved_workspaces_dir=tmp_path)

    project_id = "test-project-456"
    path = ensure_workspace(project_id)

    # Assert the correct path is returned
    assert path == tmp_path / project_id

    # Assert all required directories were created
    assert path.exists()
    assert path.is_dir()
    assert (path / "data").exists()
    assert (path / "data").is_dir()
    assert (path / "app").exists()
    assert (path / "app").is_dir()

    # Assert idempotency - calling it again shouldn't fail
    ensure_workspace(project_id)
    assert path.exists()
