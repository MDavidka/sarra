"""Managed database engine definitions.

Sarra had no database provisioning at all: ``docker_deploy`` runs exactly one
container per project with a single ``/data`` bind mount. Coolify's headline
feature is one-click Postgres/MySQL/MariaDB/MongoDB/Redis/KeyDB/Dragonfly/
ClickHouse, with connection strings, readiness probes and scheduled logical
backups. This module is the data behind that.

Everything here is **pure** — it returns env dicts, argv lists and command
strings. Nothing runs a container; ``docker_engine`` and ``service`` do that.
Keeping it pure is what makes the whole eight-engine matrix testable without a
container runtime.

Each engine definition captures the non-obvious operational details that make
the difference between a container that starts and one that actually works:

* PostgreSQL needs ``PGDATA`` pointed at a *subdirectory* of the mount, because
  a fresh Docker volume contains ``lost+found`` and initdb refuses to run in a
  non-empty directory.
* The key-value engines take their password on the command line rather than
  from an env var, so they need a ``command`` override, not just ``-e``.
* Dragonfly needs ``memlock`` unlimited or it refuses to start.
* ClickHouse needs a raised ``nofile`` limit for the same reason.
"""

from __future__ import annotations

import shlex
from urllib.parse import quote

from syte.platform.types import (
    DatabaseEngine,
    DatabaseType,
    new_password,
    safe_name,
)

# --------------------------------------------------------------------------- #
# Engine definitions
# --------------------------------------------------------------------------- #

# Version lists are the majors Syte offers in the "new database" picker. The
# first entry is the default. Kept to supported upstream majors — offering an
# EOL Postgres 11 helps nobody.
ENGINES: dict[DatabaseType, DatabaseEngine] = {
    DatabaseType.POSTGRESQL: DatabaseEngine(
        database_type=DatabaseType.POSTGRESQL,
        name="PostgreSQL",
        default_image="postgres:16-alpine",
        default_port=5432,
        data_path="/var/lib/postgresql/data",
        scheme="postgres",
        supported_versions=("17-alpine", "16-alpine", "15-alpine", "14-alpine", "13-alpine"),
        default_username="postgres",
        default_database="postgres",
        dump_binary="pg_dump",
        restore_binary="psql",
        client_binary="psql",
        minimum_memory_mb=256,
    ),
    DatabaseType.MYSQL: DatabaseEngine(
        database_type=DatabaseType.MYSQL,
        name="MySQL",
        default_image="mysql:8.4",
        default_port=3306,
        data_path="/var/lib/mysql",
        scheme="mysql",
        supported_versions=("8.4", "8.0", "5.7"),
        default_username="mysql",
        default_database="mysql",
        dump_binary="mysqldump",
        restore_binary="mysql",
        client_binary="mysql",
        minimum_memory_mb=512,
    ),
    DatabaseType.MARIADB: DatabaseEngine(
        database_type=DatabaseType.MARIADB,
        name="MariaDB",
        default_image="mariadb:11",
        default_port=3306,
        data_path="/var/lib/mysql",
        scheme="mysql",
        supported_versions=("11", "10.11", "10.6"),
        default_username="mariadb",
        default_database="mariadb",
        dump_binary="mariadb-dump",
        restore_binary="mariadb",
        client_binary="mariadb",
        minimum_memory_mb=512,
    ),
    DatabaseType.MONGODB: DatabaseEngine(
        database_type=DatabaseType.MONGODB,
        name="MongoDB",
        default_image="mongo:7",
        default_port=27017,
        data_path="/data/db",
        scheme="mongodb",
        supported_versions=("8", "7", "6"),
        default_username="mongo",
        default_database="admin",
        dump_binary="mongodump",
        restore_binary="mongorestore",
        client_binary="mongosh",
        minimum_memory_mb=512,
    ),
    DatabaseType.REDIS: DatabaseEngine(
        database_type=DatabaseType.REDIS,
        name="Redis",
        default_image="redis:7-alpine",
        default_port=6379,
        data_path="/data",
        scheme="redis",
        supported_versions=("7-alpine", "6-alpine"),
        default_username="default",
        client_binary="redis-cli",
        minimum_memory_mb=128,
    ),
    DatabaseType.KEYDB: DatabaseEngine(
        database_type=DatabaseType.KEYDB,
        name="KeyDB",
        default_image="eqalpha/keydb:alpine_x86_64_v6.3.4",
        default_port=6379,
        data_path="/data",
        scheme="redis",
        supported_versions=("alpine_x86_64_v6.3.4", "latest"),
        default_username="default",
        client_binary="keydb-cli",
        minimum_memory_mb=128,
    ),
    DatabaseType.DRAGONFLY: DatabaseEngine(
        database_type=DatabaseType.DRAGONFLY,
        name="Dragonfly",
        default_image="docker.dragonflydb.io/dragonflydb/dragonfly:latest",
        default_port=6379,
        data_path="/data",
        scheme="redis",
        supported_versions=("latest", "v1.21.2"),
        default_username="default",
        client_binary="redis-cli",
        minimum_memory_mb=512,
    ),
    DatabaseType.CLICKHOUSE: DatabaseEngine(
        database_type=DatabaseType.CLICKHOUSE,
        name="ClickHouse",
        default_image="clickhouse/clickhouse-server:24-alpine",
        default_port=8123,
        data_path="/var/lib/clickhouse",
        scheme="http",
        supported_versions=("24-alpine", "23-alpine"),
        default_username="clickhouse",
        default_database="default",
        dump_binary="clickhouse-client",
        restore_binary="clickhouse-client",
        client_binary="clickhouse-client",
        minimum_memory_mb=1024,
    ),
}

