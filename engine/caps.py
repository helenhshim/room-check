"""Daily caps: checks and searches per user, plus a global spend cap. Counters live behind a Store (memory now, Redis later)."""
import threading
import time
from typing import Protocol

DAY = 24 * 3600
LIMIT_NAMES = {"check": ("check", "checks"), "search": ("search", "searches")}


class CapReached(Exception):
    """A daily limit is used up. The message is safe to show the person."""


class Store(Protocol):
    def incr(self, key: str, amount: float, ttl: int) -> float:
        """Add to a counter (created with this TTL if new) and return the new value."""

    def get(self, key: str) -> float:
        """Current counter value, 0 if missing or expired."""


class MemoryStore:
    def __init__(self):
        self._data: dict[str, tuple[float, float]] = {}  # key -> (expires_at, value)
        self._lock = threading.Lock()

    def get(self, key: str) -> float:
        with self._lock:
            item = self._data.get(key)
            return item[1] if item and item[0] > time.time() else 0.0

    def incr(self, key: str, amount: float, ttl: int) -> float:
        with self._lock:
            item = self._data.get(key)
            if not item or item[0] <= time.time():
                item = (time.time() + ttl, 0.0)
            self._data[key] = (item[0], item[1] + amount)
            return self._data[key][1]


class Caps:
    def __init__(self, store: Store, checks_per_day: int = 10, searches_per_day: int = 3, daily_usd: float = 5.0,
                 testers: frozenset[str] = frozenset(), tester_checks_per_day: int = 50, tester_searches_per_day: int = 15):
        self.store = store
        self.limits = {"check": checks_per_day, "search": searches_per_day}
        self.daily_usd = daily_usd
        # Testers (live testing) get higher per-person limits; the global spend cap still applies.
        self.testers = testers
        self.tester_limits = {"check": tester_checks_per_day, "search": tester_searches_per_day}

    def limits_for(self, user: str) -> dict[str, int]:
        return self.tester_limits if user in self.testers else self.limits

    def day(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def use(self, user: str, kind: str) -> None:
        """Count one use, or raise CapReached if today's limit is already used up."""
        if self.spent_today() >= self.daily_usd:
            raise CapReached("The room check has hit its daily limit for everyone. Try again tomorrow (resets at midnight UTC).")
        limit = self.limits_for(user)[kind]
        used = self.store.incr(f"caps:{self.day()}:{user}:{kind}", 1, DAY)
        if used > limit:
            one, many = LIMIT_NAMES[kind]
            raise CapReached(f"You've used today's {limit} {one if limit == 1 else many}. Try again tomorrow (resets at midnight UTC).")

    def spend(self, usd: float) -> None:
        """Add a finished call's model and search cost to today's global total."""
        if usd > 0:
            self.store.incr(f"spend:{self.day()}", usd, DAY)

    def spent_today(self) -> float:
        return self.store.get(f"spend:{self.day()}")
