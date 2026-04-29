"""
Bing Images — no API key required.

Uses Bing's internal async image endpoint:
  GET https://www.bing.com/images/async?q={q}&first=1&count=5
  → HTML containing iusc elements with JSON m= attributes
  → Each m= JSON has a "murl" field with the full-size image URL
"""

import html as html_module
import json
import logging
import re
from urllib.parse import quote

import aiohttp

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_IUSC_RE = re.compile(r'class="iusc"[^>]+m="(\{[^"]+\})"')

_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)
_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=5)


async def get_image_url(query: str) -> str | None:
    """
    Run a Bing image search and return the URL of the top result.
    Returns None on any failure.
    """
    if not query or not query.strip():
        return None
    q = query.strip()
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.bing.com/",
    }
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=headers) as session:
            url = f"https://www.bing.com/images/async?q={quote(q)}&first=1&count=5&adlt=off&qft="
            async with session.get(url) as resp:
                if resp.status != 200:
                    log.warning("Bing Images HTTP %d for query=%r", resp.status, q)
                    return None
                text = await resp.text()

        matches = _IUSC_RE.findall(text)
        if not matches:
            log.info("Bing Images: 0 results for query=%r", q)
            return None

        for raw in matches:
            try:
                d = json.loads(html_module.unescape(raw))
                murl = d.get("murl")
                if murl and murl.startswith("http"):
                    return murl
            except (json.JSONDecodeError, ValueError):
                continue

        return None
    except Exception as e:
        log.warning("Bing image search failed for query=%r: %s", query, e)
        return None


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
