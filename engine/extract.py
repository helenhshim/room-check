"""Turn store responses (Shopify product JSON, product HTML) into Product objects. No network here."""
import html
import json
import re
from fractions import Fraction
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from .types import Dims, Product, Variant

_NUM = r"(\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+)"
_UNIT = r'\s*(?:"|”|″|in\b|inch(?:es)?\b)'


def to_number(text: str) -> Optional[float]:
    """'47 1/4' -> 47.25, '15.7' -> 15.7, '3/4' -> 0.75."""
    text = text.strip()
    try:
        return float(sum(Fraction(p) for p in text.split()))
    except (ValueError, ZeroDivisionError):
        return None


def parse_dims(text: str) -> dict[str, float]:
    """Find width, depth and height in inches from labeled ('H51.2"') or verbal ('15 inches tall') text."""
    t = html.unescape(text)
    found: dict[str, float] = {}
    labels = {"h": r"(?:H|Height)", "w": r"(?:W|Width)", "d": r"(?:D|Depth)"}
    for key, label in labels.items():
        m = re.search(r"\b" + label + r"\s*[:=]?\s*" + _NUM + _UNIT, t)
        if m and (n := to_number(m.group(1))) is not None:
            found[key] = n
    words = {"w": "wide", "d": "deep", "h": "tall|high"}
    for key, word in words.items():
        if key not in found:
            m = re.search(_NUM + _UNIT + r"\s*(?:" + word + r")\b", t, re.I)
            if m and (n := to_number(m.group(1))) is not None:
                found[key] = n
    return found


def strip_tags(page: str) -> str:
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _abs(url: Optional[str], base: str) -> Optional[str]:
    if not url:
        return None
    return "https:" + url if url.startswith("//") else urljoin(base, url)


def store_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return re.sub(r"^(www|shop|int)\.", "", host)


def wanted_options(url: str) -> dict[str, str]:
    """The version a link points to: ?Color=Guava, ?Size=..., ?variant=123 (keys lowercased)."""
    return {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items() if v and v[0]}


def _option_names(data: dict) -> list[str]:
    return [(o.get("name") if isinstance(o, dict) else str(o)).lower() for o in data.get("options") or []]


def from_shopify_js(url: str, data: dict) -> Product:
    """Shopify Ajax product endpoint (/products/<handle>.js). Prices are in cents. A ?variant= or ?Color= in the link
    picks that version's price and photo."""
    raw = data.get("variants", [])
    names = _option_names(data)
    wanted = wanted_options(url)

    def options(v: dict) -> dict[str, str]:
        return {n: v.get(f"option{i + 1}") for i, n in enumerate(names) if v.get(f"option{i + 1}")}

    def image_of(v: dict) -> Optional[str]:
        fi = v.get("featured_image")
        return _abs(fi.get("src") if isinstance(fi, dict) else fi, url)

    variants = [Variant(name=v.get("title", ""), price=(v.get("price") or 0) / 100, available=v.get("available"),
                        image=image_of(v), options=options(v), id=str(v.get("id", ""))) for v in raw]
    picked = [x for x, v in zip(variants, raw) if wanted.get("variant") and str(v.get("id")) == wanted["variant"]]
    asked: dict[str, str] = {}
    if not picked:
        asked = {k: val.lower() for k, val in wanted.items() if k in names}
        picked = [x for x in variants if asked and all(x.options.get(k, "").lower() == val for k, val in asked.items())]
    pool = [v for v in (picked or variants) if v.price]
    low = min(pool, key=lambda v: v.price) if pool else None
    was = None
    for v in raw:
        if low and str(v.get("id", "")) == low.id and (v.get("compare_at_price") or 0) / 100 > low.price:
            was = v["compare_at_price"] / 100
            break
    found = parse_dims(strip_tags(data.get("description") or ""))
    image = (low.image if picked and low and low.image else None) or data.get("featured_image") or (data.get("images") or [None])[0]
    chosen = ""
    if picked and low:
        chosen = " / ".join(low.options[k] for k in asked if k in low.options) if asked else ""
        chosen = chosen or " / ".join(low.options.values()) or low.name
    name = data.get("title", "")
    return Product(
        url=url,
        store=store_name(url),
        name=f"{name} ({chosen})" if chosen and chosen.lower() not in name.lower() else name,
        image=_abs(image, url),
        price=low.price if low else None,
        was_price=was,
        variants=variants,
        dims=Dims(**found, source="stated") if found else Dims(),
        available=any(v.available for v in variants) if variants else None,
        verified=True,
        chosen=chosen,
    )


