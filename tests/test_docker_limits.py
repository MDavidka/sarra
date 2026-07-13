from syte.config import settings


def test_docker_runtime_args_apply_project_limits_and_log_rotation():
    from syte.docker_deploy import docker_runtime_args

    args = docker_runtime_args()
    assert args == [
        "--cpus", "0.05",
        "--memory", "104857600",
        "--memory-swap", "104857600",
        "--log-driver", "json-file",
        "--log-opt", "max-size=10m",
        "--log-opt", "max-file=3",
    ]


def test_docker_runtime_args_with_mocked_settings(mocker):
    from syte.docker_deploy import docker_runtime_args
    from syte.docker_deploy import settings

    mocker.patch.object(settings, "docker_nano_cpus", 2_000_000_000)
    mocker.patch.object(settings, "docker_memory_bytes", 2048)
    mocker.patch.object(settings, "docker_log_max_size", "2m")
    mocker.patch.object(settings, "docker_log_max_files", 2)

    args = docker_runtime_args()
    assert args == [
        "--cpus", "2.0",
        "--memory", "2048",
        "--memory-swap", "2048",
        "--log-driver", "json-file",
        "--log-opt", "max-size=2m",
        "--log-opt", "max-file=2",
    ]
