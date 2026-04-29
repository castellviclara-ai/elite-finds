"""
/find command — image-based product search across 1688 + Weidian via Lovegobuy.

Pipeline:
  - Accept text query AND/OR an image attachment.
  - If only query: DuckDuckGo Images → top photo → use as reference.
  - Send reference image to Lovegobuy (1688 + Weidian in parallel).
  - Bucket results by USD price percentiles into premium / mid / cheap.
  - Show top-of-tier embed + agent dropdown + "show all" button.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import (
    AGENTS_DISPLAY,
    AGENTS_ORDER,
    KEYWORD_EXPANSIONS,
    TIER_EMOJI,
    TIER_LABELS,
)
from bot.agents import build_agent_url
from bot import db
from bot.services.lovegobuy import Product
from bot.services.image_search import (
    fetch_reference_image,
    search_by_image,
    bucket_by_price,
)

if TYPE_CHECKING:
    from bot.main import EliteFindsBot

log = logging.getLogger(__name__)

MAX_SUBJECT_LEN = 60
MAX_RESULTS_EMBED = 25
SEARCH_TIMEOUT_SECS = 35   # full pipeline (download + 1688 + weidian)


def _expand_query(query: str) -> str:
    lower = query.strip().lower()
    return KEYWORD_EXPANSIONS.get(lower, query.strip())


def _truncate(text: str, max_len: int = MAX_SUBJECT_LEN) -> str:
    if not text:
        return "Untitled"
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _build_product_line(p: Product, agent: Optional[str]) -> str:
    name = _truncate(p.title or p.title_cn)
    price = p.price_display or f"${p.price_usd:.2f}"
    market = p.marketplace.upper()
    src_label = "1688" if p.marketplace == "1688" else "Weidian"

    lines = [f"**{name}**", f"{price} · {market}"]
    if agent:
        buy_url = build_agent_url(agent, p.marketplace, p.product_id, p.source_link)
        lines.append(f"[🛒 Buy]({buy_url}) · [🔗 {src_label}]({p.source_link})")
    else:
        lines.append(f"[🔗 {src_label}]({p.source_link})")
    return "\n".join(lines)


def _build_results_embed(
    query: str,
    products: list[Product],
    agent: Optional[str],
    expanded_query: str,
    photo_source_url: Optional[str],
) -> discord.Embed:
    """Main 3-tier embed (top product per tier)."""
    embed = discord.Embed(
        title=f"🔍 Results for: {expanded_query}",
        color=0x000000,
    )

    tiers_found: dict[str, list[Product]] = {"premium": [], "mid": [], "cheap": []}
    for p in products:
        tiers_found.get(p.tier, tiers_found["mid"]).append(p)

    for tier_key in ("premium", "mid", "cheap"):
        items = tiers_found[tier_key]
        if not items:
            continue
        top = items[0]
        emoji = TIER_EMOJI[tier_key]
        label = TIER_LABELS[tier_key]
        embed.add_field(
            name=f"{emoji} {label}",
            value=_build_product_line(top, agent),
            inline=False,
        )

    # Show the reference photo (DuckDuckGo result or attached image) as the embed image
    top_premium = tiers_found["premium"][0] if tiers_found["premium"] else None
    if photo_source_url:
        embed.set_thumbnail(url=photo_source_url)
    if top_premium and top_premium.image:
        embed.set_image(url=top_premium.image)

    total = len(products)
    if not agent:
        embed.set_footer(text=f"Select an agent below to get buy links · {total} results found")
    else:
        agent_display = AGENTS_DISPLAY.get(agent, agent)
        embed.set_footer(text=f"Agent: {agent_display} · {total} results found")

    return embed


def _build_all_results_embed(
    query: str,
    products: list[Product],
    agent: Optional[str],
) -> discord.Embed:
    """Embed with up to 25 products, one field each."""
    embed = discord.Embed(
        title=f"📋 All results for: {query}",
        color=0x000000,
    )
    for i, p in enumerate(products[:MAX_RESULTS_EMBED], 1):
        emoji = TIER_EMOJI.get(p.tier, "")
        embed.add_field(
            name=f"{i}. {emoji} {p.price_display or f'${p.price_usd:.2f}'}",
            value=_build_product_line(p, agent),
            inline=False,
        )
    if not agent:
        embed.set_footer(text="Select an agent in the main message to get buy links")
    return embed


# ---------------------------------------------------------------------------
# Agent select dropdown
# ---------------------------------------------------------------------------

class AgentSelect(discord.ui.Select):
    def __init__(
        self,
        user_id: int,
        query: str,
        products: list[Product],
        expanded_query: str,
        photo_source_url: Optional[str],
    ):
        self.user_id = user_id
        self.query = query
        self.products = products
        self.expanded_query = expanded_query
        self.photo_source_url = photo_source_url

        options = [
            discord.SelectOption(label=AGENTS_DISPLAY[key], value=key)
            for key in AGENTS_ORDER
        ]
        super().__init__(
            placeholder="Select your agent to get buy links...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the user who ran `/find` can select an agent.", ephemeral=True
            )
            return

        agent = self.values[0]

        # Save preference (best-effort — never block on DB failure)
        try:
            await db.set_preferred_agent(
                str(interaction.user.id),
                str(interaction.user),
                agent,
            )
        except Exception as e:
            log.warning("Failed to persist preferred agent: %s", e)

        embed = _build_results_embed(
            self.query, self.products, agent, self.expanded_query, self.photo_source_url
        )
        view = ShowAllView(self.query, self.products, agent)
        await interaction.response.edit_message(embed=embed, view=view)


class AgentSelectView(discord.ui.View):
    def __init__(
        self,
        user_id: int,
        query: str,
        products: list[Product],
        expanded_query: str,
        photo_source_url: Optional[str],
    ):
        super().__init__(timeout=300)
        self.add_item(AgentSelect(user_id, query, products, expanded_query, photo_source_url))


# ---------------------------------------------------------------------------
# Show all results button
# ---------------------------------------------------------------------------

class ShowAllView(discord.ui.View):
    def __init__(self, query: str, products: list[Product], agent: Optional[str]):
        super().__init__(timeout=300)
        self.query = query
        self.products = products
        self.agent = agent

        btn = discord.ui.Button(
            label=f"📋 Show all {len(products)} results",
            style=discord.ButtonStyle.secondary,
        )
        btn.callback = self._show_all_callback
        self.add_item(btn)

    async def _show_all_callback(self, interaction: discord.Interaction) -> None:
        embed = _build_all_results_embed(self.query, self.products, self.agent)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class TicketsCog(commands.Cog):
    def __init__(self, bot: "EliteFindsBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="find",
        description="Search for a product on 1688 + Weidian by text or image",
    )
    @app_commands.describe(
        query="Product name (e.g. 'nike air force 1 white'). Optional if you attach an image.",
        image="Optional: attach a photo to skip the auto-photo step (gives best results)",
    )
    async def find(
        self,
        interaction: discord.Interaction,
        query: Optional[str] = None,
        image: Optional[discord.Attachment] = None,
    ) -> None:
        # Defer immediately — Discord requires a response within 3 seconds.
        # `thinking=True` shows the typing indicator; we then have 15 minutes.
        await interaction.response.defer(thinking=True)

        if not query and not image:
            await interaction.followup.send(
                "Please provide a `query` or attach an `image`.\n"
                "Examples:\n"
                "• `/find query: nike air force 1 white`\n"
                "• `/find image: <upload a photo>`"
            )
            return

        # Best-effort user upsert (never block search)
        try:
            await db.upsert_user(str(interaction.user.id), str(interaction.user))
        except Exception:
            pass

        expanded = _expand_query(query) if query else "image search"
        agent = None
        try:
            agent = await db.get_preferred_agent(str(interaction.user.id))
        except Exception:
            pass

        # ------------------------------------------------------------------
        # 1. Get reference image bytes
        # ------------------------------------------------------------------
        image_bytes: Optional[bytes] = None
        photo_source_url: Optional[str] = None

        if image is not None:
            if not image.content_type or not image.content_type.startswith("image/"):
                await interaction.followup.send(
                    "That attachment doesn't look like an image. Please attach a JPG/PNG/WebP."
                )
                return
            if image.size and image.size > 8_000_000:
                await interaction.followup.send(
                    "That image is too large (max 8 MB). Please use a smaller photo."
                )
                return
            try:
                image_bytes = await image.read()
                photo_source_url = image.url
            except Exception as e:
                log.warning("Failed to read attachment: %s", e)
                await interaction.followup.send("Couldn't read the attached image. Try again.")
                return
        else:
            # Text query → fetch reference photo from DuckDuckGo
            image_bytes, photo_source_url = await fetch_reference_image(expanded)
            if not image_bytes:
                await interaction.followup.send(
                    f"Couldn't find a reference photo for **{expanded}**.\n"
                    "Try a more specific query or attach an image directly."
                )
                return

        # ------------------------------------------------------------------
        # 2. Run image search (1688 + Weidian in parallel) with hard timeout
        # ------------------------------------------------------------------
        try:
            products = await asyncio.wait_for(
                search_by_image(image_bytes), timeout=SEARCH_TIMEOUT_SECS
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏱️ Search took too long. Please try again in a moment."
            )
            return
        except Exception as e:
            log.error("search_by_image error: %s", e)
            await interaction.followup.send(
                "⚠️ Search failed due to an internal error. Please try again."
            )
            return

        if not products:
            await interaction.followup.send(
                f"No products found for **{expanded}**.\n"
                "Try attaching a clearer photo, or use a more specific query."
            )
            return

        # ------------------------------------------------------------------
        # 3. Bucket by price tier and render
        # ------------------------------------------------------------------
        products = bucket_by_price(products)

        # Cache results (fire-and-forget)
        try:
            serialized = [
                {
                    "marketplace": p.marketplace,
                    "product_id": p.product_id,
                    "title": p.title,
                    "title_cn": p.title_cn,
                    "image": p.image,
                    "price_usd": p.price_usd,
                    "price_cny": p.price_cny,
                    "price_display": p.price_display,
                    "tier": p.tier,
                }
                for p in products
            ]
            await db.set_cached_results(expanded.lower(), serialized)
        except Exception:
            pass

        embed = _build_results_embed(query or "image", products, agent, expanded, photo_source_url)

        if agent:
            view = ShowAllView(query or "image", products, agent)
        else:
            view = AgentSelectView(
                interaction.user.id, query or "image", products, expanded, photo_source_url
            )

        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: "EliteFindsBot") -> None:
    await bot.add_cog(TicketsCog(bot))
