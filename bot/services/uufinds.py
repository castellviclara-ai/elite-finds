"""
UUFindsClient — real-time product search via uufinds.com API.

Authentication flow:
  1. GET /user/captcha/image/{ts}  → base64 JPEG
  2. OCR with ddddocr (~95-100% success on 4-char alphanumeric captcha)
  3. POST /user/login              → JWT (xaccessToken), valid ~7 days
  4. All search requests include X-Access-Token + sm5 headers (au + ms)

sm5 headers:
  au = UUID v1 hex (no dashes)
  ms = base64(MD5(au + SALT)).rstrip("=")
"""

import asyncio
import base64
import hashlib
import logging
import os
import time
import unicodedata
import uuid
from dataclasses import dataclass, field

import aiohttp
import ddddocr

log = logging.getLogger(__name__)

_BASE = "https://api.uufinds.com"
_ORIGIN = "https://www.uufinds.com"
_SALT = "8c69d69dcb7e47b6914b075ef076f3c4"

_BASE_HEADERS = {
    "Origin": _ORIGIN,
    "Referer": _ORIGIN + "/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Client-Type": "web",
}


def _sm5_headers() -> dict[str, str]:
    au = uuid.uuid1().hex
    ms_raw = hashlib.md5((au + _SALT).encode()).digest()
    ms = base64.b64encode(ms_raw).decode().replace("=", "")
    return {"au": au, "ms": ms}


@dataclass
class Product:
    id: str                      # uufinds internal ID → QC URL
    subject: str                 # product name (English/Chinese mix)
    marketplace: str             # "weidian" | "1688"
    product_id: str              # spuNo → used for agent links
    source_link: str             # direct URL on Weidian/1688
    price_usd: float
    qc_image_count: int
    qc_images: list[str] = field(default_factory=list)
    tier: str = "mid"            # assigned after bucketing

    @property
    def uufinds_qc_url(self) -> str:
        return f"https://www.uufinds.com/goodItemDetail/qc/{self.id}"


class UUFindsClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._lock = asyncio.Lock()
        self._ocr = ddddocr.DdddOcr(show_ad=False)
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=_BASE_HEADERS)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _do_login(self) -> str:
        session = self._get_session()
        username = os.environ["UUFINDS_USERNAME"]
        password = os.environ["UUFINDS_PASSWORD"]

        for attempt in range(3):
            ts = int(time.time() * 1000)
            # 1. Fetch captcha
            async with session.get(f"{_BASE}/user/captcha/image/{ts}") as resp:
                data = await resp.json(content_type=None)
            b64 = data.get("result", "")
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            img_bytes = base64.b64decode(b64)

            # 2. OCR
            captcha = self._ocr.classification(img_bytes)
            log.debug("Captcha attempt %d: %s", attempt + 1, captcha)

            # 3. Login
            payload = {
                "username": username,
                "password": password,
                "captcha": captcha,
                "checkKey": ts,
            }
            async with session.post(f"{_BASE}/user/login", json=payload) as resp:
                result = await resp.json(content_type=None)

            if result.get("success") and result.get("result", {}).get("xaccessToken"):
                token = result["result"]["xaccessToken"]
                log.info("uufinds login OK (attempt %d)", attempt + 1)
                return token

            log.warning("Login attempt %d failed: %s", attempt + 1, result)

        raise RuntimeError("uufinds login failed after 3 attempts")

    async def ensure_token(self) -> str:
        async with self._lock:
            if self._token is None:
                self._token = await self._do_login()
            return self._token

    async def invalidate_token(self) -> None:
        async with self._lock:
            self._token = None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        keyword: str,
        page_size: int = 24,
        rank_type: str = "NEW",
    ) -> list[Product]:
        token = await self.ensure_token()
        session = self._get_session()

        params = {
            "pageNo": 1,
            "pageSize": page_size,
            "keyword": keyword,
            "rankType": rank_type,
            "pageType": "qcFinds",
            "source": "web",
        }
        headers = {
            **_sm5_headers(),
            "X-Access-Token": token,
        }

        async with session.get(f"{_BASE}/goods", params=params, headers=headers) as resp:
            if resp.status == 401:
                raise RuntimeError("expired")
            data = await resp.json(content_type=None)

        if not data.get("success"):
            log.warning("uufinds /goods returned success=false: %s", data)
            return []

        result = data.get("result") or {}
        # API returns either a list directly or {"records": [...], ...}
        if isinstance(result, list):
            items = result
        elif isinstance(result, dict):
            items = result.get("records") or []
        else:
            return []

        products = []
        for item in items:
            try:
                p = Product(
                    id=str(item["id"]),
                    subject=item.get("subject", ""),
                    marketplace=item.get("channel", "weidian").lower(),
                    product_id=str(item.get("spuNo", "")),
                    source_link=item.get("sourceLink", ""),
                    price_usd=float(item.get("price", 0)),
                    qc_image_count=int(item.get("qcImgQuantity", 0)),
                    qc_images=item.get("qcImageList") or [],
                )
                products.append(p)
            except (KeyError, ValueError, TypeError) as e:
                log.debug("Skipping malformed product: %s", e)

        return products

    async def search_with_retry(self, keyword: str, page_size: int = 24) -> list[Product]:
        """
        Try NEW first; fall back to RECOMMEND if 0 results.
        Auto-renew token on 401.
        """
        for rank_type in ("NEW", "RECOMMEND"):
            try:
                results = await self.search(keyword, page_size=page_size, rank_type=rank_type)
            except RuntimeError as e:
                if "expired" in str(e):
                    log.info("Token expired, renewing...")
                    await self.invalidate_token()
                    results = await self.search(keyword, page_size=page_size, rank_type=rank_type)
                else:
                    raise

            if results:
                return results
            log.debug("rankType=%s returned 0 results, trying fallback", rank_type)

        return []


