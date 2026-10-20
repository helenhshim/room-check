"""Room profile helpers: the one cleaned-up profile to resend, and what's still unknown in it. No network, no model."""
import json
import re

from .types import Room


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _first_of_each(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for x in items:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def clean_room(room: Room) -> Room:
    """The one room profile Claude resends on every call in a chat: spacing tidied, blank entries and repeated likes
    dropped, order kept. Owned pieces are never merged (a room can have two of the same chair). Cleaning a cleaned
    profile changes nothing."""
    def tidy_strings(model):
        return model.model_copy(update={k: _tidy(v) for k, v in model if isinstance(v, str)})
    owned = [tidy_strings(p) for p in room.owned]
    rules = [tidy_strings(r) for r in room.rules]
    return tidy_strings(room).model_copy(update={
        "owned": [p for p in owned if p.name],
        "likes": _first_of_each([x for x in map(_tidy, room.likes) if x]),
        "rules": [r for r in rules if r.what],
    })


PLACES = "(on the floor in the middle, against a wall, off to the side)"


def _the(name: str) -> str:
    """'Sofa' -> 'the sofa'. Names starting with an acronym ('TV stand', 'IKEA Billy') keep their capitals."""
    return "the " + (name if name[:2].isupper() else name[:1].lower() + name[1:])


def room_gaps(room: Room) -> list[dict[str, str]]:
    """What judgment would be guessing about, in the order worth asking."""
    gaps = []
    if not room.floor:
        gaps.append({"field": "floor", "ask": "What's the floor (material and tone, e.g. dark wood, light oak, gray tile)?"})
    if not room.walls:
        gaps.append({"field": "walls", "ask": "What color are the walls?"})
    unplaced = [_the(p.name) for p in room.owned if not p.placement]
    if not room.owned:
        gaps.append({"field": "owned", "ask": f"What's already in the room, and where does each piece sit {PLACES}?"})
    elif unplaced:  # ask about the pieces missing a placement, not the whole room again
        names = " and ".join(unplaced) if len(unplaced) <= 2 else ", ".join(unplaced[:-1]) + ", and " + unplaced[-1]
        gaps.append({"field": "owned", "ask": f"Where {'does' if len(unplaced) == 1 else 'do'} {names} sit {PLACES}?"})
    if not room.likes:  # without likes, checks and searches fall back to generic taste (live test, 10/19: tacky picks)
        gaps.append({"field": "likes", "ask": "What do they like? A few style words (for example paper lanterns, mid-century, chrome) "
                                              "or pins and photos of rooms they love. Ask this before any check or search."})
    if not room.budget:
        gaps.append({"field": "budget", "ask": "What's the budget per piece?"})
    if not room.confirmed_rules():
        gaps.append({"field": "rules", "ask": "Any tones or materials that work only in some places? Propose a rule and confirm it with them."})
    return gaps


def profile_block(room: Room) -> str:
    """The room profile as a block to save in a Claude Project's instructions, so a new chat starts with the same room.
    The server keeps nothing; the person's Claude does. Empty for a blank room."""
    if not (room.floor or room.walls or room.owned or room.likes or room.rules):
        return ""
    return ("Room check profile. Save this in a Claude Project's instructions. At the start of a new chat, call "
            "start_room_check with exactly this room, then send the room_profile it returns on every call.\n"
            "```json\n" + json.dumps(room.model_dump(), indent=1, ensure_ascii=False) + "\n```")
