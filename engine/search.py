"""Find similar: describe what to look for, search, verify, filter for budget and taste, return the best 3."""
import os
import re
from typing import Optional
from urllib.parse import urlparse

import httpx2

from . import fetch, judge, models
from .types import FindResult, Product, Room

TAVILY_URL = "https://api.tavily.com/search"
MAX_QUERIES = 2
RESULTS_PER_SEARCH = 20  # the most a basic search returns, same cost as 8; most results are listing pages
MAX_CANDIDATES = 8
MAX_PER_STORE = 3  # variety, and fewer requests to any one store
SHOW = 3
MIN_SCORE = 3
DEFAULT_BUDGET = 500.0  # per piece, when the room has none
NOT_PRODUCT_HOSTS = fetch.NOT_PRODUCT_HOSTS
# Listing, inspiration and info pages, never one product (first live test, 10/18: they took candidate slots).
LISTING_PATH = re.compile(r"/(s|search|browse|b|c|l|cat|category|categories|product-category|shop|collections?|pages|"
                          r"inspiration|lookbook|shop-by-room|shop-the-room|ideas-and-advice|sitemap)(/|$)|"
                          r"/(blog|blogs|article|articles|guide|guides|news|journal)/|best-|guide-to-")
# Stores whose product pages the production server (Vercel) can read, checked 10/18 and 10/19 with a store-reading script.
# One search is limited to these. Dropped 10/18, readable from a laptop but not from Vercel: lunafurn.com,
# floydhome.com, schoolhouse.com, luluandgeorgia.com, pootoo.com (429, Shopify rate-limits datacenter servers) and
# ohouse.com (pages don't load from Vercel). Tried 10/19 and not added: annieselke.com, boutiquerugs.com,
# dashandalbert.com, jonathanadler.com, fermliving.com (read from a laptop, 429 from Vercel); jaipurliving.com (reads
# but the only price is the $25 swatch); safavieh.com, nuloom.com, arhaus.com, serenaandlily.com, normann-copenhagen.com
# (403); armadillo-co.com, surya.com, ethanallen.com, interiordefine.com, muuto.com (pages carry no product data);
# inmod.com (no price); lampsplus.com (serves a maintenance page); nordicknots.com, blu-dot.com (no product links
# from search); joybird.com and dwr.com (read, but pieces mostly well over a $500 budget). Added 10/19: castlery.com,
# ruggable.com, visualcomfort.com (read from Vercel); rugs.com (microdata, read since the extractor handles it; confirm
# from Vercel after the next deploy).
READABLE_STORES = ["article.com", "ikea.com", "thorvalddesign.com", "hay.com", "roveconcepts.com", "revivalrugs.com",
                   "burrow.com", "worldmarket.com", "saytouche.kr", "rugs.com", "castlery.com", "ruggable.com",
                   "visualcomfort.com"]
EXCLUDE = ["mapquest.com", "yelp.com", "target.com", "amazon.com", "pinterest.com", "etsy.com", "temu.com", "wayfair.com"]

DESCRIBE_SYSTEM = """You turn a reference (a photo, product facts, or words) into a short brief for finding similar purchasable pieces.
Return:
- looking_for: one sentence naming the piece type and its key attributes (form, material, finish, color, scale). Concrete. Keep the person's "or" as "or": alternatives stay alternatives ("black leather or dark walnut"), never "and".
- category: the kind of piece asked for, in two or three words, held strictly: "accent chair" means a living room seat (never a dining chair, stool or office chair), "dining chair" a dining seat, "floor lamp", "area rug".
- queries: exactly 2 short web search queries (4-8 words) for individual furniture product pages: the category plus 2-3 key attributes, e.g. "cream boucle lounge chair walnut legs". When the request lists alternatives joined by "or", give each query a different alternative. When the request is generic (a bare "white floor lamp"), shape the queries by the room's likes listed below instead of searching only the words given. No prices, no words like 'best' or 'ideas'.
- max_price: the price limit stated in the person's note (for example "under $300" -> 300), or null if the note gives none.
If the reference shows several pieces, describe the most prominent furniture or decor piece."""

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "looking_for": {"type": "string"},
        "category": {"type": "string"},
        "queries": {"type": "array", "items": {"type": "string"}},
        "max_price": {"type": ["number", "null"]},
    },
    "required": ["looking_for", "category", "queries", "max_price"],
    "additionalProperties": False,
}


