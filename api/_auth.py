"""Personal connector keys: issue a random key per person, check it on every call (ticket 04).

Requests reach the connector from Anthropic's servers, so the key in each person's connector URL is what tells people
apart. Only a SHA-256 of each key is stored; caps, logs and the run log see a short hash, never the key itself.
The leading underscore keeps Vercel from serving this file as its own function.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse

from engine import caps, state

log = logging.getLogger("room_check")

MISSING = ("This room check link has no personal key. Get your own link from the room check setup page, then add it "
           "in Claude as the connector URL (Customize > Connectors).")
UNKNOWN = ("This room check link's key isn't recognized. Get a new link from the room check setup page and replace "
           "the connector URL in Claude (Customize > Connectors).")
UNAVAILABLE = "The room check can't check your key right now. Try again in a minute."


class KeyRejected(Exception):
    """The call can't go ahead. The message is safe to show the person."""


def short_hash(key: str) -> str:
    """Who called, for caps, logs and the run log."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _redis_key(key: str) -> str:
    # Keys are 192 random bits, so an unsalted hash can't be reversed by guessing.
    return "key:" + hashlib.sha256(key.encode()).hexdigest()


class Keys:
    """Issued keys, in Redis when it's set up (shared by every function), else in memory (local runs, tests)."""

    def __init__(self, redis):
        self.redis = redis
        self._memory: set[str] = set()

    def issue(self) -> str:
        key = secrets.token_urlsafe(24)
        if self.redis:
            self.redis.pipeline(["SET", _redis_key(key), "1", "EX", state.KEEP])
        else:
            self._memory.add(_redis_key(key))
        return key

    def check(self, key: Optional[str]) -> str:
        """The caller's short hash, or KeyRejected with a message to relay."""
        if not key:
            raise KeyRejected(MISSING)
        try:
            # Each use keeps the key 30 more days; a key unused for 30 days stops working (spec: Redis state kept 30 days).
            known = (self.redis.pipeline(["GET", _redis_key(key)], ["EXPIRE", _redis_key(key), state.KEEP])[0] if self.redis
                     else _redis_key(key) in self._memory)
        except state.StateError as e:
            log.warning(json.dumps({"event": "keys_unavailable", "error": str(e)}))
            raise KeyRejected(UNAVAILABLE) from e
        if not known:
            raise KeyRejected(UNKNOWN)
        return short_hash(key)


# ---------- issuing (POST /api/keys, for the setup page in ticket 06) ----------

class IssueRefused(Exception):
    """Today's issuing limit is used up. The message is safe to show the person."""


# Addresses are only counted, under a keyed hash that changes daily and expires with the counter. The Upstash token is
# the secret every function already shares; local runs (memory store) use a random one per process.
_ADDRESS_SECRET = (os.environ.get("UPSTASH_REDIS_REST_TOKEN") or secrets.token_hex(16)).encode()


class Issuing:
    """Keys per address per day, plus a cap for everyone, so keys can't be minted in bulk to dodge per-key limits."""

    def __init__(self, store, per_ip: int = 3, per_day: int = 100):
        self.store, self.per_ip, self.per_day = store, per_ip, per_day

    def allow(self, address: str) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        address_hash = hmac.new(_ADDRESS_SECRET, f"{day}:{address}".encode(), hashlib.sha256).hexdigest()[:16]
        if self.store.incr(f"issue:{day}:{address_hash}", 1, caps.DAY) > self.per_ip:
            raise IssueRefused(f"This address already got {self.per_ip} room check links today. "
                               "Try again tomorrow (resets at midnight UTC).")
        if self.store.incr(f"issue:{day}:all", 1, caps.DAY) > self.per_day:
            raise IssueRefused("The room check has handed out all its links for today. "
                               "Try again tomorrow (resets at midnight UTC).")


def _address(request: Request) -> str:
    """The browser's address. Vercel sets x-real-ip and x-forwarded-for itself, so a client can't fake them there."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return request.headers.get("x-real-ip") or forwarded or (request.client.host if request.client else "unknown")


async def issue_endpoint(request: Request) -> JSONResponse:
    """Issue a new key and return the personal connector URL to paste into Claude."""
    try:
        issuing.allow(_address(request))
        key = keys.issue()
    except IssueRefused as e:
        return JSONResponse({"error": str(e)}, status_code=429)
    except state.StateError as e:  # never skip the limit: no limit check, no key
        log.warning(json.dumps({"event": "keys_unavailable", "error": str(e)}))
        return JSONResponse({"error": "Couldn't make a link right now. Try again in a minute."}, status_code=503)
    log.info(json.dumps({"event": "key_issued"}))
    # The host the browser asked for (on Vercel, the deployment's own domain, always https); forwarded-host headers
    # aren't trusted.
    base = f"{'https' if os.environ.get('VERCEL') else request.url.scheme}://{request.url.netloc}"
    return JSONResponse({"key": key, "url": f"{base}/api/mcp?key={key}"}, headers={"cache-control": "no-store"})


_redis = state.shared_client()
keys = Keys(_redis)
_ISSUE_ENV = {"per_ip": "KEYS_PER_IP_PER_DAY", "per_day": "KEYS_PER_DAY"}
issuing = Issuing(state.RedisStore(_redis) if _redis else caps.MemoryStore(),
                  **{k: int(os.environ[env]) for k, env in _ISSUE_ENV.items() if os.environ.get(env)})