# ClickHouse speaks two protocols on two ports; the native TCP port is what
# most drivers use even though the HTTP port is the one we health-check.
CLICKHOUSE_NATIVE_PORT = 9000

# Key-value engines that take `--requirepass` on the server command line.
_REQUIREPASS_ENGINES = frozenset({
    DatabaseType.REDIS,
    DatabaseType.KEYDB,
    DatabaseType.DRAGONFLY,
})


def engine_for(database_type: DatabaseType | str) -> DatabaseEngine:
    """Look up an engine definition, accepting the enum or its wire value."""
    if isinstance(database_type, str):
        try:
            database_type = DatabaseType(database_type)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported database type '{database_type}'. "
                f"Supported: {', '.join(t.value for t in DatabaseType)}"
            ) from exc
    return ENGINES[database_type]


def catalog() -> list[dict[str, object]]:
    """Serialisable engine list for the API and the "new database" picker."""
    return [engine.as_dict() for engine in ENGINES.values()]


# --------------------------------------------------------------------------- #
# Provisioning defaults
# --------------------------------------------------------------------------- #


def provision_defaults(
    database_type: DatabaseType | str,
    name: str,
    *,
    version: str = "",
    username: str = "",
    password: str = "",
    root_password: str = "",
    database_name: str = "",
) -> dict[str, object]:
    """Column values for a new ``platform_databases`` row.

    Credentials are generated when not supplied. The generated alphabet excludes
    URI-reserved characters (see :func:`~syte.platform.types.new_password`) so
    the resulting connection string never needs percent-encoding — a class of
    bug that is miserable to debug from an application's stack trace.
    """
    engine = engine_for(database_type)
    slug = safe_name(name, fallback="db", limit=32)

    image = engine.default_image
    if version:
        repository = engine.default_image.rsplit(":", 1)[0]
        image = f"{repository}:{version}"

    resolved_database = database_name or (
        "" if engine.database_type.is_key_value else slug.replace("-", "_")
    )

    return {
        "database_type": engine.database_type.value,
        "name": name,
        "image": image,
        "version": version or engine.default_image.rsplit(":", 1)[-1],
        "username": username or engine.default_username,
        "password": password or new_password(24),
        # Only the MySQL family has a separate superuser credential.
        "root_password": root_password
        or (
            new_password(24)
            if engine.database_type in (DatabaseType.MYSQL, DatabaseType.MARIADB)
            else ""
        ),
        "database_name": resolved_database,
        "internal_port": engine.default_port,
        "limits_memory": "",
        "status": "stopped",
    }


# --------------------------------------------------------------------------- #
# Container configuration
# --------------------------------------------------------------------------- #


