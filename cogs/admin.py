"""Server configuration commands for administrators."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import rcon
from settings import server_settings

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

    @app_commands.command(
        name="server",
        description="Set the CS2 server players connect to (and optional RCON access)",
    )
    @app_commands.describe(
        host="Server IP or hostname",
        port="Server port (default 27015)",
        password="Server join password (sv_password), if any",
        rcon_password="RCON password — enables auto map change and /scrimconfig rcon",
    )
    async def server(
        self,
        interaction: discord.Interaction,
        host: str,
        port: app_commands.Range[int, 1, 65535] = 27015,
        password: str | None = None,
        rcon_password: str | None = None,
    ) -> None:
        await self.bot.db.set_config(
            interaction.guild_id,
            server_host=host.strip(),
            server_port=port,
            server_password=password,
            rcon_password=rcon_password,
        )
        rcon_note = ""
        if rcon_password:
            try:
                await rcon.run_command(host.strip(), port, rcon_password, "echo scrimbot")
                rcon_note = " RCON connection verified ✅"
            except rcon.RconError as e:
                rcon_note = f" ⚠️ RCON check failed: {e}"
        await interaction.response.send_message(
            f"Server set to `{host.strip()}:{port}`"
            + (" with a join password" if password else "")
            + f".{rcon_note}",
            ephemeral=True,
        )

    @app_commands.command(
        name="rcon", description="Run an RCON command on the configured CS2 server"
    )
    @app_commands.describe(command="The command to run, e.g. changelevel de_mirage")
    async def rcon_cmd(self, interaction: discord.Interaction, command: str) -> None:
        config = await self.bot.db.get_config(interaction.guild_id)
        srv = server_settings(config)
        if not srv or not srv["rcon_password"]:
            await interaction.response.send_message(
                "No RCON access configured — set it with `/scrimconfig server` "
                "or the SERVER_HOST/RCON_PASSWORD environment variables.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            output = await rcon.run_command(
                srv["host"], srv["port"], srv["rcon_password"], command
            )
        except rcon.RconError as e:
            await interaction.followup.send(f"⚠️ RCON error: {e}", ephemeral=True)
            return
        output = output.strip() or "(no output)"
        if len(output) > 1900:
            output = output[:1900] + "…"
        await interaction.followup.send(f"```\n{output}\n```", ephemeral=True)

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
        srv = server_settings(config)
        if srv:
            server = f"`{srv['host']}:{srv['port']}`"
            server += " 🔒" if srv["password"] else ""
            server += " · RCON ✅" if srv["rcon_password"] else " · RCON ✖️"
        else:
            server = "*not set — use `/scrimconfig server` or SERVER_HOST env var*"
        embed.add_field(name="CS2 server", value=server, inline=False)
        embed.add_field(name="Announcement channel", value=announce, inline=False)
        embed.add_field(name="Ping role", value=role, inline=False)
        embed.add_field(name="Max active scrims", value=str(max_scrims), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(ScrimConfig(bot))
