"""Shared machine state on Upstash Redis: cap counters, product cache, run log and daily usage counts.

Serverless functions keep nothing between requests, so on Vercel this state lives in Redis. When the Upstash env vars
are missing (local runs, tests), each piece falls back to its in-memory version. Never stores a room profile or
anything the person typed: only counts, costs, run IDs, hashed keys and product pages.
"""
import functools
import json
import logging
import os
import threading
from typing import Any, Optional

import httpx2

from . import models  # noqa: F401  (loads .env before the Upstash settings are read)

log = logging.getLogger("room_check")
KEEP = 30 * 24 * 3600  # run log and usage counts are kept 30 days (spec section 6)


class StateError(Exception):
    """Redis couldn't be reached or refused a command."""


class Upstash:
    """Upstash's REST API: one HTTPS request per pipeline of commands."""

    def __init__(self, url: str, token: str, post=httpx2.post):
        self.url, self.token, self._post = url.rstrip("/"), token, post

    def pipeline(self, *commands: list) -> list:
        try:
            r = self._post(f"{self.url}/pipeline", timeout=5.0, headers={"Authorization": f"Bearer {self.token}"},
                           json=[[str(x) for x in c] for c in commands])
        except Exception as e:
            raise StateError(f"couldn't reach Redis ({type(e).__name__})") from e
        if r.status_code != 200:
            raise StateError(f"Redis refused the request ({r.status_code})")
        replies = r.json()
        errors = [x["error"] for x in replies if "error" in x]
        if errors:
            raise StateError(f"Redis error: {errors[0]}")
        return [x["result"] for x in replies]


def client_from_env() -> Optional[Upstash]:
    url, token = os.environ.get("UPSTASH_REDIS_REST_URL"), os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    return Upstash(url, token) if url and token else None


@functools.cache
def shared_client() -> Optional[Upstash]:
    """The one Redis client for caps, product cache and run log; None means everything stays in memory."""
    return client_from_env()


# ---------- cap counters (caps.Store) ----------

class RedisStore:
    def __init__(self, redis):
        self.redis = redis

    def incr(self, key: str, amount: float, ttl: int) -> float:
        # NX: the expiry is set by the first count of the day and never pushed back by later ones.
        value, _ = self.redis.pipeline(["INCRBYFLOAT", key, amount], ["EXPIRE", key, ttl, "NX"])
        return float(value)

    def get(self, key: str) -> float:
        [value] = self.redis.pipeline(["GET", key])
        return float(value or 0)


# ---------- product cache (same interface as fetch.MemoryCache) ----------

class RedisCache:
    """A miss when Redis is down: the page is fetched again rather than failing the check."""

    def __init__(self, redis):
        self.redis = redis

    def get(self, key: str) -> Optional[str]:
        try:
            [value] = self.redis.pipeline(["GET", key])
        except StateError as e:
            log.warning(json.dumps({"event": "cache_unavailable", "error": str(e)}))
            return None
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        try:
            self.redis.pipeline(["SET", key, value, "EX", ttl])
        except StateError as e:
            log.warning(json.dumps({"event": "cache_unavailable", "error": str(e)}))


# ---------- run log and daily usage counts (read by ticket 08) ----------

RUN_FIELDS = ("run_id", "tool", "ok", "tokens", "searches", "seconds", "usd")


def _only_cost_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in RUN_FIELDS if k in row}


def _usage(day: str, calls: dict[str, int], keys: int, runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"day": day, "calls": calls, "distinct_keys": keys, "usd": round(sum(r.get("usd", 0) for r in runs), 5), "runs": runs}


class MemoryRunLog:
    def __init__(self):
        self._runs: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def record(self, row: dict[str, Any], who: str, day: str) -> None:
        """who is a hash of the person's key, never the key itself."""
        with self._lock:
            self._runs.setdefault(day, []).append((who, _only_cost_fields(row)))

    def usage(self, day: str) -> dict[str, Any]:
        with self._lock:
            entries = list(self._runs.get(day, []))
        calls: dict[str, int] = {}
        for _, row in entries:
            calls[row["tool"]] = calls.get(row["tool"], 0) + 1
        return _usage(day, calls, len({who for who, _ in entries}), [row for _, row in entries])


class RedisRunLog:
    """One list of runs, one hash of calls per tool and one set of hashed keys per day, each kept 30 days."""

    def __init__(self, redis):
        self.redis = redis

    def record(self, row: dict[str, Any], who: str, day: str) -> None:
        row = _only_cost_fields(row)
        runs, calls, keys = f"runs:{day}", f"usage:{day}:calls", f"usage:{day}:keys"
        try:
            self.redis.pipeline(["RPUSH", runs, json.dumps(row)], ["HINCRBY", calls, row["tool"], 1], ["SADD", keys, who],
                                *(["EXPIRE", k, KEEP, "NX"] for k in (runs, calls, keys)))
        except StateError as e:
            log.warning(json.dumps({"event": "run_log_unavailable", "run_id": row.get("run_id"), "error": str(e)}))

    def usage(self, day: str) -> dict[str, Any]:
        runs, flat, keys = self.redis.pipeline(["LRANGE", f"runs:{day}", 0, -1], ["HGETALL", f"usage:{day}:calls"],
                                               ["SCARD", f"usage:{day}:keys"])
        flat = flat or []
        calls = {flat[i]: int(flat[i + 1]) for i in range(0, len(flat), 2)}
        return _usage(day, calls, int(keys or 0), [json.loads(r) for r in runs or []])


def run_log():
    redis = shared_client()
    return RedisRunLog(redis) if redis else MemoryRunLog()
