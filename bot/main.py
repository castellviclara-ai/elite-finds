"""
Elite Finds Bot — entry point.

Startup sequence:
  1. Start keep-alive HTTP server (required for Replit hosting)
  2. Pre-login to uufinds (caches JWT so first /find is fast)
  3. Load cogs
  4. Sync slash commands globally
  5. Set status
"""

import asyncio
import logging
import os
from aiohttp import web

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


async def _keep_alive() -> None:
    """Minimal HTTP server so Replit doesn't kill the process."""
    async def handle(request: web.Request) -> web.Response:
        return web.Response(text="Elite Finds is running.")

    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Keep-alive server listening on port %d", port)


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
        # Pre-warm uufinds token
        log.info("Pre-warming uufinds token...")
        try:
            await self.uufinds.ensure_token()
            log.info("uufinds token ready")
        except Exception as e:
            log.warning("uufinds pre-warm failed (will retry on first /find): %s", e)

        # Load cogs
        for cog in COGS:
            await self.load_extension(cog)
            log.info("Loaded cog: %s", cog)

        # Sync slash commands globally
        synced = await self.tree.sync()
        log.info("Synced %d slash commands", len(synced))

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
    await _keep_alive()
    token = os.environ["DISCORD_TOKEN"]
    bot = EliteFindsBot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
