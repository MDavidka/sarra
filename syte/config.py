from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYTE_")

    data_dir: Path = Path("/var/lib/syte")
    workspaces_dir: Path | None = None
    db_path: Path | None = None
    caddy_config_path: Path = Path("/etc/caddy/Caddyfile")
    host: str = "0.0.0.0"
    port: int = 8787
    agent_port_start: int = 5200
    agent_port_end: int = 5999
    public_ip: str = ""
    admin_email: str = "admin@localhost"
    docker_nano_cpus: int = 50_000_000
    docker_memory_bytes: int = 100 * 1024 * 1024
    docker_log_max_size: str = "10m"
    docker_log_max_files: int = 3
    agent_event_timeout_s: float = 300.0
    agent_cache_ttl_s: float = 300.0
    agent_cache_max_entries: int = 128

    @property
    def resolved_workspaces_dir(self) -> Path:
        return self.workspaces_dir or (self.data_dir / "workspaces")

    @property
    def resolved_db_path(self) -> Path:
        return self.db_path or (self.data_dir / "syte.db")

    @property
    def resolved_public_ip(self) -> str:
        if self.public_ip:
            return self.public_ip
        try:
            import socket

            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"

    @property
    def resolved_agent_port_start(self) -> int:
        return self.agent_port_start

    @property
    def resolved_agent_port_end(self) -> int:
        return self.agent_port_end


settings = Settings()
