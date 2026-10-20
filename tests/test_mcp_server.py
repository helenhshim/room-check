import json
"""The connector's MCP tools, called over real HTTP in-process, with a personal key in the URL the way the Claude
app sends it. The engine's paid steps are faked."""
import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from api import _auth
from api import mcp as connector
from engine import caps, state
from tests.fake_redis import FakeRedis
from engine.types import Dims, Product, Verdict

ROOM = {"name": "Living room", "floor": "dark wood", "walls": "white", "budget": 500, "likes": ["chrome"],
        "owned": [{"name": "Sofa", "placement": "against the wall"}]}
BASE = "http://127.0.0.1:8000"  # a host the local server accepts
KEY = ""  # a freshly issued personal key, set for each test


def call(tool: str, args: dict, key: "str | None" = None):
    key = KEY if key is None else key
    url = f"{BASE}/api/mcp" + (f"?key={key}" if key else "")

    async def go():
        app = connector.make_app()
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as http:
                async with Client(streamable_http_client(url, http_client=http)) as client:
                    return await client.call_tool(tool, args)
    return anyio.run(go)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    global KEY
    monkeypatch.setattr(connector, "daily_caps", caps.Caps(caps.MemoryStore(), checks_per_day=2, searches_per_day=1, daily_usd=1.0))
    monkeypatch.setattr(connector, "run_log", state.MemoryRunLog())
    monkeypatch.setattr(_auth, "keys", _auth.Keys(None))
    KEY = _auth.keys.issue()


def list_tools():
    async def go():
        async with Client(connector.server) as client:
            return await client.list_tools()
    return anyio.run(go).tools


def test_lists_the_four_room_check_tools():
    names = {t.name for t in list_tools()}
    assert names == {"start_room_check", "check_pieces", "find_similar", "ready_to_buy"}


def test_start_room_check_says_what_to_ask_and_returns_a_run_id():
    out = call("start_room_check", {"room": ROOM})
    assert not out.is_error
    body = out.structured_content
    assert [g["field"] for g in body["still_unknown"]] == ["rules"]
    assert any("never send photos" in step for step in body["flow"])
    assert body["run_id"]


def fake_engine_run(kind: str, usd: float) -> str:
    """What every paid engine step does: start a run and log its model cost."""
    rid = connector.models.new_run(kind)
    connector.models.usage_log.append({"run": rid, "usd": usd, "seconds": 1.0, "input": 100, "output": 10,
                                       "cache_read": 0, "cache_write": 0})
    return rid


BILLY = Product(url="https://www.ikea.com/us/en/p/billy/", store="ikea.com", name="BILLY Bookcase", price=79.0,
                dims=Dims(w=15.75, d=11, h=79.5, source="stated"), verified=True)


def test_check_pieces_returns_a_card_per_link_with_run_id_and_cost(monkeypatch, caplog):
    def fake_check(room, urls):
        fake_engine_run("check", 0.02)
        assert room.floor == "dark wood"  # the room profile came through intact
        return [(BILLY, Verdict(url=BILLY.url, fits="White frame stands apart from the dark floor.",
                                watch_try="Nothing major.", score=4))]
    monkeypatch.setattr(connector.judge, "check_pieces", fake_check)

    with caplog.at_level("INFO", logger="room_check"):
        out = call("check_pieces", {"room": ROOM, "links": [BILLY.url]})

    assert not out.is_error
    body = out.structured_content
    [card] = body["pieces"]
    assert (card["name"], card["price"], card["dimensions_in"]["h"], card["score"]) == ("BILLY Bookcase", 79.0, 79.5, 4)
    assert body["run_id"].startswith("check-") and body["cost"]["usd"] == 0.02
    assert body["run_id"] in caplog.text and '"usd": 0.02' in caplog.text
    assert connector.daily_caps.spent_today() == pytest.approx(0.02)


def test_daily_caps_stop_calls_before_any_engine_work(monkeypatch):
    calls = []
    monkeypatch.setattr(connector.judge, "check_pieces", lambda room, urls: calls.append(urls) or [])
    for _ in range(2):
        assert not call("check_pieces", {"room": ROOM, "links": [BILLY.url]}).is_error
    out = call("check_pieces", {"room": ROOM, "links": [BILLY.url]})
    assert out.is_error and "today's 2 checks" in out.content[0].text
    assert len(calls) == 2


