"""Scrim lifecycle: GUI panel, creation wizard, team join buttons, host controls."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import rcon
from settings import server_settings

if TYPE_CHECKING:
    from bot import ScrimBot

TEAM_NAMES = {"a": "Team A", "b": "Team B"}

GAME = "CS2"

MAP_CHOICES = [
    ("Mirage", "de_mirage"),
    ("Dust II", "de_dust2"),
    ("Inferno", "de_inferno"),
    ("Nuke", "de_nuke"),
    ("Ancient", "de_ancient"),
    ("Anubis", "de_anubis"),
    ("Overpass", "de_overpass"),
    ("Vertigo", "de_vertigo"),
    ("Train", "de_train"),
    ("Office", "cs_office"),
]


def connect_string(config) -> str | None:
    srv = server_settings(config)
    if not srv:
        return None
    line = f"connect {srv['host']}:{srv['port']}"
    if srv["password"]:
        line += f"; password {srv['password']}"
    return line


def utcnow_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def is_host_or_mod(user: discord.Member, scrim) -> bool:
    return user.id == scrim["creator_id"] or user.guild_permissions.manage_channels


# ---------------------------------------------------------------------------
# Embeds & announcement upkeep
# ---------------------------------------------------------------------------


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
    embed.add_field(name="Map", value=scrim["map"] or "*server default*", inline=True)
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

    if scrim["status"] == "open":
        embed.set_footer(text="Join a team, ready up, and the host starts the match.")
    elif scrim["status"] == "live":
        embed.set_footer(text="Match in progress — the host reports the score when done.")
    return embed


async def refresh_announcement(bot: ScrimBot, scrim_id: int) -> None:
    scrim = await bot.db.get_scrim(scrim_id)
    if not scrim or not scrim["announce_channel_id"] or not scrim["announce_message_id"]:
        return
    channel = bot.get_channel(scrim["announce_channel_id"])
    if channel is None:
        return
    if scrim["status"] == "open":
        view = OpenScrimView(scrim_id)
    elif scrim["status"] == "live":
        view = LiveScrimView(scrim_id)
    else:
        view = None
    try:
        message = await channel.fetch_message(scrim["announce_message_id"])
        await message.edit(embed=await build_scrim_embed(bot, scrim), view=view)
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
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
    }
    for p in players:
        member = guild.get_member(p["user_id"])
        if member:
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


# ---------------------------------------------------------------------------
# Shared lifecycle actions (used by both buttons and slash commands)
# ---------------------------------------------------------------------------


async def create_scrim_channels(
    guild: discord.Guild, scrim_id: int, game: str, host: discord.Member
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


async def cleanup_channels(bot: ScrimBot, scrim) -> None:
    guild = bot.get_guild(scrim["guild_id"])
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


async def launch_scrim(
    bot: ScrimBot,
    guild: discord.Guild,
    host: discord.Member,
    fallback_channel: discord.abc.Messageable,
    map_name: str | None,
    team_size: int,
    in_hours: float | None,
) -> tuple[int | None, str]:
    """Create a scrim end-to-end; returns (scrim_id, status message)."""
    config = await bot.db.get_config(guild.id)
    max_open = config["max_open_scrims"] if config else 5
    open_scrims = await bot.db.list_scrims(guild.id, ("open", "live"))
    if len(open_scrims) >= max_open:
        return None, (
            f"❌ This server already has {max_open} active scrims. "
            "Finish or cancel one first."
        )

    scheduled_for = str(utcnow_ts() + int(in_hours * 3600)) if in_hours else None
    scrim_id = await bot.db.create_scrim(
        guild.id, host.id, GAME, team_size, scheduled_for
    )
    if map_name:
        await bot.db.update_scrim(scrim_id, map=map_name)

    label = f"{GAME} ({map_name})" if map_name else GAME
    try:
        category, lobby = await create_scrim_channels(guild, scrim_id, label, host)
    except discord.Forbidden:
        await bot.db.update_scrim(scrim_id, status="cancelled")
        return None, "❌ I need the **Manage Channels** permission to create scrim channels."

    announce_channel = None
    if config and config["announce_channel_id"]:
        announce_channel = guild.get_channel(config["announce_channel_id"])
    announce_channel = announce_channel or fallback_channel

    await bot.db.update_scrim(
        scrim_id,
        category_id=category.id,
        lobby_channel_id=lobby.id,
        announce_channel_id=announce_channel.id,
    )
    scrim = await bot.db.get_scrim(scrim_id)
    message = await announce_channel.send(
        content=(
            f"<@&{config['scrim_role_id']}> a new scrim is up!"
            if config and config["scrim_role_id"]
            else None
        ),
        embed=await build_scrim_embed(bot, scrim),
        view=OpenScrimView(scrim_id),
    )
    await bot.db.update_scrim(scrim_id, announce_message_id=message.id)

    await lobby.send(
        f"Welcome to **Scrim #{scrim_id} — {label}**! "
        f"Host: {host.mention}. Team channels are on the left; "
        "they unlock for players as they join via the announcement buttons. "
        "The server connect info drops here when the host starts the match."
    )
    return scrim_id, (
        f"✅ Scrim **#{scrim_id}** created — announcement posted in "
        f"{announce_channel.mention}, private channels under **{category.name}**."
    )


async def start_scrim(bot: ScrimBot, guild: discord.Guild, scrim) -> str:
    players = await bot.db.get_players(scrim["id"])
    not_ready = [p for p in players if not p["ready"]]
    note = f"\n⚠️ {len(not_ready)} player(s) had not readied up." if not_ready else ""
    await bot.db.update_scrim(scrim["id"], status="live")
    await refresh_announcement(bot, scrim["id"])

    config = await bot.db.get_config(guild.id)
    srv = server_settings(config)

    # Switch the server to the picked map if RCON is configured.
    if srv and srv["rcon_password"] and scrim["map"]:
        try:
            await rcon.run_command(
                srv["host"],
                srv["port"],
                srv["rcon_password"],
                f"changelevel {scrim['map']}",
            )
            note += f"\n🗺️ Server switched to `{scrim['map']}` via RCON."
        except rcon.RconError as e:
            note += f"\n⚠️ RCON map change failed: {e}"

    lobby = guild.get_channel(scrim["lobby_channel_id"])
    if lobby:
        mentions = " ".join(f"<@{p['user_id']}>" for p in players)
        connect = connect_string(config)
        connect_line = (
            f"\n🖥️ Connect to the server:\n```\n{connect}\n```"
            if connect
            else "\n⚠️ No server configured — an admin can set it with `/scrimconfig server`."
        )
        await lobby.send(
            f"🏁 **Scrim #{scrim['id']} is LIVE!** {mentions}\n"
            f"Hop into your team voice channels. GLHF!{connect_line}{note}"
        )
    return f"🏁 Scrim #{scrim['id']} is now **live**!{note}"


async def finish_scrim(
    bot: ScrimBot, guild: discord.Guild, scrim, team_a_score: int, team_b_score: int
) -> str:
    await bot.db.update_scrim(
        scrim["id"],
        status="finished",
        team_a_score=team_a_score,
        team_b_score=team_b_score,
    )
    players = await bot.db.get_players(scrim["id"])
    if team_a_score == team_b_score:
        outcomes = {"a": "draw", "b": "draw"}
    elif team_a_score > team_b_score:
        outcomes = {"a": "win", "b": "loss"}
    else:
        outcomes = {"a": "loss", "b": "win"}
    for p in players:
        if p["team"] in outcomes:
            await bot.db.record_result(guild.id, p["user_id"], outcomes[p["team"]])

    extra: list[str] = []
    is_draw = team_a_score == team_b_score
    winner_team = None if is_draw else ("a" if team_a_score > team_b_score else "b")

    # Elo: team-average expected score, K=32, same delta for every player.
    team_a_ids = [p["user_id"] for p in players if p["team"] == "a"]
    team_b_ids = [p["user_id"] for p in players if p["team"] == "b"]
    if team_a_ids and team_b_ids:

        async def avg_elo(ids: list[int]) -> float:
            total = 0
            for uid in ids:
                row = await bot.db.get_stats(guild.id, uid)
                total += row["elo"] if row else 1000
            return total / len(ids)

        elo_a, elo_b = await avg_elo(team_a_ids), await avg_elo(team_b_ids)
        score_a = 0.5 if is_draw else (1.0 if winner_team == "a" else 0.0)
        expected_a = 1 / (1 + 10 ** ((elo_b - elo_a) / 400))
        delta = round(32 * (score_a - expected_a))
        for uid in team_a_ids:
            await bot.db.adjust_elo(guild.id, uid, delta)
        for uid in team_b_ids:
            await bot.db.adjust_elo(guild.id, uid, -delta)
        if delta:
            extra.append(f"📈 Elo: Team A {delta:+d}, Team B {-delta:+d}.")

    # Coins + XP for playing.
    coin_reward = {"win": 100, "loss": 25, "draw": 50}
    xp_reward = {"win": 100, "loss": 40, "draw": 60}
    for p in players:
        if p["team"] in outcomes:
            outcome = outcomes[p["team"]]
            await bot.db.add_coins(guild.id, p["user_id"], coin_reward[outcome])
            await bot.db.add_xp(guild.id, p["user_id"], xp_reward[outcome])
    if outcomes:
        extra.append("🪙 Coins and XP paid out to all players.")

    # Settle spectator bets: 2x on a win, refund on a draw.
    bets = await bot.db.get_bets(scrim["id"])
    if bets:
        for bet in bets:
            if is_draw:
                await bot.db.add_coins(guild.id, bet["user_id"], bet["amount"])
            elif bet["team"] == winner_team:
                await bot.db.add_coins(guild.id, bet["user_id"], bet["amount"] * 2)
        extra.append(
            f"🎫 {len(bets)} bet(s) settled"
            + (" (draw — all refunded)." if is_draw else ".")
        )
    await bot.db.clear_bets(scrim["id"])

    # Settle a duel wager: winner takes the pot, draw refunds both.
    duel = await bot.db.get_duel(scrim["id"])
    if duel:
        pot = duel["wager"] * 2
        if is_draw:
            await bot.db.add_coins(guild.id, duel["challenger_id"], duel["wager"])
            await bot.db.add_coins(guild.id, duel["target_id"], duel["wager"])
            extra.append("⚔️ Duel drawn — wagers refunded.")
        else:
            winner_id = (
                duel["challenger_id"] if winner_team == "a" else duel["target_id"]
            )
            await bot.db.add_coins(guild.id, winner_id, pot)
            extra.append(f"⚔️ <@{winner_id}> takes the **{pot:,}** coin duel pot!")
        await bot.db.settle_duel(scrim["id"])

    await refresh_announcement(bot, scrim["id"])
    await cleanup_channels(bot, scrim)

    winner = (
        "It's a **draw**!"
        if is_draw
        else f"**{'Team A' if winner_team == 'a' else 'Team B'} wins!**"
    )
    lines = "\n".join(extra)
    return (
        f"🏆 Scrim #{scrim['id']} finished **{team_a_score} : {team_b_score}** — {winner} "
        f"GGs! 🎉\n{lines}"
    )


async def cancel_scrim(bot: ScrimBot, scrim) -> str:
    await bot.db.update_scrim(scrim["id"], status="cancelled")
    note = ""
    bets = await bot.db.get_bets(scrim["id"])
    for bet in bets:
        await bot.db.add_coins(scrim["guild_id"], bet["user_id"], bet["amount"])
    if bets:
        note += f" {len(bets)} bet(s) refunded."
    await bot.db.clear_bets(scrim["id"])
    duel = await bot.db.get_duel(scrim["id"])
    if duel:
        await bot.db.add_coins(scrim["guild_id"], duel["challenger_id"], duel["wager"])
        await bot.db.add_coins(scrim["guild_id"], duel["target_id"], duel["wager"])
        await bot.db.settle_duel(scrim["id"])
        note += " Duel wagers refunded."
    await refresh_announcement(bot, scrim["id"])
    await cleanup_channels(bot, scrim)
    return f"🗑️ Scrim #{scrim['id']} cancelled and channels removed.{note}"


# ---------------------------------------------------------------------------
# Player buttons (persistent via DynamicItem)
# ---------------------------------------------------------------------------


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
                emoji="🔵" if team == "a" else "🔴",
                style=discord.ButtonStyle.primary,
                custom_id=f"scrim:join:{team}:{scrim_id}",
                row=0,
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
                emoji="🚪",
                style=discord.ButtonStyle.secondary,
                custom_id=f"scrim:leave:{scrim_id}",
                row=0,
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
                label="Ready",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"scrim:ready:{scrim_id}",
                row=0,
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


# ---------------------------------------------------------------------------
# Host control buttons (persistent via DynamicItem)
# ---------------------------------------------------------------------------


async def _host_gate(interaction: discord.Interaction, scrim_id: int):
    """Fetch the scrim and verify the clicker may manage it. Returns scrim or None."""
    bot: ScrimBot = interaction.client
    scrim = await bot.db.get_scrim(scrim_id)
    if not scrim or scrim["guild_id"] != interaction.guild_id:
        await interaction.response.send_message("Scrim not found.", ephemeral=True)
        return None
    if not is_host_or_mod(interaction.user, scrim):
        await interaction.response.send_message(
            "Only the host or a moderator can use the host controls.", ephemeral=True
        )
        return None
    return scrim


class StartButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"scrim:start:(?P<id>\d+)",
):
    def __init__(self, scrim_id: int) -> None:
        self.scrim_id = scrim_id
        super().__init__(
            discord.ui.Button(
                label="Start Match",
                emoji="🏁",
                style=discord.ButtonStyle.success,
                custom_id=f"scrim:start:{scrim_id}",
                row=1,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        scrim = await _host_gate(interaction, self.scrim_id)
        if scrim is None:
            return
        if scrim["status"] != "open":
            await interaction.response.send_message(
                f"Scrim #{self.scrim_id} is {scrim['status']}, not open.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        result = await start_scrim(interaction.client, interaction.guild, scrim)
        await interaction.followup.send(result, ephemeral=True)


class ReportScoreModal(discord.ui.Modal):
    team_a = discord.ui.TextInput(
        label="Team A score", placeholder="e.g. 13", max_length=4
    )
    team_b = discord.ui.TextInput(
        label="Team B score", placeholder="e.g. 7", max_length=4
    )

    def __init__(self, scrim_id: int) -> None:
        super().__init__(title=f"Report score — Scrim #{scrim_id}")
        self.scrim_id = scrim_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            a = int(str(self.team_a.value).strip())
            b = int(str(self.team_b.value).strip())
            if a < 0 or b < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Scores must be non-negative whole numbers.", ephemeral=True
            )
            return

        bot: ScrimBot = interaction.client
        scrim = await bot.db.get_scrim(self.scrim_id)
        if not scrim or scrim["status"] not in ("open", "live"):
            await interaction.response.send_message(
                "This scrim can no longer be reported.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        result = await finish_scrim(bot, interaction.guild, scrim, a, b)
        await interaction.followup.send(result, ephemeral=True)
        announce = interaction.guild.get_channel(scrim["announce_channel_id"])
        if announce:
            await announce.send(result)


class ReportButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"scrim:report:(?P<id>\d+)",
):
    def __init__(self, scrim_id: int) -> None:
        self.scrim_id = scrim_id
        super().__init__(
            discord.ui.Button(
                label="Report Score",
                emoji="🏆",
                style=discord.ButtonStyle.primary,
                custom_id=f"scrim:report:{scrim_id}",
                row=1,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        scrim = await _host_gate(interaction, self.scrim_id)
        if scrim is None:
            return
        if scrim["status"] not in ("open", "live"):
            await interaction.response.send_message(
                f"Scrim #{self.scrim_id} is already {scrim['status']}.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ReportScoreModal(self.scrim_id))


class CancelButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"scrim:cancel:(?P<id>\d+)",
):
    def __init__(self, scrim_id: int) -> None:
        self.scrim_id = scrim_id
        super().__init__(
            discord.ui.Button(
                label="Cancel",
                emoji="🗑️",
                style=discord.ButtonStyle.danger,
                custom_id=f"scrim:cancel:{scrim_id}",
                row=1,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        scrim = await _host_gate(interaction, self.scrim_id)
        if scrim is None:
            return
        if scrim["status"] in ("finished", "cancelled"):
            await interaction.response.send_message(
                f"Scrim #{self.scrim_id} is already {scrim['status']}.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        result = await cancel_scrim(interaction.client, scrim)
        await interaction.followup.send(result, ephemeral=True)


class ConnectButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"scrim:connect:(?P<id>\d+)",
):
    def __init__(self, scrim_id: int) -> None:
        self.scrim_id = scrim_id
        super().__init__(
            discord.ui.Button(
                label="Connect Info",
                emoji="🖥️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"scrim:connect:{scrim_id}",
                row=0,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScrimBot = interaction.client
        scrim = await bot.db.get_scrim(self.scrim_id)
        if not scrim:
            await interaction.response.send_message("Scrim not found.", ephemeral=True)
            return
        players = await bot.db.get_players(self.scrim_id)
        in_scrim = any(p["user_id"] == interaction.user.id for p in players)
        if not (in_scrim or is_host_or_mod(interaction.user, scrim)):
            await interaction.response.send_message(
                "Join the scrim to get the server connect info.", ephemeral=True
            )
            return
        connect = connect_string(await bot.db.get_config(interaction.guild_id))
        if not connect:
            await interaction.response.send_message(
                "No server configured — an admin can set it with `/scrimconfig server`.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Paste this into your console:\n```\n{connect}\n```", ephemeral=True
        )


class OpenScrimView(discord.ui.View):
    """Announcement buttons while a scrim is open: player row + host row."""

    def __init__(self, scrim_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(JoinButton("a", scrim_id))
        self.add_item(JoinButton("b", scrim_id))
        self.add_item(ReadyButton(scrim_id))
        self.add_item(LeaveButton(scrim_id))
        self.add_item(StartButton(scrim_id))
        self.add_item(ReportButton(scrim_id))
        self.add_item(CancelButton(scrim_id))


class LiveScrimView(discord.ui.View):
    """Announcement buttons while a scrim is live: host controls only."""

    def __init__(self, scrim_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(ConnectButton(scrim_id))
        self.add_item(ReportButton(scrim_id))
        self.add_item(CancelButton(scrim_id))


# ---------------------------------------------------------------------------
# Creation wizard (ephemeral GUI with select menus)
# ---------------------------------------------------------------------------


class ScrimWizard(discord.ui.View):
    """Ephemeral step-by-step scrim creator driven entirely by selects/buttons."""

    def __init__(self, bot: ScrimBot, user_id: int) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.user_id = user_id
        self.map_name: str | None = None
        self.team_size: int | None = None
        self.in_hours: float | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This wizard belongs to someone else — press **Create Scrim** "
                "on the panel to get your own.",
                ephemeral=True,
            )
            return False
        return True

    @staticmethod
    def make_embed() -> discord.Embed:
        embed = discord.Embed(
            title="🛠️ Create a CS2 scrim",
            description=(
                "1️⃣ Pick the **map** (or leave it — server default)\n"
                "2️⃣ Pick the **team size**\n"
                "3️⃣ Optionally pick a **start time**\n\n"
                "Then hit **✅ Create** — I'll set up private lobby, team text, "
                "and voice channels, and post a sign-up announcement. The server "
                "connect info drops when the host starts the match."
            ),
            color=discord.Color.green(),
        )
        return embed

    @discord.ui.select(
        placeholder="🗺️ Choose a map (optional)…",
        row=0,
        options=[
            discord.SelectOption(label=label, value=value, description=value)
            for label, value in MAP_CHOICES
        ],
    )
    async def map_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        self.map_name = select.values[0]
        select.placeholder = f"Map: {self.map_name}"
        await interaction.response.edit_message(view=self)

    @discord.ui.select(
        placeholder="👥 Choose a team size…",
        row=1,
        options=[
            discord.SelectOption(label=f"{n}v{n}", value=str(n))
            for n in range(1, 11)
        ],
    )
    async def size_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        self.team_size = int(select.values[0])
        select.placeholder = f"Team size: {self.team_size}v{self.team_size}"
        await interaction.response.edit_message(view=self)

    @discord.ui.select(
        placeholder="🕒 Start time (optional, default: now)…",
        row=2,
        options=[
            discord.SelectOption(label="Now", value="0"),
            discord.SelectOption(label="In 1 hour", value="1"),
            discord.SelectOption(label="In 2 hours", value="2"),
            discord.SelectOption(label="In 3 hours", value="3"),
            discord.SelectOption(label="In 6 hours", value="6"),
            discord.SelectOption(label="In 12 hours", value="12"),
            discord.SelectOption(label="Tomorrow (24h)", value="24"),
        ],
    )
    async def time_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        hours = float(select.values[0])
        self.in_hours = hours or None
        label = "Now" if not hours else f"In {select.values[0]}h"
        select.placeholder = f"Start time: {label}"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Create", emoji="✅", style=discord.ButtonStyle.success, row=3)
    async def create_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.team_size is None:
            await interaction.response.send_message(
                "Pick a **team size** first.", ephemeral=True
            )
            return
        await interaction.response.edit_message(
            content="⏳ Setting up your scrim…", embed=None, view=None
        )
        self.stop()
        _, result = await launch_scrim(
            self.bot,
            interaction.guild,
            interaction.user,
            interaction.channel,
            self.map_name,
            self.team_size,
            self.in_hours,
        )
        await interaction.edit_original_response(content=result)

    @discord.ui.button(label="Discard", emoji="✖️", style=discord.ButtonStyle.secondary, row=3)
    async def discard_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Scrim creation discarded.", embed=None, view=None
        )


class PanelButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"scrim:panel:create",
):
    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Create Scrim",
                emoji="🎮",
                style=discord.ButtonStyle.success,
                custom_id="scrim:panel:create",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        wizard = ScrimWizard(interaction.client, interaction.user.id)
        await interaction.response.send_message(
            embed=ScrimWizard.make_embed(), view=wizard, ephemeral=True
        )


class PanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(PanelButton())


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


class Scrims(commands.Cog):
    """Create and manage scrim matches."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(
            JoinButton,
            LeaveButton,
            ReadyButton,
            StartButton,
            ReportButton,
            CancelButton,
            ConnectButton,
            PanelButton,
        )

    scrim = app_commands.Group(name="scrim", description="Create and manage scrims")

    @scrim.command(
        name="panel",
        description="Post the scrim panel with a Create Scrim button (admin)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="💣 CS2 Scrim Hub",
            description=(
                "Ready to run a match on our server?\n\n"
                "Hit **Create Scrim** below and a step-by-step wizard walks you "
                "through picking the map, team size, and start time. The bot then "
                "creates private lobby, team text, and voice channels, and posts a "
                "sign-up announcement where players join with one click.\n\n"
                "When the host hits **Start Match**, the server connect info is "
                "posted in the scrim lobby and the map is loaded automatically."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="Scrims get their own private channels — cleaned up automatically.")
        await interaction.channel.send(embed=embed, view=PanelView())
        await interaction.response.send_message("Scrim panel posted. 📌", ephemeral=True)

    @scrim.command(name="create", description="Open the scrim creation wizard")
    async def create(self, interaction: discord.Interaction) -> None:
        wizard = ScrimWizard(self.bot, interaction.user.id)
        await interaction.response.send_message(
            embed=ScrimWizard.make_embed(), view=wizard, ephemeral=True
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

    @scrim.command(name="kick", description="Remove a player from a scrim (host only)")
    @app_commands.describe(scrim_id="The scrim number", player="The player to remove")
    async def kick(
        self, interaction: discord.Interaction, scrim_id: int, player: discord.Member
    ) -> None:
        scrim = await self.bot.db.get_scrim(scrim_id)
        if not scrim or scrim["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("Scrim not found.", ephemeral=True)
            return
        if not is_host_or_mod(interaction.user, scrim):
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
