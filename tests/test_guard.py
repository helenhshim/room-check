import pytest

from engine import fetch


@pytest.mark.parametrize("url", [
    "http://example.com/",                 # not https
    "https://127.0.0.1/",                  # loopback
    "https://localhost/",                  # resolves to loopback
    "https://10.0.0.5/",                   # private
    "https://169.254.169.254/latest/",     # cloud metadata address
    "https://example.com:8443/",           # non-standard port
    "https://user:pass@example.com/",      # credentials in URL
    "file:///etc/passwd",
])
def test_unsafe_urls_rejected(url):
    with pytest.raises(fetch.Unsafe):
        fetch.check_url(url)


def test_unsafe_url_never_fetched():
    p = fetch.get_product("https://127.0.0.1/products/x")
    assert p.blocked_reason and p.blocked_reason.startswith("not fetched")
    assert not p.verified
