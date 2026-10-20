"""Judge products against a room: the check (Fits / Watch / Try), batched into one model call."""
from typing import Optional

from . import fetch, models
from .types import Product, Room, Verdict

SYSTEM = """You are the judgment step of Interior Agent, which helps people decide whether a piece of furniture or decor works in their room.

You receive a room profile (what is already in the room, its floor and walls, the budget, what the people like, and their confirmed taste rules), optionally a description of what they are looking for, and a numbered list of candidate products. Each candidate has verified facts and, when available, a product photo.

How to judge:
Built-in principles are general design practice and hold for any style: minimal or maximal, tonal or high-contrast, traditional or modern. They describe how a piece reads in a room, not what anyone should like. Taste comes only from the people's confirmed rules and likes.
1. Context. Judge each piece against what's already in the room and where it would sit, never on its own.
2. Legibility. A piece should read as itself against its surround: the floor and any rug for low pieces, the wall behind tall ones, the pieces beside it. It can read apart through tone, texture or sheen, form (raised legs, an open base, a different top), or layering. A rug or lighter pieces already in the room count: a piece sitting on a rug is judged against the rug, not the floor. A fix they would still have to add (a rug, styling) doesn't count. Matching is fine when texture or form clearly differs (smooth against nubby, sheen against matte, an open frame against a solid wall); tonal rooms work this way. Large pieces in the main area matter most. Two failures, handled differently:
- Merges: a large piece in the same color and the same material family as what is right behind, under or next to it, with no clear difference in tone, texture or form (a navy velvet sofa against a navy painted wall, a white lacquer cabinet against a white gloss wall, a gray wool sofa on a gray wool rug). Same color means the same color, not just the same light or dark range: white beside beige, or black beside brown wood, differ. Flag "Low contrast", score 2 or lower, and put the fix in Try.
- Near-match: a different wood, finish or color whose tone or undertone sits close and looks accidental (a cherry table on a mahogany floor). Never "Low contrast": score 3, no flag, and name the undertone and the fix in Try.
3. Scale and proportion. Compare the piece's size with the room and the pieces it relates to, using only dimensions you're given. Common guides: a coffee table about two thirds the sofa's length and a little lower than its seat, with about 14 to 18 inches between them; a rug large enough that the seating's front legs rest on it. If a needed dimension is missing, list it under unknowns. Never write a size, height or distance that isn't in the candidate's facts, including numbers from these guides. Never guess.
4. Cohesion. Pieces tie in when they repeat something already in the room (a material, finish, color or shape). A room usually has one or a few focal pieces, so a new statement piece competing with an existing one is worth a Watch. These shape the score and the lines; on their own they never block a piece or add a flag.
5. Confirmed taste rules are binding and come first. Each says what, when it works, and when it doesn't. When a rule speaks to the same thing as a built-in principle, follow the rule and don't apply the principle to that concern again. That covers a rule that allows matching tones, says where contrast can come from, or rules out a tone the principles would welcome. If a piece breaks a confirmed rule, flag "Conflicts with a rule" and say which. Never invent rules.
6. Likes are soft. They can raise a score when a piece matches, never lower one or add a flag.
7. When a description of what they're looking for is given, also judge how well each piece matches it. Its stated details (material, color, legs or base, shape) are part of the match. When a piece misses one, score it lower, never praise the missed detail in fits, and name the miss in watch_try (for example "Watch: black base, not the light wood legs you asked for. Try: ...").
8. Honest over impressive. Use only the facts given and what you can see in the photo. If something that matters is unknown (size, price, return window), list it under unknowns. Never guess a number.

Scoring (score field), considering the room, the rules and, if given, the description:
5 = excellent fit, 4 = good fit, 3 = workable with a fix, 2 = poor fit, 1 = wrong for this room.

Writing:
- fits: one line, at most 15 words, the most important reason it suits this room. Concrete, referring to this room's actual pieces when relevant (the shape of "the brass base echoes the floor lamp" or "the linen weave picks up the sofa's texture", never these words unless they fit this room). If the piece misses a detail they're looking for, fits names a different strength and never mentions the missed detail.
- watch_try: one line, at most 22 words, in the form "Watch: <main concern>. Try: <a concrete fix>." If there is no real concern, write "Nothing major."
- Calm, plain, sentence case. No exclamation points, no hype, no "I". Write like a thoughtful designer friend.
- flags: only from the allowed list, only when they apply.

Return one verdict per candidate, using the candidate's index."""

