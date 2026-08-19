from __future__ import annotations

import stat
from pathlib import Path

from syte import docker_deploy


def test_detect_container_port_prefers_hard_coded_next_start_port(tmp_path: Path) -> None:
    """A Next.js start script must override an incompatible Docker EXPOSE port."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM node:20-alpine\nEXPOSE 3000\nCMD [\"npm\", \"start\"]\n")
    (tmp_path / "package.json").write_text(
        '{"scripts":{"start":"next start -p 9676"},"dependencies":{"next":"15.5.0"}}'
    )

    assert docker_deploy.detect_container_port(dockerfile) == 9676


def test_detect_container_port_uses_expose_when_no_runtime_port(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM nginx:alpine\nEXPOSE 8080/tcp\n")

    assert docker_deploy.detect_container_port(dockerfile) == 8080


def test_runtime_env_file_preserves_values_and_uses_private_permissions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(docker_deploy, "workspace_path", lambda _project_id: tmp_path)
    env_file = docker_deploy._runtime_env_file(
        "demo",
        {"MONGO_API_KEY": "test-only-value", "PORT": "9676"},
    )

    assert env_file.read_text().splitlines() == [
        "MONGO_API_KEY=test-only-value",
        "PORT=9676",
    ]
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_wait_for_http_ready_accepts_responding_release(monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback) -> None:
            return None

    monkeypatch.setattr(docker_deploy.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    assert docker_deploy._wait_for_http_ready(3000, timeout_seconds=0.1) == (True, "HTTP 200")
