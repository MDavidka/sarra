"""Tests for the managed database catalog — all eight Coolify engines.

Sarra had no database provisioning before this, so every engine gets coverage of
the four things that actually break in production: the init env vars, the server
command line (key-value engines take their password there, not via env), the
readiness probe, and the connection URL.
"""

from __future__ import annotations

import shlex
from urllib.parse import urlsplit

import pytest

from syte.platform import database_catalog as catalog
from syte.platform.types import DatabaseType


@pytest.fixture(params=list(DatabaseType), ids=lambda t: t.value)
def engine_type(request: pytest.FixtureRequest) -> DatabaseType:
    return request.param


def sample(kind: DatabaseType) -> dict[str, object]:
    """Deterministic provisioned row for assertions."""
    row = catalog.provision_defaults(kind, "My App DB")
    row["password"] = "TestPass123"
    row["root_password"] = "RootPass456"
    return row


# --------------------------------------------------------------------------- #
# Catalog integrity
# --------------------------------------------------------------------------- #


def test_every_database_type_has_an_engine() -> None:
    assert set(catalog.ENGINES) == set(DatabaseType)


def test_catalog_is_serialisable_for_the_api() -> None:
    entries = catalog.catalog()
    assert len(entries) == len(DatabaseType)
    for entry in entries:
        assert entry["type"] in {t.value for t in DatabaseType}
        assert entry["default_port"] > 0
        assert entry["supported_versions"]
        assert isinstance(entry["supports_backup"], bool)


def test_engine_for_accepts_enum_and_wire_value() -> None:
    assert catalog.engine_for(DatabaseType.REDIS).name == "Redis"
    assert catalog.engine_for("redis").name == "Redis"


def test_engine_for_rejects_unknown_type_with_actionable_message() -> None:
    with pytest.raises(ValueError, match="Supported:"):
        catalog.engine_for("cockroachdb")


# --------------------------------------------------------------------------- #
# Provisioning
# --------------------------------------------------------------------------- #


def test_provision_defaults_generates_credentials(engine_type: DatabaseType) -> None:
    row = catalog.provision_defaults(engine_type, "My App DB")
    assert row["database_type"] == engine_type.value
    assert row["password"]
    assert row["internal_port"] == catalog.ENGINES[engine_type].default_port
    assert row["status"] == "stopped"


def test_provision_defaults_passwords_are_url_safe(engine_type: DatabaseType) -> None:
    """Generated credentials must survive being embedded in a connection URL."""
    row = catalog.provision_defaults(engine_type, "db")
    url = catalog.connection_url(row, host="db-host")
    # Round-tripping through a URL parser proves nothing needed escaping.
    parts = urlsplit(url)
    assert parts.hostname == "db-host"
    if not engine_type.is_key_value or row["password"]:
        assert str(row["password"]) in url


def test_only_mysql_family_gets_a_root_password() -> None:
    for kind in DatabaseType:
        row = catalog.provision_defaults(kind, "db")
        has_root = bool(row["root_password"])
        assert has_root is (kind in (DatabaseType.MYSQL, DatabaseType.MARIADB)), kind


def test_key_value_engines_get_no_logical_database_name() -> None:
    for kind in DatabaseType:
        row = catalog.provision_defaults(kind, "My App DB")
        if kind.is_key_value:
            assert row["database_name"] == ""
        else:
            # Must be a valid SQL identifier — hyphens are not.
            assert row["database_name"] == "my_app_db"


def test_version_override_rewrites_the_image_tag() -> None:
    row = catalog.provision_defaults(DatabaseType.POSTGRESQL, "db", version="15-alpine")
    assert row["image"] == "postgres:15-alpine"
    assert row["version"] == "15-alpine"


def test_supplied_credentials_are_respected() -> None:
    row = catalog.provision_defaults(
        DatabaseType.POSTGRESQL, "db",
        username="app", password="secret", database_name="appdb",
    )
    assert (row["username"], row["password"], row["database_name"]) == ("app", "secret", "appdb")


