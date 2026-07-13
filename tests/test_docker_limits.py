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