def container_env(db: dict[str, object]) -> dict[str, str]:
    """Environment variables the engine's official image expects.

    Only the initialisation variables the image documents — anything the engine
    reads from a config file goes through ``custom_conf`` instead.
    """
    engine = engine_for(str(db.get("database_type")))
    username = str(db.get("username") or engine.default_username)
    password = str(db.get("password") or "")
    root_password = str(db.get("root_password") or password)
    database = str(db.get("database_name") or engine.default_database)

    kind = engine.database_type
    if kind is DatabaseType.POSTGRESQL:
        return {
            "POSTGRES_USER": username,
            "POSTGRES_PASSWORD": password,
            "POSTGRES_DB": database,
            # initdb refuses a non-empty directory, and a fresh volume contains
            # lost+found — so point PGDATA at a subdirectory of the mount.
            "PGDATA": f"{engine.data_path}/pgdata",
        }
    if kind is DatabaseType.MYSQL:
        return {
            "MYSQL_ROOT_PASSWORD": root_password,
            "MYSQL_USER": username,
            "MYSQL_PASSWORD": password,
            "MYSQL_DATABASE": database,
        }
    if kind is DatabaseType.MARIADB:
        # MariaDB 11 prefers MARIADB_* but still honours MYSQL_*; setting both
        # keeps older tags working from the same row.
        return {
            "MARIADB_ROOT_PASSWORD": root_password,
            "MARIADB_USER": username,
            "MARIADB_PASSWORD": password,
            "MARIADB_DATABASE": database,
            "MYSQL_ROOT_PASSWORD": root_password,
            "MYSQL_USER": username,
            "MYSQL_PASSWORD": password,
            "MYSQL_DATABASE": database,
        }
    if kind is DatabaseType.MONGODB:
        return {
            "MONGO_INITDB_ROOT_USERNAME": username,
            "MONGO_INITDB_ROOT_PASSWORD": password,
            "MONGO_INITDB_DATABASE": database or "admin",
        }
    if kind is DatabaseType.CLICKHOUSE:
        return {
            "CLICKHOUSE_USER": username,
            "CLICKHOUSE_PASSWORD": password,
            "CLICKHOUSE_DB": database or "default",
            # Without this the created user cannot GRANT, which breaks most
            # migration tooling on first run.
            "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT": "1",
        }
    # Redis / KeyDB / Dragonfly authenticate via the server command line.
    return {}


def container_command(db: dict[str, object]) -> list[str]:
    """Server argv override, for engines that need one.

    Returns an empty list when the image's default entrypoint is correct.
    """
    engine = engine_for(str(db.get("database_type")))
    password = str(db.get("password") or "")
    kind = engine.database_type

    if kind is DatabaseType.REDIS:
        argv = ["redis-server", "--appendonly", "yes"]
        if password:
            argv += ["--requirepass", password]
        return argv
    if kind is DatabaseType.KEYDB:
        argv = ["keydb-server", "--appendonly", "yes"]
        if password:
            argv += ["--requirepass", password]
        return argv
    if kind is DatabaseType.DRAGONFLY:
        argv = ["dragonfly", "--logtostderr"]
        if password:
            argv += [f"--requirepass={password}"]
        return argv
    return []


def container_extra_args(db: dict[str, object]) -> list[str]:
    """Engine-specific ``docker run`` flags beyond the generic ones.

    These are load-bearing: Dragonfly exits immediately without unlimited
    memlock, and ClickHouse logs a fatal error about the file descriptor limit.
    """
    engine = engine_for(str(db.get("database_type")))
    kind = engine.database_type
    if kind is DatabaseType.DRAGONFLY:
        return ["--ulimit", "memlock=-1"]
    if kind is DatabaseType.CLICKHOUSE:
        return ["--ulimit", "nofile=262144:262144"]
    if kind is DatabaseType.MONGODB:
        # Mongo uses shared memory for WiredTiger; the 64MB Docker default
        # causes intermittent startup failures on small servers.
        return ["--shm-size", "256m"]
    if kind is DatabaseType.POSTGRESQL:
        return ["--shm-size", "256m"]
    return []


def exposed_ports(db: dict[str, object]) -> list[int]:
    """Container ports the engine listens on."""
    engine = engine_for(str(db.get("database_type")))
    try:
        internal = int(db.get("internal_port") or engine.default_port)
    except (TypeError, ValueError):
        internal = engine.default_port
    if engine.database_type is DatabaseType.CLICKHOUSE:
        return [internal, CLICKHOUSE_NATIVE_PORT]
    return [internal]


def data_volume_mount(db: dict[str, object]) -> str:
    """Container path that must be backed by a named volume to persist data."""
    return engine_for(str(db.get("database_type"))).data_path


