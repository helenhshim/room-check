"""Claude calls for the engine: model routing, structured JSON output, prompt caching, cost logging."""
import contextvars
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import anthropic


def _load_dotenv() -> None:
    """Local development only: read .env without overriding real environment variables (Vercel sets those)."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# Model routing lives in config, not in calling code.
MODELS = {
    "small": os.environ.get("MODEL_SMALL", "claude-haiku-5-5"),   # describe what to look for, write queries
    "strong": os.environ.get("MODEL_STRONG", "claude-sonnet-5-5"),
    # Batched judgment. Haiku passed every judgment eval in 3 of 3 runs; Sonnet over-blocked warm accents.
    "judge": os.environ.get("MODEL_JUDGE", "claude-haiku-5-5"),
}
EFFORT = {"small": "low", "strong": "medium", "judge": "medium"}

# USD per million tokens (input, output, cache read). Cache writes bill at 1.25x input.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
}

_client: Optional[anthropic.Anthropic] = None
run_id: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="none")
usage_log: list[dict[str, Any]] = []  # in-memory; the server writes each run's totals to the run log (engine/state.py)
search_log: list[str] = []  # run ID per web search made


class ModelError(Exception):
    """The model call failed or returned something unusable. Callers mark the work 'needs you'."""


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=60.0, max_retries=2)
    return _client


def new_run(kind: str) -> str:
    rid = f"{kind}-{uuid.uuid4().hex[:8]}"
    run_id.set(rid)
    return rid


def _cost(model: str, usage: Any) -> float:
    pin, pout, pread = PRICES.get(model, (0.0, 0.0, 0.0))
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (usage.input_tokens * pin + write * pin * 1.25 + read * pread + usage.output_tokens * pout) / 1_000_000


def ask_json(tier: str, system: str, content: list[dict], schema: dict, max_tokens: int = 8000) -> dict:
    """One structured call. The fixed system prompt is cached; the variable content goes in the user turn."""
    model = MODELS[tier]
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": EFFORT[tier], "format": {"type": "json_schema", "schema": schema}},
    )
    started = time.time()
    try:
        if model.startswith("claude-sonnet-5-5"):
            # Server-side fallback: if a safety classifier declines, the API retries on another model.
            response = client().beta.messages.create(
                **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        else:
            response = client().messages.create(**kwargs)
    except anthropic.RateLimitError as e:
        raise ModelError("rate limited by the model provider; try again shortly") from e
    except anthropic.APIStatusError as e:
        raise ModelError(f"model call failed ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ModelError("couldn't reach the model provider") from e

    usage_log.append({
        "run": run_id.get(), "tier": tier, "model": response.model, "seconds": round(time.time() - started, 2),
        "input": response.usage.input_tokens, "output": response.usage.output_tokens,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "usd": round(_cost(model, response.usage), 5), "stop": response.stop_reason,
    })
    if response.stop_reason == "refusal":
        raise ModelError("the model declined this request")
    if response.stop_reason == "max_tokens":
        raise ModelError("the answer was cut off (max tokens)")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ModelError("no answer returned")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ModelError("the answer wasn't valid JSON") from e


def count_search() -> None:
    search_log.append(run_id.get())


def run_cost(rid: Optional[str] = None) -> dict[str, float]:
    rid = rid or run_id.get()
    rows = [r for r in usage_log if r["run"] == rid]
    return {
        "calls": len(rows),
        "usd": round(sum(r["usd"] for r in rows), 5),
        "seconds": round(sum(r["seconds"] for r in rows), 2),
        "input_tokens": sum(r["input"] + r["cache_read"] + r["cache_write"] for r in rows),
        "output_tokens": sum(r["output"] for r in rows),
        "searches": search_log.count(rid),
    }