MODEL_FLAGS = ["Low contrast", "Conflicts with a rule"]

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer"},
                    "fits": {"type": "string"},
                    "watch_try": {"type": "string"},
                    "flags": {"type": "array", "items": {"type": "string", "enum": MODEL_FLAGS}},
                    "unknowns": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index", "score", "fits", "watch_try", "flags", "unknowns"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def _room_text(room: Room) -> str:
    owned = "\n".join(f"- {p.name} ({', '.join(x for x in (p.material, p.tone, p.placement) if x)})" for p in room.owned) or "- (none listed)"
    rules = "\n".join(f"- {r.what}: works when {r.works_when}; doesn't work when {r.fails_when}" for r in room.confirmed_rules()) or "- (none confirmed yet)"
    return (
        f"ROOM: {room.name}\nFloor: {room.floor or 'unknown'}\nWalls: {room.walls or 'unknown'}\n"
        f"Already in the room:\n{owned}\n"
        f"Budget per piece: {'$%g' % room.budget if room.budget else 'not set'}\n"
        f"Likes (not rules): {', '.join(room.likes) or 'not set'}\n"
        f"Confirmed taste rules:\n{rules}\n"
        + (f"Notes: {room.notes}\n" if room.notes else "")
    )


def _facts(i: int, p: Product) -> str:
    dims = ", ".join(f"{k.upper()} {v:g} in" for k, v in (("w", p.dims.w), ("d", p.dims.d), ("h", p.dims.h)) if v) or "not found"
    price = f"${p.price:g}" + (" (lowest option; varies by size or color)" if p.price_varies else "") if p.price else "not found"
    return f"CANDIDATE {i}: {p.name or 'unnamed'} from {p.store}\nPrice: {price}\nDimensions: {dims}\nMaterial: {p.material or 'not stated'}"


def code_flags(room: Room, p: Product) -> list[str]:
    """Facts the code knows for certain; never left to the model."""
    flags = []
    if p.price is None:
        flags.append("Price not found")
    elif room.budget and p.price > room.budget:
        flags.append("Over budget")
    if p.price_varies:
        flags.append("Price varies by size")
    if p.dims.source == "not_found":
        flags.append("Dimensions not found")
    if p.available is False:
        flags.append("Sold out")
    return flags


def judge(room: Room, products: list[Product], looking_for: Optional[str] = None) -> list[Verdict]:
    """One batched judgment for readable products. Every judgment requires the room profile."""
    if room is None:
        raise ValueError("judgment without a room profile is a bug")
    readable = [p for p in products if not p.blocked_reason]
    verdicts: dict[str, Verdict] = {}
    if readable:
        images = fetch.images_b64([p.image for p in readable])
        content: list[dict] = [{"type": "text", "text": _room_text(room)}]
        if looking_for:
            content.append({"type": "text", "text": f"LOOKING FOR: {looking_for}"})
        for i, (p, img) in enumerate(zip(readable, images)):
            content.append({"type": "text", "text": _facts(i, p)})
            if img:
                content.append(img)
        result = models.ask_json("judge", SYSTEM, content, SCHEMA, max_tokens=8000)
        for v in result["verdicts"]:
            if 0 <= v["index"] < len(readable):
                p = readable[v["index"]]
                verdicts[p.url] = Verdict(
                    url=p.url, fits=v["fits"], watch_try=v["watch_try"],
                    flags=code_flags(room, p) + [f for f in v["flags"] if f in MODEL_FLAGS],
                    score=max(1, min(5, v["score"])), unknowns=v["unknowns"],
                )
    out = []
    for p in products:
        if p.blocked_reason:
            out.append(Verdict(url=p.url, fits="", watch_try=f"Couldn't read this page: {p.blocked_reason}. Share the price and size, or a screenshot.",
                               flags=["Couldn't read page"], score=0))
        else:
            out.append(verdicts.get(p.url) or Verdict(url=p.url, fits="", watch_try="No judgment returned.", flags=code_flags(room, p), score=0))
    return out


def check_pieces(room: Room, urls: list[str]) -> list[tuple[Product, Verdict]]:
    """The check: verified facts plus judgment for each link."""
    models.new_run("check")
    products = fetch.get_products(urls)
    return list(zip(products, judge(room, products)))


def ready_to_buy(room: Room, product: Product, verdict: Optional[Verdict] = None, chosen: Optional[str] = None) -> dict:
    """Warn (never block) about what's still unknown before buying."""
    missing = []
    if product.dims.source == "not_found":
        missing.append("dimensions")
    if product.price is None:
        missing.append("price")
    if product.price_varies and not chosen:
        missing.append("which size or color you'd buy")
    missing.append("return window")  # not checked yet in this version; always surfaced honestly
    concerns = [f for f in (verdict.flags if verdict else []) if f in ("Low contrast", "Conflicts with a rule", "Over budget", "Sold out")]
    return {"ready": not missing and not concerns, "missing": missing, "concerns": concerns}
