"""Timed giveaways with a join button; prizes can include coins."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from bot import ScrimBot

COIN = "🪙"


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class GiveawayJoinButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"gv:join:(?P<id>\d+)",
):
    def __init__(self, giveaway_id: int) -> None:
        self.giveaway_id = giveaway_id
        super().__init__(
            discord.ui.Button(
                label="Join Giveaway",
                emoji="🎉",
                style=discord.ButtonStyle.success,
                custom_id=f"gv:join:{giveaway_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScrimBot = interaction.client
        giveaway = await bot.db.get_giveaway(self.giveaway_id)
        if not giveaway or giveaway["ended"]:
            await interaction.response.send_message(
                "This giveaway is over.", ephemeral=True
            )
            return
        if await bot.db.enter_giveaway(self.giveaway_id, interaction.user.id):
            count = await bot.db.giveaway_entry_count(self.giveaway_id)
            await interaction.response.send_message(
                f"🎉 You're in! **{count}** entr{'y' if count == 1 else 'ies'} so far.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "You've already entered.", ephemeral=True
            )


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(GiveawayJoinButton(giveaway_id))


class Giveaways(commands.Cog):
    """Run giveaways that draw automatically when time is up."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(GiveawayJoinButton)
        self.check_giveaways.start()

    async def cog_unload(self) -> None:
        self.check_giveaways.cancel()

    @tasks.loop(seconds=30)
    async def check_giveaways(self) -> None:
        for giveaway in await self.bot.db.due_giveaways(now_ts()):
            await self.bot.db.end_giveaway(giveaway["id"])
            channel = self.bot.get_channel(giveaway["channel_id"])
            if channel is None:
                continue
            winner_id = await self.bot.db.random_giveaway_winner(giveaway["id"])
            if winner_id is None:
                await channel.send(
                    f"🎉 Giveaway for **{giveaway['prize']}** ended — nobody entered. 🪦"
                )
                continue
            if giveaway["coins"]:
                await self.bot.db.add_coins(
                    giveaway["guild_id"], winner_id, giveaway["coins"]
                )
            await channel.send(
                f"🎉 **Giveaway over!** <@{winner_id}> wins **{giveaway['prize']}**!"
                + (f" ({giveaway['coins']:,} {COIN} paid out)" if giveaway["coins"] else "")
            )
            try:
                if giveaway["message_id"]:
                    message = await channel.fetch_message(giveaway["message_id"])
                    await message.edit(view=None)
            except discord.HTTPException:
                pass

    @check_giveaways.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="giveaway", description="Start a giveaway (mods)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        prize="What's being given away",
        minutes="How long the giveaway runs",
        coins="Coins to auto-pay the winner (0 for a non-coin prize)",
    )
    async def giveaway(
        self,
        interaction: discord.Interaction,
        prize: str,
        minutes: app_commands.Range[int, 1, 20160],
        coins: app_commands.Range[int, 0, 10_000_000] = 0,
    ) -> None:
        ends_at = now_ts() + minutes * 60
        giveaway_id = await self.bot.db.create_giveaway(
            interaction.guild_id, interaction.channel_id, prize, coins, ends_at
        )
        embed = discord.Embed(
            title="🎉 GIVEAWAY 🎉",
            description=(
                f"**Prize:** {prize}"
                + (f" ({coins:,} {COIN})" if coins else "")
                + f"\n**Ends:** <t:{ends_at}:R>\n\nHit the button to enter!"
            ),
            color=discord.Color.magenta(),
        )
        await interaction.response.send_message(embed=embed, view=GiveawayView(giveaway_id))
        message = await interaction.original_response()
        await self.bot.db.set_giveaway_message(giveaway_id, message.id)


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(Giveaways(bot))
