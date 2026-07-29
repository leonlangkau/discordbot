"""Entry point for the scrim bot."""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from database import Database

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("scrimbot")

COGS = (
    "cogs.scrims",
    "cogs.stats",
    "cogs.admin",
    "cogs.economy",
    "cogs.casino",
    "cogs.duels",
    "cogs.giveaways",
    "cogs.status",
    "cogs.security",
)


class ScrimBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.db = Database()

    async def setup_hook(self) -> None:
        await self.db.connect()
        for cog in COGS:
            await self.load_extension(cog)

        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced commands to guild %s", guild_id)
        else:
            await self.tree.sync()
            log.info("Synced commands globally")

    async def close(self) -> None:
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="/scrim create"
            )
        )


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    asyncio.run(ScrimBot().start(token))


if __name__ == "__main__":
    main()