def _jsonld_product(page: str) -> Optional[dict]:
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page, re.S):
        try:
            data = json.loads(html.unescape(block.strip()))
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if isinstance(item, dict) and "Product" in str(item.get("@type")):
                return item
    return None


_MICRO_TAG = re.compile(r"<(?:meta|link|span|div|p|h1|time)\b[^>]*?\bitemprop=[\"']?(\w+)[\"']?[^>]*>", re.I)


def _microdata_product(page: str) -> Optional[dict]:
    """Product facts from itemprop microdata (rugs.com has no JSON-LD). Shaped like a JSON-LD Product."""
    found: dict[str, str] = {}
    for m in _MICRO_TAG.finditer(page):
        attr = re.search(r"\b(?:content|href)=[\"']([^\"']*)", m.group(0), re.I)
        if attr and attr.group(1) and m.group(1).lower() not in found:
            found[m.group(1).lower()] = html.unescape(attr.group(1))
    if not found.get("price") or not found.get("name"):
        return None
    return {"@type": "Product", "name": found["name"], "image": found.get("image"), "description": found.get("description"),
            "color": found.get("color"), "size": found.get("size"), "material": found.get("material"),
            "offers": {"price": found["price"], "priceCurrency": found.get("pricecurrency"),
                       "availability": found.get("availability", "")}}


_LABEL_END = r"(?=\s+[A-Z][A-Za-z.&'/-]*(?:\s[A-Za-z.&'/-]+){0,3}\s:\s|\s*$)"
_MATERIAL = re.compile(r"\b((?:(?:Seat|Frame|Base|Leg|Legs|Upholstery|Cushion|Shade|Top|Cover|Fabric|Table|Shell|Arm|Back) )?Material(?: Details)?)\s*:\s*(.{3,120}?)" + _LABEL_END)


def text_material(text: str) -> str:
    """'Material Details : Iron base with acrylic shade Bulb Base : E26' -> 'Iron base with acrylic shade'. A labeled
    part ('Seat Material') is kept as 'seat: ...'."""
    parts: list[str] = []
    for label, value in _MATERIAL.findall(text):
        prefix = "" if label.startswith("Material") else label.split()[0].lower()
        part = f"{prefix}: {value.strip()}" if prefix else value.strip()
        if part not in parts:
            parts.append(part)
    return "; ".join(parts)


def _meta(page: str, prop: str) -> Optional[str]:
    m = re.search(r'<meta[^>]+property=["\']og:' + prop + r'["\'][^>]+content=["\']([^"\']+)', page) or re.search(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:' + prop, page
    )
    return html.unescape(m.group(1)) if m else None


def _float(raw) -> Optional[float]:
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _price(raw) -> Optional[float]:
    """A price the person could pay: zero is a sample or placeholder (rugs.com sample swatches), not a price."""
    n = _float(raw)
    return n if n and n > 0 else None


def _read_offer(node: dict) -> tuple[Optional[float], Optional[float], str, Optional[bool]]:
    """(price, was_price, currency, in_stock) from a Product node's offers."""
    offers = node.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if isinstance(offers, dict) and "offers" in offers:  # AggregateOffer wrapping offers
        inner = offers["offers"]
        offers = inner[0] if isinstance(inner, list) and inner else inner
    if not isinstance(offers, dict):
        return None, None, "USD", None
    raw_price = offers.get("price") or offers.get("lowPrice")
    # Sale prices can sit in priceSpecification instead (IKEA): the current price plus a strikethrough "was" price.
    specs = offers.get("priceSpecification")
    specs = [x for x in (specs if isinstance(specs, list) else [specs]) if isinstance(x, dict)]
    was = next((x.get("price") for x in specs if "Strikethrough" in str(x.get("priceType"))), None)
    if raw_price in (None, ""):
        raw_price = next((x.get("price") for x in specs if "Strikethrough" not in str(x.get("priceType"))), None)
    available = ("InStock" in str(offers.get("availability"))) if offers.get("availability") else None
    return _price(raw_price), _float(was), offers.get("priceCurrency") or "USD", available


def _image_of(node: dict) -> Optional[str]:
    image = node.get("image")
    image = image[0] if isinstance(image, list) and image else image
    if isinstance(image, dict):
        image = image.get("contentUrl") or image.get("url")
    return image or None