def describe(reference_text: str = "", reference_image: Optional[dict] = None, reference_products: Optional[list[Product]] = None,
             budget: Optional[float] = None, note: str = "", likes: Optional[list[str]] = None) -> dict:
    content: list[dict] = []
    if reference_image:
        content.append(reference_image)
    for p in reference_products or []:
        content.append({"type": "text", "text": f"Reference product: {p.name} from {p.store}, material {p.material or 'unknown'}"})
        if p.image and (img := fetch.image_b64(p.image)):
            content.append(img)
    text = reference_text or "Find pieces like the reference."
    if budget:
        text += f"\nBudget per piece: ${budget:g}"
    if note:
        text += f"\nThe person's note: {note}"
    if likes:
        text += f"\nThe room's likes (style words and pins the person gave): {'; '.join(likes)}"
    content.append({"type": "text", "text": text})
    return models.ask_json("small", DESCRIBE_SYSTEM, content, DESCRIBE_SCHEMA, max_tokens=1500)


def web_search(query: str, max_results: int = 8, include_domains: Optional[list[str]] = None) -> list[dict]:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise models.ModelError("search isn't configured (missing TAVILY_API_KEY)")
    r = httpx2.post(TAVILY_URL, timeout=20.0, headers={"Authorization": f"Bearer {key}"},
                    json={"query": query, "max_results": max_results, "search_depth": "basic", "topic": "general",
                          "exclude_domains": EXCLUDE, **({"include_domains": include_domains} if include_domains else {})})
    models.count_search()  # counted once the search API answered, whatever it said
    if r.status_code != 200:
        raise models.ModelError(f"search failed ({r.status_code})")
    return [{"url": x.get("url", ""), "title": x.get("title", "")} for x in r.json().get("results", [])]


def _normalize(url: str) -> str:
    p = urlparse(url)
    return f"https://{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}"


def _candidate_urls(results: list[dict], exclude: set[str]) -> list[str]:
    """Only links that came back from search (never invented), product-looking, deduped."""
    seen, out, per_store = set(exclude), [], {}
    for r in results:
        url = r["url"]
        if not url.startswith("https://"):
            continue
        host_path = urlparse(url).netloc.lower() + urlparse(url).path.lower()
        if any(h in host_path for h in fetch.SKIP_HOSTS + NOT_PRODUCT_HOSTS):
            continue
        path = urlparse(url).path.lower()
        if path.strip("/") == "":  # a store's home page
            continue
        if LISTING_PATH.search(path) and "/products/" not in path and "/product/" not in path:
            continue
        key = _normalize(url)
        if key in seen:
            continue
        store = urlparse(key).netloc
        if per_store.get(store, 0) >= MAX_PER_STORE:
            continue
        seen.add(key)
        per_store[store] = per_store.get(store, 0) + 1
        out.append(url.split("#")[0])
    return out


COLOR_WORDS = frozenset("""black white cream ivory beige tan brown walnut oak natural gray grey charcoal silver gold brass
    chrome red orange yellow green blue navy pink purple rust terracotta sage olive blush mauve lichen guava cognac
    camel taupe linen boucle light dark pale""".split())
LIVING_SEAT_WORDS = ("accent chair", "armchair", "arm chair", "lounge chair", "reading chair", "club chair", "occasional chair")
NOT_LIVING_SEAT = re.compile(r"\b(dining|bar ?stool|counter ?stool|barstool|stool|office|desk|task|gaming|folding|high ?chair|nursery|outdoor|patio)\b")


def category_ok(category: str, text: str) -> bool:
    """An accent chair is a living room seat, never a dining chair, stool or office chair (live test, 10/19)."""
    c = category.lower()
    if any(w in c for w in LIVING_SEAT_WORDS) and "dining" not in c:
        return not NOT_LIVING_SEAT.search(text.lower())
    return True


def design_key(p: Product) -> str:
    """One key per design: colorways and versions of one product ("POÄNG Armchair, ... cream" / "... black") match."""
    head = re.split(r"\s[-–—|:]\s|,|\sin\s|\(", p.name.lower())[0]
    words = [w for w in re.findall(r"[\w']+", head) if w not in COLOR_WORDS and w != "the"]
    return f"{p.store}:{' '.join(words)}" if words else ""


def _page_key(p: Product) -> str:
    """The page a link landed on (after redirects), so two links to one product match."""
    return _normalize(p.final_url or p.url)


def _failure_reason(picks: int, candidates: int, pages_read: int, left_out: dict[str, int]) -> str:
    """Why a result has fewer than 3 picks: what was found, what could be read, why each was dropped."""
    dropped = "; ".join(f"{n} {why}" for why, n in left_out.items())
    return (f"Only {picks} of {SHOW}: search found {candidates} product links across the {len(READABLE_STORES)} stores "
            f"this tool can read, and {pages_read} of those pages could be read." + (f" Left out: {dropped}." if dropped else ""))


