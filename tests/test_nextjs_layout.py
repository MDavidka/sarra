import json
import pytest
from pathlib import Path

from syte.nextjs_layout import is_nextjs_repo


def test_is_nextjs_repo_missing_package_json(tmp_path: Path):
    """Test that a missing package.json returns False."""
    assert is_nextjs_repo(tmp_path) is False


def test_is_nextjs_repo_has_next_in_dependencies(tmp_path: Path):
    """Test when next is in dependencies."""
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({
        "dependencies": {
            "next": "^13.0.0"
        }
    }))
    assert is_nextjs_repo(tmp_path) is True


def test_is_nextjs_repo_has_next_in_dev_dependencies(tmp_path: Path):
    """Test when next is in devDependencies."""
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({
        "devDependencies": {
            "next": "^13.0.0"
        }
    }))
    assert is_nextjs_repo(tmp_path) is True


def test_is_nextjs_repo_no_next(tmp_path: Path):
    """Test when next is not in any dependencies."""
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({
        "dependencies": {
            "react": "^18.0.0"
        }
    }))
    assert is_nextjs_repo(tmp_path) is False


def test_is_nextjs_repo_invalid_json(tmp_path: Path):
    """Test when package.json has invalid JSON."""
    pkg = tmp_path / "package.json"
    pkg.write_text("{ invalid_json: true")
    assert is_nextjs_repo(tmp_path) is False


def test_is_nextjs_repo_os_error_is_dir(tmp_path: Path):
    """Test when package.json is actually a directory (raises OSError)."""
    pkg = tmp_path / "package.json"
    pkg.mkdir()
    assert is_nextjs_repo(tmp_path) is False
