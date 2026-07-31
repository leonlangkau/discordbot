"""Security: full join/leave logging and anti-nuke protection.

Joins are logged in full to a #bot-logs channel — identity, account age,
badges, avatar/banner, and which invite they came in on. The channel is
found (or created) automatically; /security logchannel overrides it.

Anti-nuke watches the audit log for bursts of destructive actions
(channel/role deletions, bans, kicks). Anyone who crosses a threshold —
human or bot — is stopped: banned if possible, otherwise stripped of
roles. The server owner, the bot itself, and whitelisted users are exempt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ScrimBot

log = logging.getLogger("scrimbot.security")

# Fallback channel used when no log channel has been configured explicitly.
DEFAULT_LOG_CHANNEL = "bot-logs"
# Stored in log_channel_id to mean "an admin turned logging off on purpose",
# as distinct from NULL, which means "not configured, use the default".
LOGGING_DISABLED = 0

# action -> (max actions, within seconds)
THRESHOLDS = {
    "channel_delete": (10, 60),
    "role_delete": (5, 60),
    "ban": (7, 60),
    "kick": (8, 60),
}
PUNISH_COOLDOWN = 300  # don't re-punish the same actor for 5 minutes
NEW_ACCOUNT_DAYS = 7  # younger than this gets flagged in the embed


@dataclass(frozen=True)
class InviteUse:
    """What we know about the invite a member joined on.

    Kept separate from discord.Invite because an invite that hits its use
    limit is deleted immediately — by the time we look, the only record of
    it is our own snapshot.
    """

    code: str
    uses: int
    inviter_id: int | None = None
    inviter_name: str | None = None
    spent: bool = False

    @classmethod
    def from_invite(cls, invite: discord.Invite) -> InviteUse:
        return cls(
            code=invite.code,
            uses=invite.uses or 0,
            inviter_id=invite.inviter.id if invite.inviter else None,
            inviter_name=str(invite.inviter) if invite.inviter else None,
        )

    def exhausted(self) -> InviteUse:
        """The same invite, one use later, now deleted for hitting its limit."""
        return replace(self, uses=self.uses + 1, spent=True)

    def describe_inviter(self) -> str:
        if self.inviter_id is None:
            return "unknown"
        return f"<@{self.inviter_id}> (`{self.inviter_name}` / `{self.inviter_id}`)"


def describe_flags(user: discord.User | discord.Member) -> str:
    flags = [name.replace("_", " ").title() for name, on in user.public_flags if on]
    return ", ".join(flags) if flags else "none"


def humanize_age(created: datetime) -> str:
    delta = datetime.now(timezone.utc) - created
    days = delta.days
    if days >= 365:
        return f"{days // 365}y {days % 365}d old"
    if days >= 1:
        return f"{days}d old"
    return f"{delta.seconds // 3600}h old"


async def build_join_embed(
    bot: ScrimBot,
    member: discord.Member,
    *,
    invite: InviteUse | None = None,
    previous_joins: int = 0,
) -> discord.Embed:
    created = member.created_at
    age_days = (datetime.now(timezone.utc) - created).days
    new_account = age_days < NEW_ACCOUNT_DAYS
    embed = discord.Embed(
        title="📥 Member joined",
        description=f"{member.mention} — `{member.id}`",
        color=discord.Color.orange() if new_account else discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Username", value=f"`{member}`", inline=True)
    embed.add_field(
        name="Display name", value=member.global_name or member.name, inline=True
    )
    embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
    embed.add_field(
        name="Account created",
        value=(
            f"<t:{int(created.timestamp())}:F>\n"
            f"<t:{int(created.timestamp())}:R> — {humanize_age(created)}"
        ),
        inline=False,
    )
    embed.add_field(name="Member #", value=str(member.guild.member_count), inline=True)
    embed.add_field(
        name="Account type",
        value="🤖 bot" if member.bot else ("⚙️ system" if member.system else "👤 user"),
        inline=True,
    )
    embed.add_field(
        name="Screening",
        value="⏳ pending" if member.pending else "✅ passed",
        inline=True,
    )

    if invite is not None:
        spent_note = " — *hit its use limit and was deleted*" if invite.spent else ""
        embed.add_field(
            name="Invite used",
            value=(
                f"`{invite.code}` — {invite.uses} use(s){spent_note}\n"
                f"Created by {invite.describe_inviter()}"
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="Invite used",
            value="*couldn't determine* (needs **Manage Server**, or a vanity/one-time invite)",
            inline=False,
        )

    if previous_joins:
        embed.add_field(
            name="⚠️ Rejoin",
            value=f"This user has joined **{previous_joins}x** before.",
            inline=False,
        )

    embed.add_field(name="Badges", value=describe_flags(member), inline=False)
    embed.add_field(
        name="Avatar", value=f"[link]({member.display_avatar.url})", inline=True
    )

    # Banner and accent color are only present on a full user fetch.
    try:
        full_user = await bot.fetch_user(member.id)
        if full_user.banner:
            embed.add_field(
                name="Banner", value=f"[link]({full_user.banner.url})", inline=True
            )
            embed.set_image(url=full_user.banner.url)
        if full_user.accent_color and not new_account:
            embed.color = full_user.accent_color
    except discord.HTTPException:
        pass

    if new_account:
        embed.set_footer(text=f"⚠️ New account — created {age_days} day(s) ago")
    return embed


class Security(commands.Cog):
    """Join logging and nuke protection."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot
        # (guild_id, user_id, action) -> deque[timestamp]
        self._actions: dict[tuple[int, int, str], deque[float]] = defaultdict(deque)
        self._punished: dict[tuple[int, int], float] = {}
        # guild_id -> {invite code: snapshot}, diffed on join to see which was used
        self._invites: dict[int, dict[str, InviteUse]] = {}
        self._channel_locks: dict[int, asyncio.Lock] = {}

    # ---- log channel ------------------------------------------------------

    async def _log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Resolve the log channel: configured one, else #bot-logs, else create it."""
        config = await self.bot.db.get_config(guild.id)
        if config and config["log_channel_id"] == LOGGING_DISABLED:
            return None
        if config and config["log_channel_id"]:
            channel = guild.get_channel(config["log_channel_id"])
            if channel is not None:
                return channel
            # Configured channel was deleted — fall through and re-resolve.

        # One creator at a time per guild, or simultaneous joins race and we
        # end up with several #bot-logs channels.
        lock = self._channel_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            existing = discord.utils.get(guild.text_channels, name=DEFAULT_LOG_CHANNEL)
            if existing is not None:
                await self.bot.db.set_config(guild.id, log_channel_id=existing.id)
                return existing

            if not guild.me.guild_permissions.manage_channels:
                log.warning(
                    "No #%s in guild %s and I lack Manage Channels to create it.",
                    DEFAULT_LOG_CHANNEL,
                    guild.id,
                )
                return None
            try:
                created = await guild.create_text_channel(
                    DEFAULT_LOG_CHANNEL,
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(
                            read_messages=False
                        ),
                        guild.me: discord.PermissionOverwrite(
                            read_messages=True, send_messages=True, embed_links=True
                        ),
                    },
                    reason="Audit log channel for member join/leave logging",
                    topic="Member join/leave and security logs.",
                )
            except discord.HTTPException:
                log.exception("Couldn't create #%s in guild %s", DEFAULT_LOG_CHANNEL, guild.id)
                return None
            await self.bot.db.set_config(guild.id, log_channel_id=created.id)
            return created

    # ---- invite tracking --------------------------------------------------

    async def _cache_invites(self, guild: discord.Guild) -> None:
        """Snapshot invite state so a join can be attributed to one."""
        try:
            self._invites[guild.id] = {
                inv.code: InviteUse.from_invite(inv) for inv in await guild.invites()
            }
        except (discord.Forbidden, discord.HTTPException):
            # Needs Manage Server; without it we just can't attribute invites.
            self._invites[guild.id] = {}

    async def _consume_invite(self, guild: discord.Guild) -> InviteUse | None:
        """Diff invite uses against the cached snapshot to find the one just used."""
        before = self._invites.get(guild.id, {})
        try:
            current = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None

        used = next(
            (
                InviteUse.from_invite(inv)
                for inv in current
                if (inv.uses or 0) > (before[inv.code].uses if inv.code in before else 0)
            ),
            None,
        )
        if used is None:
            # A max-use invite is deleted the moment it's exhausted, so it's gone
            # from `current` entirely. The snapshot is the only record of it left.
            spent = set(before) - {inv.code for inv in current}
            if len(spent) == 1:
                used = before[spent.pop()].exhausted()

        self._invites[guild.id] = {
            inv.code: InviteUse.from_invite(inv) for inv in current
        }
        return used

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild:
            self._invites.setdefault(invite.guild.id, {})[invite.code] = (
                InviteUse.from_invite(invite)
            )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild:
            self._invites.get(invite.guild.id, {}).pop(invite.code, None)

    # ---- join / leave logging --------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        invite = await self._consume_invite(member.guild)
        previous_joins = await self.bot.db.log_member(
            member.guild.id,
            member.id,
            str(member),
            display_name=member.global_name or member.name,
            account_created=member.created_at.isoformat(),
            invite_code=invite.code if invite else None,
            inviter_id=invite.inviter_id if invite else None,
            is_bot=member.bot,
        )
        channel = await self._log_channel(member.guild)
        if channel is None:
            return
        try:
            await channel.send(
                embed=await build_join_embed(
                    self.bot, member, invite=invite, previous_joins=previous_joins
                )
            )
        except discord.HTTPException:
            log.exception("Failed to post join log for %s", member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        channel = await self._log_channel(member.guild)
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
        embed.add_field(
            name="Account created",
            value=f"<t:{int(member.created_at.timestamp())}:R>",
            inline=True,
        )
        if member.joined_at:
            embed.add_field(
                name="Was member since",
                value=(
                    f"<t:{int(member.joined_at.timestamp())}:F>\n"
                    f"<t:{int(member.joined_at.timestamp())}:R>"
                ),
                inline=False,
            )
        embed.add_field(
            name="Roles", value=" ".join(roles) if roles else "none", inline=False
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Failed to post leave log for %s", member.id)

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

        channel = await self._log_channel(guild)
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
        name="logchannel",
        description=f"Set the join/leave log channel (omit to reset to #{DEFAULT_LOG_CHANNEL})",
    )
    @app_commands.describe(
        channel=f"Where to post logs (omit to use #{DEFAULT_LOG_CHANNEL})",
        disable="Turn join/leave logging off entirely",
    )
    async def logchannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        disable: bool = False,
    ) -> None:
        if disable:
            await self.bot.db.set_config(
                interaction.guild_id, log_channel_id=LOGGING_DISABLED
            )
            await interaction.response.send_message(
                "🔇 Join/leave logging **disabled**.", ephemeral=True
            )
            return

        await self.bot.db.set_config(
            interaction.guild_id, log_channel_id=channel.id if channel else None
        )
        await interaction.response.send_message(
            f"📋 Join/leave logs will go to {channel.mention}."
            if channel
            else (
                f"Reset — logs will go to #{DEFAULT_LOG_CHANNEL}, "
                f"which I'll create on the next join if it doesn't exist."
            ),
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
                "🔇 *disabled*"
                if config and config["log_channel_id"] == LOGGING_DISABLED
                else f"<#{config['log_channel_id']}>"
                if config and config["log_channel_id"]
                else f"*#{DEFAULT_LOG_CHANNEL} (auto)*"
            ),
            inline=True,
        )
        embed.add_field(
            name="Invite tracking",
            value=(
                "🟢 on"
                if interaction.guild.me.guild_permissions.manage_guild
                else "🔴 needs **Manage Server**"
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
