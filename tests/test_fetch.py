"""Product fetching, offline: the network layer is faked."""
import pytest

from engine import fetch


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(fetch, "check_url", lambda url: None)
    monkeypatch.setattr(fetch, "_allowed_by_robots", lambda url: True)
    monkeypatch.setattr(fetch, "cache", fetch.MemoryCache())

    def serve(pages):
        monkeypatch.setattr(fetch, "_get", lambda url: pages[url])
    return serve


PAGE = '<script type="application/ld+json">{"@type":"Product","name":"Svelti","offers":{"price":"79"}}</script>'


def test_the_product_remembers_the_page_a_redirect_landed_on(offline):
    offline({"https://s.com/p/3059": (200, "https://s.com/p/18027", PAGE)})
    p = fetch.get_product("https://s.com/p/3059")
    assert p.url == "https://s.com/p/3059" and p.final_url == "https://s.com/p/18027"
