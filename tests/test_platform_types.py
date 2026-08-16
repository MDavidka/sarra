"""Tests for the platform layer value objects — enums, limits, healthchecks, naming."""

from __future__ import annotations

import pytest

from syte.platform.types import (
    BuildPack,
    DatabaseType,
    DeploymentRequest,
    DeploymentStatus,
    EnvVar,
    HealthCheckConfig,
    ResourceLimits,
    ResourceType,
    ServerTarget,
    VariableScope,
    container_name,
    image_name,
    managed_labels,
    new_password,
    new_uuid,
    safe_name,
    volume_name,
)


def test_new_uuid_is_dns_label_safe() -> None:
    """These identifiers end up inside container names and preview subdomains."""
    for _ in range(200):
        value = new_uuid()
        assert value[0].isalpha(), value
        assert value.islower()
        assert value.isalnum()
        assert len(value) == 20


def test_new_password_excludes_uri_reserved_characters() -> None:
    """Passwords are interpolated into connection URLs without percent-encoding."""
    forbidden = set("@:/?#[]%&=+ \"'\\`$!*(),;<>{}|^~")
    for _ in range(200):
        assert not (set(new_password(32)) & forbidden)


def test_safe_name_normalises_hostile_input() -> None:
    assert safe_name("My Cool App!!") == "my-cool-app"
    assert safe_name("  ") == "resource"
    assert safe_name("123numeric") == "resource-123numeric"
    assert safe_name("a" * 200).__len__() <= 60
    assert "--" not in safe_name("a  ///  b")


def test_build_pack_capabilities() -> None:
    assert BuildPack.NIXPACKS.generates_dockerfile
    assert BuildPack.STATIC.generates_dockerfile
    assert not BuildPack.DOCKERFILE.generates_dockerfile
    assert not BuildPack.DOCKERIMAGE.needs_git
    # Compose stacks cannot be swapped atomically — same limitation Coolify has.
    assert not BuildPack.DOCKERCOMPOSE.supports_rolling_update
    assert BuildPack.DOCKERFILE.supports_rolling_update


def test_deployment_status_terminality() -> None:
    assert DeploymentStatus.QUEUED.is_active
    assert DeploymentStatus.IN_PROGRESS.is_active
    assert DeploymentStatus.FINISHED.is_terminal
    assert DeploymentStatus.FAILED.is_terminal
    assert DeploymentStatus.CANCELLED_BY_USER.is_terminal
    # The hyphen is Coolify's wire value; API consumers match on it literally.
    assert DeploymentStatus.CANCELLED_BY_USER.value == "cancelled-by-user"


def test_database_type_backup_and_kv_classification() -> None:
    kv = {DatabaseType.REDIS, DatabaseType.KEYDB, DatabaseType.DRAGONFLY}
    for kind in DatabaseType:
        assert kind.is_key_value is (kind in kv)
    assert DatabaseType.POSTGRESQL.supports_backup
    assert DatabaseType.MONGODB.supports_backup
    assert not DatabaseType.REDIS.supports_backup
    # ClickHouse has no logical dump path in Coolify either.
    assert not DatabaseType.CLICKHOUSE.supports_backup


def test_variable_scope_precedence_is_narrowest_wins() -> None:
    order = [
        VariableScope.TEAM,
        VariableScope.PROJECT,
        VariableScope.ENVIRONMENT,
        VariableScope.RESOURCE,
    ]
    assert [s.precedence for s in order] == sorted(s.precedence for s in order)


def test_resource_type_maps_to_table() -> None:
    assert ResourceType.APPLICATION.table == "platform_applications"
    assert ResourceType.DATABASE.table == "platform_databases"
    assert ResourceType.SERVICE.table == "platform_services"


def test_healthcheck_docker_args_and_disable() -> None:
    args = HealthCheckConfig(path="/healthz", interval=10, retries=5).docker_args(
        container_port=8080
    )
    joined = " ".join(args)
    assert "--health-cmd" in joined
    assert "http://127.0.0.1:8080/healthz" in joined
    assert "--health-interval 10s" in joined
    assert "--health-retries 5" in joined
    assert HealthCheckConfig(enabled=False).docker_args() == ["--no-healthcheck"]


