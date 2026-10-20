import pytest

from engine import caps, state
from tests.fake_redis import FakeRedis


@pytest.fixture(params=["memory", "redis"])
def make(request):
    """Every cap test runs on both stores: in memory (local) and Redis (Vercel)."""
    def build(**kw):
        store = caps.MemoryStore() if request.param == "memory" else state.RedisStore(FakeRedis())
        return caps.Caps(store, **kw)
    return build


def test_each_user_gets_their_own_daily_checks_and_searches(make):
    c = make(checks_per_day=2, searches_per_day=1)
    c.use("ana", "check")
    c.use("ana", "check")
    with pytest.raises(caps.CapReached, match="2 checks"):
        c.use("ana", "check")
    c.use("ana", "search")  # searches are counted separately
    with pytest.raises(caps.CapReached, match="1 search"):
        c.use("ana", "search")
    c.use("bo", "check")  # another user is unaffected


def test_testers_get_higher_limits_but_the_global_spend_cap_still_applies(make):
    c = make(checks_per_day=1, searches_per_day=1, daily_usd=0.10, testers=frozenset({"tester"}),
             tester_checks_per_day=3, tester_searches_per_day=2)
    for _ in range(3):
        c.use("tester", "check")
    with pytest.raises(caps.CapReached, match="3 checks"):
        c.use("tester", "check")
    c.use("tester", "search")
    c.use("tester", "search")
    c.use("ana", "check")
    with pytest.raises(caps.CapReached, match="1 check"):
        c.use("ana", "check")  # everyone else keeps the normal limit
    c.spend(0.10)
    with pytest.raises(caps.CapReached, match="daily limit"):
        c.use("tester", "search")


def test_global_spend_cap_stops_everyone_once_reached(make):
    c = make(daily_usd=0.10)
    c.use("ana", "check")
    c.spend(0.06)
    c.use("bo", "check")  # under the cap still runs
    c.spend(0.05)
    assert c.spent_today() == pytest.approx(0.11)
    with pytest.raises(caps.CapReached, match="daily limit"):
        c.use("cy", "search")


def test_counts_reset_on_a_new_day(make, monkeypatch):
    c = make(checks_per_day=1, daily_usd=0.01)
    monkeypatch.setattr(c, "day", lambda: "2026-10-17")
    c.use("ana", "check")
    c.spend(0.02)
    monkeypatch.setattr(c, "day", lambda: "2026-10-18")
    c.use("ana", "check")
    assert c.spent_today() == 0


def test_redis_counters_expire_a_day_after_they_start_and_the_expiry_is_never_pushed_back():
    redis = FakeRedis()
    store = state.RedisStore(redis)
    assert store.incr("caps:2026-10-17:ana:check", 1, caps.DAY) == 1
    redis.now += 3600
    assert store.incr("caps:2026-10-17:ana:check", 1, caps.DAY) == 2
    redis.now += caps.DAY - 3600
    assert store.get("caps:2026-10-17:ana:check") == 0