# --------------------------------------------------------------------------- #
# Container configuration
# --------------------------------------------------------------------------- #


def test_postgres_pgdata_points_at_a_subdirectory() -> None:
    """A fresh volume contains lost+found and initdb refuses a non-empty dir."""
    env = catalog.container_env(sample(DatabaseType.POSTGRESQL))
    assert env["PGDATA"] == "/var/lib/postgresql/data/pgdata"
    assert env["PGDATA"].startswith(catalog.data_volume_mount(sample(DatabaseType.POSTGRESQL)))


def test_relational_engines_configure_user_and_database_via_env() -> None:
    expectations = {
        DatabaseType.POSTGRESQL: ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"),
        DatabaseType.MYSQL: ("MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"),
        DatabaseType.MARIADB: ("MARIADB_USER", "MARIADB_PASSWORD", "MARIADB_DATABASE"),
        DatabaseType.MONGODB: (
            "MONGO_INITDB_ROOT_USERNAME", "MONGO_INITDB_ROOT_PASSWORD", "MONGO_INITDB_DATABASE",
        ),
        DatabaseType.CLICKHOUSE: ("CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DB"),
    }
    for kind, keys in expectations.items():
        env = catalog.container_env(sample(kind))
        for key in keys:
            assert key in env, (kind, key)


def test_mariadb_sets_both_variable_families() -> None:
    """MariaDB 11 prefers MARIADB_* but older tags only honour MYSQL_*."""
    env = catalog.container_env(sample(DatabaseType.MARIADB))
    assert env["MARIADB_ROOT_PASSWORD"] == env["MYSQL_ROOT_PASSWORD"] == "RootPass456"


def test_key_value_engines_take_password_on_the_command_line() -> None:
    for kind in (DatabaseType.REDIS, DatabaseType.KEYDB, DatabaseType.DRAGONFLY):
        row = sample(kind)
        assert catalog.container_env(row) == {}, kind
        command = catalog.container_command(row)
        assert command, kind
        assert any("TestPass123" in part for part in command), kind


def test_redis_family_enables_persistence() -> None:
    for kind in (DatabaseType.REDIS, DatabaseType.KEYDB):
        assert "--appendonly" in catalog.container_command(sample(kind))


def test_relational_engines_use_the_image_default_entrypoint() -> None:
    for kind in (DatabaseType.POSTGRESQL, DatabaseType.MYSQL, DatabaseType.MONGODB):
        assert catalog.container_command(sample(kind)) == []


def test_dragonfly_requires_unlimited_memlock() -> None:
    """Dragonfly exits immediately without it."""
    args = catalog.container_extra_args(sample(DatabaseType.DRAGONFLY))
    assert args == ["--ulimit", "memlock=-1"]


def test_clickhouse_requires_raised_file_descriptor_limit() -> None:
    args = catalog.container_extra_args(sample(DatabaseType.CLICKHOUSE))
    assert "nofile=262144:262144" in args


def test_clickhouse_exposes_http_and_native_ports() -> None:
    ports = catalog.exposed_ports(sample(DatabaseType.CLICKHOUSE))
    assert ports == [8123, catalog.CLICKHOUSE_NATIVE_PORT]


def test_single_port_engines_expose_one_port(engine_type: DatabaseType) -> None:
    if engine_type is DatabaseType.CLICKHOUSE:
        pytest.skip("ClickHouse intentionally exposes two protocols")
    assert len(catalog.exposed_ports(sample(engine_type))) == 1


def test_every_engine_declares_a_data_volume_path(engine_type: DatabaseType) -> None:
    path = catalog.data_volume_mount(sample(engine_type))
    assert path.startswith("/")


# --------------------------------------------------------------------------- #
# Connection strings
# --------------------------------------------------------------------------- #


