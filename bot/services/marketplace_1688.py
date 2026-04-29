"""Prototype 1688 search integration.

This service is intentionally minimal: it wraps the `search1688api`
package and exposes an async search method. The search uses optional
proxy support through the MARKETPLACE_PROXY environment variable.
"""

import asyncio
import logging
import os
from typing import Any

from search1688api import Sync1688Session

log = logging.getLogger(__name__)

PROXY_ENV = "MARKETPLACE_PROXY"
DEBUG_ENV = "MARKETPLACE_DEBUG"


def _build_proxies() -> dict[str, str] | None:
    proxy_url = os.environ.get(PROXY_ENV)
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def _debug_enabled() -> bool:
    return os.environ.get(DEBUG_ENV, "0") in {"1", "true", "True", "yes", "YES"}


class Search1688Client:
    def __init__(self) -> None:
        self.proxies = _build_proxies()
        self.debug = _debug_enabled()

    def _build_session(self) -> Sync1688Session:
        session = Sync1688Session(debug=self.debug)
        if self.proxies:
            session.proxies.update(self.proxies)
        return session

    def _search_sync(self, query: str) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []

        try:
            with self._build_session() as session:
                results = session.search_by_text(query)
                return results or []
        except Exception as exc:
            log.warning("1688 search failed for query '%s': %s", query, exc)
            return []

    async def search(self, query: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._search_sync, query)
