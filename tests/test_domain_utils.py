import pytest
from syte.domain_utils import is_valid_ip, normalize_domain, build_https_url, build_direct_url

def test_is_valid_ip():
    # Valid IPs
    assert is_valid_ip("192.168.1.1") is True
    assert is_valid_ip("0.0.0.0") is True
    assert is_valid_ip("255.255.255.255") is True
    assert is_valid_ip("  127.0.0.1  ") is True  # with spaces

    # Invalid IPs
    assert is_valid_ip("256.0.0.1") is False  # out of range
    assert is_valid_ip("1.2.3") is False  # too few parts
    assert is_valid_ip("1.2.3.4.5") is False  # too many parts
    assert is_valid_ip("abc.def.ghi.jkl") is False  # non-numeric
    assert is_valid_ip("") is False  # empty string
    assert is_valid_ip(None) is False  # None

def test_normalize_domain():
    # Normal domains
    assert normalize_domain("example.com") == "example.com"
    assert normalize_domain("  example.com  ") == "example.com"

    # Domains with schemes
    assert normalize_domain("http://example.com") == "example.com"
    assert normalize_domain("https://example.com") == "example.com"

    # Domains with paths
    assert normalize_domain("example.com/path") == "example.com"
    assert normalize_domain("https://example.com/path/to/resource") == "example.com"

    # Domains with ports
    assert normalize_domain("example.com:8080") == "example.com"
    assert normalize_domain("https://example.com:443/path") == "example.com"

    # Domains with trailing dot
    assert normalize_domain("example.com.") == "example.com"
    assert normalize_domain("https://example.com.") == "example.com"

def test_build_https_url():
    assert build_https_url("example.com") == "https://example.com"
    assert build_https_url("http://example.com") == "https://example.com"
    assert build_https_url("example.com/path") == "https://example.com"

def test_build_direct_url():
    # Valid IP
    assert build_direct_url("192.168.1.1", 8080) == "http://192.168.1.1:8080"

    # Invalid IP (should fallback to 127.0.0.1)
    assert build_direct_url("invalid_ip", 8080) == "http://127.0.0.1:8080"
    assert build_direct_url("", 9000) == "http://127.0.0.1:9000"
    assert build_direct_url("256.0.0.1", 3000) == "http://127.0.0.1:3000"
