"""
Elite Finds Bot — entry point.

Startup sequence:
  1. Pre-login to uufinds (caches JWT so first /find is fast)
  2. Load cogs
  3. Sync slash commands globally
  4. Set status
"""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.services.uufinds import UUFindsClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

COGS = [
    "bot.cogs.tickets",
    "bot.cogs.link_converter",
]


class EliteFindsBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = False  # not needed for slash commands

        super().__init__(
            command_prefix="!",  # unused, slash commands only
            intents=intents,
            help_command=None,
        )
        self.uufinds = UUFindsClient()

    async def setup_hook(self) -> None:
        # Load cogs
        for cog in COGS:
            await self.load_extension(cog)
            log.info("Loaded cog: %s", cog)

        # Sync slash commands globally
        synced = await self.tree.sync()
        log.info("Synced %d slash commands", len(synced))

        # Pre-warm uufinds token in background — don't block Discord connection
        asyncio.create_task(self._prewarm_uufinds())

    async def _prewarm_uufinds(self) -> None:
        log.info("Pre-warming uufinds token...")
        try:
            await self.uufinds.ensure_token()
            log.info("uufinds token ready")
        except Exception as e:
            log.warning("uufinds pre-warm failed (will retry on first /find): %s", e)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="/find to search products",
            )
        )

    async def close(self) -> None:
        await self.uufinds.close()
        await super().close()


async def main() -> None:
    token = os.environ["DISCORD_TOKEN"]
    bot = EliteFindsBot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
