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

def test_read_env_vars_invalid_json():
    """Test read_env_vars with invalid JSON strings explicitly."""
    result = read_env_vars('invalid json string')
    assert result == {}