def test_a_tester_key_gets_higher_limits_and_sees_them(monkeypatch):
    monkeypatch.setattr(connector, "daily_caps", caps.Caps(caps.MemoryStore(), checks_per_day=2, testers=frozenset(
        {_auth.short_hash(KEY)}), tester_checks_per_day=4, tester_searches_per_day=3))
    monkeypatch.setattr(connector.judge, "check_pieces", lambda room, urls: [])
    assert call("start_room_check", {"room": ROOM}).structured_content["daily_limits"] == {"checks": 4, "searches": 3}
    assert [call("check_pieces", {"room": ROOM, "links": [BILLY.url]}).is_error for _ in range(5)] == [False] * 4 + [True]
    other = _auth.keys.issue()
    assert call("start_room_check", {"room": ROOM}, key=other).structured_content["daily_limits"]["checks"] == 2


def test_daily_spend_cap_stops_searches_for_everyone(monkeypatch):
    connector.daily_caps.spend(1.0)
    monkeypatch.setattr(connector.search, "find_similar", lambda *a, **k: pytest.fail("search ran past the spend cap"))
    out = call("find_similar", {"room": ROOM, "reference": "cream boucle lounge chair"})
    assert out.is_error and "daily limit for everyone" in out.content[0].text


def test_model_failure_is_a_tool_error_with_its_run_id_and_cost_still_counted(monkeypatch):
    def failing(room, urls):
        fake_engine_run("check", 0.01)
        raise connector.models.ModelError("rate limited by the model provider; try again shortly")
    monkeypatch.setattr(connector.judge, "check_pieces", failing)
    out = call("check_pieces", {"room": ROOM, "links": [BILLY.url]})
    assert out.is_error and "rate limited" in out.content[0].text and "(run check-" in out.content[0].text
    assert connector.daily_caps.spent_today() == pytest.approx(0.01)


def test_too_many_links_are_rejected():
    out = call("check_pieces", {"room": ROOM, "links": [BILLY.url] * 7})
    assert out.is_error


def test_no_tool_takes_an_image():
    for tool in list_tools():
        assert "image" not in str(tool.input_schema).lower(), tool.name


def test_find_similar_searches_under_the_given_budget_and_reports_what_was_left_out(monkeypatch):
    seen = {}
    def fake_find(room, reference_text="", reference_urls=None, note=""):
        fake_engine_run("find", 0.05)
        seen.update(budget=room.budget, reference=reference_text, note=note)
        return connector.FindResult(looking_for="a cream boucle lounge chair",
                                    picks=[(BILLY, Verdict(url=BILLY.url, fits="f", watch_try="Nothing major.", score=4))],
                                    left_out={"over $300": 2}, message="Found 1 that fit well.")
    monkeypatch.setattr(connector.search, "find_similar", fake_find)

    out = call("find_similar", {"room": ROOM, "reference": "cream boucle lounge chair", "budget": 300, "note": "light legs"})

    assert seen == {"budget": 300, "reference": "cream boucle lounge chair", "note": "light legs"}
    body = out.structured_content
    assert [p["name"] for p in body["picks"]] == ["BILLY Bookcase"]
    assert body["left_out"] == {"over $300": 2} and body["run_id"].startswith("find-")


def test_ready_to_buy_warns_without_blocking():
    rug = Product(url="https://pootoo.com/products/rug", store="pootoo.com", name="Rug", price=200.0, verified=True,
                  variants=[{"name": "5x8", "price": 200.0}, {"name": "8x10", "price": 400.0}])
    verdict = Verdict(url=rug.url, fits="f", watch_try="w", flags=["Conflicts with a rule"], score=2)

    def run(version):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(connector.judge, "check_pieces", lambda room, urls: [(rug, verdict)])
            return call("ready_to_buy", {"room": ROOM, "link": rug.url, "version": version}).structured_content

    no_version = run("")
    assert no_version["ready"] is False
    assert "dimensions" in no_version["missing"] and "which size or color you'd buy" in no_version["missing"]
    assert no_version["concerns"] == ["Conflicts with a rule"]
    assert no_version["piece"]["link"] == rug.url
    assert "which size or color you'd buy" not in run("8x10")["missing"]


