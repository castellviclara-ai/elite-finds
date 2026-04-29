"""Prototype cog for 1688 marketplace search."""

import os
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.marketplace_1688 import Search1688Client


MAX_1688_FIELDS = 5


def _build_product_field(item: dict[str, object]) -> tuple[str, str]:
    title = (
        item.get("title")
        or item.get("offerTitle")
        or item.get("subject")
        or item.get("productName")
        or "Untitled product"
    )

    price = item.get("price") or item.get("priceText") or "N/A"
    link = item.get("detailUrl") or item.get("url") or item.get("offerUrl")
    if not link:
        offer_id = item.get("offerId") or item.get("id")
        if offer_id:
            link = f"https://detail.1688.com/offer/{offer_id}.html"
        else:
            link = "https://www.1688.com/"

    value = f"Price: `{price}`\n[View on 1688]({link})"
    return title, value


class MarketSearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.client = Search1688Client()

    @app_commands.command(name="search1688", description="Prototype 1688 text search")
    @app_commands.describe(query="Search query for 1688")
    async def search1688(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True)

        results = await self.client.search(query)
        if not results:
            note = "No results returned from 1688."
            if os.environ.get("MARKETPLACE_PROXY") is None:
                note += " Set MARKETPLACE_PROXY to a residential proxy and retry."
            await interaction.followup.send(note, ephemeral=True)
            return

        embed = discord.Embed(
            title=f"1688 prototype search: {query}",
            description=f"Returned {len(results)} raw items from 1688",
            color=0x000000,
        )

        for item in results[:MAX_1688_FIELDS]:
            name, value = _build_product_field(item)
            embed.add_field(name=name[:80], value=value, inline=False)

        footer_text = "Prototype 1688 search. Results are best-effort and may be blocked by 1688 anti-bot."
        if os.environ.get("MARKETPLACE_PROXY"):
            footer_text += " Proxy active."
        else:
            footer_text += " Proxy not configured."
        embed.set_footer(text=footer_text)

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MarketSearchCog(bot))
