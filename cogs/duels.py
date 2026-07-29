"""1v1 challenge duels with a coin wager, played as a mini scrim."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from cogs.scrims import launch_scrim, refresh_announcement, sync_channel_permissions

if TYPE_CHECKING:
    from bot import ScrimBot

COIN = "🪙"


class DuelChallengeView(discord.ui.View):
    def __init__(
        self,
        bot: ScrimBot,
        challenger: discord.Member,
        target: discord.Member,
        wager: int,
        map_name: str | None,
    ) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.challenger = challenger
        self.target = target
        self.wager = wager
        self.map_name = map_name
        self.resolved = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message(
                "This challenge isn't for you.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if not self.resolved:
            self.resolved = True
            await self.bot.db.add_coins(
                self.challenger.guild.id, self.challenger.id, self.wager
            )

    @discord.ui.button(label="Accept", emoji="⚔️", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.resolved:
            return
        if not await self.bot.db.try_spend(
            interaction.guild_id, self.target.id, self.wager
        ):
            await interaction.response.send_message(
                f"You can't cover the **{self.wager:,}** {COIN} wager.", ephemeral=True
            )
            return
        self.resolved = True
        self.stop()
        await interaction.response.edit_message(
            content=f"⚔️ Duel accepted — setting up the arena…", view=None
        )

        scrim_id, result = await launch_scrim(
            self.bot,
            interaction.guild,
            self.challenger,
            interaction.channel,
            self.map_name,
            1,
            None,
        )
        if scrim_id is None:
            # Could not create the scrim (limit reached etc.) — refund both.
            await self.bot.db.add_coins(interaction.guild_id, self.challenger.id, self.wager)
            await self.bot.db.add_coins(interaction.guild_id, self.target.id, self.wager)
            await interaction.edit_original_response(
                content=f"{result}\nDuel wagers refunded."
            )
            return

        await self.bot.db.add_player(scrim_id, self.challenger.id, "a")
        await self.bot.db.add_player(scrim_id, self.target.id, "b")
        await self.bot.db.create_duel(
            scrim_id, interaction.guild_id, self.challenger.id, self.target.id, self.wager
        )
        await refresh_announcement(self.bot, scrim_id)
        await sync_channel_permissions(self.bot, scrim_id)
        await interaction.edit_original_response(
            content=(
                f"⚔️ **Duel on!** {self.challenger.mention} vs {self.target.mention} "
                f"for a **{self.wager * 2:,}** {COIN} pot — scrim #{scrim_id}.\n{result}\n"
                "Host starts the match from the announcement; report the score there too."
            )
        )

    @discord.ui.button(label="Decline", emoji="🏳️", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.resolved:
            return
        self.resolved = True
        self.stop()
        await self.bot.db.add_coins(interaction.guild_id, self.challenger.id, self.wager)
        await interaction.response.edit_message(
            content=(
                f"🏳️ {self.target.mention} declined the duel — "
                f"{self.challenger.mention} got their wager back."
            ),
            view=None,
        )


class Duels(commands.Cog):
    """Settle it on the server."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="duel", description="Challenge someone to a 1v1 for coins — winner takes the pot"
    )
    @app_commands.describe(
        opponent="Who you're calling out",
        wager="Coins each of you puts in",
        map="Optional map (e.g. de_mirage)",
    )
    async def duel(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        wager: app_commands.Range[int, 10, 1_000_000],
        map: str | None = None,
    ) -> None:
        if opponent.id == interaction.user.id or opponent.bot:
            await interaction.response.send_message(
                "Find a real opponent.", ephemeral=True
            )
            return
        if not await self.bot.db.try_spend(
            interaction.guild_id, interaction.user.id, wager
        ):
            await interaction.response.send_message(
                f"You can't cover a **{wager:,}** {COIN} wager.", ephemeral=True
            )
            return
        view = DuelChallengeView(self.bot, interaction.user, opponent, wager, map)
        await interaction.response.send_message(
            f"⚔️ {opponent.mention} — {interaction.user.mention} challenges you to a "
            f"**1v1**{f' on `{map}`' if map else ''} for **{wager:,}** {COIN} each "
            f"(**{wager * 2:,}** pot). Accept?",
            view=view,
        )


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(Duels(bot))
