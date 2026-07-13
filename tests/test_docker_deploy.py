from pathlib import Path
from syte.docker_deploy import detect_container_port

def test_detect_container_port_oserror(tmp_path):
    non_existent = tmp_path / "Dockerfile"
    port = detect_container_port(non_existent)
    assert port == 3000

def test_detect_container_port_valueerror(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("EXPOSE not_a_number/tcp")
    port = detect_container_port(dockerfile)
    assert port == 3000

def test_detect_container_port_success(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("EXPOSE 8080/tcp")
    port = detect_container_port(dockerfile)
    assert port == 8080

def test_detect_container_port_success_no_slash(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("EXPOSE 5000")
    port = detect_container_port(dockerfile)
    assert port == 5000
