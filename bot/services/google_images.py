"""
Bing Images — no API key required.

Uses Bing's internal async image endpoint:
  GET https://www.bing.com/images/async?q={q}&first=1&count=N
  → HTML containing iusc elements with JSON m= attributes
  → Each m= JSON has a "murl" field with the full-size image URL

We deliberately filter out images hosted on official brand domains
(nike.com, adidas.com, etc.) and on retail aggregators (stockx, goat,
amazon, etc.). Reverse-image search on 1688/Weidian works much better
with marketplace-style photos than with clean studio shots.
"""

import html as html_module
import json
import logging
import re
from urllib.parse import quote, urlparse

import aiohttp

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_IUSC_RE = re.compile(r'class="iusc"[^>]+m="(\{[^"]+\})"')

_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)
_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=5)

# Domains we want to AVOID for reference images.
# - Official brand sites: clean PNGs that don't match marketplace inventory.
# - Big western retailers: same problem (curated studio photos).
# - Reference / wiki sites: rarely product photos.
_BAD_DOMAINS = {
    # Sneaker / sportswear brands
    "nike.com", "static.nike.com",
    "adidas.com", "adidas.co.uk", "adidas.de",
    "puma.com",
    "newbalance.com", "newbalance.co.uk",
    "jordan.com",
    "converse.com",
    "vans.com",
    "reebok.com",
    "asics.com",
    "hoka.com", "hokaoneone.com",
    "salomon.com",
    "underarmour.com",
    # Streetwear / luxury
    "supremenewyork.com",
    "louisvuitton.com",
    "gucci.com",
    "dior.com",
    "prada.com",
    "balenciaga.com",
    "fendi.com",
    "off---white.com", "off-white.com",
    "stoneisland.com",
    "moncler.com",
    "burberry.com",
    "hermes.com",
    "chanel.com",
    "versace.com",
    # Jewelry / watch / beauty brands
    "pandora.net", "us.pandora.net",
    "cartier.com",
    "tiffany.com",
    "swarovski.com",
    "bulgari.com", "bvlgari.com",
    "rolex.com",
    "omegawatches.com",
    "audemarspiguet.com",
    "patek.com",
    "richardmille.com",
    "hublot.com",
    "tagheuer.com",
    "panerai.com",
    "iwc.com",
    "sephora.com", "sephora.es",
    # Western retailers / aggregators
    "amazon.com", "amazon.co.uk", "amazon.de", "amazon.es",
    "amazon.fr", "amazon.it",
    "walmart.com", "walmartimages.com", "i5.walmartimages.com",
    "target.com",
    "ebay.com", "ebay.co.uk", "ebayimg.com",
    "etsy.com",
    "stockx.com", "images.stockx.com",
    "goat.com", "image.goat.com",
    "farfetch.com",
    "ssense.com",
    "endclothing.com",
    "footlocker.com", "footlocker.eu",
    "finishline.com",
    "champssports.com",
    "jdsports.com", "jdsports.es",
    "size.co.uk",
    "snipes.com", "snipes.es",
    "kithnyc.com", "kith.com",
    "asos.com",
    "zalando.com", "zalando.es",
    "courir.com",
    "elcorteingles.es",
    "macys.com",
    "nordstrom.com",
    "bloomingdales.com",
    "neimanmarcus.com",
    "saksfifthavenue.com",
    "shopbop.com",
    "revolve.com",
    # Resale / user-photo platforms (random user photos, not in marketplaces)
    "grailed.com", "media-assets.grailed.com",
    "depop.com", "media-photos.depop.com", "media.depop.com",
    "vestiairecollective.com",
    "thrillhouse.com",
    "mercari.com",
    "vinted.com",
    "poshmark.com",
    # Sneaker / hype editorial (clean studio + lifestyle shots — bad for reverse)
    "highsnobiety.com",
    "hypebeast.com", "hypebae.com",
    "sneakernews.com",
    "kicksonfire.com",
    "sneakerfreaker.com",
    "complex.com",
    "crepslocker.com",
    "kickz.com",
    "flightclub.com",
    # Reference
    "wikipedia.org", "wikimedia.org",
    "pinimg.com",  # Pinterest CDN — often watermarked or aggregated
    "clothbase.com", "cdn.clothbase.com",
}


def _is_bad_host(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
    except Exception:
        return False
    if not host:
        return False
    for d in _BAD_DOMAINS:
        if host == d or host.endswith("." + d):
            return True
    return False


async def _fetch_bing_html(query: str, count: int) -> str | None:
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.bing.com/",
    }
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=headers) as session:
            url = (
                f"https://www.bing.com/images/async?q={quote(query)}"
                f"&first=1&count={count}&adlt=off&qft="
            )
            async with session.get(url) as resp:
                if resp.status != 200:
                    log.warning("Bing Images HTTP %d for query=%r", resp.status, query)
                    return None
                return await resp.text()
    except Exception as e:
        log.warning("Bing image search failed for query=%r: %s", query, e)
        return None


def _parse_candidates(html_text: str) -> list[str]:
    out: list[str] = []
    for raw in _IUSC_RE.findall(html_text):
        try:
            d = json.loads(html_module.unescape(raw))
            murl = d.get("murl")
            if murl and murl.startswith("http"):
                out.append(murl)
        except (json.JSONDecodeError, ValueError):
            continue
    return out


async def get_image_candidates(query: str, max_results: int = 6) -> list[str]:
    """
    Return up to ``max_results`` candidate image URLs for the query, with
    official brand sites and big retailers filtered out. Falls back to the
    unfiltered list if filtering would leave nothing.
    """
    if not query or not query.strip():
        return []
    # Ask Bing for a healthy pool so we still have candidates after filtering.
    pool = max(max_results * 4, 20)
    html_text = await _fetch_bing_html(query.strip(), pool)
    if not html_text:
        return []
    candidates = _parse_candidates(html_text)
    if not candidates:
        log.info("Bing Images: 0 candidates for query=%r", query)
        return []
    filtered = [u for u in candidates if not _is_bad_host(u)]
    chosen = filtered if filtered else candidates
    return chosen[:max_results]


async def get_image_url(query: str) -> str | None:
    """Backwards-compatible single-result helper."""
    candidates = await get_image_candidates(query, max_results=1)
    return candidates[0] if candidates else None


async def download(url: str, max_bytes: int = 8_000_000) -> bytes | None:
    """
    Download an image URL and return raw bytes, or None on failure.
    """
    if not url:
        return None
    headers = {
        "User-Agent": _UA,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    try:
        async with aiohttp.ClientSession(timeout=_DOWNLOAD_TIMEOUT, headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    log.info("Image download HTTP %d for %s", resp.status, url)
                    return None
                buf = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        log.info("Image download exceeds %d bytes — discarded: %s", max_bytes, url)
                        return None
                return bytes(buf)
    except Exception as e:
        log.warning("Image download failed for %s: %s", url, e)
        return None
