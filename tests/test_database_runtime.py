from syte.platform.database_catalog import provision_defaults
from syte.platform.database_runtime import _argv, container_name, network_name, volume_name


def test_database_runtime_defaults_to_private_network_and_named_volume():
    row = provision_defaults("postgresql", "App DB")
    row.update({"uuid": "db123456", "destination_network": "syte-private"})
    args = _argv(row)
    assert "--network" in args
    assert args[args.index("--network") + 1] == "syte-private"
    assert "-v" in args
    assert volume_name(row) in args[args.index("-v") + 1]
    assert "-p" not in args
    assert container_name(row).startswith("syte-db-app-db-")


def test_database_runtime_publishes_only_when_requested():
    row = provision_defaults("redis", "Cache")
    row.update({"uuid": "redis123", "is_public": True, "public_port": 16379})
    args = _argv(row)
    assert "-p" in args
    assert args[args.index("-p") + 1] == "16379:6379"
    assert network_name(row) == "syte"