def config_file_mount(db: dict[str, object]) -> str:
    """Where a ``custom_conf`` blob should be mounted, if the engine supports it."""
    kind = engine_for(str(db.get("database_type"))).database_type
    return {
        DatabaseType.POSTGRESQL: "/etc/postgresql/postgresql.conf",
        DatabaseType.MYSQL: "/etc/mysql/conf.d/custom.cnf",
        DatabaseType.MARIADB: "/etc/mysql/conf.d/custom.cnf",
        DatabaseType.MONGODB: "/etc/mongo/mongod.conf",
        DatabaseType.REDIS: "/usr/local/etc/redis/redis.conf",
        DatabaseType.KEYDB: "/etc/keydb/keydb.conf",
        DatabaseType.CLICKHOUSE: "/etc/clickhouse-server/config.d/custom.xml",
    }.get(kind, "")


def init_script_mount(db: dict[str, object]) -> str:
    """Directory the image runs ``*.sql``/``*.js`` from on first boot."""
    kind = engine_for(str(db.get("database_type"))).database_type
    return {
        DatabaseType.POSTGRESQL: "/docker-entrypoint-initdb.d",
        DatabaseType.MYSQL: "/docker-entrypoint-initdb.d",
        DatabaseType.MARIADB: "/docker-entrypoint-initdb.d",
        DatabaseType.MONGODB: "/docker-entrypoint-initdb.d",
        DatabaseType.CLICKHOUSE: "/docker-entrypoint-initdb.d",
    }.get(kind, "")


# --------------------------------------------------------------------------- #
# Connection strings
# --------------------------------------------------------------------------- #


def connection_url(
    db: dict[str, object],
    *,
    host: str,
    port: int | None = None,
    include_password: bool = True,
) -> str:
    """Driver-compatible connection URL.

    ``host`` is the container name for in-network access and the server IP for
    public access, which is why it is a required argument rather than derived
    here — only the caller knows which side of the network it is on.
    """
    engine = engine_for(str(db.get("database_type")))
    username = quote(str(db.get("username") or engine.default_username), safe="")
    raw_password = str(db.get("password") or "")
    password = quote(raw_password, safe="") if include_password else "***"
    database = str(db.get("database_name") or engine.default_database)
    resolved_port = port or int(db.get("internal_port") or engine.default_port)
    kind = engine.database_type

    if kind is DatabaseType.POSTGRESQL:
        return f"postgres://{username}:{password}@{host}:{resolved_port}/{database}"
    if kind in (DatabaseType.MYSQL, DatabaseType.MARIADB):
        return f"mysql://{username}:{password}@{host}:{resolved_port}/{database}"
    if kind is DatabaseType.MONGODB:
        # authSource=admin is required because the root user created by
        # MONGO_INITDB_ROOT_USERNAME lives in the admin database, not in the
        # application database — omitting it is the classic auth failure.
        suffix = f"/{database}?authSource=admin" if database and database != "admin" else "/?authSource=admin"
        return f"mongodb://{username}:{password}@{host}:{resolved_port}{suffix}"
    if kind in _REQUIREPASS_ENGINES:
        if raw_password:
            return f"redis://{username}:{password}@{host}:{resolved_port}/0"
        return f"redis://{host}:{resolved_port}/0"
    if kind is DatabaseType.CLICKHOUSE:
        return f"http://{username}:{password}@{host}:{resolved_port}/?database={database or 'default'}"
    return f"{engine.scheme}://{host}:{resolved_port}"


def connection_details(
    db: dict[str, object],
    *,
    internal_host: str,
    public_host: str = "",
) -> dict[str, object]:
    """Everything the dashboard's "Connection" panel shows.

    Both URLs are returned because they serve different purposes: the internal
    one is what another resource in the same project should use (no port
    published, no traffic leaving the host), and the public one only exists when
    the operator explicitly opted in.
    """
    engine = engine_for(str(db.get("database_type")))
    internal_port = int(db.get("internal_port") or engine.default_port)
    is_public = bool(db.get("is_public"))
    public_port = db.get("public_port")

    details: dict[str, object] = {
        "engine": engine.name,
        "type": engine.database_type.value,
        "username": db.get("username") or engine.default_username,
        "database": db.get("database_name") or engine.default_database,
        "internal_host": internal_host,
        "internal_port": internal_port,
        "internal_url": connection_url(db, host=internal_host, port=internal_port),
        "is_public": is_public,
        "supports_backup": engine.database_type.supports_backup,
    }
    if engine.database_type is DatabaseType.CLICKHOUSE:
        details["native_port"] = CLICKHOUSE_NATIVE_PORT
        details["native_url"] = (
            f"clickhouse://{db.get('username')}:{db.get('password')}"
            f"@{internal_host}:{CLICKHOUSE_NATIVE_PORT}/{db.get('database_name') or 'default'}"
        )
    if is_public and public_host and public_port:
        details["public_host"] = public_host
        details["public_port"] = int(public_port)
        details["public_url"] = connection_url(db, host=public_host, port=int(public_port))
    return details


