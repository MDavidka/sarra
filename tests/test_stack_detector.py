from pathlib import Path

from syte.stack_detector import detect_stack


def test_detects_next_and_redacts_env_values(tmp_path, monkeypatch):
    workspace = tmp_path / "demo" / "app"
    workspace.mkdir(parents=True)
    (workspace / "package.json").write_text('{"dependencies":{"next":"16.0.0"},"scripts":{"build":"next build","start":"next start"}}')
    (workspace / "pnpm-lock.yaml").write_text("lockfileVersion: 9")
    (workspace / ".env").write_text("DATABASE_URL=super-secret\nPUBLIC_FLAG=true\n")
    monkeypatch.setattr("syte.stack_detector.ensure_workspace", lambda _: tmp_path / "demo")
    result = detect_stack("demo")
    assert result["framework"] == "Next.js"
    assert result["package_manager"] == "pnpm"
    assert {item["name"] for item in result["env_keys"]} == {"DATABASE_URL", "PUBLIC_FLAG"}
    assert "super-secret" not in str(result)


def test_docker_takes_precedence(tmp_path, monkeypatch):
    workspace = tmp_path / "demo" / "app"
    workspace.mkdir(parents=True)
    (workspace / "Dockerfile").write_text("FROM node:22")
    monkeypatch.setattr("syte.stack_detector.ensure_workspace", lambda _: tmp_path / "demo")
    result = detect_stack("demo")
    assert result["deploy_type"] == "docker"
    assert result["dockerfile_path"] == "Dockerfile"


def test_empty_workspace_warns(tmp_path, monkeypatch):
    (tmp_path / "demo" / "app").mkdir(parents=True)
    monkeypatch.setattr("syte.stack_detector.ensure_workspace", lambda _: tmp_path / "demo")
    result = detect_stack("demo")
    assert result["warnings"]
  
