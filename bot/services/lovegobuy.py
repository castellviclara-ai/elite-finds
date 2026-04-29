"""
Lovegobuy public API client — image search for 1688 and Weidian.

Discovered via reverse-engineering the lovegobuy.com SPA bundle.
No authentication required (CORS open, no Access-Token enforced).

Endpoints (POST, multipart/form-data, field name = "file"):
  https://www.lovegobuy.com/index.php?s=/api/goods/searchImage1688
  https://www.lovegobuy.com/index.php?s=/api/goods/serachImgWeidian
        (yes, "serach" — typo in their backend)

Response shape:
  {
    "status": 200,
    "message": "success",
    "data": {
      "list": {
        "data": [
          {
            "goods_id": 871672793755,
            "goods_name": "Gift Box Socks ...",     # English-translated
            "goods_name_cn": "礼盒装袜子男女长筒...",  # Original Chinese
            "goods_image": "https://cbu01.alicdn.com/...",
            "goods_price": "9.99",                    # CNY
            "goods_price_min_": "1.57",               # USD numeric
            "goods_price_min_show": "$1.57",          # USD pretty
            "source": "1688_open" | "weidian_open"
          },
          ...
        ]
      }
    }
  }
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

_BASE = "https://www.lovegobuy.com/index.php?s=/api"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "currency": "USD",
    "lang": "en",
    "Platform": "H5",
    "Device-Type": "1",
    "Origin": "https://www.lovegobuy.com",
    "Referer": "https://www.lovegobuy.com/",
}

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=10)


@dataclass
class Product:
    """One product result from Lovegobuy image search."""

    marketplace: str          # "1688" or "weidian"
    product_id: str           # used to build affiliate links via agents.py
    title: str                # English-translated name
    title_cn: str             # original Chinese name
    image: str                # CDN image URL
    price_usd: float          # numeric USD
    price_cny: float          # numeric CNY (original)
    price_display: str        # "$1.57"
    tier: str = "mid"         # set by bucket_by_price()

    @property
    def source_link(self) -> str:
        if self.marketplace == "1688":
            return f"https://detail.1688.com/offer/{self.product_id}.html"
        return f"https://weidian.com/item.html?itemID={self.product_id}"


async def _post_image(
    session: aiohttp.ClientSession,
    url: str,
    image_bytes: bytes,
    filename: str = "image.jpg",
) -> Optional[dict]:
    form = aiohttp.FormData()
    form.add_field(
        "file",
        image_bytes,
        filename=filename,
        content_type="image/jpeg",
    )
    try:
        async with session.post(url, data=form, headers=_HEADERS, timeout=_REQUEST_TIMEOUT) as resp:
            return await resp.json(content_type=None)
    except Exception as e:
        log.warning("Lovegobuy POST %s failed: %s", url, e)
        return None


def _parse(data: Optional[dict], marketplace: str) -> list[Product]:
    if not data or data.get("status") != 200:
        return []
    items = ((data.get("data") or {}).get("list") or {}).get("data") or []
    out: list[Product] = []
    for it in items:
        try:
            product_id = str(it.get("goods_id") or "").strip()
            if not product_id:
                continue
            price_usd = float(it.get("goods_price_min_") or 0)
            price_cny = float(it.get("goods_price") or 0)
            out.append(
                Product(
                    marketplace=marketplace,
                    product_id=product_id,
                    title=(it.get("goods_name") or "").strip(),
                    title_cn=(it.get("goods_name_cn") or "").strip(),
                    image=(it.get("goods_image") or "").strip(),
                    price_usd=price_usd,
                    price_cny=price_cny,
                    price_display=(it.get("goods_price_min_show") or f"${price_usd:.2f}"),
                )
            )
        except (KeyError, ValueError, TypeError) as e:
            log.debug("Skipping malformed lovegobuy item: %s", e)
    return out


async def search_1688(image_bytes: bytes, filename: str = "image.jpg") -> list[Product]:
    """Search 1688 by image. Returns up to 50 products, [] on failure."""
    async with aiohttp.ClientSession() as session:
        data = await _post_image(
            session,
            f"{_BASE}/goods/searchImage1688",
            image_bytes,
            filename=filename,
        )
    return _parse(data, "1688")


async def search_weidian(image_bytes: bytes, filename: str = "image.jpg") -> list[Product]:
    """Search Weidian by image. Returns up to 50 products, [] on failure."""
    async with aiohttp.ClientSession() as session:
        data = await _post_image(
            session,
            f"{_BASE}/goods/serachImgWeidian",  # typo intentional — that's their endpoint
            image_bytes,
            filename=filename,
        )
    return _parse(data, "weidian")