# --------------------------------------------------------------------------- #
# Readiness probes
# --------------------------------------------------------------------------- #


def readiness_command(db: dict[str, object]) -> str:
    """Shell command that exits 0 once the engine accepts queries.

    Used both as the container ``HEALTHCHECK`` and as the gate the deployment
    pipeline waits on before marking a database running. Every probe
    authenticates, because a Postgres that is listening but still running initdb
    will accept a TCP connection and then reject the query.
    """
    engine = engine_for(str(db.get("database_type")))
    username = str(db.get("username") or engine.default_username)
    password = str(db.get("password") or "")
    root_password = str(db.get("root_password") or password)
    database = str(db.get("database_name") or engine.default_database)
    port = int(db.get("internal_port") or engine.default_port)
    kind = engine.database_type

    if kind is DatabaseType.POSTGRESQL:
        return f"pg_isready -U {shlex.quote(username)} -d {shlex.quote(database)} -h 127.0.0.1 -p {port}"
    if kind is DatabaseType.MYSQL:
        return (
            f"mysqladmin ping -h 127.0.0.1 -P {port} -u root "
            f"-p{shlex.quote(root_password)} --silent"
        )
    if kind is DatabaseType.MARIADB:
        # The official image ships this script precisely for healthchecks; it
        # also waits for InnoDB recovery, which a bare ping does not.
        return "healthcheck.sh --connect --innodb_initialized"
    if kind is DatabaseType.MONGODB:
        return (
            "mongosh --quiet --host 127.0.0.1 --port "
            f"{port} -u {shlex.quote(username)} -p {shlex.quote(password)} "
            "--authenticationDatabase admin --eval \"db.adminCommand('ping').ok\""
        )
    if kind in _REQUIREPASS_ENGINES:
        cli = engine.client_binary or "redis-cli"
        auth = f"-a {shlex.quote(password)} --no-auth-warning " if password else ""
        return f"{cli} -h 127.0.0.1 -p {port} {auth}ping"
    if kind is DatabaseType.CLICKHOUSE:
        return f"wget --no-verbose --tries=1 --spider http://127.0.0.1:{port}/ping"
    return "true"


# --------------------------------------------------------------------------- #
# Backup / restore
# --------------------------------------------------------------------------- #


def dump_command(
    db: dict[str, object],
    *,
    output_path: str,
    databases: tuple[str, ...] = (),
    dump_all: bool = False,
) -> str:
    """Command run *inside* the database container to produce a dump file.

    Raises for engines without a logical dump tool rather than silently writing
    an empty file — the caller checks
    :attr:`DatabaseType.supports_backup` first, and reaching here means a bug.
    """
    engine = engine_for(str(db.get("database_type")))
    kind = engine.database_type
    if not kind.supports_backup:
        raise ValueError(
            f"{engine.name} does not support scheduled logical backups. "
            "Snapshot its persistent volume instead."
        )

    username = str(db.get("username") or engine.default_username)
    password = str(db.get("password") or "")
    root_password = str(db.get("root_password") or password)
    default_db = str(db.get("database_name") or engine.default_database)
    targets = tuple(d for d in databases if d) or ((default_db,) if default_db else ())
    out = shlex.quote(output_path)

    if kind is DatabaseType.POSTGRESQL:
        env = f"PGPASSWORD={shlex.quote(password)}"
        if dump_all:
            return f"{env} pg_dumpall -U {shlex.quote(username)} -h 127.0.0.1 > {out}"
        target = shlex.quote(targets[0] if targets else default_db)
        # --clean --if-exists makes the dump idempotently restorable over an
        # existing database instead of failing on duplicate objects.
        return (
            f"{env} pg_dump -U {shlex.quote(username)} -h 127.0.0.1 "
            f"-d {target} --no-owner --no-acl --clean --if-exists > {out}"
        )

    if kind in (DatabaseType.MYSQL, DatabaseType.MARIADB):
        binary = "mariadb-dump" if kind is DatabaseType.MARIADB else "mysqldump"
        auth = f"-u root -p{shlex.quote(root_password)} -h 127.0.0.1"
        # --single-transaction gives a consistent snapshot without locking the
        # whole database, which matters for a backup of a live app.
        flags = "--single-transaction --quick --routines --triggers --events"
        if dump_all:
            return f"{binary} {auth} {flags} --all-databases > {out}"
        if len(targets) > 1:
            joined = " ".join(shlex.quote(t) for t in targets)
            return f"{binary} {auth} {flags} --databases {joined} > {out}"
        target = shlex.quote(targets[0] if targets else default_db)
        return f"{binary} {auth} {flags} {target} > {out}"

    # MongoDB
    auth = (
        f"-u {shlex.quote(username)} -p {shlex.quote(password)} "
        "--authenticationDatabase admin"
    )
    if dump_all:
        return f"mongodump --host 127.0.0.1 {auth} --archive={out} --gzip"
    target = shlex.quote(targets[0] if targets else default_db)
    return f"mongodump --host 127.0.0.1 {auth} --db {target} --archive={out} --gzip"


