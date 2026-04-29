"""
Supabase wrapper — users, tickets, query cache.
All methods are async-friendly (supabase-py uses httpx internally).
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta

from supabase import create_client, Client

log = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = create_client(url, key)
    return _client


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def get_user(discord_id: str) -> dict | None:
    try:
        res = get_client().table("users").select("*").eq("discord_id", discord_id).single().execute()
        return res.data
    except Exception:
        return None


async def upsert_user(discord_id: str, username: str) -> dict:
    """Create user if not exists, return the row."""
    existing = await get_user(discord_id)
    if existing:
        return existing
    data = {
        "discord_id": discord_id,
        "username": username,
        "cashback_balance": 0.0,
        "total_earned": 0.0,
        "xp": 0,
        "level": 1,
        "streak_days": 0,
        "last_active": datetime.now(timezone.utc).isoformat(),
    }
    res = get_client().table("users").insert(data).execute()
    return res.data[0]


async def set_preferred_agent(discord_id: str, username: str, agent: str) -> None:
    await upsert_user(discord_id, username)
    get_client().table("users").update({"preferred_agent": agent}).eq("discord_id", discord_id).execute()


async def get_preferred_agent(discord_id: str) -> str | None:
    user = await get_user(discord_id)
    if user:
        return user.get("preferred_agent")
    return None


# ---------------------------------------------------------------------------
# Query cache
# ---------------------------------------------------------------------------

CACHE_TTL_HOURS = 6


async def get_cached_results(query_normalized: str) -> list | None:
    try:
        res = (
            get_client()
            .table("query_cache")
            .select("results, expires_at")
            .eq("query_normalized", query_normalized)
            .single()
            .execute()
        )
        row = res.data
        if not row:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires_at:
            return None
        # Increment hit counter (fire-and-forget)
        try:
            get_client().table("query_cache").update({"hits": row.get("hits", 0) + 1}).eq("query_normalized", query_normalized).execute()
        except Exception:
            pass
        return row["results"]
    except Exception:
        return None


async def set_cached_results(query_normalized: str, results: list) -> None:
    try:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS)).isoformat()
        data = {
            "query_normalized": query_normalized,
            "results": results,
            "hits": 1,
            "expires_at": expires_at,
        }
        get_client().table("query_cache").upsert(data, on_conflict="query_normalized").execute()
    except Exception as e:
        log.warning("Cache write failed: %s", e)


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

async def create_ticket(discord_id: str, query: str, channel_id: str) -> dict:
    data = {
        "discord_id": discord_id,
        "query": query,
        "channel_id": channel_id,
        "status": "open",
    }
    res = get_client().table("tickets").insert(data).execute()
    return res.data[0]


async def close_ticket(ticket_id: int, agent_chosen: str) -> None:
    get_client().table("tickets").update({
        "status": "closed",
        "agent_chosen": agent_chosen,
        "closed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", ticket_id).execute()