def test_healthcheck_custom_command_wins() -> None:
    config = HealthCheckConfig(command="nc -z localhost 5432")
    assert config.probe_command() == "nc -z localhost 5432"
    assert config.compose_healthcheck()["test"] == ["CMD-SHELL", "nc -z localhost 5432"]


def test_healthcheck_from_row_tolerates_junk() -> None:
    config = HealthCheckConfig.from_row(
        {
            "health_check_enabled": 1,
            "health_check_path": "/ping",
            "health_check_port": "",
            "health_check_interval": "not-a-number",
            "health_check_response_text": "",
        }
    )
    assert config.path == "/ping"
    assert config.port is None
    assert config.interval == 30
    assert config.response_text is None


@pytest.mark.parametrize(
    "value",
    ["", "0", "none", "unlimited", "off", "-1", "  NONE  "],
)
def test_resource_limits_treats_sentinels_as_unlimited(value: str) -> None:
    assert ResourceLimits(memory=value, cpus=value).docker_args() == []


def test_resource_limits_emits_only_configured_caps() -> None:
    args = ResourceLimits(
        memory="512m", cpus="0.5", pids_limit=128, memory_swappiness=10
    ).docker_args()
    assert args == [
        "--memory", "512m",
        "--memory-swappiness", "10",
        "--cpus", "0.5",
        "--pids-limit", "128",
    ]


def test_resource_limits_rejects_out_of_range_swappiness() -> None:
    assert "--memory-swappiness" not in ResourceLimits(memory_swappiness=500).docker_args()


def test_env_var_masking_never_leaks_full_secret() -> None:
    assert EnvVar("K", "plain").masked() == "plain"
    assert EnvVar("K", "abc", is_secret=True).masked() == "****"
    masked = EnvVar("K", "supersecretvalue", is_secret=True).masked()
    assert "supersecretvalue" not in masked
    assert masked.startswith("su") and masked.endswith("ue")


def test_naming_helpers_are_deterministic_and_safe() -> None:
    assert container_name("AbC123", "My App!") == "syte-my-app-abc123"
    assert container_name("AbC123", "My App!") == container_name("AbC123", "My App!")
    assert container_name("u1", "app", suffix="new").endswith("-new")
    assert image_name("u1", "My App") == "syte/my-app-u1"
    assert volume_name("u1", "pg data") == "syte-u1-pg-data"


def test_managed_labels_drop_empty_values_and_include_pr() -> None:
    labels = managed_labels(
        resource_uuid="u1",
        resource_type=ResourceType.APPLICATION,
        resource_name="web",
        pull_request_id=7,
    )
    assert labels["syte.managed"] == "true"
    assert labels["syte.resource.uuid"] == "u1"
    assert labels["syte.pull_request"] == "7"
    assert "syte.server.uuid" not in labels
    assert managed_labels(
        resource_uuid="u1", resource_type=ResourceType.DATABASE
    ).get("syte.pull_request") is None


def test_server_target_local_has_no_ssh_prefix() -> None:
    assert ServerTarget().ssh_prefix() == []


def test_server_target_remote_ssh_prefix_is_non_interactive() -> None:
    prefix = ServerTarget(
        uuid="s1", ip="10.0.0.5", user="deploy", port=2222,
        private_key_path="/keys/id", is_local=False,
    ).ssh_prefix()
    joined = " ".join(prefix)
    # BatchMode is what stops a host-key or password prompt hanging the
    # deployment worker with nobody able to answer it.
    assert "BatchMode=yes" in joined
    assert "-p 2222" in joined
    assert "-i /keys/id" in joined
    assert prefix[-1] == "deploy@10.0.0.5"


def test_deployment_request_derived_flags() -> None:
    assert not DeploymentRequest(resource_uuid="u1").is_rollback
    assert DeploymentRequest(resource_uuid="u1", rollback_image_tag="abc").is_rollback
    assert DeploymentRequest(resource_uuid="u1", pull_request_id=4).is_preview
    assert not DeploymentRequest(resource_uuid="u1").is_preview
