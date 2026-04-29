"""
/convert  — convert any product link to an affiliate link for a chosen agent
/setagent — save preferred agent to Supabase
/myagent  — show current preferred agent
/agents   — list all supported agents
"""

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import AGENTS_DISPLAY, AGENTS_ORDER
from bot.agents import convert_link
from bot import db

if TYPE_CHECKING:
    from bot.main import EliteFindsBot

log = logging.getLogger(__name__)

# Choices for /convert and /setagent agent parameter
_AGENT_CHOICES = [
    app_commands.Choice(name=AGENTS_DISPLAY[key], value=key)
    for key in AGENTS_ORDER
]


class LinkConverterCog(commands.Cog):
    def __init__(self, bot: "EliteFindsBot") -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /convert
    # ------------------------------------------------------------------

    @app_commands.command(
        name="convert",
        description="Convert a product link to your preferred agent's affiliate link",
    )
    @app_commands.describe(
        link="Product URL (Weidian, 1688, or any supported agent link)",
        agent="Agent to use (defaults to your saved preferred agent)",
    )
    @app_commands.choices(agent=_AGENT_CHOICES)
    async def convert(
        self,
        interaction: discord.Interaction,
        link: str,
        agent: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        discord_id = str(interaction.user.id)
        chosen_agent = agent.value if agent else await db.get_preferred_agent(discord_id)

        if not chosen_agent:
            await interaction.followup.send(
                "You don't have a preferred agent set.\n"
                "Use `/setagent` to save one, or pass the `agent` parameter directly.",
                ephemeral=True,
            )
            return

        result = convert_link(link.strip(), chosen_agent)
        if result is None:
            await interaction.followup.send(
                "❌ Could not parse that link. Supported formats:\n"
                "• Weidian: `weidian.com/item.html?itemID=...`\n"
                "• 1688: `detail.1688.com/offer/...`\n"
                "• Any supported agent link (Hipobuy, Sugargoo, Kakobuy, etc.)",
                ephemeral=True,
            )
            return

        agent_display = AGENTS_DISPLAY.get(chosen_agent, chosen_agent)
        embed = discord.Embed(
            title="🔗 Link Converted",
            color=0x57F287,
        )
        embed.add_field(name="Agent", value=agent_display, inline=True)
        embed.add_field(name="Link", value=result, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /setagent
    # ------------------------------------------------------------------

    @app_commands.command(
        name="setagent",
        description="Save your preferred buying agent",
    )
    @app_commands.describe(agent="The agent you want to use by default")
    @app_commands.choices(agent=_AGENT_CHOICES)
    async def setagent(
        self,
        interaction: discord.Interaction,
        agent: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        await db.set_preferred_agent(
            str(interaction.user.id),
            str(interaction.user),
            agent.value,
        )

        agent_display = AGENTS_DISPLAY.get(agent.value, agent.value)
        await interaction.followup.send(
            f"✅ Preferred agent set to **{agent_display}**.\n"
            "All future `/find` results will include buy links for this agent.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /myagent
    # ------------------------------------------------------------------

    @app_commands.command(
        name="myagent",
        description="Show your current preferred agent",
    )
    async def myagent(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        agent = await db.get_preferred_agent(str(interaction.user.id))
        if not agent:
            await interaction.followup.send(
                "You haven't set a preferred agent yet. Use `/setagent` to choose one.",
                ephemeral=True,
            )
            return

        agent_display = AGENTS_DISPLAY.get(agent, agent)
        await interaction.followup.send(
            f"Your current preferred agent is **{agent_display}**.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /agents
    # ------------------------------------------------------------------

    @app_commands.command(
        name="agents",
        description="List all supported buying agents",
    )
    async def agents(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        lines = []
        for i, key in enumerate(AGENTS_ORDER, 1):
            display = AGENTS_DISPLAY[key]
            lines.append(f"`{i:02d}.` {display}")

        embed = discord.Embed(
            title="🛒 Supported Agents",
            description="\n".join(lines),
            color=0x5865F2,
        )
        embed.set_footer(text="Use /setagent to save your preferred agent")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "EliteFindsBot") -> None:
    await bot.add_cog(LinkConverterCog(bot))
