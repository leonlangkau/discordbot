"""`.g` layered games menu, the betting window GUI, and the item shop.

Typing `.slots` (no amount) or pressing a game button opens a BetPanel:
a styled window with ½ / ×2 / All-In (confirmed) / custom-amount buttons,
an option dropdown where the game needs one, and a ▶ Play button. The
panel feeds the exact same game callbacks as the slash/prefix commands.

Shop items use custom server emojis automatically: upload an emoji whose
name matches the item key (e.g. :rare_pepe:, :golden_dildo:) and the bot
picks it up; otherwise it falls back to the unicode emoji.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ScrimBot

COIN = "🪙"
MAX_BET = 250_000

# item key -> (fallback emoji, name, price). Custom server emoji named after
# the key (e.g. :rare_pepe:) overrides the fallback automatically.
ITEMS: dict[str, tuple[str, str, int]] = {
    "hotdog": ("🌭", "Hotdog", 500),
    "beer": ("🍺", "Beer", 1_000),
    "copium": ("💊", "Copium", 5_000),
    "fish": ("🐟", "Wet Fish", 25_000),
    "rare_pepe": ("🐸", "Rare Pepe", 50_000),
    "mystery_box": ("📦", "Mystery Box", 75_000),
    "awp": ("🔫", "Airsoft AWP", 100_000),
    "smug_pepe": ("😏", "Smug Pepe", 250_000),
    "chain": ("💎", "Diamond Chain", 1_000_000),
    "pepe_crown": ("👑", "Pepe Crown", 5_000_000),
    "lambo": ("🏎️", "Lambo", 100_000_000),
    "golden_pepe": ("✨", "Golden Pepe", 500_000_000),
    "golden_dildo": ("🍆", "Golden Dildo", 10_000_000_000),
}

MYSTERY_ITEMS = ["hotdog", "beer", "copium", "fish", "rare_pepe", "awp"]


def item_emoji(guild: discord.Guild | None, key: str, fallback: str) -> str:
    """Use a custom server emoji named after the item key when available."""
    if guild is not None:
        emoji = discord.utils.get(guild.emojis, name=key)
        if emoji:
            return str(emoji)
    return fallback


def choice(value: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=value, value=value)


# game key -> (emoji, title, cog, command, option spec)
# option spec: (field label, default value, [(label, value)...]) or None
GAME_META: dict[str, tuple] = {
    "slots": ("🎰", "Slots", "Casino", "slots", None),
    "dice": ("🎲", "Dice", "Casino", "dice", None),
    "tower": ("🗼", "Tower", "Casino", "tower", None),
    "blackjack": ("🃏", "Blackjack", "Casino", "blackjack", None),
    "coinflip": (
        "🪙", "Coinflip", "Casino", "coinflip",
        ("Side", "heads", [("Heads", "heads"), ("Tails", "tails")]),
    ),
    "rps": (
        "✊", "Rock Paper Scissors", "Casino", "rps",
        ("Throw", "rock", [("🪨 Rock", "rock"), ("📄 Paper", "paper"), ("✂️ Scissors", "scissors")]),
    ),
    "plinko": (
        "🔻", "Plinko", "Casino", "plinko",
        ("Risk", "medium",
         [("🟢 Low (max 3x)", "low"), ("🟡 Medium (max 7x)", "medium"),
          ("🟠 High (max 15x)", "high"), ("🔴 Extreme (max 60x)", "extreme")]),
    ),
    "mines": (
        "💣", "Mines", "Casino", "mines",
        ("Mines", "3", [("1 mine (chill)", "1"), ("3 mines", "3"), ("5 mines", "5"),
                        ("7 mines (spicy)", "7"), ("10 mines (insane)", "10")]),
    ),
    "baccarat": (
        "🎴", "Baccarat", "Casino", "baccarat",
        ("Bet on", "player",
         [("Player (2x)", "player"), ("Banker (1.95x)", "banker"), ("Tie (9x)", "tie")]),
    ),
    "roulette": (
        "🎡", "Roulette", "Casino", "roulette",
        ("Bet on", "red",
         [("🔴 Red (2x)", "red"), ("⚫ Black (2x)", "black"), ("Even (2x)", "even"),
          ("Odd (2x)", "odd"), ("🎯 Single number (36x)…", "number")]),
    ),
}


# ---------------------------------------------------------------------------
# Bet panel — the pretty betting window
# ---------------------------------------------------------------------------


class AmountModal(discord.ui.Modal, title="Enter your bet"):
    amount = discord.ui.TextInput(
        label="Bet amount (number, 'all', or '10k')", placeholder="e.g. 2500", max_length=12
    )

    def __init__(self, panel: BetPanel) -> None:
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.amount.value).strip().lower().replace(",", "")
        if raw in ("all", "max"):
            value = min(self.panel.balance, MAX_BET)
        else:
            try:
                value = int(raw.replace("k", "000"))
            except ValueError:
                await interaction.response.send_message(
                    f"`{raw}` isn't an amount.", ephemeral=True
                )
                return
        if value < 10:
            await interaction.response.send_message(
                f"Minimum bet is 10 {COIN}.", ephemeral=True
            )
            return
        self.panel.bet = min(value, MAX_BET)
        await self.panel.render(interaction)


class NumberModal(discord.ui.Modal, title="Roulette number"):
    number = discord.ui.TextInput(label="Number (0-36)", placeholder="17", max_length=2)

    def __init__(self, panel: BetPanel) -> None:
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.number.value).strip()
        if not raw.isdigit() or not 0 <= int(raw) <= 36:
            await interaction.response.send_message(
                "Pick a number between 0 and 36.", ephemeral=True
            )
            return
        self.panel.option = "number"
        self.panel.number = int(raw)
        await self.panel.render(interaction)


class BetPanel(discord.ui.View):
    """The betting window: adjust the bet with buttons, then ▶ Play."""

    def __init__(self, bot: ScrimBot, guild: discord.Guild, user_id: int, game: str) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.guild = guild
        self.user_id = user_id
        self.game = game
        self.bet = 100
        self.balance = 0
        self.number: int | None = None
        spec = GAME_META[game][4]
        self.option: str | None = spec[1] if spec else None
        self.confirming = False
        self.build()

    @classmethod
    async def create(
        cls, bot: ScrimBot, guild: discord.Guild, user_id: int, game: str
    ) -> "BetPanel":
        panel = cls(bot, guild, user_id, game)
        econ = await bot.db.get_econ(guild.id, user_id)
        panel.balance = econ["balance"]
        panel.bet = min(100, max(10, panel.balance))
        return panel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                f"Not your table — open one with `.{self.game}`", ephemeral=True
            )
            return False
        return True

    # ---- rendering --------------------------------------------------------

    def embed(self) -> discord.Embed:
        emoji, title, *_ , spec = GAME_META[self.game]
        if self.confirming:
            amount = min(self.balance, MAX_BET)
            embed = discord.Embed(
                title="💰 ALL IN?",
                description=(
                    f"You're about to bet **{amount:,}** {COIN} — "
                    + ("your **entire balance**." if amount == self.balance
                       else f"the table max (balance {self.balance:,}).")
                    + "\n\nNo take-backs. Are you sure?"
                ),
                color=discord.Color.red(),
            )
            embed.set_footer(text="Fortune favors the brave… usually.")
            return embed

        embed = discord.Embed(
            title=f"{emoji} {title}",
            description="Set your bet, then hit **▶ Play**.",
            color=discord.Color.from_str("#2ecc71"),
        )
        embed.add_field(name=f"{COIN} Balance", value=f"```{self.balance:,}```", inline=True)
        embed.add_field(name="💵 Bet", value=f"```{self.bet:,}```", inline=True)
        if spec:
            label = spec[0]
            shown = str(self.number) if self.option == "number" and self.number is not None \
                else (self.option or spec[1])
            embed.add_field(name=f"🎯 {label}", value=f"```{shown}```", inline=True)
        embed.set_footer(text=f"½ / ×2 to adjust · ✏️ exact amount · max bet {MAX_BET:,}")
        return embed

    async def render(self, interaction: discord.Interaction) -> None:
        econ = await self.bot.db.get_econ(self.guild.id, self.user_id)
        self.balance = econ["balance"]
        self.bet = max(10, min(self.bet, MAX_BET))
        self.build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    def build(self) -> None:
        self.clear_items()
        if self.confirming:
            confirm = discord.ui.Button(label="YES, ALL IN", emoji="💰",
                                        style=discord.ButtonStyle.danger)
            confirm.callback = self.confirm_allin
            back = discord.ui.Button(label="Back", emoji="↩️",
                                     style=discord.ButtonStyle.secondary)
            back.callback = self.cancel_allin
            self.add_item(confirm)
            self.add_item(back)
            return

        spec = GAME_META[self.game][4]
        if spec:
            label, default, options = spec
            select = discord.ui.Select(
                placeholder=f"{label}: {self.option or default}",
                options=[discord.SelectOption(label=lab, value=val) for lab, val in options],
                row=0,
            )
            select.callback = self.pick_option(select)
            self.add_item(select)

        half = discord.ui.Button(label="½", style=discord.ButtonStyle.secondary, row=1)
        half.callback = self.halve
        double = discord.ui.Button(label="×2", style=discord.ButtonStyle.secondary, row=1)
        double.callback = self.double
        exact = discord.ui.Button(label="Amount", emoji="✏️",
                                  style=discord.ButtonStyle.primary, row=1)
        exact.callback = self.enter_amount
        allin = discord.ui.Button(label="All In", emoji="💰",
                                  style=discord.ButtonStyle.danger, row=1)
        allin.callback = self.ask_allin
        for item in (half, double, exact, allin):
            self.add_item(item)

        play = discord.ui.Button(label="▶  P L A Y", style=discord.ButtonStyle.success, row=2)
        play.callback = self.play
        close = discord.ui.Button(label="Close", emoji="✖️",
                                  style=discord.ButtonStyle.secondary, row=2)
        close.callback = self.close
        self.add_item(play)
        self.add_item(close)

    # ---- button actions ----------------------------------------------------

    def pick_option(self, select: discord.ui.Select):
        async def callback(interaction: discord.Interaction) -> None:
            value = select.values[0]
            if self.game == "roulette" and value == "number":
                await interaction.response.send_modal(NumberModal(self))
                return
            self.option = value
            self.number = None
            await self.render(interaction)
        return callback

    async def halve(self, interaction: discord.Interaction) -> None:
        self.bet = max(10, self.bet // 2)
        await self.render(interaction)

    async def double(self, interaction: discord.Interaction) -> None:
        self.bet = min(self.bet * 2, MAX_BET)
        await self.render(interaction)

    async def enter_amount(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AmountModal(self))

    async def ask_allin(self, interaction: discord.Interaction) -> None:
        self.confirming = True
        self.build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def confirm_allin(self, interaction: discord.Interaction) -> None:
        self.confirming = False
        econ = await self.bot.db.get_econ(self.guild.id, self.user_id)
        self.balance = econ["balance"]
        self.bet = max(10, min(self.balance, MAX_BET))
        self.build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def cancel_allin(self, interaction: discord.Interaction) -> None:
        self.confirming = False
        await self.render(interaction)

    async def close(self, interaction: discord.Interaction) -> None:
        self.stop()
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            await interaction.response.edit_message(view=None)

    async def play(self, interaction: discord.Interaction) -> None:
        econ = await self.bot.db.get_econ(self.guild.id, self.user_id)
        self.balance = econ["balance"]
        if self.bet > self.balance:
            await interaction.response.send_message(
                f"Your bet ({self.bet:,}) is more than your balance "
                f"({self.balance:,}). Lower it.",
                ephemeral=True,
            )
            return
        _, _, cog_name, command_name, spec = GAME_META[self.game]
        cog = self.bot.get_cog(cog_name)
        callback = getattr(cog, command_name).callback
        args: list = [self.bet]
        if spec:
            if self.game == "roulette":
                if self.option == "number":
                    if self.number is None:
                        await interaction.response.send_message(
                            "Pick your number first (Bet on → Single number).",
                            ephemeral=True,
                        )
                        return
                    args += [choice("number"), self.number]
                else:
                    args += [choice(self.option), None]
            elif self.game == "mines":
                args.append(int(self.option))
            else:
                args.append(choice(self.option))
        await callback(cog, interaction, *args)


# ---------------------------------------------------------------------------
# Layered menu
# ---------------------------------------------------------------------------


class GamesMenu(discord.ui.View):
    def __init__(self, bot: ScrimBot, guild: discord.Guild, user_id: int) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.guild = guild
        self.user_id = user_id
        self.show_main()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own menu with `.g`", ephemeral=True
            )
            return False
        return True

    def button(self, label, style, callback, row=0, emoji=None):
        btn = discord.ui.Button(label=label, style=style, row=row, emoji=emoji)
        btn.callback = callback
        self.add_item(btn)

    def nav(self, layer_builder, embed_builder):
        async def callback(interaction: discord.Interaction) -> None:
            layer_builder()
            await interaction.response.edit_message(embed=embed_builder(), view=self)
        return callback

    def game_button(self, game: str):
        async def callback(interaction: discord.Interaction) -> None:
            panel = await BetPanel.create(self.bot, self.guild, self.user_id, game)
            await interaction.response.send_message(embed=panel.embed(), view=panel)
        return callback

    def cog_button(self, cog_name: str, command: str, *extra_args):
        async def callback(interaction: discord.Interaction) -> None:
            cog = self.bot.get_cog(cog_name)
            await getattr(cog, command).callback(cog, interaction, *extra_args)
        return callback

    # ---- embeds ------------------------------------------------------------

    def main_embed(self) -> discord.Embed:
        return discord.Embed(
            title="🕹️ Game Menu",
            description=(
                "Pick a category — every game opens a betting window, "
                "no commands to type.\n\n"
                "🎰 **Casino** — slots, plinko, mines, tower…\n"
                "🃏 **Table games** — blackjack, baccarat, roulette\n"
                "💰 **Economy** — daily, work, balance\n"
                "🛒 **Item shop** — pepes, lambos and *the* 🍆\n"
                "📈 **Stats** — profile, leaderboards, history"
            ),
            color=discord.Color.green(),
        )

    def casino_embed(self) -> discord.Embed:
        return discord.Embed(
            title="🎰 Casino", description="Pick a game — a betting window opens.",
            color=discord.Color.dark_teal(),
        )

    def table_embed(self) -> discord.Embed:
        return discord.Embed(
            title="🃏 Table Games", description="Pick a game — a betting window opens.",
            color=discord.Color.dark_green(),
        )

    def economy_embed(self) -> discord.Embed:
        return discord.Embed(
            title="💰 Economy", description="Claim, grind, check.", color=discord.Color.gold()
        )

    def shop_embed(self) -> discord.Embed:
        lines = [
            f"{item_emoji(self.guild, key, emoji)} **{name}** — {price:,} {COIN}"
            for key, (emoji, name, price) in ITEMS.items()
        ]
        return discord.Embed(
            title="🛒 Item Shop",
            description="\n".join(lines) + "\n\nPick an item below to buy it. "
            "📦 Mystery Boxes open instantly!",
            color=discord.Color.magenta(),
        )

    def stats_embed(self) -> discord.Embed:
        return discord.Embed(
            title="📈 Stats", description="Profiles and boards.", color=discord.Color.blurple()
        )

    # ---- layers ------------------------------------------------------------

    def show_main(self) -> None:
        self.clear_items()
        self.button("Casino", discord.ButtonStyle.success,
                    self.nav(self.show_casino, self.casino_embed), 0, "🎰")
        self.button("Table Games", discord.ButtonStyle.success,
                    self.nav(self.show_table, self.table_embed), 0, "🃏")
        self.button("Economy", discord.ButtonStyle.primary,
                    self.nav(self.show_economy, self.economy_embed), 1, "💰")
        self.button("Item Shop", discord.ButtonStyle.primary,
                    self.nav(self.show_shop, self.shop_embed), 1, "🛒")
        self.button("Stats", discord.ButtonStyle.secondary,
                    self.nav(self.show_stats, self.stats_embed), 1, "📈")

    def add_back(self, row=4) -> None:
        self.button("Back", discord.ButtonStyle.danger,
                    self.nav(self.show_main, self.main_embed), row, "↩️")

    def show_casino(self) -> None:
        self.clear_items()
        self.button("Slots", discord.ButtonStyle.success, self.game_button("slots"), 0, "🎰")
        self.button("Coinflip", discord.ButtonStyle.success, self.game_button("coinflip"), 0, "🪙")
        self.button("Dice", discord.ButtonStyle.success, self.game_button("dice"), 0, "🎲")
        self.button("RPS", discord.ButtonStyle.success, self.game_button("rps"), 0, "✊")
        self.button("Plinko", discord.ButtonStyle.primary, self.game_button("plinko"), 1, "🔻")
        self.button("Mines", discord.ButtonStyle.primary, self.game_button("mines"), 1, "💣")
        self.button("Tower", discord.ButtonStyle.primary, self.game_button("tower"), 1, "🗼")
        self.add_back()

    def show_table(self) -> None:
        self.clear_items()
        self.button("Blackjack", discord.ButtonStyle.success, self.game_button("blackjack"), 0, "🃏")
        self.button("Baccarat", discord.ButtonStyle.success, self.game_button("baccarat"), 0, "🎴")
        self.button("Roulette", discord.ButtonStyle.success, self.game_button("roulette"), 0, "🎡")
        self.add_back()

    def show_economy(self) -> None:
        self.clear_items()
        self.button("Daily", discord.ButtonStyle.success,
                    self.cog_button("Economy", "daily"), 0, "📅")
        self.button("Work", discord.ButtonStyle.success,
                    self.cog_button("Economy", "work"), 0, "🔨")
        self.button("Balance", discord.ButtonStyle.primary,
                    self.cog_button("Economy", "balance", None), 0, "🪙")
        self.add_back()

    def show_shop(self) -> None:
        self.clear_items()
        options = [
            discord.SelectOption(
                label=f"{name} — {price:,} coins"[:100],
                value=key,
                emoji=item_emoji(self.guild, key, emoji),
            )
            for key, (emoji, name, price) in ITEMS.items()
        ]
        select = discord.ui.Select(placeholder="Buy an item…", options=options)
        select.callback = self.buy_item(select)
        self.add_item(select)
        self.button("My Inventory", discord.ButtonStyle.primary, self.show_inventory, 1, "🎒")
        self.add_back(row=1)

    def show_stats(self) -> None:
        self.clear_items()
        self.button("Profile", discord.ButtonStyle.primary,
                    self.cog_button("Stats", "stats", None), 0, "👤")
        self.button("History", discord.ButtonStyle.primary,
                    self.cog_button("Stats", "history", None), 0, "📜")
        self.button("Top Wins", discord.ButtonStyle.secondary,
                    self.cog_button("Stats", "leaderboard", choice("wins")), 1, "🏆")
        self.button("Top Elo", discord.ButtonStyle.secondary,
                    self.cog_button("Stats", "leaderboard", choice("elo")), 1, "📈")
        self.button("Top Coins", discord.ButtonStyle.secondary,
                    self.cog_button("Stats", "leaderboard", choice("coins")), 1, "🪙")
        self.add_back(row=2)

    # ---- shop actions ------------------------------------------------------

    async def open_mystery_box(self, interaction: discord.Interaction) -> None:
        roll = random.random()
        if roll < 0.40:  # coins, modest
            amount = random.randint(10_000, 90_000)
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
            result = f"…it's **{amount:,}** {COIN}!"
        elif roll < 0.75:  # a random cheap item
            key = random.choice(MYSTERY_ITEMS)
            emoji, name, _ = ITEMS[key]
            await self.bot.db.add_inventory_item(
                interaction.guild_id, interaction.user.id, key
            )
            result = f"…it's a {item_emoji(self.guild, key, emoji)} **{name}**!"
        elif roll < 0.97:  # coins, nice
            amount = random.randint(100_000, 250_000)
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
            result = f"…it's a fat stack of **{amount:,}** {COIN}! 🤑"
        else:  # jackpot item
            emoji, name, _ = ITEMS["chain"]
            await self.bot.db.add_inventory_item(
                interaction.guild_id, interaction.user.id, "chain"
            )
            result = f"…**JACKPOT!** A {item_emoji(self.guild, 'chain', emoji)} **{name}**!"
        await interaction.response.send_message(
            f"📦 {interaction.user.mention} rips open a Mystery Box… {result}"
        )

    def buy_item(self, select: discord.ui.Select):
        async def callback(interaction: discord.Interaction) -> None:
            key = select.values[0]
            emoji, name, price = ITEMS[key]
            shown = item_emoji(self.guild, key, emoji)
            if not await self.bot.db.try_spend(
                interaction.guild_id, interaction.user.id, price
            ):
                econ = await self.bot.db.get_econ(
                    interaction.guild_id, interaction.user.id
                )
                short = price - econ["balance"]
                await interaction.response.send_message(
                    f"{shown} **{name}** costs **{price:,}** {COIN} — "
                    f"you're **{short:,}** short. Keep grinding.",
                    ephemeral=True,
                )
                return
            if key == "mystery_box":
                await self.open_mystery_box(interaction)
                return
            await self.bot.db.add_inventory_item(
                interaction.guild_id, interaction.user.id, key
            )
            if key == "golden_dildo":
                await interaction.response.send_message(
                    f"# {shown}👑 HISTORY HAS BEEN MADE\n"
                    f"{interaction.user.mention} just dropped "
                    f"**{price:,}** {COIN} on the **GOLDEN DILDO**.\n"
                    f"Absolute legend. Bow down."
                )
                return
            await interaction.response.send_message(
                f"{interaction.user.mention} bought {shown} **{name}** "
                f"for {price:,} {COIN}!"
            )
        return callback

    async def show_inventory(self, interaction: discord.Interaction) -> None:
        items = await self.bot.db.get_inventory(interaction.guild_id, interaction.user.id)
        if not items:
            await interaction.response.send_message(
                "🎒 Your inventory is empty. Hit the shop!", ephemeral=True
            )
            return
        lines = []
        for row in items:
            emoji, name, _ = ITEMS.get(row["item_key"], ("❓", row["item_key"], 0))
            qty = f" ×{row['qty']}" if row["qty"] > 1 else ""
            lines.append(f"{item_emoji(self.guild, row['item_key'], emoji)} **{name}**{qty}")
        embed = discord.Embed(
            title=f"🎒 {interaction.user.display_name}'s inventory",
            description="\n".join(lines),
            color=discord.Color.magenta(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class GamesMenuCog(commands.Cog, name="GamesMenu"):
    """The .g menu and betting windows."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    @commands.command(name="g", aliases=["games", "menu"])
    async def g(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return
        view = GamesMenu(self.bot, ctx.guild, ctx.author.id)
        await ctx.send(embed=view.main_embed(), view=view)

    @app_commands.command(name="games", description="Open the games menu")
    async def games_slash(self, interaction: discord.Interaction) -> None:
        view = GamesMenu(self.bot, interaction.guild, interaction.user.id)
        await interaction.response.send_message(embed=view.main_embed(), view=view)

    @commands.command(name="inv", aliases=["inventory", "items"])
    async def inv(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return
        items = await self.bot.db.get_inventory(ctx.guild.id, ctx.author.id)
        if not items:
            await ctx.send("🎒 Your inventory is empty. Open the shop with `.g`!")
            return
        lines = []
        for row in items:
            emoji, name, _ = ITEMS.get(row["item_key"], ("❓", row["item_key"], 0))
            qty = f" ×{row['qty']}" if row["qty"] > 1 else ""
            lines.append(f"{item_emoji(ctx.guild, row['item_key'], emoji)} **{name}**{qty}")
        embed = discord.Embed(
            title=f"🎒 {ctx.author.display_name}'s inventory",
            description="\n".join(lines),
            color=discord.Color.magenta(),
        )
        await ctx.send(embed=embed)


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(GamesMenuCog(bot))
