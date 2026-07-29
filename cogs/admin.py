"""Server configuration commands for administrators."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ScrimBot


@app_commands.default_permissions(manage_guild=True)
class ScrimConfig(commands.GroupCog, group_name="scrimconfig"):
    """Configure how scrims work in this server."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="announce-channel",
        description="Set the channel where new scrims are announced",
    )
    async def announce_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        await self.bot.db.set_config(interaction.guild_id, announce_channel_id=channel.id)
        await interaction.response.send_message(
            f"New scrims will be announced in {channel.mention}."
        )

    @app_commands.command(
        name="ping-role",
        description="Set a role to ping when a new scrim is created (omit to clear)",
    )
    async def ping_role(
        self, interaction: discord.Interaction, role: discord.Role | None = None
    ) -> None:
        await self.bot.db.set_config(
            interaction.guild_id, scrim_role_id=role.id if role else None
        )
        await interaction.response.send_message(
            f"New scrims will ping {role.mention}." if role else "Scrim ping role cleared.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="max-scrims",
        description="Set how many scrims can be active at once (default 5)",
    )
    async def max_scrims(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 25],
    ) -> None:
        await self.bot.db.set_config(interaction.guild_id, max_open_scrims=limit)
        await interaction.response.send_message(
            f"This server can now have up to **{limit}** active scrims."
        )

    @app_commands.command(name="show", description="Show the current scrim configuration")
    async def show(self, interaction: discord.Interaction) -> None:
        config = await self.bot.db.get_config(interaction.guild_id)
        embed = discord.Embed(title="Scrim configuration", color=discord.Color.blurple())
        if config:
            announce = (
                f"<#{config['announce_channel_id']}>"
                if config["announce_channel_id"]
                else "*channel where `/scrim create` is used*"
            )
            role = f"<@&{config['scrim_role_id']}>" if config["scrim_role_id"] else "*none*"
            max_scrims = config["max_open_scrims"]
        else:
            announce, role, max_scrims = (
                "*channel where `/scrim create` is used*",
                "*none*",
                5,
            )
        embed.add_field(name="Announcement channel", value=announce, inline=False)
        embed.add_field(name="Ping role", value=role, inline=False)
        embed.add_field(name="Max active scrims", value=str(max_scrims), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(ScrimConfig(bot))
