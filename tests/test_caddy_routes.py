import pytest
from syte.caddy_routes import host_zone

@pytest.mark.parametrize("hostname, expected", [
    ("example.com", "example.com"),
    ("sub.example.com", "example.com"),
    ("a.b.example.com", "example.com"),
    ("localhost", "localhost"),
    ("https://example.com/path", "example.com"),
    ("http://sub.example.com:8080", "example.com"),
    ("", ""),
    ("com", "com"),
])
def test_host_zone(hostname: str, expected: str):
    assert host_zone(hostname) == expected