def test_connection_url_scheme_per_engine() -> None:
    expected = {
        DatabaseType.POSTGRESQL: "postgres",
        DatabaseType.MYSQL: "mysql",
        DatabaseType.MARIADB: "mysql",
        DatabaseType.MONGODB: "mongodb",
        DatabaseType.REDIS: "redis",
        DatabaseType.KEYDB: "redis",
        DatabaseType.DRAGONFLY: "redis",
        DatabaseType.CLICKHOUSE: "http",
    }
    for kind, scheme in expected.items():
        assert catalog.connection_url(sample(kind), host="h").startswith(f"{scheme}://"), kind


def test_mongo_url_sets_auth_source_admin() -> None:
    """The root user lives in `admin`; omitting authSource is the classic failure."""
    url = catalog.connection_url(sample(DatabaseType.MONGODB), host="h")
    assert "authSource=admin" in url


def test_connection_url_can_mask_the_password() -> None:
    url = catalog.connection_url(sample(DatabaseType.POSTGRESQL), host="h", include_password=False)
    assert "TestPass123" not in url
    assert "***" in url


def test_connection_url_percent_encodes_hostile_passwords() -> None:
    row = sample(DatabaseType.POSTGRESQL)
    row["password"] = "p@ss/w:rd?"
    url = catalog.connection_url(row, host="h")
    assert "p@ss/w:rd?" not in url
    assert urlsplit(url).hostname == "h"


def test_redis_url_omits_credentials_when_no_password_set() -> None:
    row = sample(DatabaseType.REDIS)
    row["password"] = ""
    assert catalog.connection_url(row, host="h") == "redis://h:6379/0"


def test_connection_details_always_reports_internal_and_gates_public() -> None:
    row = sample(DatabaseType.POSTGRESQL)
    private = catalog.connection_details(row, internal_host="syte-db-x", public_host="1.2.3.4")
    assert private["internal_url"].startswith("postgres://")
    assert private["is_public"] is False
    assert "public_url" not in private

    row["is_public"] = True
    row["public_port"] = 55432
    public = catalog.connection_details(row, internal_host="syte-db-x", public_host="1.2.3.4")
    assert public["public_url"].endswith("@1.2.3.4:55432/my_app_db")


def test_connection_details_adds_clickhouse_native_url() -> None:
    details = catalog.connection_details(sample(DatabaseType.CLICKHOUSE), internal_host="ch")
    assert details["native_port"] == catalog.CLICKHOUSE_NATIVE_PORT
    assert details["native_url"].startswith("clickhouse://")


# --------------------------------------------------------------------------- #
# Readiness probes
# --------------------------------------------------------------------------- #


def test_every_engine_has_a_shell_safe_readiness_probe(engine_type: DatabaseType) -> None:
    probe = catalog.readiness_command(sample(engine_type))
    assert probe
    # Balanced quoting — the probe is handed to `sh -c`.
    shlex.split(probe)


def test_readiness_probes_authenticate() -> None:
    """A Postgres still running initdb accepts TCP but rejects queries."""
    assert "-U postgres" in catalog.readiness_command(sample(DatabaseType.POSTGRESQL))
    assert "RootPass456" in catalog.readiness_command(sample(DatabaseType.MYSQL))
    assert "TestPass123" in catalog.readiness_command(sample(DatabaseType.REDIS))
    assert "authenticationDatabase admin" in catalog.readiness_command(sample(DatabaseType.MONGODB))


def test_mariadb_uses_the_shipped_healthcheck_script() -> None:
    """It also waits for InnoDB recovery, which a bare ping does not."""
    assert catalog.readiness_command(sample(DatabaseType.MARIADB)) == (
        "healthcheck.sh --connect --innodb_initialized"
    )


# --------------------------------------------------------------------------- #
# Backup / restore
# --------------------------------------------------------------------------- #


