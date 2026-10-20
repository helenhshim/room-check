"""An in-memory stand-in for Upstash's REST pipeline, covering only the commands engine/state.py sends."""
import time
from typing import Any


class FakeRedis:
    def __init__(self):
        self.now = time.time()
        self.data: dict[str, Any] = {}
        self.expires: dict[str, float] = {}
        self.sent: list[list] = []
        self.down = False

    def _live(self, key: str):
        if key in self.expires and self.expires[key] <= self.now:
            self.data.pop(key, None)
            self.expires.pop(key, None)
        return self.data.get(key)

    def pipeline(self, *commands: list) -> list:
        if self.down:
            from engine.state import StateError
            raise StateError("redis unreachable")
        commands = tuple([str(x) for x in c] for c in commands)  # Upstash gets every argument as a string
        self.sent.extend(commands)
        return [self._run(*c) for c in commands]

    def _run(self, op: str, key: str, *args):
        op, value = op.upper(), self._live(key)
        if op == "GET":
            return value
        if op == "SET":
            self.data[key] = args[0]
            if len(args) == 3 and args[1].upper() == "EX":
                self.expires[key] = self.now + int(args[2])
            return "OK"
        if op == "INCRBYFLOAT":
            self.data[key] = str(float(value or 0) + float(args[0]))
            return self.data[key]
        if op == "EXPIRE":
            if value is None or ("NX" in args and key in self.expires):
                return 0
            self.expires[key] = self.now + int(args[0])
            return 1
        if op == "HINCRBY":
            h = self.data.setdefault(key, {})
            h[args[0]] = h.get(args[0], 0) + int(args[1])
            return h[args[0]]
        if op == "HGETALL":
            return [x for k, v in (value or {}).items() for x in (k, str(v))]
        if op == "SADD":
            s = self.data.setdefault(key, set())
            before = len(s)
            s.update(args)
            return len(s) - before
        if op == "SCARD":
            return len(value or ())
        if op == "RPUSH":
            self.data.setdefault(key, []).extend(args)
            return len(self.data[key])
        if op == "LRANGE":
            items = value or []
            start, stop = int(args[0]), int(args[1])
            return items[start:None if stop == -1 else stop + 1]
        raise ValueError(f"FakeRedis doesn't know {op}")
