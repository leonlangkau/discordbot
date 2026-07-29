"""Economy: coins, daily/work, transfers, role shop, and betting on scrims."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ScrimBot

COIN = "🪙"
DAILY_AMOUNT = 250
DAILY_COOLDOWN = 20 * 3600
WORK_MIN, WORK_MAX = 50, 150
WORK_COOLDOWN = 3600

WORK_LINES = [
    "You boosted a rank account and earned {amount} {coin}.",
    "You coached a movement demo review for {amount} {coin}.",
    "You farmed cases and sold the drops for {amount} {coin}.",
    "You ran server maintenance on the VPS for {amount} {coin}.",
    "You clipped a 1v5 and the highlights channel paid {amount} {coin}.",
]


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class Economy(commands.Cog):
    """Coins and the things you can do with them."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    # ---- wallet -----------------------------------------------------------

    @app_commands.command(name="balance", description="Check a wallet")
    @app_commands.describe(player="Whose wallet (defaults to yours)")
    async def balance(
        self, interaction: discord.Interaction, player: discord.Member | None = None
    ) -> None:
        target = player or interaction.user
        econ = await self.bot.db.get_econ(interaction.guild_id, target.id)
        await interaction.response.send_message(
            f"{COIN} **{target.display_name}** has **{econ['balance']:,}** coins.",
            ephemeral=True,
        )

    @app_commands.command(name="daily", description="Claim your daily coins")
    async def daily(self, interaction: discord.Interaction) -> None:
        econ = await self.bot.db.get_econ(interaction.guild_id, interaction.user.id)
        now = now_ts()
        remaining = econ["last_daily"] + DAILY_COOLDOWN - now
        if remaining > 0:
            await interaction.response.send_message(
                f"Already claimed — come back <t:{now + remaining}:R>.", ephemeral=True
            )
            return
        await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, DAILY_AMOUNT)
        await self.bot.db.set_econ_time(
            interaction.guild_id, interaction.user.id, "last_daily", now
        )
        await interaction.response.send_message(
            f"{COIN} Daily claimed: **+{DAILY_AMOUNT}** coins!"
        )

    @app_commands.command(name="work", description="Do a job for some coins (1h cooldown)")
    async def work(self, interaction: discord.Interaction) -> None:
        econ = await self.bot.db.get_econ(interaction.guild_id, interaction.user.id)
        now = now_ts()
        remaining = econ["last_work"] + WORK_COOLDOWN - now
        if remaining > 0:
            await interaction.response.send_message(
                f"You're tired — work again <t:{now + remaining}:R>.", ephemeral=True
            )
            return
        amount = random.randint(WORK_MIN, WORK_MAX)
        await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
        await self.bot.db.set_econ_time(
            interaction.guild_id, interaction.user.id, "last_work", now
        )
        await interaction.response.send_message(
            random.choice(WORK_LINES).format(amount=f"**+{amount}**", coin=COIN)
        )

    @app_commands.command(name="pay", description="Send coins to another player")
    @app_commands.describe(player="Who to pay", amount="How many coins")
    async def pay(
        self,
        interaction: discord.Interaction,
        player: discord.Member,
        amount: app_commands.Range[int, 1, 1_000_000],
    ) -> None:
        if player.id == interaction.user.id or player.bot:
            await interaction.response.send_message("Nice try.", ephemeral=True)
            return
        if not await self.bot.db.try_spend(interaction.guild_id, interaction.user.id, amount):
            await interaction.response.send_message(
                "You don't have that many coins.", ephemeral=True
            )
            return
        await self.bot.db.add_coins(interaction.guild_id, player.id, amount)
        await interaction.response.send_message(
            f"{COIN} {interaction.user.mention} paid **{amount:,}** coins to {player.mention}."
        )

    # ---- scrim betting ----------------------------------------------------

    @app_commands.command(name="bet", description="Bet coins on an open scrim (pays 2x)")
    @app_commands.describe(
        scrim_id="The scrim number", team="The team you're backing", amount="Coins to bet"
    )
    @app_commands.choices(
        team=[
            app_commands.Choice(name="Team A", value="a"),
            app_commands.Choice(name="Team B", value="b"),
        ]
    )
    async def bet(
        self,
        interaction: discord.Interaction,
        scrim_id: int,
        team: app_commands.Choice[str],
        amount: app_commands.Range[int, 10, 1_000_000],
    ) -> None:
        scrim = await self.bot.db.get_scrim(scrim_id)
        if not scrim or scrim["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("Scrim not found.", ephemeral=True)
            return
        if scrim["status"] != "open":
            await interaction.response.send_message(
                "Bets are only open before the match starts.", ephemeral=True
            )
            return
        players = await self.bot.db.get_players(scrim_id)
        if any(p["user_id"] == interaction.user.id for p in players):
            await interaction.response.send_message(
                "You're playing in this scrim — you can't bet on it.", ephemeral=True
            )
            return
        if not await self.bot.db.try_spend(interaction.guild_id, interaction.user.id, amount):
            await interaction.response.send_message(
                "You don't have that many coins.", ephemeral=True
            )
            return
        if not await self.bot.db.place_bet(scrim_id, interaction.user.id, team.value, amount):
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
            await interaction.response.send_message(
                "You already have a bet on this scrim.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"{COIN} {interaction.user.mention} bet **{amount:,}** coins on "
            f"**{team.name}** in scrim #{scrim_id}! (pays 2x, refunded on draw/cancel)"
        )

    # ---- shop -------------------------------------------------------------

    @app_commands.command(name="shop", description="Browse and buy from the coin shop")
    async def shop(self, interaction: discord.Interaction) -> None:
        items = await self.bot.db.get_shop_items(interaction.guild_id)
        if not items:
            await interaction.response.send_message(
                "The shop is empty — admins can stock it with `/shopadmin add`.",
                ephemeral=True,
            )
            return
        econ = await self.bot.db.get_econ(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(
            title="🛒 Coin Shop",
            description=f"Your balance: **{econ['balance']:,}** {COIN}",
            color=discord.Color.gold(),
        )
        options = []
        for item in items:
            embed.add_field(
                name=f"{item['name']} — {item['price']:,} {COIN}",
                value=f"Grants <@&{item['role_id']}>",
                inline=False,
            )
            options.append(
                discord.SelectOption(
                    label=f"{item['name']} ({item['price']:,} coins)", value=str(item["id"])
                )
            )

        view = ShopView(self.bot, interaction.user.id, options)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ShopView(discord.ui.View):
    def __init__(self, bot: ScrimBot, user_id: int, options: list[discord.SelectOption]):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        select = discord.ui.Select(placeholder="Buy an item…", options=options[:25])
        select.callback = self.buy
        self.select = select
        self.add_item(select)

    async def buy(self, interaction: discord.Interaction) -> None:
        item = await self.bot.db.get_shop_item(
            interaction.guild_id, int(self.select.values[0])
        )
        if item is None:
            await interaction.response.send_message("Item no longer exists.", ephemeral=True)
            return
        role = interaction.guild.get_role(item["role_id"])
        if role is None:
            await interaction.response.send_message(
                "That item's role was deleted — tell an admin.", ephemeral=True
            )
            return
        if role in interaction.user.roles:
            await interaction.response.send_message(
                "You already own that role.", ephemeral=True
            )
            return
        if not await self.bot.db.try_spend(
            interaction.guild_id, interaction.user.id, item["price"]
        ):
            await interaction.response.send_message(
                "You can't afford that.", ephemeral=True
            )
            return
        try:
            await interaction.user.add_roles(role, reason="Coin shop purchase")
        except discord.Forbidden:
            await self.bot.db.add_coins(
                interaction.guild_id, interaction.user.id, item["price"]
            )
            await interaction.response.send_message(
                "I can't assign that role (check my role is above it). Refunded.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ Bought **{item['name']}** — you now have {role.mention}!", ephemeral=True
        )


@app_commands.default_permissions(manage_guild=True)
class ShopAdmin(commands.GroupCog, group_name="shopadmin"):
    """Stock the coin shop."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    @app_commands.command(name="add", description="Add a role to the coin shop")
    @app_commands.describe(name="Item name", price="Cost in coins", role="Role to grant")
    async def add(
        self,
        interaction: discord.Interaction,
        name: str,
        price: app_commands.Range[int, 1, 10_000_000],
        role: discord.Role,
    ) -> None:
        item_id = await self.bot.db.add_shop_item(
            interaction.guild_id, name, price, role.id
        )
        await interaction.response.send_message(
            f"Added shop item **#{item_id} {name}** — {price:,} {COIN} for {role.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="remove", description="Remove an item from the shop")
    @app_commands.describe(item_id="The item number shown in /shop")
    async def remove(self, interaction: discord.Interaction, item_id: int) -> None:
        removed = await self.bot.db.remove_shop_item(interaction.guild_id, item_id)
        await interaction.response.send_message(
            f"Removed item #{item_id}." if removed else "No such item.", ephemeral=True
        )

    @app_commands.command(name="give", description="Grant coins to a player (admin)")
    @app_commands.describe(player="Who", amount="How many coins (negative to take)")
    async def give(
        self,
        interaction: discord.Interaction,
        player: discord.Member,
        amount: app_commands.Range[int, -10_000_000, 10_000_000],
    ) -> None:
        await self.bot.db.add_coins(interaction.guild_id, player.id, amount)
        econ = await self.bot.db.get_econ(interaction.guild_id, player.id)
        await interaction.response.send_message(
            f"{COIN} {player.mention} now has **{econ['balance']:,}** coins.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(Economy(bot))
    await bot.add_cog(ShopAdmin(bot))
