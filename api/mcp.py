"""Room check connector: a remote MCP server over HTTP (Vercel entry point). A thin adapter over engine/.

Stateless: the room profile comes in with every call. No Notion access. Room photos stay with the person's Claude,
which turns them into the text room profile; no tool here accepts an image.

Local run:  .venv/bin/python -m uvicorn api.mcp:app --port 8000   ->  http://127.0.0.1:8000/api/mcp
"""
import inspect
import json
import logging
import os
from contextvars import ContextVar
from typing import Annotated, Any, Callable, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from api import _auth
from engine import caps, guide, judge, models, search, state
from engine.types import FindResult, Product, Room, Verdict

log = logging.getLogger("room_check")
logging.basicConfig(level=logging.INFO, format="%(message)s")

MAX_LINKS = 6
MAX_REFERENCE_LINKS = 3
MAX_ROOM_CHARS = 10_000  # a room profile is short text; anything bigger is not a room profile
MAX_TEXT = 200  # store-supplied strings are trimmed before they reach the person's Claude

# Caps can be overridden by env vars (defaults live in caps.Caps). Counts, spend and the run log live in Redis when
# the Upstash env vars are set, so they survive between serverless invocations; in memory otherwise.
# TESTER_KEYS lists the short hashes (never the keys) of tester keys, comma-separated.
_CAP_ENV = {"checks_per_day": ("CAP_CHECKS_PER_DAY", int), "searches_per_day": ("CAP_SEARCHES_PER_DAY", int),
            "daily_usd": ("CAP_DAILY_USD", float),
            "tester_checks_per_day": ("CAP_TESTER_CHECKS_PER_DAY", int),
            "tester_searches_per_day": ("CAP_TESTER_SEARCHES_PER_DAY", int)}
_redis = state.shared_client()
daily_caps = caps.Caps(state.RedisStore(_redis) if _redis else caps.MemoryStore(),
                       testers=frozenset(h.strip() for h in os.environ.get("TESTER_KEYS", "").split(",") if h.strip()),
                       **{k: cast(os.environ[env]) for k, (env, cast) in _CAP_ENV.items() if os.environ.get(env)})
caller: ContextVar[str] = ContextVar("caller", default="")  # the current call's short hash, set by _run
run_log = state.run_log()

# The connector's flow, told to the person's Claude by start_room_check.
FLOW = [
    "Ask for photos of the room. Read them yourself and turn them into the room profile; never send photos to this server.",
    "Fill what the photos can't show by asking: floor and wall tones, budget per piece, and where each owned piece sits.",
    "Propose taste rules from what the person says they like and dislike. Mark a rule confirmed only after they agree to it.",
    "Call start_room_check with the room profile. From then on, send the room_profile it returns, exactly, on every call.",
    "Then call check_pieces with links they're considering, or find_similar with what they're looking for.",
    "When they want several pieces that go together (a set, a whole corner), call find_similar for each piece, show all "
    "the options, suggest 1 or 2 combinations that echo their reference photos, then ask them to choose.",
    "Before they buy one piece, call ready_to_buy with that link and the size or color they'd pick.",
]

# Every tool says this, so each call in one chat judges against the same room (ticket 09).
SAME_ROOM = ("Room profile: send the exact room_profile that start_room_check returned, unchanged, on every call in "
             "this chat. Change it only when the person corrects or adds something, then call start_room_check again "
             "with the change and use the room_profile it returns from then on.")

server = MCPServer(
    "Room check",
    instructions="Checks furniture and decor against a person's room. Start with start_room_check. "
                 "Turn room photos into the room profile yourself; never send photos to this server. " + SAME_ROOM,
)

RoomArg = Annotated[Room, Field(description="The room profile, built from the person's photos and answers. Text only.")]


# ---------- translation: engine types -> tool JSON ----------

def _trim(text: str) -> str:
    return text[:MAX_TEXT]


