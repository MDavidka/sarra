import pytest
from syte.domain_utils import is_valid_ip


@pytest.mark.parametrize(
    "ip, expected",
    [
        # Valid IPs
        ("192.168.1.1", True),
        ("0.0.0.0", True),
        ("255.255.255.255", True),
        ("127.0.0.1", True),
        ("1.2.3.4", True),
        # With whitespace
        ("  192.168.1.1  ", True),
        ("\t10.0.0.1\n", True),
        # Out of range
        ("256.0.0.1", False),
        ("192.168.1.256", False),
        ("192.168.256.1", False),
        ("192.256.1.1", False),
        ("999.999.999.999", False),
        # Invalid format
        ("-1.0.0.0", False),
        ("192.168.1", False),
        ("192.168.1.1.1", False),
        ("abc.def.ghi.jkl", False),
        ("192.168.1.a", False),
        ("...", False),
        ("192.168..1", False),
        ("1234.0.0.1", False),
        # Edge cases
        ("", False),
        (None, False),
    ],
)
def test_is_valid_ip(ip, expected):
    assert is_valid_ip(ip) is expected
