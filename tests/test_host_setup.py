from __future__ import annotations

from syte import host_setup


def test_docker_cli_without_service_installs_docker_engine(monkeypatch) -> None:
    commands: list[list[str]] = []
    available = {"dnf", "curl", "docker", "systemctl", "caddy", "firewall-cmd"}

    monkeypatch.setattr(host_setup, "_command_exists", lambda name: name in available)
    monkeypatch.setattr(host_setup, "_docker_unit_loaded", lambda: False)

    def run_checked(command, label, *, timeout=600.0):
        commands.append(command)
        return True, "ok"

    monkeypatch.setattr(host_setup, "_run_checked", run_checked)

    ok, messages = host_setup._ensure_almalinux_packages()

    assert ok is True
    assert "Docker CLI found but docker.service is missing; installing Docker CE engine." in messages
    assert "Docker already installed." not in messages
    assert any("docker-ce" in command for command in commands)
    assert any("containerd.io" in command for command in commands)


def test_docker_package_is_skipped_when_engine_service_is_loaded(monkeypatch) -> None:
    commands: list[list[str]] = []
    available = {"dnf", "curl", "docker", "systemctl", "caddy", "firewall-cmd"}

    monkeypatch.setattr(host_setup, "_command_exists", lambda name: name in available)
    monkeypatch.setattr(host_setup, "_docker_unit_loaded", lambda: True)

    def run_checked(command, label, *, timeout=600.0):
        commands.append(command)
        return True, "ok"

    monkeypatch.setattr(host_setup, "_run_checked", run_checked)

    ok, messages = host_setup._ensure_almalinux_packages()

    assert ok is True
    assert "Docker already installed." in messages
    assert not any("docker-ce" in command for command in commands)