def _card(p: Product, v: Verdict) -> dict[str, Any]:
    """Verified facts plus the judgment. Structured fields only; never page text."""
    return {
        "name": _trim(p.name), "store": p.store, "link": p.url, "image": p.image,
        "price": p.price, "was_price": p.was_price, "currency": p.currency, "price_varies": p.price_varies,
        "versions": [{"name": _trim(x.name), "price": x.price, "available": x.available} for x in p.variants[:20]],
        "dimensions_in": p.dims.model_dump(), "material": _trim(p.material), "available": p.available,
        "verified": p.verified, "couldnt_read": p.blocked_reason,
        "fits": v.fits, "watch_try": p.next_step or v.watch_try, "next_step": p.next_step, "flags": v.flags, "score": v.score, "unknowns": v.unknowns,
    }


def _find_result(r: FindResult) -> dict[str, Any]:
    return {"looking_for": r.looking_for, "picks": [_card(p, v) for p, v in r.picks],
            "left_out": r.left_out, "message": r.message, "failed": r.failed, "reason": r.reason}


# ---------- plumbing every tool shares: key, caps, run ID, cost, run log ----------

def _key(ctx: Context) -> Optional[str]:
    """The personal key from the connector URL (?key=...), or a header for scripts. Never logged."""
    request = ctx.request_context.request
    key = getattr(request, "query_params", {}).get("key") if request is not None else None
    return (key or (ctx.headers or {}).get("x-room-check-key") or "")[:64] or None


