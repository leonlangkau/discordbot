"""Player stats and leaderboard commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ScrimBot


class Stats(commands.Cog):
    """Win/loss records from finished scrims."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    @app_commands.command(name="stats", description="Show a player's scrim record")
    @app_commands.describe(player="The player to look up (defaults to you)")
    async def stats(
        self, interaction: discord.Interaction, player: discord.Member | None = None
    ) -> None:
        target = player or interaction.user
        row = await self.bot.db.get_stats(interaction.guild_id, target.id)
        if row is None or row["played"] == 0:
            await interaction.response.send_message(
                f"{target.display_name} hasn't finished any scrims yet.", ephemeral=True
            )
            return
        winrate = row["wins"] / row["played"] * 100
        embed = discord.Embed(
            title=f"Scrim record — {target.display_name}",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Played", value=str(row["played"]))
        embed.add_field(name="Wins", value=str(row["wins"]))
        embed.add_field(name="Losses", value=str(row["losses"]))
        embed.add_field(name="Draws", value=str(row["draws"]))
        embed.add_field(name="Win rate", value=f"{winrate:.0f}%")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Top scrim players in this server")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.leaderboard(interaction.guild_id)
        if not rows:
            await interaction.response.send_message(
                "No scrims have been played yet.", ephemeral=True
            )
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            rank = medals[i] if i < 3 else f"`#{i + 1}`"
            winrate = row["wins"] / row["played"] * 100 if row["played"] else 0
            lines.append(
                f"{rank} <@{row['user_id']}> — **{row['wins']}W / {row['losses']}L**"
                f" ({winrate:.0f}% over {row['played']} scrims)"
            )
        embed = discord.Embed(
            title="🏆 Scrim Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(Stats(bot))