def test_any_failure_still_logs_its_cost_without_the_raw_key(monkeypatch, caplog):
    def search_down(*a, **k):
        fake_engine_run("find", 0.01)  # the describe step already ran
        raise TimeoutError("search timed out")
    monkeypatch.setattr(connector.search, "find_similar", search_down)

    with caplog.at_level("INFO", logger="room_check"):
        out = call("find_similar", {"room": ROOM, "reference": "a chair"})

    assert out.is_error
    assert connector.daily_caps.spent_today() == pytest.approx(0.01)
    assert '"ok": false' in caplog.text and "find-" in caplog.text
    assert KEY not in caplog.text  # who called is logged as a hash, never the key itself


MESSY_ROOM = {"name": " Living room ", "floor": "  dark   wood ", "walls": "white ", "budget": 500,
              "owned": [{"name": " Sofa ", "placement": "against the wall"}, {"name": "  "},
                        {"name": "Sofa", "placement": "against the wall"}],
              "likes": ["polished chrome", " polished chrome ", ""],
              "rules": [{"what": "dark wood", "works_when": "off to the side", "fails_when": "low in the middle",
                         "status": "confirmed"}, {"what": " ", "works_when": "", "fails_when": ""}]}


def test_start_room_check_returns_one_cleaned_up_room_profile_to_resend():
    body = call("start_room_check", {"room": MESSY_ROOM}).structured_content
    profile = body["room_profile"]
    assert (profile["name"], profile["floor"], profile["walls"], profile["budget"]) == ("Living room", "dark wood", "white", 500)
    assert [(p["name"], p["placement"]) for p in profile["owned"]] == [("Sofa", "against the wall")] * 2  # never merged
    assert profile["likes"] == ["polished chrome"]
    assert [r["what"] for r in profile["rules"]] == ["dark wood"]
    assert [g["field"] for g in body["still_unknown"]] == []


def test_resending_the_room_profile_gets_the_same_profile_back():
    first = call("start_room_check", {"room": MESSY_ROOM}).structured_content["room_profile"]
    again = call("start_room_check", {"room": first}).structured_content["room_profile"]
    assert again == first


def test_every_tool_tells_claude_to_resend_the_exact_room_profile():
    for tool in list_tools():
        text = tool.description.lower()
        assert "room_profile" in text and "exact" in text and "every call" in text, tool.name
        assert "only when the person corrects or adds" in text, tool.name
        assert "call start_room_check again" in text, tool.name


def test_each_call_adds_a_run_log_row_with_cost_and_searches_but_no_room(monkeypatch):
    def fake_find(room, reference_text="", reference_urls=None, note=""):
        fake_engine_run("find", 0.05)
        connector.models.count_search()
        connector.models.count_search()
        return connector.FindResult(looking_for="a chair", message="none")
    monkeypatch.setattr(connector.search, "find_similar", fake_find)

    out = call("find_similar", {"room": ROOM, "reference": "a chair", "note": "my roommate hates orange"})

    usage = connector.run_log.usage(connector.daily_caps.day())
    [row] = usage["runs"]
    assert row == {"run_id": out.structured_content["run_id"], "tool": "find", "ok": True, "tokens": 110,
                   "searches": 2, "seconds": 1.0, "usd": 0.05}
    assert usage["calls"] == {"find": 1} and usage["distinct_keys"] == 1
    assert "roommate" not in str(usage) and "dark wood" not in str(usage)


def test_redis_down_stops_paid_calls_before_any_work_instead_of_skipping_the_caps(monkeypatch):
    redis = FakeRedis()
    redis.down = True
    monkeypatch.setattr(connector, "daily_caps", caps.Caps(state.RedisStore(redis)))
    monkeypatch.setattr(connector.judge, "check_pieces", lambda room, urls: pytest.fail("ran without checking caps"))
    out = call("check_pieces", {"room": ROOM, "links": [BILLY.url]})
    assert out.is_error and "can't check today's limits" in out.content[0].text