def _run(ctx: Context, tool: str, cap: Optional[str], room: Optional[Room], work: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        who = _auth.keys.check(_key(ctx))  # caps, logs and the run log see this short hash, never the key itself
    except _auth.KeyRejected as e:
        raise ToolError(str(e)) from e
    caller.set(who)
    models.new_run(tool)  # engine steps may start their own run; whichever is current at the end is the one reported
    if room is not None and len(room.model_dump_json()) > MAX_ROOM_CHARS:
        raise ToolError("That room profile is too long. Keep it to the floor, walls, pieces, budget, likes and rules.")
    try:
        if cap:
            daily_caps.use(who, cap)
    except caps.CapReached as e:
        raise ToolError(str(e)) from e
    except state.StateError as e:  # never skip the caps: no limits check, no paid work
        log.warning(json.dumps({"event": "caps_unavailable", "tool": tool, "error": str(e)}))
        raise ToolError("The room check can't check today's limits right now. Try again in a minute.") from e
    try:
        result = work()
    except models.ModelError as e:
        _record_run(tool, who, ok=False)
        raise ToolError(f"{e} (run {models.run_id.get()})") from e
    except Exception:
        _record_run(tool, who, ok=False)  # a search timeout still spent the model calls before it
        raise
    return {**result, **_record_run(tool, who, ok=True)}


def _record_run(tool: str, who: str, ok: bool) -> dict[str, Any]:
    """Log this run's cost, add it to today's global spend and the run log, and return the run ID and cost for the reply."""
    rid = models.run_id.get()
    cost = models.run_cost(rid)
    try:
        daily_caps.spend(cost["usd"])
    except state.StateError as e:
        log.warning(json.dumps({"event": "spend_not_counted", "run_id": rid, "usd": cost["usd"], "error": str(e)}))
    log.info(json.dumps({"event": "tool_call", "tool": tool, "run_id": rid, "user": who, "ok": ok, **cost}))
    run_log.record({"run_id": rid, "tool": tool, "ok": ok, "tokens": cost["input_tokens"] + cost["output_tokens"],
                    "searches": cost["searches"], "seconds": cost["seconds"], "usd": cost["usd"]},
                   who=who, day=daily_caps.day())
    return {"run_id": rid, "cost": cost}


# ---------- tools ----------

def _room_tool(fn: Callable) -> Callable:
    return server.tool(description=f"{inspect.cleandoc(fn.__doc__ or '')}\n\n{SAME_ROOM}")(fn)


@_room_tool
def start_room_check(ctx: Context, room: Optional[RoomArg] = None) -> dict[str, Any]:
    """Start here. Returns the steps of a room check, the cleaned-up room profile (room_profile) and what's
    still unknown about the room, so you know what to ask. Call again after filling in or changing the profile. Free; doesn't count toward daily limits."""
    sent = room or Room()

    def work() -> dict[str, Any]:
        cleaned = guide.clean_room(sent)  # after _run's size check, so padding can't slip past it
        limits = daily_caps.limits_for(caller.get())
        return {
            "flow": FLOW,
            "room_profile": cleaned.model_dump(),
            "still_unknown": guide.room_gaps(cleaned),
            "daily_limits": {"checks": limits["check"], "searches": limits["search"]},
            # Last, so it is the end of what Claude shows: the profile to save for the next chat.
            "save_for_next_chat": guide.profile_block(cleaned),
        }
    return _run(ctx, "start", None, sent, work)


@_room_tool
def check_pieces(
    ctx: Context,
    room: RoomArg,
    links: Annotated[list[str], Field(min_length=1, max_length=MAX_LINKS, description="Product page links (https) the person is considering.")],
) -> dict[str, Any]:
    """Check pieces against the room: verified price, size and material from each store page, contrast with what's
    already there, budget, and a Fits / Watch / Try line for each. Counts as one check toward the daily limit."""
    return _run(ctx, "check", "check", room, lambda: {
        "pieces": [_card(p, v) for p, v in judge.check_pieces(room, links)],
    })


@_room_tool
def find_similar(
    ctx: Context,
    room: RoomArg,
    reference: Annotated[str, Field(max_length=500, description="What they're looking for, in words (piece type, material, color, shape).")],
    reference_links: Annotated[list[str], Field(max_length=MAX_REFERENCE_LINKS, description="Optional product links to find pieces like.")] = [],
    budget: Annotated[Optional[float], Field(gt=0, description="Price limit per piece in USD, if different from the room's budget.")] = None,
    note: Annotated[str, Field(max_length=500, description="Anything else from the person, e.g. 'cream or light upholstery'.")] = "",
) -> dict[str, Any]:
    """Search the web for pieces like the reference that suit this room: verifies every link and price, filters on
    budget and taste, and returns the best 3 plus what was left out and why. Counts as one search toward the daily limit."""
    searched_room = room.model_copy(update={"budget": budget}) if budget else room
    return _run(ctx, "find", "search", room, lambda: _find_result(search.find_similar(
        searched_room, reference_text=reference, reference_urls=reference_links or None, note=note)))


@_room_tool
def ready_to_buy(
    ctx: Context,
    room: RoomArg,
    link: Annotated[str, Field(description="The product page link they're about to buy.")],
    version: Annotated[str, Field(max_length=200, description="The size or color they'd pick, if the piece comes in several.")] = "",
) -> dict[str, Any]:
    """Last look before buying one piece. Warns (never blocks) about missing dimensions, return window, which version,
    price, or a confirmed taste rule it breaks. Counts as one check toward the daily limit."""
    def work() -> dict[str, Any]:
        product, verdict = judge.check_pieces(room, [link])[0]
        return {**judge.ready_to_buy(room, product, verdict, chosen=version or None), "piece": _card(product, verdict)}
    return _run(ctx, "ready", "check", room, work)


# Vercel's Python runtime serves this ASGI app. Stateless JSON responses suit serverless (no sessions to keep).
# Localhost-only host checks are on for local runs and off on Vercel, where the host is the deployment's domain.
server.custom_route("/api/keys", methods=["POST"])(_auth.issue_endpoint)  # Vercel routes it to api/keys.py


def make_app():
    return server.streamable_http_app(
        streamable_http_path="/api/mcp", stateless_http=True, json_response=True,
        host="0.0.0.0" if os.environ.get("VERCEL") else "127.0.0.1",
    )


app = make_app()
