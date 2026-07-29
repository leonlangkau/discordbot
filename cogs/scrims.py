"""Scrim lifecycle: create lobbies, join teams, start matches, report scores."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ScrimBot

TEAM_NAMES = {"a": "Team A", "b": "Team B"}


def utcnow_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


async def build_scrim_embed(bot: ScrimBot, scrim) -> discord.Embed:
    players = await bot.db.get_players(scrim["id"])
    team_a = [p for p in players if p["team"] == "a"]
    team_b = [p for p in players if p["team"] == "b"]
    size = scrim["team_size"]

    colors = {
        "open": discord.Color.green(),
        "live": discord.Color.orange(),
        "finished": discord.Color.blurple(),
        "cancelled": discord.Color.dark_grey(),
    }
    embed = discord.Embed(
        title=f"Scrim #{scrim['id']} — {scrim['game']}",
        color=colors.get(scrim["status"], discord.Color.green()),
    )
    embed.add_field(name="Format", value=f"{size}v{size}", inline=True)
    embed.add_field(name="Status", value=scrim["status"].capitalize(), inline=True)
    embed.add_field(name="Host", value=f"<@{scrim['creator_id']}>", inline=True)
    if scrim["scheduled_for"]:
        embed.add_field(
            name="Scheduled", value=f"<t:{scrim['scheduled_for']}:F>", inline=False
        )

    def roster(team: list) -> str:
        lines = [
            f"{'✅' if p['ready'] else '⬜'} <@{p['user_id']}>" for p in team
        ]
        lines += ["⬜ *open slot*"] * (size - len(team))
        return "\n".join(lines)

    embed.add_field(name=f"Team A ({len(team_a)}/{size})", value=roster(team_a), inline=True)
    embed.add_field(name=f"Team B ({len(team_b)}/{size})", value=roster(team_b), inline=True)

    if scrim["status"] == "finished" and scrim["team_a_score"] is not None:
        a, b = scrim["team_a_score"], scrim["team_b_score"]
        winner = "Draw" if a == b else ("Team A" if a > b else "Team B")
        embed.add_field(name="Result", value=f"**{a} : {b}** — {winner}", inline=False)

    embed.set_footer(text="Use the buttons below to join, leave, or ready up.")
    return embed


async def refresh_announcement(bot: ScrimBot, scrim_id: int) -> None:
    scrim = await bot.db.get_scrim(scrim_id)
    if not scrim or not scrim["announce_channel_id"] or not scrim["announce_message_id"]:
        return
    channel = bot.get_channel(scrim["announce_channel_id"])
    if channel is None:
        return
    try:
        message = await channel.fetch_message(scrim["announce_message_id"])
        embed = await build_scrim_embed(bot, scrim)
        view = ScrimView(scrim["id"]) if scrim["status"] == "open" else None
        await message.edit(embed=embed, view=view)
    except discord.HTTPException:
        pass


async def sync_channel_permissions(bot: ScrimBot, scrim_id: int) -> None:
    """Grant every joined player access to the scrim's private channels."""
    scrim = await bot.db.get_scrim(scrim_id)
    if not scrim or not scrim["category_id"]:
        return
    guild = bot.get_guild(scrim["guild_id"])
    category = guild.get_channel(scrim["category_id"]) if guild else None
    if category is None:
        return

    players = await bot.db.get_players(scrim_id)
    member_teams: dict[discord.Member, str] = {}
    for p in players:
        member = guild.get_member(p["user_id"])
        if member:
            member_teams[member] = p["team"]

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
    }
    for member in member_teams:
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True)
    host = guild.get_member(scrim["creator_id"])
    if host:
        overwrites[host] = discord.PermissionOverwrite(view_channel=True, connect=True)

    try:
        await category.edit(overwrites=overwrites)
        for channel in category.channels:
            await channel.edit(sync_permissions=True)
    except discord.HTTPException:
        pass


class JoinButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"scrim:join:(?P<team>[ab]):(?P<id>\d+)",
):
    def __init__(self, team: str, scrim_id: int) -> None:
        self.team = team
        self.scrim_id = scrim_id
        super().__init__(
            discord.ui.Button(
                label=f"Join {TEAM_NAMES[team]}",
                style=discord.ButtonStyle.success if team == "a" else discord.ButtonStyle.primary,
                custom_id=f"scrim:join:{team}:{scrim_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["team"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScrimBot = interaction.client
        scrim = await bot.db.get_scrim(self.scrim_id)
        if not scrim or scrim["status"] != "open":
            await interaction.response.send_message(
                "This scrim is no longer open.", ephemeral=True
            )
            return

        players = await bot.db.get_players(self.scrim_id)
        on_team = [p for p in players if p["team"] == self.team]
        me = next((p for p in players if p["user_id"] == interaction.user.id), None)

        if me and me["team"] == self.team:
            await interaction.response.send_message(
                f"You are already on {TEAM_NAMES[self.team]}.", ephemeral=True
            )
            return
        if len(on_team) >= scrim["team_size"]:
            await interaction.response.send_message(
                f"{TEAM_NAMES[self.team]} is full.", ephemeral=True
            )
            return

        if me:
            await bot.db.set_team(self.scrim_id, interaction.user.id, self.team)
        else:
            await bot.db.add_player(self.scrim_id, interaction.user.id, self.team)

        await interaction.response.send_message(
            f"You joined **{TEAM_NAMES[self.team]}** for scrim #{self.scrim_id}.",
            ephemeral=True,
        )
        await refresh_announcement(bot, self.scrim_id)
        await sync_channel_permissions(bot, self.scrim_id)


class LeaveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"scrim:leave:(?P<id>\d+)",
):
    def __init__(self, scrim_id: int) -> None:
        self.scrim_id = scrim_id
        super().__init__(
            discord.ui.Button(
                label="Leave",
                style=discord.ButtonStyle.danger,
                custom_id=f"scrim:leave:{scrim_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScrimBot = interaction.client
        scrim = await bot.db.get_scrim(self.scrim_id)
        if not scrim or scrim["status"] != "open":
            await interaction.response.send_message(
                "This scrim is no longer open.", ephemeral=True
            )
            return
        removed = await bot.db.remove_player(self.scrim_id, interaction.user.id)
        if not removed:
            await interaction.response.send_message(
                "You are not in this scrim.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"You left scrim #{self.scrim_id}.", ephemeral=True
        )
        await refresh_announcement(bot, self.scrim_id)
        await sync_channel_permissions(bot, self.scrim_id)


class ReadyButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"scrim:ready:(?P<id>\d+)",
):
    def __init__(self, scrim_id: int) -> None:
        self.scrim_id = scrim_id
        super().__init__(
            discord.ui.Button(
                label="Ready / Unready",
                style=discord.ButtonStyle.secondary,
                custom_id=f"scrim:ready:{scrim_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScrimBot = interaction.client
        players = await bot.db.get_players(self.scrim_id)
        me = next((p for p in players if p["user_id"] == interaction.user.id), None)
        if me is None:
            await interaction.response.send_message(
                "Join the scrim before readying up.", ephemeral=True
            )
            return
        new_state = not me["ready"]
        await bot.db.set_ready(self.scrim_id, interaction.user.id, new_state)
        await interaction.response.send_message(
            "You are **ready**." if new_state else "You are **no longer ready**.",
            ephemeral=True,
        )
        await refresh_announcement(bot, self.scrim_id)


class ScrimView(discord.ui.View):
    def __init__(self, scrim_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(JoinButton("a", scrim_id))
        self.add_item(JoinButton("b", scrim_id))
        self.add_item(ReadyButton(scrim_id))
        self.add_item(LeaveButton(scrim_id))


class Scrims(commands.Cog):
    """Create and manage scrim matches."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(JoinButton, LeaveButton, ReadyButton)

    scrim = app_commands.Group(name="scrim", description="Create and manage scrims")

    # ---- helpers ----------------------------------------------------------

    async def _is_host_or_mod(self, interaction: discord.Interaction, scrim) -> bool:
        return (
            interaction.user.id == scrim["creator_id"]
            or interaction.user.guild_permissions.manage_channels
        )

    async def _create_scrim_channels(
        self, guild: discord.Guild, scrim_id: int, game: str, host: discord.Member
    ) -> tuple[discord.CategoryChannel, discord.TextChannel]:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
            host: discord.PermissionOverwrite(view_channel=True, connect=True),
        }
        category = await guild.create_category(
            f"🎮 Scrim #{scrim_id} — {game}"[:100], overwrites=overwrites
        )
        lobby = await category.create_text_channel("lobby")
        await category.create_text_channel("team-a")
        await category.create_text_channel("team-b")
        await category.create_voice_channel("🔊 Lobby VC")
        await category.create_voice_channel("🔵 Team A VC")
        await category.create_voice_channel("🔴 Team B VC")
        return category, lobby

    async def _cleanup_channels(self, scrim) -> None:
        guild = self.bot.get_guild(scrim["guild_id"])
        category = guild.get_channel(scrim["category_id"]) if guild else None
        if category is None:
            return
        for channel in list(category.channels):
            try:
                await channel.delete(reason=f"Scrim #{scrim['id']} ended")
            except discord.HTTPException:
                pass
        try:
            await category.delete(reason=f"Scrim #{scrim['id']} ended")
        except discord.HTTPException:
            pass

    # ---- commands ---------------------------------------------------------

    @scrim.command(name="create", description="Create a new scrim with its own private channels")
    @app_commands.describe(
        game="The game being played (e.g. Valorant, CS2, Rocket League)",
        team_size="Players per team (1-10)",
        in_hours="Optional: schedule the scrim this many hours from now",
    )
    async def create(
        self,
        interaction: discord.Interaction,
        game: str,
        team_size: app_commands.Range[int, 1, 10],
        in_hours: app_commands.Range[float, 0.0, 168.0] | None = None,
    ) -> None:
        config = await self.bot.db.get_config(interaction.guild_id)
        max_open = config["max_open_scrims"] if config else 5
        open_scrims = await self.bot.db.list_scrims(interaction.guild_id, ("open", "live"))
        if len(open_scrims) >= max_open:
            await interaction.response.send_message(
                f"This server already has {max_open} active scrims. "
                "Finish or cancel one first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        scheduled_for = str(utcnow_ts() + int(in_hours * 3600)) if in_hours else None
        scrim_id = await self.bot.db.create_scrim(
            interaction.guild_id, interaction.user.id, game, team_size, scheduled_for
        )

        try:
            category, lobby = await self._create_scrim_channels(
                interaction.guild, scrim_id, game, interaction.user
            )
        except discord.Forbidden:
            await self.bot.db.update_scrim(scrim_id, status="cancelled")
            await interaction.followup.send(
                "I need the **Manage Channels** permission to create scrim channels.",
                ephemeral=True,
            )
            return

        announce_channel_id = (
            config["announce_channel_id"] if config and config["announce_channel_id"] else None
        )
        announce_channel = (
            interaction.guild.get_channel(announce_channel_id)
            if announce_channel_id
            else interaction.channel
        ) or interaction.channel

        await self.bot.db.update_scrim(
            scrim_id,
            category_id=category.id,
            lobby_channel_id=lobby.id,
            announce_channel_id=announce_channel.id,
        )
        scrim = await self.bot.db.get_scrim(scrim_id)
        embed = await build_scrim_embed(self.bot, scrim)
        message = await announce_channel.send(
            content=(
                f"<@&{config['scrim_role_id']}> a new scrim is up!"
                if config and config["scrim_role_id"]
                else None
            ),
            embed=embed,
            view=ScrimView(scrim_id),
        )
        await self.bot.db.update_scrim(scrim_id, announce_message_id=message.id)

        await lobby.send(
            f"Welcome to **Scrim #{scrim_id} — {game}**! "
            f"Host: {interaction.user.mention}. Team channels are on the left; "
            "they unlock for players as they join via the announcement buttons."
        )
        await interaction.followup.send(
            f"Scrim **#{scrim_id}** created — announcement posted in "
            f"{announce_channel.mention}, private channels under **{category.name}**."
        )

    @scrim.command(name="list", description="List active scrims in this server")
    async def list_cmd(self, interaction: discord.Interaction) -> None:
        scrims = await self.bot.db.list_scrims(interaction.guild_id, ("open", "live"))
        if not scrims:
            await interaction.response.send_message(
                "No active scrims. Start one with `/scrim create`!", ephemeral=True
            )
            return
        embed = discord.Embed(title="Active Scrims", color=discord.Color.green())
        for s in scrims:
            players = await self.bot.db.get_players(s["id"])
            embed.add_field(
                name=f"#{s['id']} — {s['game']} ({s['team_size']}v{s['team_size']})",
                value=(
                    f"Status: **{s['status']}** · Players: {len(players)}/"
                    f"{s['team_size'] * 2} · Host: <@{s['creator_id']}>"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @scrim.command(name="start", description="Start the scrim (host only)")
    @app_commands.describe(scrim_id="The scrim number to start")
    async def start(self, interaction: discord.Interaction, scrim_id: int) -> None:
        scrim = await self.bot.db.get_scrim(scrim_id)
        if not scrim or scrim["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("Scrim not found.", ephemeral=True)
            return
        if not await self._is_host_or_mod(interaction, scrim):
            await interaction.response.send_message(
                "Only the host or a moderator can start this scrim.", ephemeral=True
            )
            return
        if scrim["status"] != "open":
            await interaction.response.send_message(
                f"Scrim #{scrim_id} is {scrim['status']}, not open.", ephemeral=True
            )
            return

        players = await self.bot.db.get_players(scrim_id)
        not_ready = [p for p in players if not p["ready"]]
        note = (
            f"\n⚠️ {len(not_ready)} player(s) had not readied up." if not_ready else ""
        )
        await self.bot.db.update_scrim(scrim_id, status="live")
        await refresh_announcement(self.bot, scrim_id)

        lobby = interaction.guild.get_channel(scrim["lobby_channel_id"])
        if lobby:
            mentions = " ".join(f"<@{p['user_id']}>" for p in players)
            await lobby.send(
                f"🏁 **Scrim #{scrim_id} is LIVE!** {mentions}\n"
                f"Hop into your team voice channels. GLHF!{note}"
            )
        await interaction.response.send_message(f"Scrim #{scrim_id} is now **live**!{note}")

    @scrim.command(name="report", description="Report the final score and close the scrim")
    @app_commands.describe(
        scrim_id="The scrim number", team_a_score="Team A's score", team_b_score="Team B's score"
    )
    async def report(
        self,
        interaction: discord.Interaction,
        scrim_id: int,
        team_a_score: app_commands.Range[int, 0, 999],
        team_b_score: app_commands.Range[int, 0, 999],
    ) -> None:
        scrim = await self.bot.db.get_scrim(scrim_id)
        if not scrim or scrim["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("Scrim not found.", ephemeral=True)
            return
        if not await self._is_host_or_mod(interaction, scrim):
            await interaction.response.send_message(
                "Only the host or a moderator can report the score.", ephemeral=True
            )
            return
        if scrim["status"] not in ("open", "live"):
            await interaction.response.send_message(
                f"Scrim #{scrim_id} is already {scrim['status']}.", ephemeral=True
            )
            return

        await interaction.response.defer()
        await self.bot.db.update_scrim(
            scrim_id,
            status="finished",
            team_a_score=team_a_score,
            team_b_score=team_b_score,
        )

        players = await self.bot.db.get_players(scrim_id)
        if team_a_score == team_b_score:
            outcomes = {"a": "draw", "b": "draw"}
        elif team_a_score > team_b_score:
            outcomes = {"a": "win", "b": "loss"}
        else:
            outcomes = {"a": "loss", "b": "win"}
        for p in players:
            if p["team"] in outcomes:
                await self.bot.db.record_result(
                    interaction.guild_id, p["user_id"], outcomes[p["team"]]
                )

        await refresh_announcement(self.bot, scrim_id)
        await self._cleanup_channels(scrim)

        winner = (
            "It's a **draw**!"
            if team_a_score == team_b_score
            else f"**{'Team A' if team_a_score > team_b_score else 'Team B'} wins!**"
        )
        await interaction.followup.send(
            f"Scrim #{scrim_id} finished **{team_a_score} : {team_b_score}** — {winner} "
            "Stats recorded and scrim channels cleaned up. GGs! 🎉"
        )

    @scrim.command(name="cancel", description="Cancel a scrim and delete its channels")
    @app_commands.describe(scrim_id="The scrim number to cancel")
    async def cancel(self, interaction: discord.Interaction, scrim_id: int) -> None:
        scrim = await self.bot.db.get_scrim(scrim_id)
        if not scrim or scrim["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("Scrim not found.", ephemeral=True)
            return
        if not await self._is_host_or_mod(interaction, scrim):
            await interaction.response.send_message(
                "Only the host or a moderator can cancel this scrim.", ephemeral=True
            )
            return
        if scrim["status"] in ("finished", "cancelled"):
            await interaction.response.send_message(
                f"Scrim #{scrim_id} is already {scrim['status']}.", ephemeral=True
            )
            return

        await interaction.response.defer()
        await self.bot.db.update_scrim(scrim_id, status="cancelled")
        await refresh_announcement(self.bot, scrim_id)
        await self._cleanup_channels(scrim)
        await interaction.followup.send(f"Scrim #{scrim_id} cancelled and channels removed.")

    @scrim.command(name="kick", description="Remove a player from a scrim (host only)")
    @app_commands.describe(scrim_id="The scrim number", player="The player to remove")
    async def kick(
        self, interaction: discord.Interaction, scrim_id: int, player: discord.Member
    ) -> None:
        scrim = await self.bot.db.get_scrim(scrim_id)
        if not scrim or scrim["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("Scrim not found.", ephemeral=True)
            return
        if not await self._is_host_or_mod(interaction, scrim):
            await interaction.response.send_message(
                "Only the host or a moderator can kick players.", ephemeral=True
            )
            return
        removed = await self.bot.db.remove_player(scrim_id, player.id)
        if not removed:
            await interaction.response.send_message(
                f"{player.mention} is not in scrim #{scrim_id}.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Removed {player.mention} from scrim #{scrim_id}."
        )
        await refresh_announcement(self.bot, scrim_id)
        await sync_channel_permissions(self.bot, scrim_id)


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(Scrims(bot))
