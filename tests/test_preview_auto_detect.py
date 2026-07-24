"""Preview command auto-detection for AI-scaffolded stacks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from syte.preview_manager import (
    detect_dev_command,
    preview_process_env,
    project_stack_hint,
)


def _write_pkg(repo: Path, data: dict) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "package.json").write_text(json.dumps(data), encoding="utf-8")


def test_detect_next_dev_script(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _write_pkg(
        repo,
        {
            "scripts": {"dev": "next dev"},
            "dependencies": {"next": "14.0.0", "react": "18.0.0"},
        },
    )
    cmd = detect_dev_command(repo)
    assert cmd is not None
    assert "npm run dev" in cmd
    assert "--hostname 0.0.0.0" in cmd
    assert "$SYTE_PREVIEW_PORT" in cmd


def test_detect_next_from_deps_without_dev(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _write_pkg(repo, {"dependencies": {"next": "14.0.0", "react": "18.0.0"}})
    cmd = detect_dev_command(repo)
    assert cmd is not None
    assert "next dev" in cmd
    assert "$SYTE_PREVIEW_PORT" in cmd


def test_detect_vite_dev_script(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _write_pkg(
        repo,
        {
            "scripts": {"dev": "vite"},
            "devDependencies": {"vite": "5.0.0"},
        },
    )
    (repo / "vite.config.js").write_text("export default {}", encoding="utf-8")
    cmd = detect_dev_command(repo)
    assert cmd is not None
    assert "vite" in cmd
    assert "--host 0.0.0.0" in cmd


def test_detect_cra_start_only(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _write_pkg(
        repo,
        {
            "scripts": {"start": "react-scripts start"},
            "dependencies": {"react-scripts": "5.0.0", "react": "18.0.0"},
        },
    )
    assert detect_dev_command(repo) == "npm start"


def test_detect_express_start_only(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _write_pkg(
        repo,
        {
            "scripts": {"start": "node index.js"},
            "dependencies": {"express": "4.19.0"},
        },
    )
    (repo / "index.js").write_text("console.log('ok')", encoding="utf-8")
    assert detect_dev_command(repo) == "npm start"


def test_detect_astro_dev(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _write_pkg(
        repo,
        {
            "scripts": {"dev": "astro dev"},
            "dependencies": {"astro": "4.0.0"},
        },
    )
    cmd = detect_dev_command(repo)
    assert cmd is not None
    assert "astro" in cmd
    assert "--host 0.0.0.0" in cmd


def test_detect_nuxt_from_deps(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _write_pkg(
        repo,
        {
            "scripts": {"dev": "nuxt dev"},
            "dependencies": {"nuxt": "3.0.0"},
        },
    )
    cmd = detect_dev_command(repo)
    assert cmd is not None
    assert "nuxi" in cmd or "nuxt" in cmd


def test_detect_fastapi_python(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir(parents=True)
    (repo / "requirements.txt").write_text("fastapi==0.115.0\nuvicorn[standard]==0.30.0\n")
    (repo / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n",
        encoding="utf-8",
    )
    cmd = detect_dev_command(repo)
    assert cmd is not None
    assert "uvicorn" in cmd
    assert "main:app" in cmd
    assert "$SYTE_PREVIEW_PORT" in cmd


def test_detect_flask_python(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir(parents=True)
    (repo / "requirements.txt").write_text("flask==3.0.0\n")
    (repo / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n",
        encoding="utf-8",
    )
    cmd = detect_dev_command(repo)
    assert cmd is not None
    assert "flask" in cmd
    assert "$SYTE_PREVIEW_PORT" in cmd


def test_detect_static_html(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir(parents=True)
    (repo / "index.html").write_text("<html><body>Hi</body></html>", encoding="utf-8")
    cmd = detect_dev_command(repo)
    assert cmd is not None
    assert "http.server" in cmd
    assert "$SYTE_PREVIEW_PORT" in cmd


def test_detect_static_public_index(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    (repo / "public").mkdir(parents=True)
    (repo / "public" / "index.html").write_text("<html></html>", encoding="utf-8")
    cmd = detect_dev_command(repo)
    assert cmd is not None
    assert "http.server" in cmd


def test_stack_hint_javascript(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir(parents=True)
    (repo / "index.js").write_text("console.log(1)", encoding="utf-8")
    # No package.json — fall back to SYTE_STACK hint.
    assert detect_dev_command(repo, stack_hint="javascript") == "node index.js"


def test_stack_hint_html5_empty_dir(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir(parents=True)
    cmd = detect_dev_command(repo, stack_hint="html5")
    assert cmd is not None
    assert "http.server" in cmd


def test_empty_repo_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir(parents=True)
    assert detect_dev_command(repo) is None


def test_project_stack_hint_from_env() -> None:
    project = {"env_vars": json.dumps({"SYTE_STACK": "python"})}
    assert project_stack_hint(project) == "python"
    assert project_stack_hint({"env_vars": "{}"}) is None


def test_preview_process_env_sets_host_and_flask(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir(parents=True)
    (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
    env = preview_process_env({"env_vars": "{}"}, 4321, repo=repo)
    assert env["PORT"] == "4321"
    assert env["HOST"] == "0.0.0.0"
    assert env["HOSTNAME"] == "0.0.0.0"
    assert env["FLASK_APP"] == "main.py"
    assert "DANGEROUSLY_DISABLE_HOST_CHECK" in env


def test_node_main_field_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _write_pkg(repo, {"main": "server.js", "dependencies": {}})
    (repo / "server.js").write_text("console.log('ok')", encoding="utf-8")
    assert detect_dev_command(repo) == "node server.js"


def test_generic_dev_script_gets_host_port(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _write_pkg(
        repo,
        {
            "scripts": {"dev": "tsx watch src/index.ts"},
            "dependencies": {"tsx": "4.0.0"},
        },
    )
    cmd = detect_dev_command(repo)
    assert cmd == "npm run dev -- --host 0.0.0.0 --port $SYTE_PREVIEW_PORT"
