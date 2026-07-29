"""Security: full join/leave logging and anti-nuke protection.

Anti-nuke watches the audit log for bursts of destructive actions
(channel/role deletions, bans, kicks). Anyone who crosses a threshold —
human or bot — is stopped: banned if possible, otherwise stripped of
roles. The server owner, the bot itself, and whitelisted users are exempt.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ScrimBot

# action -> (max actions, within seconds)
THRESHOLDS = {
    "channel_delete": (10, 60),
    "role_delete": (5, 60),
    "ban": (7, 60),
    "kick": (8, 60),
}
PUNISH_COOLDOWN = 300  # don't re-punish the same actor for 5 minutes


def describe_flags(user: discord.User | discord.Member) -> str:
    flags = [name.replace("_", " ").title() for name, on in user.public_flags if on]
    return ", ".join(flags) if flags else "none"


async def build_join_embed(bot: ScrimBot, member: discord.Member) -> discord.Embed:
    created = member.created_at
    age_days = (datetime.now(timezone.utc) - created).days
    embed = discord.Embed(
        title="📥 Member joined",
        color=discord.Color.green() if age_days >= 7 else discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User", value=f"{member.mention}\n`{member}`", inline=True)
    embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
    embed.add_field(
        name="Display name", value=member.global_name or member.name, inline=True
    )
    embed.add_field(
        name="Account created",
        value=f"<t:{int(created.timestamp())}:F>\n(<t:{int(created.timestamp())}:R>)",
        inline=True,
    )
    embed.add_field(
        name="Member #", value=str(member.guild.member_count), inline=True
    )
    embed.add_field(name="Bot", value="🤖 yes" if member.bot else "no", inline=True)
    embed.add_field(name="Badges", value=describe_flags(member), inline=False)
    embed.add_field(name="Avatar", value=f"[link]({member.display_avatar.url})", inline=True)

    # Banner and accent color are only present on a full user fetch.
    try:
        full_user = await bot.fetch_user(member.id)
        if full_user.banner:
            embed.set_image(url=full_user.banner.url)
        if full_user.accent_color:
            embed.color = full_user.accent_color
    except discord.HTTPException:
        pass

    if age_days < 7:
        embed.set_footer(text=f"⚠️ New account — created {age_days} day(s) ago")
    return embed


class Security(commands.Cog):
    """Join logging and nuke protection."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot
        # (guild_id, user_id, action) -> deque[timestamp]
        self._actions: dict[tuple[int, int, str], deque[float]] = defaultdict(deque)
        self._punished: dict[tuple[int, int], float] = {}

    # ---- join / leave logging --------------------------------------------

    async def _log_channel(self, guild_id: int) -> discord.TextChannel | None:
        config = await self.bot.db.get_config(guild_id)
        if not config or not config["log_channel_id"]:
            return None
        return self.bot.get_channel(config["log_channel_id"])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self.bot.db.log_member(member.guild.id, member.id, str(member))
        channel = await self._log_channel(member.guild.id)
        if channel is None:
            return
        try:
            await channel.send(embed=await build_join_embed(self.bot, member))
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        channel = await self._log_channel(member.guild.id)
        if channel is None:
            return
        roles = [r.mention for r in member.roles if r != member.guild.default_role]
        embed = discord.Embed(
            title="📤 Member left",
            color=discord.Color.dark_grey(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"`{member}`", inline=True)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        if member.joined_at:
            embed.add_field(
                name="Was member since",
                value=f"<t:{int(member.joined_at.timestamp())}:R>",
                inline=True,
            )
        embed.add_field(
            name="Roles", value=" ".join(roles) if roles else "none", inline=False
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ---- anti-nuke --------------------------------------------------------

    async def _antinuke_enabled(self, guild_id: int) -> bool:
        config = await self.bot.db.get_config(guild_id)
        return bool(config and config["antinuke"])

    async def _find_actor(
        self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int
    ) -> discord.Member | None:
        """Look up who performed the audit-logged action just now."""
        try:
            async for entry in guild.audit_logs(limit=6, action=action):
                if (
                    entry.target
                    and entry.target.id == target_id
                    and (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 15
                ):
                    if isinstance(entry.user, discord.Member):
                        return entry.user
                    return guild.get_member(entry.user.id) if entry.user else None
        except discord.Forbidden:
            return None
        return None

    async def _record(self, guild: discord.Guild, actor: discord.Member, kind: str) -> None:
        if actor is None or actor.id in (guild.owner_id, self.bot.user.id):
            return
        if actor.id in await self.bot.db.get_whitelist(guild.id):
            return

        key = (guild.id, actor.id, kind)
        now = time.monotonic()
        limit, window = THRESHOLDS[kind]
        history = self._actions[key]
        history.append(now)
        while history and now - history[0] > window:
            history.popleft()
        if len(history) < limit:
            return

        last = self._punished.get((guild.id, actor.id), 0)
        if now - last < PUNISH_COOLDOWN:
            return
        self._punished[(guild.id, actor.id)] = now
        await self._punish(guild, actor, kind, len(history), window)

    async def _punish(
        self, guild: discord.Guild, actor: discord.Member, kind: str, count: int, window: int
    ) -> None:
        action_taken = "no permissions to act ⚠️"
        me = guild.me
        try:
            if me.guild_permissions.ban_members and actor.top_role < me.top_role:
                await guild.ban(
                    actor,
                    reason=f"Anti-nuke: {count}x {kind} in {window}s",
                    delete_message_days=0,
                )
                action_taken = "**banned** 🔨"
            elif me.guild_permissions.manage_roles:
                removable = [
                    r for r in actor.roles
                    if r != guild.default_role and r < me.top_role
                ]
                if removable:
                    await actor.remove_roles(
                        *removable, reason=f"Anti-nuke: {count}x {kind} in {window}s"
                    )
                    action_taken = "**stripped of all roles** 🔒"
        except discord.HTTPException:
            pass

        channel = await self._log_channel(guild.id)
        if channel is None:
            channel = guild.system_channel
        if channel:
            embed = discord.Embed(
                title="🚨 ANTI-NUKE TRIGGERED",
                description=(
                    f"{actor.mention} (`{actor}` / `{actor.id}`) performed "
                    f"**{count}x {kind.replace('_', ' ')}** within {window}s "
                    f"and was {action_taken}"
                ),
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc),
            )
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if not await self._antinuke_enabled(channel.guild.id):
            return
        actor = await self._find_actor(
            channel.guild, discord.AuditLogAction.channel_delete, channel.id
        )
        if actor:
            await self._record(channel.guild, actor, "channel_delete")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if not await self._antinuke_enabled(role.guild.id):
            return
        actor = await self._find_actor(
            role.guild, discord.AuditLogAction.role_delete, role.id
        )
        if actor:
            await self._record(role.guild, actor, "role_delete")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        if not await self._antinuke_enabled(guild.id):
            return
        actor = await self._find_actor(guild, discord.AuditLogAction.ban, user.id)
        if actor:
            await self._record(guild, actor, "ban")

    @commands.Cog.listener("on_member_remove")
    async def detect_kick(self, member: discord.Member) -> None:
        if not await self._antinuke_enabled(member.guild.id):
            return
        actor = await self._find_actor(
            member.guild, discord.AuditLogAction.kick, member.id
        )
        if actor:
            await self._record(member.guild, actor, "kick")

    # ---- commands ---------------------------------------------------------

    security = app_commands.Group(
        name="security",
        description="Join logging and anti-nuke protection",
        default_permissions=discord.Permissions(administrator=True),
    )

    @security.command(
        name="logchannel", description="Set the join/leave log channel (omit to disable)"
    )
    async def logchannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self.bot.db.set_config(
            interaction.guild_id, log_channel_id=channel.id if channel else None
        )
        await interaction.response.send_message(
            f"📋 Join/leave logs will go to {channel.mention}."
            if channel
            else "Join/leave logging disabled.",
            ephemeral=True,
        )

    @security.command(name="antinuke", description="Enable or disable anti-nuke protection")
    @app_commands.describe(enabled="Turn protection on or off")
    async def antinuke(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self.bot.db.set_config(interaction.guild_id, antinuke=int(enabled))
        missing = []
        me = interaction.guild.me
        if not me.guild_permissions.view_audit_log:
            missing.append("View Audit Log")
        if not me.guild_permissions.ban_members:
            missing.append("Ban Members")
        note = (
            f"\n⚠️ I'm missing: **{', '.join(missing)}** — protection will be limited."
            if missing and enabled
            else ""
        )
        limits = ", ".join(
            f"{n}x {k.replace('_', ' ')}/{w}s" for k, (n, w) in THRESHOLDS.items()
        )
        await interaction.response.send_message(
            (
                f"🛡️ Anti-nuke **enabled**. Triggers: {limits}. "
                f"Offenders are banned (or role-stripped). "
                f"Whitelist trusted admins with `/security whitelist`.{note}"
            )
            if enabled
            else "Anti-nuke **disabled**.",
            ephemeral=True,
        )

    @security.command(
        name="whitelist", description="Add or remove a trusted user from the anti-nuke whitelist"
    )
    @app_commands.describe(user="The user to trust/untrust", remove="Remove instead of add")
    async def whitelist(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        remove: bool = False,
    ) -> None:
        if remove:
            removed = await self.bot.db.remove_whitelist(interaction.guild_id, user.id)
            msg = (
                f"Removed {user.mention} from the whitelist."
                if removed
                else f"{user.mention} wasn't whitelisted."
            )
        else:
            await self.bot.db.add_whitelist(interaction.guild_id, user.id)
            msg = f"✅ {user.mention} is now trusted by anti-nuke."
        await interaction.response.send_message(
            msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @security.command(name="status", description="Show the current security configuration")
    async def status(self, interaction: discord.Interaction) -> None:
        config = await self.bot.db.get_config(interaction.guild_id)
        whitelist = await self.bot.db.get_whitelist(interaction.guild_id)
        embed = discord.Embed(title="🛡️ Security status", color=discord.Color.blurple())
        embed.add_field(
            name="Anti-nuke",
            value="🟢 enabled" if config and config["antinuke"] else "🔴 disabled",
            inline=True,
        )
        embed.add_field(
            name="Log channel",
            value=(
                f"<#{config['log_channel_id']}>"
                if config and config["log_channel_id"]
                else "*not set*"
            ),
            inline=True,
        )
        embed.add_field(
            name="Whitelist",
            value=" ".join(f"<@{uid}>" for uid in whitelist) or "*empty*",
            inline=False,
        )
        embed.add_field(
            name="Thresholds",
            value="\n".join(
                f"• {k.replace('_', ' ')}: {n} in {w}s"
                for k, (n, w) in THRESHOLDS.items()
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(Security(bot))
