"""
DuckDuckGo Images — no API key required.

Two-step undocumented endpoint that DDG's own UI uses:
  1. GET https://duckduckgo.com/?q={q}&iax=images&ia=images
       → response HTML contains a `vqd=...` token
  2. GET https://duckduckgo.com/i.js?o=json&q={q}&vqd={token}&...
       → JSON: { "results": [ { "image": "...", "thumbnail": "..." }, ... ] }

We use it as a free, key-less Google-Images-style step before sending the
chosen photo into Lovegobuy's image search.
"""

import logging
import re
from urllib.parse import quote

import aiohttp

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_VQD_RE = re.compile(r'vqd=["\']?(\d-[\d-]+)["\']?')

_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)
_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=5)


async def get_image_url(query: str) -> str | None:
    """
    Run a DuckDuckGo image search and return the URL of the top result.
    Returns None on any failure (network, parse, no results, etc.).
    """
    if not query or not query.strip():
        return None
    q = query.strip()
    headers = {"User-Agent": _UA}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=headers) as session:
            # Step 1 — fetch vqd token from the search page HTML
            search_url = f"https://duckduckgo.com/?q={quote(q)}&iax=images&ia=images"
            async with session.get(search_url) as resp:
                html = await resp.text()
            match = _VQD_RE.search(html)
            if not match:
                log.warning("DuckDuckGo: vqd token not found in HTML for query=%r", q)
                return None
            vqd = match.group(1)

            # Step 2 — fetch image results JSON
            api_url = (
                "https://duckduckgo.com/i.js"
                f"?l=us-en&o=json&q={quote(q)}&vqd={vqd}&f=,,,,,&p=1"
            )
            async with session.get(api_url, headers={"Referer": search_url}) as resp:
                data = await resp.json(content_type=None)
            results = data.get("results") or []
            if not results:
                log.info("DuckDuckGo: 0 image results for query=%r", q)
                return None
            return results[0].get("image")
    except Exception as e:
        log.warning("DuckDuckGo image search failed for query=%r: %s", query, e)
        return None


async def download(url: str, max_bytes: int = 8_000_000) -> bytes | None:
    """
    Download an image URL and return the raw bytes, or None on failure.
    Hard-caps the response size to avoid memory bombs.
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
                # Read up to max_bytes + 1 to detect oversize
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