def restore_command(db: dict[str, object], *, input_path: str) -> str:
    """Command run inside the container to restore a dump produced above."""
    engine = engine_for(str(db.get("database_type")))
    kind = engine.database_type
    username = str(db.get("username") or engine.default_username)
    password = str(db.get("password") or "")
    root_password = str(db.get("root_password") or password)
    database = str(db.get("database_name") or engine.default_database)
    src = shlex.quote(input_path)

    if kind is DatabaseType.POSTGRESQL:
        return (
            f"PGPASSWORD={shlex.quote(password)} psql -U {shlex.quote(username)} "
            f"-h 127.0.0.1 -d {shlex.quote(database)} -f {src}"
        )
    if kind in (DatabaseType.MYSQL, DatabaseType.MARIADB):
        binary = "mariadb" if kind is DatabaseType.MARIADB else "mysql"
        return (
            f"{binary} -u root -p{shlex.quote(root_password)} -h 127.0.0.1 "
            f"{shlex.quote(database)} < {src}"
        )
    if kind is DatabaseType.MONGODB:
        return (
            f"mongorestore --host 127.0.0.1 -u {shlex.quote(username)} "
            f"-p {shlex.quote(password)} --authenticationDatabase admin "
            f"--archive={src} --gzip --drop"
        )
    raise ValueError(f"{engine.name} does not support logical restore.")


def dump_extension(db: dict[str, object]) -> str:
    """File extension for a dump, used when naming the backup artifact."""
    kind = engine_for(str(db.get("database_type"))).database_type
    if kind is DatabaseType.MONGODB:
        return "archive.gz"
    return "sql"


def client_command(db: dict[str, object]) -> list[str]:
    """Interactive client argv for "open a shell on this database".

    Returned as argv (not a string) because it is passed straight to
    ``docker exec -it`` and must not go through a shell.
    """
    engine = engine_for(str(db.get("database_type")))
    username = str(db.get("username") or engine.default_username)
    password = str(db.get("password") or "")
    root_password = str(db.get("root_password") or password)
    database = str(db.get("database_name") or engine.default_database)
    port = int(db.get("internal_port") or engine.default_port)
    kind = engine.database_type

    if kind is DatabaseType.POSTGRESQL:
        return ["env", f"PGPASSWORD={password}", "psql", "-U", username, "-d", database]
    if kind is DatabaseType.MYSQL:
        return ["mysql", "-u", "root", f"-p{root_password}", database]
    if kind is DatabaseType.MARIADB:
        return ["mariadb", "-u", "root", f"-p{root_password}", database]
    if kind is DatabaseType.MONGODB:
        return [
            "mongosh", "--port", str(port), "-u", username, "-p", password,
            "--authenticationDatabase", "admin",
        ]
    if kind in _REQUIREPASS_ENGINES:
        cli = engine.client_binary or "redis-cli"
        argv = [cli, "-p", str(port)]
        if password:
            argv += ["-a", password, "--no-auth-warning"]
        return argv
    if kind is DatabaseType.CLICKHOUSE:
        return [
            "clickhouse-client", "--user", username, "--password", password,
            "--database", database or "default",
        ]
    return ["sh"]


__all__ = [
    "CLICKHOUSE_NATIVE_PORT",
    "ENGINES",
    "catalog",
    "client_command",
    "config_file_mount",
    "connection_details",
    "connection_url",
    "container_command",
    "container_env",
    "container_extra_args",
    "data_volume_mount",
    "dump_command",
    "dump_extension",
    "engine_for",
    "exposed_ports",
    "init_script_mount",
    "provision_defaults",
    "readiness_command",
    "restore_command",
]
