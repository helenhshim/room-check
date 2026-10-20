"""Issuing personal connector keys (POST /api/keys), over HTTP in-process. No network."""
import anyio
import httpx2
import pytest

from api import _auth
from api import keys as keys_function
from api import mcp as connector
from engine import caps
from tests.fake_redis import FakeRedis

BASE = "http://127.0.0.1:8000"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(_auth, "keys", _auth.Keys(None))
    monkeypatch.setattr(_auth, "issuing", _auth.Issuing(caps.MemoryStore(), per_ip=2, per_day=3))


def post(app=None, ip="203.0.113.7", method="POST", headers=None):
    async def go():
        target = app or keys_function.app
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=target), base_url=BASE) as http:
            return await http.request(method, "/api/keys", headers={"x-real-ip": ip, **(headers or {})})
    return anyio.run(go)


def test_issues_a_random_key_and_the_personal_connector_url_that_works(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    r = post(headers={"host": "room-check.vercel.app", "x-forwarded-host": "evil.example"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert len(body["key"]) >= 32
    assert body["url"] == f"https://room-check.vercel.app/api/mcp?key={body['key']}"
    assert _auth.keys.check(body["key"]) == _auth.short_hash(body["key"])
    assert post().json()["key"] != body["key"]


def test_the_local_server_issues_keys_too():
    r = post(app=connector.make_app())
    assert r.status_code == 200 and r.json()["url"].startswith(f"{BASE}/api/mcp?key=")


def test_only_post_issues_a_key():
    assert post(method="GET").status_code == 405


def test_issuing_is_limited_per_address_and_for_everyone_each_day():
    assert [post(ip="203.0.113.7").status_code for _ in range(3)] == [200, 200, 429]
    assert "Try again tomorrow" in post(ip="203.0.113.7").json()["error"]
    assert post(ip="198.51.100.2").status_code == 200  # another address still gets one
    r = post(ip="192.0.2.9")  # but only 3 a day for everyone
    assert r.status_code == 429 and "for today" in r.json()["error"]


def test_addresses_and_keys_are_stored_hashed_never_in_plain_text(monkeypatch, caplog):
    redis = FakeRedis()
    monkeypatch.setattr(_auth, "keys", _auth.Keys(redis))
    monkeypatch.setattr(_auth, "issuing", _auth.Issuing(_auth.state.RedisStore(redis), per_ip=2, per_day=3))
    with caplog.at_level("INFO", logger="room_check"):
        key = post(ip="203.0.113.7").json()["key"]
    stored = str(redis.data)
    assert key not in stored and "203.0.113.7" not in stored
    assert key not in caplog.text


def test_redis_down_refuses_to_issue_rather_than_skipping_the_limit(monkeypatch):
    redis = FakeRedis()
    redis.down = True
    monkeypatch.setattr(_auth, "issuing", _auth.Issuing(_auth.state.RedisStore(redis)))
    r = post()
    assert r.status_code == 503 and "Try again in a minute" in r.json()["error"]


def test_keys_last_30_days_from_their_last_use():
    redis = FakeRedis()
    keys = _auth.Keys(redis)
    key = keys.issue()
    redis.now += 29 * 24 * 3600
    keys.check(key)  # used: another 30 days from now
    redis.now += 29 * 24 * 3600
    assert keys.check(key)
    redis.now += 31 * 24 * 3600
    with pytest.raises(_auth.KeyRejected, match="isn't recognized"):
        keys.check(key)
