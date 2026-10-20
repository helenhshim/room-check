"""Core types shared by every surface (Notion runner, MCP server, evals)."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

Source = Literal["stated", "read_by_ai", "not_found"]


class OwnedPiece(BaseModel):
    name: str
    category: str = ""
    material: str = ""
    tone: str = ""
    placement: str = ""  # where it sits, e.g. "on the floor, center of the room"


class Rule(BaseModel):
    """A taste rule. Only confirmed rules drive judgment and evals."""
    what: str  # tone, color or material, e.g. "dark wood"
    works_when: str
    fails_when: str
    status: Literal["proposed", "confirmed"] = "proposed"


class Room(BaseModel):
    name: str = "Living room"
    floor: str = ""  # e.g. "dark wood"
    walls: str = ""
    owned: list[OwnedPiece] = Field(default_factory=list)
    budget: Optional[float] = None  # per item, in USD
    likes: list[str] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    notes: str = ""

    def confirmed_rules(self) -> list[Rule]:
        return [r for r in self.rules if r.status == "confirmed"]


class Variant(BaseModel):
    name: str
    price: Optional[float] = None
    available: Optional[bool] = None
    image: Optional[str] = None
    options: dict[str, str] = Field(default_factory=dict)  # e.g. {"color": "Guava", "size": "5'3\" x 8'"}
    id: str = ""
    sku: str = ""


class Dims(BaseModel):
    w: Optional[float] = None  # inches
    d: Optional[float] = None
    h: Optional[float] = None
    source: Source = "not_found"


class Product(BaseModel):
    url: str
    store: str = ""
    name: str = ""
    image: Optional[str] = None
    price: Optional[float] = None  # lowest verified price
    was_price: Optional[float] = None
    currency: str = "USD"
    variants: list[Variant] = Field(default_factory=list)
    dims: Dims = Field(default_factory=Dims)
    material: str = ""
    available: Optional[bool] = None
    verified: bool = False  # True only if fetched from the page itself
    blocked_reason: Optional[str] = None  # set when the page could not be read
    final_url: Optional[str] = None  # the page the link landed on after redirects
    chosen: str = ""  # the version the link points to (?Color=Guava), whose price and photo this product carries
    next_step: str = ""  # what to do when the page is not a product page (a pin, a listing)

    @property
    def price_varies(self) -> bool:
        prices = {v.price for v in self.variants if v.price is not None}
        return len(prices) > 1


class Verdict(BaseModel):
    url: str
    fits: str
    watch_try: str  # "Watch: ... Try: ..." or "Nothing major."
    flags: list[str] = Field(default_factory=list)
    score: int = 0  # 1-5, how well it suits the room and the reference
    unknowns: list[str] = Field(default_factory=list)


class FindResult(BaseModel):
    looking_for: str
    picks: list[tuple[Product, Verdict]] = Field(default_factory=list)
    left_out: dict[str, int] = Field(default_factory=dict)  # reason -> count
    message: str = ""
    failed: bool = False  # fewer than 3 picks is a failed result, and says why
    reason: str = ""
    candidates: int = 0  # links found by search
    pages_read: int = 0  # of those, pages that could be read