def find_similar(room: Room, reference_text: str = "", reference_image: Optional[dict] = None,
                 reference_urls: Optional[list[str]] = None, note: str = "") -> FindResult:
    models.new_run("find")
    ref_products = fetch.get_products(reference_urls) if reference_urls else []
    usual = room.budget or DEFAULT_BUDGET
    brief = describe(reference_text, reference_image, [p for p in ref_products if not p.blocked_reason], usual, note,
                     likes=room.likes)
    note_budget = brief.get("max_price")  # the person's note wins, up or down
    budget = note_budget or usual
    category = brief.get("category", "")

    # The reference is never a pick: its page after redirects, and other versions of the same design.
    seen_pages = {_normalize(u) for u in (reference_urls or [])} | {_page_key(p) for p in ref_products}
    reference_designs = {k for p in ref_products if p.name and (k := design_key(p))}
    seen_designs: set[str] = set()
    tried = set(seen_pages)

    left_out: dict[str, int] = {}
    def drop(reason: str) -> None:
        left_out[reason] = left_out.get(reason, 0) + 1

    def search_urls(queries: list[str]) -> list[str]:
        # Searches go to stores we can read: from Vercel most open-web results couldn't be read (10/18: Shopify
        # stores 429, big retailers 403), so they took candidate slots and left find similar short of 3 picks.
        results: list[dict] = []
        for query in queries:
            results += web_search(query + " buy", RESULTS_PER_SEARCH, include_domains=READABLE_STORES)
        results = [r for r in results if category_ok(category, r["url"] + " " + r.get("title", ""))]
        found = _candidate_urls(results, tried)
        tried.update(_normalize(u) for u in found)
        return found

    queue = search_urls(brief["queries"][:MAX_QUERIES])
    candidates, pages_read = len(queue), 0
    widened = False
    passing: list[tuple[Product, Verdict]] = []
    unpriced: list[Product] = []

    # Judge in rounds; search wider before settling for fewer than 3.
    looking_for = "\n".join([brief["looking_for"]] + [f"In their words: {t}" for t in (reference_text, note) if t])
    judged_room = room.model_copy(update={"budget": budget})  # the budget in force, so code flags and the model agree with the filter

    def judge_pool(pool: list[Product]) -> None:
        verdicts = judge.judge(judged_room, pool, looking_for=looking_for)
        for p, v in sorted(zip(pool, verdicts), key=lambda pv: pv[1].score, reverse=True):
            if v.score >= MIN_SCORE:
                passing.append((p, v))
            else:
                drop("doesn't suit the room or what you're looking for")

    while len(passing) < SHOW:
        if not queue and not widened:
            widened = True
            queue = search_urls([" ".join([category or brief["looking_for"]] + room.likes[:1])])
            candidates += len(queue)
        if not queue:
            break
        batch, queue = queue[: MAX_CANDIDATES + 4], queue[MAX_CANDIDATES + 4:]
        pool = []
        for p in fetch.get_products(batch):
            if p.blocked_reason:
                drop("couldn't read the page")
                continue
            pages_read += 1
            page, design = _page_key(p), design_key(p)
            if page in seen_pages:
                drop("same page as another result")
            elif design and design in reference_designs:
                drop("the piece you gave as the reference")
            elif not category_ok(category, p.name):
                drop("not the kind of piece you asked for")
            elif design and design in seen_designs:
                drop("another version of a design already considered")
            elif p.price is None:
                if note_budget:  # a price we can't read never beats a budget the person just stated
                    drop("price not found")
                else:
                    unpriced.append(p)
            elif p.price > budget:
                drop(f"over ${budget:g}")
            else:
                pool.append(p)
            seen_pages.add(page)
            if design:
                seen_designs.add(design)
        if pool:
            judge_pool(pool[:MAX_CANDIDATES])

    priced_count = len(passing)
    if priced_count < SHOW and unpriced:  # unknown prices only when nothing else qualifies, shown last and labeled
        judge_pool(unpriced[: MAX_CANDIDATES])
    else:
        for _ in unpriced:
            drop("price not found")

    priced_first = sorted(passing[:priced_count], key=lambda pv: pv[1].score, reverse=True) + passing[priced_count:]
    picks = []
    for p, v in priced_first:
        if len(picks) >= SHOW:
            drop("not in the top 3")
            continue
        if p.price and p.price > usual:  # allowed by the note, above the usual budget
            concern = v.watch_try.removeprefix("Watch: ") if v.watch_try != "Nothing major." else ""
            v.watch_try = f"Watch: over your usual ${usual:g} budget (within the ${budget:g} you asked for)." + (f" {concern}" if concern else "")
        picks.append((p, v))

    failed = len(picks) < SHOW
    reason = _failure_reason(len(picks), candidates, pages_read, left_out) if failed else ""
    message = "" if not failed else (
        f"Found {len(picks)} that fit well." if picks else "Nothing fit well enough to recommend. Try a different description or budget.")
    return FindResult(looking_for=brief["looking_for"], picks=picks, left_out=left_out, message=message,
                      failed=failed, reason=reason, candidates=candidates, pages_read=pages_read)
