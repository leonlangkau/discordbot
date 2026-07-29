"""Auto-updating CS2 server status embed, refreshed via RCON."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

import rcon
from settings import server_settings

if TYPE_CHECKING:
    from bot import ScrimBot


def parse_status(raw: str) -> dict[str, str]:
    """Best-effort parse of CS2 `status` output."""
    info: dict[str, str] = {}
    patterns = {
        "hostname": r"hostname\s*:\s*(.+)",
        "map": r"(?:map|spawngroup)[^:\n]*:\s*([\w-]+)",
        "players": r"players\s*:\s*(\d+\s*humans?[^\n]*)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            info[key] = match.group(1).strip()
    return info


class ServerStatus(commands.Cog):
    """Keeps a live status embed for the CS2 server."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.update_status.start()

    async def cog_unload(self) -> None:
        self.update_status.cancel()

    def build_embed(self, srv: dict, info: dict[str, str] | None, error: str | None) -> discord.Embed:
        online = info is not None
        embed = discord.Embed(
            title="🖥️ CS2 Server Status",
            color=discord.Color.green() if online else discord.Color.red(),
        )
        embed.add_field(
            name="Status", value="🟢 Online" if online else "🔴 Unreachable", inline=True
        )
        embed.add_field(name="Address", value=f"`{srv['host']}:{srv['port']}`", inline=True)
        if online:
            if info.get("hostname"):
                embed.add_field(name="Name", value=info["hostname"][:100], inline=False)
            embed.add_field(name="Map", value=info.get("map", "?"), inline=True)
            embed.add_field(name="Players", value=info.get("players", "?"), inline=True)
        elif error:
            embed.add_field(name="Error", value=error[:200], inline=False)
        embed.set_footer(text="Updates every 5 minutes")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    @tasks.loop(minutes=5)
    async def update_status(self) -> None:
        for config in await self.bot.db.all_status_configs():
            srv = server_settings(config)
            if not srv or not srv["rcon_password"]:
                continue
            channel = self.bot.get_channel(config["status_channel_id"])
            if channel is None:
                continue
            info, error = None, None
            try:
                raw = await rcon.run_command(
                    srv["host"], srv["port"], srv["rcon_password"], "status"
                )
                info = parse_status(raw)
            except rcon.RconError as e:
                error = str(e)
            embed = self.build_embed(srv, info, error)

            message = None
            if config["status_message_id"]:
                try:
                    message = await channel.fetch_message(config["status_message_id"])
                except discord.HTTPException:
                    message = None
            try:
                if message:
                    await message.edit(embed=embed)
                else:
                    message = await channel.send(embed=embed)
                    await self.bot.db.set_config(
                        config["guild_id"], status_message_id=message.id
                    )
            except discord.HTTPException:
                continue

    @update_status.before_loop
    async def before_update(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="statuschannel",
        description="Set (or clear) the auto-updating server status channel",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="Channel for the status embed (omit to disable)")
    async def statuschannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self.bot.db.set_config(
            interaction.guild_id,
            status_channel_id=channel.id if channel else None,
            status_message_id=None,
        )
        if channel is None:
            await interaction.response.send_message(
                "Server status updates disabled.", ephemeral=True
            )
            return
        config = await self.bot.db.get_config(interaction.guild_id)
        srv = server_settings(config)
        if not srv or not srv["rcon_password"]:
            await interaction.response.send_message(
                f"Status channel set to {channel.mention}, but I need the server's "
                "RCON configured (`/scrimconfig server` or RCON_PASSWORD env var) "
                "before I can post live status.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Live server status will be posted in {channel.mention} "
            "(first update within 5 minutes).",
            ephemeral=True,
        )


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(ServerStatus(bot))
