from engine import guide
from engine.types import OwnedPiece, Room, Rule


def test_empty_room_asks_for_everything_that_drives_judgment():
    gaps = guide.room_gaps(Room())
    assert [g["field"] for g in gaps] == ["floor", "walls", "owned", "likes", "budget", "rules"]


def test_complete_room_has_no_gaps():
    room = Room(floor="dark wood", walls="white", budget=500, likes=["chrome"],
                owned=[OwnedPiece(name="Sofa", placement="against the wall")],
                rules=[Rule(what="dark wood", works_when="off to the side", fails_when="low and central", status="confirmed")])
    assert guide.room_gaps(room) == []


def test_owned_pieces_without_placement_are_a_gap():
    room = Room(floor="oak", walls="white", budget=300, likes=["chrome"], owned=[OwnedPiece(name="Coffee table")],
                rules=[Rule(what="x", works_when="y", fails_when="z", status="confirmed")])
    assert [g["field"] for g in guide.room_gaps(room)] == ["owned"]


def test_one_unplaced_piece_is_asked_about_by_name():
    room = Room(owned=[OwnedPiece(name="Sofa"), OwnedPiece(name="Stool", placement="off to the side")])
    ask = next(g["ask"] for g in guide.room_gaps(room) if g["field"] == "owned")
    assert ask.startswith("Where does the sofa sit") and "already in the room" not in ask


def test_several_unplaced_pieces_are_named_together():
    room = Room(owned=[OwnedPiece(name="Sofa"), OwnedPiece(name="TV stand"), OwnedPiece(name="Arc lamp")])
    ask = next(g["ask"] for g in guide.room_gaps(room) if g["field"] == "owned")
    assert ask.startswith("Where do the sofa, the TV stand, and the arc lamp sit")


def test_no_owned_pieces_asks_whats_in_the_room():
    ask = next(g["ask"] for g in guide.room_gaps(Room()) if g["field"] == "owned")
    assert ask.startswith("What's already in the room")


def test_only_proposed_rules_still_counts_as_no_rules():
    room = Room(floor="oak", walls="white", budget=300, likes=["chrome"], owned=[OwnedPiece(name="Sofa", placement="wall")],
                rules=[Rule(what="x", works_when="y", fails_when="z")])
    assert [g["field"] for g in guide.room_gaps(room)] == ["rules"]


def test_empty_likes_are_unknown_and_asked_before_any_check_or_search():
    room = Room(floor="oak", walls="white", budget=300, owned=[OwnedPiece(name="Sofa", placement="wall")],
                rules=[Rule(what="x", works_when="y", fails_when="z", status="confirmed")])
    [gap] = guide.room_gaps(room)
    assert gap["field"] == "likes" and "before any check or search" in gap["ask"]
    assert guide.room_gaps(room.model_copy(update={"likes": ["paper lanterns"]})) == []


def test_profile_block_is_json_the_person_can_save_and_paste_back():
    import json
    room = guide.clean_room(Room(floor="dark wood", likes=["chrome"], budget=500))
    block = guide.profile_block(room)
    assert "Claude Project" in block and "start_room_check" in block
    saved = block.split("```json\n")[1].split("\n```")[0]
    assert Room(**json.loads(saved)) == room


def test_a_blank_room_has_no_profile_to_save():
    assert guide.profile_block(Room()) == ""
