"""Player stats: W/L, Elo ranks, XP levels, leaderboards, and match history."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ScrimBot

COIN = "🪙"

RANKS = [
    (0, "Silver I", "🥈"),
    (750, "Silver Elite", "🥈"),
    (850, "Gold Nova", "🥉"),
    (950, "Gold Nova Master", "🥉"),
    (1050, "Master Guardian", "🛡️"),
    (1150, "Distinguished MG", "🛡️"),
    (1250, "Legendary Eagle", "🦅"),
    (1350, "Supreme Master", "⭐"),
    (1450, "Global Elite", "🌐"),
]

MESSAGE_XP = 20
MESSAGE_XP_COOLDOWN = 60


def rank_for(elo: int) -> str:
    name, emoji = RANKS[0][1], RANKS[0][2]
    for threshold, rank_name, rank_emoji in RANKS:
        if elo >= threshold:
            name, emoji = rank_name, rank_emoji
    return f"{emoji} {name}"


def level_for(xp: int) -> int:
    return int(math.sqrt(xp / 100))


def xp_for_level(level: int) -> int:
    return 100 * level * level


class Stats(commands.Cog):
    """Progression from scrims and chat."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot
        self._xp_cooldowns: dict[tuple[int, int], float] = {}

    # ---- chat XP ----------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._xp_cooldowns.get(key, 0) < MESSAGE_XP_COOLDOWN:
            return
        self._xp_cooldowns[key] = now
        old_xp, new_xp = await self.bot.db.add_xp(
            message.guild.id, message.author.id, MESSAGE_XP
        )
        old_level, new_level = level_for(old_xp), level_for(new_xp)
        if new_level > old_level:
            try:
                await message.channel.send(
                    f"🆙 {message.author.mention} reached **level {new_level}**!"
                )
            except discord.HTTPException:
                pass

    # ---- commands ---------------------------------------------------------

    @app_commands.command(name="stats", description="Show a player's full profile")
    @app_commands.describe(player="The player to look up (defaults to you)")
    async def stats(
        self, interaction: discord.Interaction, player: discord.Member | None = None
    ) -> None:
        target = player or interaction.user
        row = await self.bot.db.get_stats(interaction.guild_id, target.id)
        econ = await self.bot.db.get_econ(interaction.guild_id, target.id)

        elo = row["elo"] if row else 1000
        xp = row["xp"] if row else 0
        level = level_for(xp)
        embed = discord.Embed(
            title=f"Profile — {target.display_name}",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Rank", value=rank_for(elo), inline=True)
        embed.add_field(name="Elo", value=str(elo), inline=True)
        embed.add_field(name="Coins", value=f"{econ['balance']:,} {COIN}", inline=True)
        embed.add_field(
            name="Level",
            value=f"{level} ({xp:,}/{xp_for_level(level + 1):,} XP)",
            inline=True,
        )
        if row and row["played"]:
            winrate = row["wins"] / row["played"] * 100
            embed.add_field(
                name="Scrims",
                value=(
                    f"{row['wins']}W / {row['losses']}L / {row['draws']}D "
                    f"({winrate:.0f}% of {row['played']})"
                ),
                inline=True,
            )
        else:
            embed.add_field(name="Scrims", value="none played yet", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Server leaderboards")
    @app_commands.describe(board="Which leaderboard to show")
    @app_commands.choices(
        board=[
            app_commands.Choice(name="🏆 Wins", value="wins"),
            app_commands.Choice(name="📈 Elo", value="elo"),
            app_commands.Choice(name="🪙 Coins", value="coins"),
            app_commands.Choice(name="🆙 XP", value="xp"),
        ]
    )
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        board: app_commands.Choice[str] | None = None,
    ) -> None:
        kind = board.value if board else "wins"
        medals = ["🥇", "🥈", "🥉"]

        if kind == "coins":
            rows = await self.bot.db.coins_leaderboard(interaction.guild_id)
            fmt = lambda r: f"**{r['balance']:,}** {COIN}"
        else:
            rows = await self.bot.db.stats_leaderboard(interaction.guild_id, kind)
            if kind == "wins":
                fmt = lambda r: f"**{r['wins']}W / {r['losses']}L**"
            elif kind == "elo":
                fmt = lambda r: f"**{r['elo']}** · {rank_for(r['elo'])}"
            else:
                fmt = lambda r: f"**{r['xp']:,} XP** · level {level_for(r['xp'])}"

        rows = [r for r in rows if (r["balance"] if kind == "coins" else r[kind])]
        if not rows:
            await interaction.response.send_message(
                "Nothing on this board yet.", ephemeral=True
            )
            return
        lines = [
            f"{medals[i] if i < 3 else f'`#{i + 1}`'} <@{r['user_id']}> — {fmt(r)}"
            for i, r in enumerate(rows)
        ]
        title = {"wins": "🏆 Wins", "elo": "📈 Elo", "coins": "🪙 Coins", "xp": "🆙 XP"}[kind]
        embed = discord.Embed(
            title=f"{title} Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="history", description="A player's recent scrim results")
    @app_commands.describe(player="The player to look up (defaults to you)")
    async def history(
        self, interaction: discord.Interaction, player: discord.Member | None = None
    ) -> None:
        target = player or interaction.user
        matches = await self.bot.db.match_history(interaction.guild_id, target.id)
        if not matches:
            await interaction.response.send_message(
                f"{target.display_name} has no finished scrims yet.", ephemeral=True
            )
            return
        lines = []
        for m in matches:
            a, b = m["team_a_score"], m["team_b_score"]
            if a == b:
                icon = "➖"
            else:
                winner = "a" if a > b else "b"
                icon = "✅" if m["my_team"] == winner else "❌"
            lines.append(
                f"{icon} `#{m['id']}` {m['map'] or 'server map'} — "
                f"**{a} : {b}** (Team {m['my_team'].upper()})"
            )
        embed = discord.Embed(
            title=f"Match history — {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(Stats(bot))
