"""
/find command — product search with tier bucketing and agent dropdown.
"""

import logging
from typing import TYPE_CHECKING

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
from bot.services.uufinds import Product, bucket_products, filter_results

if TYPE_CHECKING:
    from bot.main import EliteFindsBot

log = logging.getLogger(__name__)

MAX_SUBJECT_LEN = 60
MAX_RESULTS_EMBED = 25


def _expand_query(query: str) -> str:
    lower = query.strip().lower()
    return KEYWORD_EXPANSIONS.get(lower, query.strip())


def _truncate(text: str, max_len: int = MAX_SUBJECT_LEN) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _build_product_line(p: Product, agent: str | None) -> str:
    name = _truncate(p.subject)
    price = f"${p.price_usd:.2f}"
    qc = f"📸 {p.qc_image_count}"

    if agent:
        buy_url = build_agent_url(agent, p.marketplace, p.product_id, p.source_link)
        return f"**{name}**\n{price} · {qc} · [🛒 Buy]({buy_url}) · [📸 QC]({p.uufinds_qc_url})"
    else:
        return f"**{name}**\n{price} · {qc} · [📸 QC]({p.uufinds_qc_url})"


def _build_results_embed(
    query: str,
    products: list[Product],
    agent: str | None,
    expanded_query: str,
) -> discord.Embed:
    """Build the main 3-tier embed (top 1 per tier)."""
    embed = discord.Embed(
        title=f"🔍 Results for: {expanded_query}",
        color=0x000000,
    )

    tiers_found = {"premium": [], "mid": [], "cheap": []}
    for p in products:
        tiers_found[p.tier].append(p)

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
    agent: str | None,
) -> discord.Embed:
    """Embed with up to 25 products, one field each."""
    embed = discord.Embed(
        title=f"📋 All results for: {query}",
        color=0x000000,
    )
    for i, p in enumerate(products[:MAX_RESULTS_EMBED], 1):
        emoji = TIER_EMOJI.get(p.tier, "")
        embed.add_field(
            name=f"{i}. {emoji} ${p.price_usd:.2f}",
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
    def __init__(self, user_id: int, query: str, products: list[Product], expanded_query: str):
        self.user_id = user_id
        self.query = query
        self.products = products
        self.expanded_query = expanded_query

        options = []
        for key in AGENTS_ORDER:
            label = AGENTS_DISPLAY[key]
            options.append(discord.SelectOption(label=label, value=key))

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

        # Save to DB
        await db.set_preferred_agent(
            str(interaction.user.id),
            str(interaction.user),
            agent,
        )

        # Rebuild embed with agent links
        embed = _build_results_embed(self.query, self.products, agent, self.expanded_query)

        # Replace view with show-all button only (no more dropdown)
        view = ShowAllView(self.query, self.products, agent)
        await interaction.response.edit_message(embed=embed, view=view)


class AgentSelectView(discord.ui.View):
    def __init__(self, user_id: int, query: str, products: list[Product], expanded_query: str):
        super().__init__(timeout=300)
        self.add_item(AgentSelect(user_id, query, products, expanded_query))


# ---------------------------------------------------------------------------
# Show all results button
# ---------------------------------------------------------------------------

class ShowAllView(discord.ui.View):
    def __init__(self, query: str, products: list[Product], agent: str | None):
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

    @app_commands.command(name="find", description="Search for a product on uufinds")
    @app_commands.describe(query="Product name (e.g. nike air force 1, aj4, yeezy 350)")
    async def find(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True)

        expanded = _expand_query(query)
        discord_id = str(interaction.user.id)
        username = str(interaction.user)

        # Ensure user exists in DB
        await db.upsert_user(discord_id, username)

        # Get preferred agent
        agent = await db.get_preferred_agent(discord_id)

        # Search
        try:
            raw = await self.bot.uufinds.search_with_retry(expanded)
        except Exception as e:
            log.error("uufinds search error: %s", e)
            await interaction.followup.send(
                "⚠️ Search failed due to an API error. Please try again in a moment.",
                ephemeral=True,
            )
            return

        if not raw:
            await interaction.followup.send(
                f"No results found for **{expanded}**.\n"
                "Try a different product name (brand + model in English works best)."
            )
            return

        # Filter + bucket
        filtered = filter_results(raw, expanded)
        products = bucket_products(filtered)

        # Cache results (fire-and-forget)
        try:
            serialized = [
                {
                    "id": p.id,
                    "subject": p.subject,
                    "marketplace": p.marketplace,
                    "product_id": p.product_id,
                    "source_link": p.source_link,
                    "price_usd": p.price_usd,
                    "qc_image_count": p.qc_image_count,
                }
                for p in products
            ]
            await db.set_cached_results(expanded.lower(), serialized)
        except Exception:
            pass

        embed = _build_results_embed(query, products, agent, expanded)

        if agent:
            view = ShowAllView(query, products, agent)
        else:
            view = AgentSelectView(interaction.user.id, query, products, expanded)

        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: "EliteFindsBot") -> None:
    await bot.add_cog(TicketsCog(bot))