# ---------------------------------------------------------------------------
# Post-filtering and bucketing
# ---------------------------------------------------------------------------

def _is_cjk(char: str) -> bool:
    try:
        return unicodedata.name(char).startswith(("CJK", "HIRAGANA", "KATAKANA", "HANGUL"))
    except ValueError:
        return False


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(1 for c in text if _is_cjk(c))
    return cjk / len(text)


def filter_results(products: list[Product], query: str) -> list[Product]:
    """
    Post-filter to remove irrelevant / unverified products.

    Rules:
    1. Remove products with qc_image_count == 0
    2. If >40% CJK chars in subject, keep only if qc_image_count >= 50
    3. Token matching — two modes:
       a. Single token: must appear in subject
       b. Multi-token: ALL tokens must appear in subject.
          Exception: generic clothing words (shorts, pants, shirt, etc.)
          are optional — but brand/model tokens are required.
    4. If fewer than 3 pass, relax to "at least half tokens match"
    5. If still fewer than 3, return original list unfiltered
    """
    # Words that are too generic to be required on their own
    _GENERIC = {
        "shorts", "pants", "shirt", "tee", "hoodie", "jacket", "coat",
        "shoes", "sneakers", "boots", "sandals", "bag", "hat", "cap",
        "socks", "belt", "wallet", "watch", "glasses", "sunglasses",
        "dress", "skirt", "jeans", "joggers", "sweatpants", "tracksuit",
        "low", "high", "mid", "men", "women", "unisex", "kids",
        "white", "black", "red", "blue", "green", "grey", "gray",
    }

    all_tokens = [t.lower() for t in query.split() if len(t) > 1]
    # Required = non-generic tokens (brand/model words)
    required = [t for t in all_tokens if t not in _GENERIC]
    # If everything is generic, require all tokens
    if not required:
        required = all_tokens

    def _matches_strict(subject_lower: str) -> bool:
        return all(t in subject_lower for t in required)

    def _matches_relaxed(subject_lower: str) -> bool:
        if not all_tokens:
            return True
        matches = sum(1 for t in all_tokens if t in subject_lower)
        return matches >= len(all_tokens) / 2

    filtered = []
    for p in products:
        if p.qc_image_count == 0:
            continue
        if _cjk_ratio(p.subject) > 0.4 and p.qc_image_count < 50:
            continue
        if required and _matches_strict(p.subject.lower()):
            filtered.append(p)

    if len(filtered) >= 3:
        return filtered

    # Relax to half-token match
    filtered_relaxed = []
    for p in products:
        if p.qc_image_count == 0:
            continue
        if _cjk_ratio(p.subject) > 0.4 and p.qc_image_count < 50:
            continue
        if _matches_relaxed(p.subject.lower()):
            filtered_relaxed.append(p)

    return filtered_relaxed if len(filtered_relaxed) >= 3 else products


def bucket_products(products: list[Product]) -> list[Product]:
    """
    Assign tier (premium / mid / cheap) by price percentiles (33/66).
    Sort within each tier by qc_image_count descending.

    RULE: tier = relative price within this result-set, never by keyword.
    """
    if not products:
        return products

    prices = sorted(p.price_usd for p in products)
    n = len(prices)

    # Always split into 3 equal index-based thirds regardless of n
    lo_idx = max(1, n // 3)
    hi_idx = max(2, (2 * n) // 3)

    lo = prices[lo_idx]
    hi = prices[hi_idx]

    # If thresholds are too close, spread them using min/max
    if hi - lo < 2.0:
        lo = prices[0] + (prices[-1] - prices[0]) / 3
        hi = prices[0] + 2 * (prices[-1] - prices[0]) / 3

    for p in products:
        if p.price_usd >= hi:
            p.tier = "premium"
        elif p.price_usd >= lo:
            p.tier = "mid"
        else:
            p.tier = "cheap"

    # Sort within tiers:
    #   premium → highest price first
    #   mid     → most QC photos first
    #   cheap   → lowest price first
    tier_order = {"premium": 0, "mid": 1, "cheap": 2}
    products.sort(key=lambda p: (
        tier_order[p.tier],
        -p.price_usd if p.tier == "premium" else (
            -p.qc_image_count if p.tier == "mid" else p.price_usd
        ),
    ))

    return products