def _variants_of(group: dict, url: str) -> list[Variant]:
    """A ProductGroup's hasVariant list (Ondu rugs, World Market): one Variant per color and size."""
    out = []
    for v in group.get("hasVariant") or []:
        if not isinstance(v, dict):
            continue
        price, _, _, available = _read_offer(v)
        opts = {k: str(v[k]) for k in ("color", "size") if v.get(k)}
        vid = (re.search(r"variant=([^&#]+)", str(v.get("url") or "")) or [None, ""])[1]
        out.append(Variant(name=" / ".join(opts.values()) or str(v.get("name") or ""), price=price, available=available,
                           image=_abs(_image_of(v), url), options=opts, id=vid, sku=str(v.get("sku") or "")))
    return out


def _sku_image(page: str, sku: str) -> Optional[str]:
    """Some stores list one photo for every version in their data but name the photo files after the version's SKU
    (Ondu: YKRR13ONDOR-1-ove.jpg is Guava, ...ONDCO... is Cream). The first photo on the page named for this SKU."""
    stem = re.sub(r"\d+X\d+$", "", sku, flags=re.I)
    if len(stem) < 6:
        return None
    m = re.search(r"https://[^\"'\s\\<>]*/" + re.escape(stem) + r"[^\"'\s\\<>]*\.(?:jpe?g|png|webp)[^\"'\s\\<>]*", page)
    return html.unescape(m.group(0)) if m else None


def from_html(url: str, page: str) -> Product:
    """Any product page: JSON-LD Product (or ProductGroup) first, then itemprop microdata, then Open Graph tags, then
    labeled text for dimensions and material."""
    ld = _jsonld_product(page) or _microdata_product(page) or {}
    variants = _variants_of(ld, url)
    chosen, node = "", ld
    if variants:
        wanted = wanted_options(url)
        picked = [v for v in variants if wanted.get("variant") and v.id and unquote(wanted["variant"]) in unquote(v.id)]
        asked: dict[str, str] = {}
        if not picked:
            asked = {k: val.lower() for k, val in wanted.items() if k in ("color", "colour", "size")}
            asked = {("color" if k == "colour" else k): val for k, val in asked.items()}
            picked = [v for v in variants if asked and all(v.options.get(k, "").lower() == val for k, val in asked.items())]
        priced = [v for v in (picked or variants) if v.price is not None]
        low = min(priced, key=lambda v: v.price) if priced else None
        if picked and low:  # name only what the link asked for ("Guava"), not the size that happens to be cheapest
            chosen = " / ".join(low.options[k] for k in asked if k in low.options) if asked else ""
            chosen = chosen or " / ".join(low.options.values()) or low.name
        if low:  # the cheapest of the asked-for versions (or of all), whose photo and sale price go with it
            node = next((x for x in ld["hasVariant"] if isinstance(x, dict) and _read_offer(x)[0] == low.price
                         and (not low.options or all(str(x.get(k)) == val for k, val in low.options.items()))), ld)
    price, was_price, currency, available = _read_offer(node)
    if price is None and node is not ld:
        price, was_price, currency, available = _read_offer(ld)
    image = (chosen and low and low.sku and _sku_image(page, low.sku)) or (_image_of(node) if chosen else None) or _meta(page, "image") or _image_of(ld)

    found: dict[str, float] = {}
    for key, field in (("w", "width"), ("d", "depth"), ("h", "height")):
        value = ld.get(field)
        if isinstance(value, dict):
            value = value.get("value")
        if value and (m := re.match(r"\s*" + _NUM, str(value))) and (n := to_number(m.group(1))) is not None:
            found[key] = n
    text = strip_tags(page)
    if not found:
        found = parse_dims(text)
    material = str(ld.get("material") or "") or text_material(text)

    name = (ld.get("name") or _meta(page, "title") or "").strip()
    if chosen and chosen.lower() not in name.lower():
        name = f"{name} ({chosen})"
    return Product(
        url=url,
        store=store_name(url),
        name=name,
        image=_abs(image, url),
        price=price,
        was_price=was_price if was_price and price and was_price > price else None,
        currency=currency,
        variants=variants,
        dims=Dims(**found, source="stated") if found else Dims(),
        material=material,
        available=available,
        verified=bool(ld) or price is not None or (_meta(page, "type") or "").lower() == "product",
        chosen=chosen,
    )
