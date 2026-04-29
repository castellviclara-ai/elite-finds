"""
Orchestrator for the new image-based search pipeline.

Pipeline:
  1. If user attached an image → use those bytes directly.
  2. Else: query → DuckDuckGo Images → top result URL → download bytes.
  3. Send bytes to Lovegobuy 1688 + Weidian endpoints in PARALLEL
     (asyncio.gather, return_exceptions=True so one failure doesn't kill the other).
  4. Merge, dedupe, bucket by USD price percentiles (33/66).
"""

import asyncio
import logging

from bot.services.google_images import get_image_url, download
from bot.services.lovegobuy import Product, search_1688, search_weidian

log = logging.getLogger(__name__)


async def fetch_reference_image(query: str) -> tuple[bytes | None, str | None]:
    """
    Resolve a text query to (image_bytes, source_url) using DuckDuckGo Images.
    Returns (None, None) if anything fails.
    """
    photo_url = await get_image_url(query)
    if not photo_url:
        return None, None
    image_bytes = await download(photo_url)
    if not image_bytes:
        return None, photo_url
    return image_bytes, photo_url


async def search_by_image(image_bytes: bytes) -> list[Product]:
    """
    Run 1688 + Weidian image search in parallel.
    Failures in one source do not affect the other.
    """
    results = await asyncio.gather(
        search_1688(image_bytes),
        search_weidian(image_bytes),
        return_exceptions=True,
    )
    products: list[Product] = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("Image-search source raised: %s", r)
            continue
        products.extend(r)
    return _dedupe(products)


def _dedupe(products: list[Product]) -> list[Product]:
    seen: set[tuple[str, str]] = set()
    out: list[Product] = []
    for p in products:
        key = (p.marketplace, p.product_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def bucket_by_price(products: list[Product]) -> list[Product]:
    """
    Assign tier (premium / mid / cheap) by USD price percentiles.
    Mutates p.tier in place. Sorts: premium (high→low), mid, cheap (low→high).
    """
    priced = [p for p in products if p.price_usd > 0]
    if not priced:
        for p in products:
            p.tier = "mid"
        return products

    if len(priced) < 3:
        for p in products:
            p.tier = "mid"
    else:
        prices = sorted(p.price_usd for p in priced)
        n = len(prices)
        lo = prices[max(1, n // 3)]
        hi = prices[max(2, (2 * n) // 3)]
        # If thresholds collapse, spread across full range
        if hi - lo < 1.0:
            lo = prices[0] + (prices[-1] - prices[0]) / 3
            hi = prices[0] + 2 * (prices[-1] - prices[0]) / 3
        for p in products:
            if p.price_usd >= hi:
                p.tier = "premium"
            elif p.price_usd >= lo:
                p.tier = "mid"
            else:
                p.tier = "cheap"

    tier_order = {"premium": 0, "mid": 1, "cheap": 2}
    products.sort(
        key=lambda p: (
            tier_order.get(p.tier, 1),
            -p.price_usd if p.tier == "premium" else p.price_usd,
        )
    )
    return products