def test_cap_counters_in_redis_are_keyed_by_a_hash_never_the_persons_key(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(connector, "daily_caps", caps.Caps(state.RedisStore(redis)))
    monkeypatch.setattr(connector.judge, "check_pieces", lambda room, urls: [])
    assert not call("check_pieces", {"room": ROOM, "links": [BILLY.url]}).is_error
    assert any(k.startswith("caps:") for k in redis.data)
    assert not any(KEY in k for k in redis.data)


def test_a_call_without_a_key_is_rejected_with_a_message_claude_can_relay(monkeypatch):
    monkeypatch.setattr(connector.judge, "check_pieces", lambda room, urls: pytest.fail("ran without a key"))
    for tool, args in (("start_room_check", {}), ("check_pieces", {"room": ROOM, "links": [BILLY.url]})):
        out = call(tool, args, key="")
        assert out.is_error and "no personal key" in out.content[0].text, tool
        assert "setup page" in out.content[0].text


def test_an_unknown_key_is_rejected_before_any_work(monkeypatch):
    monkeypatch.setattr(connector.judge, "check_pieces", lambda room, urls: pytest.fail("ran with a made-up key"))
    out = call("check_pieces", {"room": ROOM, "links": [BILLY.url]}, key="made-up-key")
    assert out.is_error and "isn't recognized" in out.content[0].text


def test_each_key_gets_its_own_daily_checks(monkeypatch):
    monkeypatch.setattr(connector.judge, "check_pieces", lambda room, urls: [])
    for _ in range(2):
        assert not call("check_pieces", {"room": ROOM, "links": [BILLY.url]}).is_error
    assert call("check_pieces", {"room": ROOM, "links": [BILLY.url]}).is_error
    other = _auth.keys.issue()
    assert not call("check_pieces", {"room": ROOM, "links": [BILLY.url]}, key=other).is_error


def test_key_store_being_down_rejects_calls_instead_of_letting_them_through(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(_auth, "keys", _auth.Keys(redis))
    key = _auth.keys.issue()
    redis.down = True
    out = call("start_room_check", {}, key=key)
    assert out.is_error and "can't check your key" in out.content[0].text


def test_the_daily_spend_cap_covers_every_key_together(monkeypatch):
    def spend(room, urls):
        fake_engine_run("check", 0.6)
        return []
    monkeypatch.setattr(connector.judge, "check_pieces", spend)
    assert not call("check_pieces", {"room": ROOM, "links": [BILLY.url]}).is_error
    assert not call("check_pieces", {"room": ROOM, "links": [BILLY.url]}, key=_auth.keys.issue()).is_error
    out = call("check_pieces", {"room": ROOM, "links": [BILLY.url]}, key=_auth.keys.issue())
    assert out.is_error and "daily limit for everyone" in out.content[0].text


def test_start_room_check_ends_with_a_profile_a_new_chat_can_start_from():
    first = call("start_room_check", {"room": MESSY_ROOM}).structured_content
    block = first["save_for_next_chat"]
    saved = json.loads(block.split("```json\n")[1].split("\n```")[0])
    again = call("start_room_check", {"room": saved}).structured_content
    assert again["room_profile"] == first["room_profile"]  # a new chat given only the block gets the same room


def test_the_flow_tells_claude_to_offer_combinations_for_sets():
    flow = " ".join(call("start_room_check", {"room": ROOM}).structured_content["flow"]).lower()
    assert "go together" in flow and "combinations" in flow and "ask them to choose" in flow


def test_a_pinterest_pin_gets_the_pin_next_step_not_a_price_and_size_request(monkeypatch):
    from engine import fetch, judge as real_judge
    monkeypatch.setattr(fetch, "cache", fetch.MemoryCache())
    out = call("check_pieces", {"room": ROOM, "links": ["https://www.pinterest.com/pin/123456/"]})
    [card] = out.structured_content["pieces"]
    assert card["watch_try"].startswith("This is a pin, not a product page")
    assert "product link it points to" in card["watch_try"] and "price and size" not in card["watch_try"]
    assert card["store"] == ""  # Pinterest is not a store
