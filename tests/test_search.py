"""Find similar's selection logic, offline: search, fetch and judge are faked, so these tests cost nothing."""
import pytest

from engine import fetch, judge, models, search
from engine.types import Product, Room, Verdict

ROOM = Room(floor="dark wood", walls="white", budget=500, likes=["paper lanterns", "mushroom lamps"])


def product(name, url, price=100.0, store="example.com", final_url=None, **kw):
    return Product(url=url, store=store, name=name, price=price, verified=True, final_url=final_url, **kw)


@pytest.fixture
def run(monkeypatch):
    """run(products, brief=..., scores=..., more_results=...) -> FindResult, with the faked steps recorded."""
    def go(products, brief=None, scores=None, search_hits=None, reference_urls=None, reference_products=None,
           reference_text="", note="", wide_hits=None):
        calls = {"describe": [], "queries": [], "judged": []}
        base = {"looking_for": "an accent chair", "queries": ["accent chair walnut", "accent chair leather"],
                "max_price": None, "category": "accent chair"}
        brief = {**base, **(brief or {})}
        monkeypatch.setattr(models, "new_run", lambda *a, **k: None)
        monkeypatch.setattr(search, "describe", lambda *a, **k: calls["describe"].append((a, k)) or brief)

        def fake_search(query, max_results=8, include_domains=None):
            calls["queries"].append(query)
            hits = (wide_hits if wide_hits is not None and len(calls["queries"]) > 2 else search_hits)
            return [{"url": p.url, "title": p.name} for p in (hits if hits is not None else products)]
        monkeypatch.setattr(search, "web_search", fake_search)
        by_url = {p.url: p for p in products + (wide_hits or [])}

        def fake_get(urls, *a, **k):
            return [by_url.get(u) or (reference_products or {}).get(u) or product("ref", u) for u in urls]
        monkeypatch.setattr(fetch, "get_products", fake_get)

        def fake_judge(room, pool, looking_for=None):
            calls["judged"].append([p.url for p in pool])
            return [Verdict(url=p.url, fits="ok", watch_try="Nothing major.", score=(scores or {}).get(p.url, 4)) for p in pool]
        monkeypatch.setattr(judge, "judge", fake_judge)
        result = search.find_similar(ROOM, reference_text=reference_text, reference_urls=reference_urls, note=note)
        go.calls = calls
        return result
    go.calls = {}
    return go


def urls(result):
    return [p.url for p, _ in result.picks]


def test_two_links_that_redirect_to_one_product_give_one_pick(run):
    a = product("Svelti chair", "https://s.com/p/3059", final_url="https://s.com/p/18027")
    b = product("Svelti chair", "https://s.com/p/18027", final_url="https://s.com/p/18027")
    c = product("Other chair", "https://s.com/p/other")
    r = run([a, b, c])
    assert urls(r).count("https://s.com/p/3059") + urls(r).count("https://s.com/p/18027") == 1
    assert "https://s.com/p/other" in urls(r)


def test_colorways_of_one_design_count_as_one_pick(run):
    cream = product("POÄNG Armchair, birch veneer/Knisa cream", "https://i.com/p/poang-cream", store="i.com")
    black = product("POÄNG Armchair, birch veneer/Knisa black", "https://i.com/p/poang-black", store="i.com")
    other = product("The Wishbone Chair - Natural", "https://r.com/p/wishbone", store="r.com")
    third = product("Series 7 Chair", "https://h.com/p/series7", store="h.com")
    r = run([cream, black, other, third])
    assert len(r.picks) == 3
    assert sum("poang" in u for u in urls(r)) == 1


def test_the_reference_itself_is_never_a_pick_even_after_redirects(run):
    ref_link = "https://r.com/short/wishbone"
    ref = product("Wishbone Chair", ref_link, final_url="https://r.com/p/wishbone-chair")
    same = product("Wishbone Chair", "https://r.com/p/wishbone-chair", final_url="https://r.com/p/wishbone-chair")
    others = [product(f"Chair {i}", f"https://r.com/p/{i}") for i in range(3)]
    r = run([same] + others, reference_urls=[ref_link], reference_products={ref_link: ref})
    assert "https://r.com/p/wishbone-chair" not in urls(r)
    assert len(r.picks) == 3


def test_an_accent_chair_request_never_returns_dining_chairs(run):
    dining = product("Odessa Dining Chair", "https://a.com/p/dining-1")
    stool = product("Counter Stool", "https://a.com/p/stool")
    lounge = [product(f"Lounge {i}", f"https://a.com/p/lounge-{i}") for i in range(3)]
    r = run([dining, stool] + lounge)
    assert all("dining" not in u and "stool" not in u for u in urls(r))
    assert len(r.picks) == 3


def test_a_dining_chair_request_keeps_dining_chairs(run):
    dining = [product(f"Dining Chair {i}", f"https://a.com/p/dining-{i}") for i in range(3)]
    r = run(dining, brief={"category": "dining chair"})
    assert len(r.picks) == 3


def test_fewer_than_three_picks_is_a_failed_result_with_a_reason(run):
    items = [product("Chair one", "https://a.com/p/1"), product("Chair two", "https://a.com/p/2"),
             product("Chair three", "https://a.com/p/3", price=900.0)]
    r = run(items)
    assert len(r.picks) == 2 and r.failed
    assert r.reason and "over $500" in r.reason
    assert str(r.candidates) in r.reason and str(r.pages_read) in r.reason


def test_three_picks_is_not_a_failure(run):
    r = run([product(f"Chair {i}", f"https://a.com/p/{i}") for i in range(3)])
    assert not r.failed and r.reason == ""


def test_no_picks_still_gives_a_reason(run):
    r = run([])
    assert r.failed and r.reason


def test_searches_wider_before_returning_fewer_than_three(run):
    first = [product("Chair one", "https://a.com/p/1"), product("Chair two", "https://a.com/p/2")]
    more = [product("Chair three", "https://b.com/p/3"), product("Chair four", "https://c.com/p/4")]
    r = run(first, search_hits=first, wide_hits=more)
    assert len(r.picks) == 3 and not r.failed
    assert len(run.calls["queries"]) == 3


def test_the_search_description_gets_the_rooms_likes(run):
    r = run([product("Lamp", "https://a.com/p/1")])
    (args, kwargs), = run.calls["describe"]
    assert "paper lanterns" in str(args) + str(kwargs)


def test_unknown_price_never_beats_a_priced_pick(run):
    unknown = product("Joan set", "https://w.com/p/joan", price=None)
    priced = [product(f"Chair {i}", f"https://a.com/p/{i}") for i in range(2)]
    r = run([unknown] + priced, scores={"https://w.com/p/joan": 5})
    assert urls(r)[0] != "https://w.com/p/joan"


def test_unknown_price_is_dropped_when_the_note_gives_a_budget(run):
    unknown = product("Joan set", "https://w.com/p/joan", price=None)
    priced = [product(f"Chair {i}", f"https://a.com/p/{i}") for i in range(2)]
    r = run([unknown] + priced, brief={"max_price": 250})
    assert "https://w.com/p/joan" not in urls(r)
    assert r.left_out.get("price not found") == 1
