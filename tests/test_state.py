"""Shared state on Redis, against a fake: no network."""
import pytest

from engine import state
from tests.fake_redis import FakeRedis


class Reply:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body


def test_upstash_sends_one_pipeline_request_with_the_token_and_returns_each_result():
    sent = {}
    def post(url, **kw):
        sent.update(url=url, **kw)
        return Reply(200, [{"result": "1.5"}, {"result": 1}])
    redis = state.Upstash("https://example.upstash.io/", "tok", post=post)

    assert redis.pipeline(["INCRBYFLOAT", "spend:2026-10-17", 1.5], ["EXPIRE", "spend:2026-10-17", 86400, "NX"]) == ["1.5", 1]
    assert sent["url"] == "https://example.upstash.io/pipeline"
    assert sent["headers"] == {"Authorization": "Bearer tok"}
    assert sent["json"] == [["INCRBYFLOAT", "spend:2026-10-17", "1.5"], ["EXPIRE", "spend:2026-10-17", "86400", "NX"]]


@pytest.mark.parametrize("post", [
    lambda url, **kw: Reply(401, {"error": "Unauthorized"}),
    lambda url, **kw: Reply(200, [{"error": "WRONGTYPE"}]),
    lambda url, **kw: (_ for _ in ()).throw(TimeoutError()),
])
def test_upstash_failures_raise_state_error(post):
    with pytest.raises(state.StateError):
        state.Upstash("https://example.upstash.io", "tok", post=post).pipeline(["GET", "x"])


def test_redis_is_used_only_when_both_env_vars_are_set(monkeypatch):
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "tok")
    assert state.client_from_env() is None
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://example.upstash.io")
    assert isinstance(state.client_from_env(), state.Upstash)


def test_product_cache_keeps_pages_24_hours_under_the_same_keys():
    redis = FakeRedis()
    cache = state.RedisCache(redis)
    cache.set("product:https://www.ikea.com/us/en/p/billy/", '{"name": "BILLY"}', 24 * 3600)
    assert ["SET", "product:https://www.ikea.com/us/en/p/billy/", '{"name": "BILLY"}', "EX", "86400"] in redis.sent
    assert cache.get("product:https://www.ikea.com/us/en/p/billy/") == '{"name": "BILLY"}'
    redis.now += 24 * 3600
    assert cache.get("product:https://www.ikea.com/us/en/p/billy/") is None


def test_product_cache_treats_redis_being_down_as_a_miss():
    redis = FakeRedis()
    redis.down = True
    cache = state.RedisCache(redis)
    cache.set("product:x", "{}", 60)  # no error: the page is just fetched again next time
    assert cache.get("product:x") is None


def run_row(tool, usd=0.01, ok=True):
    return {"run_id": f"{tool}-abc", "tool": tool, "ok": ok, "tokens": 1200, "searches": 1 if tool == "find" else 0,
            "seconds": 2.5, "usd": usd}


@pytest.fixture(params=["memory", "redis"])
def run_log(request):
    return state.MemoryRunLog() if request.param == "memory" else state.RedisRunLog(FakeRedis())


def test_usage_per_day_counts_calls_per_tool_distinct_keys_and_cost(run_log):
    run_log.record(run_row("check", 0.02), who="a1", day="2026-10-17")
    run_log.record(run_row("check", 0.03), who="b2", day="2026-10-17")
    run_log.record(run_row("find", 0.05), who="a1", day="2026-10-17")
    run_log.record(run_row("check"), who="c3", day="2026-10-18")

    day = run_log.usage("2026-10-17")
    assert day["calls"] == {"check": 2, "find": 1}
    assert day["distinct_keys"] == 2
    assert day["usd"] == pytest.approx(0.10)
    assert day["runs"][2] == run_row("find", 0.05)
    assert run_log.usage("2026-10-19") == {"day": "2026-10-19", "calls": {}, "distinct_keys": 0, "usd": 0, "runs": []}


def test_run_log_rows_keep_only_cost_fields_never_room_or_user_text(run_log):
    row = {**run_row("check"), "room": {"floor": "dark wood"}, "note": "my roommate hates it"}
    run_log.record(row, who="a1", day="2026-10-17")
    assert set(run_log.usage("2026-10-17")["runs"][0]) == {"run_id", "tool", "ok", "tokens", "searches", "seconds", "usd"}


def test_redis_run_log_is_kept_30_days_and_redis_being_down_never_fails_the_call():
    redis = FakeRedis()
    log = state.RedisRunLog(redis)
    log.record(run_row("check"), who="a1", day="2026-10-17")
    redis.now += 30 * 24 * 3600
    assert log.usage("2026-10-17")["calls"] == {}
    redis.down = True
    log.record(run_row("check"), who="a1", day="2026-10-17")  # logged as a warning, not raised