def test_dump_command_only_for_engines_that_support_it(engine_type: DatabaseType) -> None:
    row = sample(engine_type)
    if engine_type.supports_backup:
        command = catalog.dump_command(row, output_path="/backup/out.sql")
        assert "/backup/out.sql" in command
        shlex.split(command.split(">")[0])
    else:
        with pytest.raises(ValueError, match="does not support scheduled logical backups"):
            catalog.dump_command(row, output_path="/backup/out.sql")


def test_postgres_dump_is_idempotently_restorable() -> None:
    command = catalog.dump_command(sample(DatabaseType.POSTGRESQL), output_path="/b.sql")
    assert "--clean" in command
    assert "--if-exists" in command
    assert "--no-owner" in command


def test_mysql_dump_uses_a_consistent_non_locking_snapshot() -> None:
    command = catalog.dump_command(sample(DatabaseType.MYSQL), output_path="/b.sql")
    assert "--single-transaction" in command


def test_dump_all_switches_to_the_cluster_wide_tool() -> None:
    assert "pg_dumpall" in catalog.dump_command(
        sample(DatabaseType.POSTGRESQL), output_path="/b.sql", dump_all=True
    )
    assert "--all-databases" in catalog.dump_command(
        sample(DatabaseType.MYSQL), output_path="/b.sql", dump_all=True
    )


def test_multiple_databases_use_the_databases_flag() -> None:
    command = catalog.dump_command(
        sample(DatabaseType.MYSQL), output_path="/b.sql", databases=("a", "b")
    )
    assert "--databases" in command


def test_mariadb_uses_its_own_dump_binary() -> None:
    assert catalog.dump_command(sample(DatabaseType.MARIADB), output_path="/b.sql").startswith(
        "mariadb-dump"
    )


def test_dump_extension_matches_the_produced_artifact() -> None:
    assert catalog.dump_extension(sample(DatabaseType.MONGODB)) == "archive.gz"
    assert catalog.dump_extension(sample(DatabaseType.POSTGRESQL)) == "sql"


def test_restore_command_round_trips_supported_engines(engine_type: DatabaseType) -> None:
    row = sample(engine_type)
    if engine_type.supports_backup:
        assert "/b.sql" in catalog.restore_command(row, input_path="/b.sql")
    else:
        with pytest.raises(ValueError):
            catalog.restore_command(row, input_path="/b.sql")


def test_dump_and_restore_quote_hostile_paths() -> None:
    command = catalog.dump_command(
        sample(DatabaseType.POSTGRESQL), output_path="/tmp/a b; rm -rf /"
    )
    assert "'/tmp/a b; rm -rf /'" in command


# --------------------------------------------------------------------------- #
# Interactive client
# --------------------------------------------------------------------------- #


def test_client_command_is_argv_not_a_shell_string(engine_type: DatabaseType) -> None:
    """It is passed straight to `docker exec -it` and must not hit a shell."""
    argv = catalog.client_command(sample(engine_type))
    assert isinstance(argv, list)
    assert argv
    assert all(isinstance(part, str) for part in argv)


def test_client_command_authenticates_per_engine() -> None:
    assert "PGPASSWORD=TestPass123" in catalog.client_command(sample(DatabaseType.POSTGRESQL))
    assert "-pRootPass456" in catalog.client_command(sample(DatabaseType.MYSQL))
    assert "TestPass123" in catalog.client_command(sample(DatabaseType.REDIS))


# --------------------------------------------------------------------------- #
# Mount points
# --------------------------------------------------------------------------- #


def test_init_script_mount_only_for_engines_with_entrypoint_support() -> None:
    assert catalog.init_script_mount(sample(DatabaseType.POSTGRESQL)) == "/docker-entrypoint-initdb.d"
    assert catalog.init_script_mount(sample(DatabaseType.REDIS)) == ""


def test_config_file_mount_paths_are_absolute_or_empty(engine_type: DatabaseType) -> None:
    path = catalog.config_file_mount(sample(engine_type))
    assert path == "" or path.startswith("/")
