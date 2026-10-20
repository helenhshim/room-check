"""Fetch product pages safely: public https only, robots.txt respected, blocks never bypassed, results cached."""
import base64
import io
import ipaddress
import json
import socket
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import httpx2

from . import extract, state
from .types import Product

USER_AGENT = "InteriorAgent/0.1 (personal project)"
MAX_BYTES = 3_000_000
TIMEOUT = httpx2.Timeout(12.0, connect=5.0)
MAX_REDIRECTS = 3
SKIP_HOSTS = ("amazon.", "amzn.")  # pages can't be read reliably; skipped by policy
# Social, inspiration and editorial hosts: never a store's product page, so they are not fetched.
NOT_PRODUCT_HOSTS = ("pinterest.", "instagram.", "reddit.", "youtube.", "tiktok.", "facebook.", "houzz.", "wikipedia.",
                     "mapquest.", "yelp.", "tripadvisor.", "hgtv.", "nytimes.", "wirecutter.", "architecturaldigest.")
PIN_NEXT_STEP = "This is a pin, not a product page: share the product link it points to, or describe it and I'll find similar."
NOT_PRODUCT_NEXT_STEP = "This isn't a product page: share the link to the product itself, or describe it and I'll find similar."
BOT_CHECK_WORDS = ("captcha", "verify you are human", "are you a robot", "access denied", "challenge-platform")


class Blocked(Exception):
    """The page refused automated access, or policy says not to fetch it."""


class Unsafe(Exception):
    """The URL points somewhere the agent must never fetch (not public https)."""


# ---------- guardrails ----------

def check_url(url: str) -> None:
    """Allow only public https URLs on the default port. Resolves DNS so private addresses can't sneak in."""
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname:
        raise Unsafe("only https URLs are allowed")
    if p.port not in (None, 443):
        raise Unsafe("non-standard ports are not allowed")
    if p.username or p.password:
        raise Unsafe("credentials in URLs are not allowed")
    try:
        infos = socket.getaddrinfo(p.hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise Unsafe("host does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise Unsafe("private or internal address")


# ---------- cache (in memory locally; Redis on Vercel, same interface) ----------

class MemoryCache:
    def __init__(self):
        self._data: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            item = self._data.get(key)
            if not item or item[0] < time.time():
                return None
            return item[1]

    def set(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl, value)


_redis = state.shared_client()
cache = state.RedisCache(_redis) if _redis else MemoryCache()
PRODUCT_TTL = 24 * 3600
PRODUCT_KEY = "product:v3:"  # bump when extraction changes, so cached facts from the old extractor aren't reused

_host_locks: dict[str, threading.Lock] = {}
_host_locks_guard = threading.Lock()
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


def _host_lock(host: str) -> threading.Lock:
    with _host_locks_guard:
        return _host_locks.setdefault(host, threading.Lock())


# ---------- raw fetch ----------

def _get_bytes(url: str, max_bytes: int = MAX_BYTES) -> tuple[int, str, bytes, str]:
    """GET with guardrails re-checked on every redirect hop and a size cap.
    Returns (status, final_url, body, encoding)."""
    with httpx2.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url)
            with client.stream("GET", url) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        break
                    chunks.append(chunk)
                return r.status_code, url, b"".join(chunks), r.encoding or "utf-8"
    raise Blocked("too many redirects")


def _get(url: str) -> tuple[int, str, str]:
    status, final_url, body, encoding = _get_bytes(url)
    return status, final_url, body.decode(encoding, "ignore")


def _allowed_by_robots(url: str) -> bool:
    p = urlparse(url)
    host = f"{p.scheme}://{p.netloc}"
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            status, _, text = _get(host + "/robots.txt")
            rp.parse(text.splitlines() if status == 200 else [])
        except (Unsafe, Blocked, httpx2.HTTPError):
            rp.parse([])
        _robots[host] = rp
    return _robots[host].can_fetch("*", url)


def _looks_blocked(status: int, text: str) -> Optional[str]:
    if status in (401, 403, 429):
        return f"the store refused automated access ({status})"
    if status >= 400:
        return f"the page returned an error ({status})"
    head = text[:30000].lower()
    if any(w in head for w in BOT_CHECK_WORDS) and "application/ld+json" not in head and 'og:title' not in head:
        return "the store showed a bot check"
    return None


# ---------- product ----------

def get_product(url: str) -> Product:
    """Verified product facts for one URL. Never raises for store problems: returns a Product with blocked_reason set."""
    url = url.split("#")[0]
    cached = cache.get(PRODUCT_KEY + url)
    if cached:
        return Product.model_validate_json(cached)
    host = urlparse(url).netloc.lower()
    if any(h in host for h in NOT_PRODUCT_HOSTS):  # not a store: nothing to fetch, and the next step differs
        pin = "pinterest." in host or "pin.it" in host
        return Product(url=url, blocked_reason="this is a pin, not a product page" if pin else "this isn't a product page",
                       next_step=PIN_NEXT_STEP if pin else NOT_PRODUCT_NEXT_STEP)
    try:
        check_url(url)
        if any(s in host for s in SKIP_HOSTS):
            raise Blocked("this store isn't supported (its pages can't be read reliably)")
        with _host_lock(host):  # one request at a time per store
            if not _allowed_by_robots(url):
                raise Blocked("the store's robots.txt doesn't allow it")
            product = None
            path = urlparse(url).path
            if "/products/" in path:  # Shopify stores: public product endpoint
                js_url = f"https://{urlparse(url).netloc}{path.rstrip('/')}.js"
                if _allowed_by_robots(js_url):
                    status, js_final, text = _get(js_url)
                    if status == 200:
                        try:
                            product = extract.from_shopify_js(url, json.loads(text))
                            product.final_url = js_final.removesuffix(".js")
                        except (json.JSONDecodeError, KeyError, TypeError):
                            product = None
            if product is None:
                status, final_url, text = _get(url)
                reason = _looks_blocked(status, text)
                if reason:
                    raise Blocked(reason)
                product = extract.from_html(url, text)
                product.final_url = final_url
                if not product.verified:
                    raise Blocked("no product information found on the page")
    except Unsafe as e:
        return Product(url=url, store=extract.store_name(url), blocked_reason=f"not fetched: {e}")
    except Blocked as e:
        product = Product(url=url, store=extract.store_name(url), blocked_reason=str(e))
    except httpx2.HTTPError:
        product = Product(url=url, store=extract.store_name(url), blocked_reason="the page didn't load")
    cache.set(PRODUCT_KEY + url, product.model_dump_json(), PRODUCT_TTL)
    return product


def get_products(urls: list[str], workers: int = 6) -> list[Product]:
    """Fetch several products in parallel (still one request at a time per store). Keeps input order."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(get_product, urls))


# ---------- images for judgment ----------

def image_b64(url: str, max_px: int = 400) -> Optional[dict]:
    """Download a product image and downscale it, returning an Anthropic base64 image block (or None)."""
    from PIL import Image  # imported lazily so the server starts fast

    try:
        status, _, body, _ = _get_bytes(url, max_bytes=8_000_000)
        if status != 200:
            return None
        img = Image.open(io.BytesIO(body)).convert("RGB")
        img.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.standard_b64encode(buf.getvalue()).decode()}}
    except Exception:  # an image is optional; judgment proceeds without it
        return None


def images_b64(urls: list[Optional[str]], workers: int = 6) -> list[Optional[dict]]:
    fn: Callable[[Optional[str]], Optional[dict]] = lambda u: image_b64(u) if u else None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, urls))
