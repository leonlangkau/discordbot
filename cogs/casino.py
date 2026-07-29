"""Casino games: coinflip, dice, RPS, slots, roulette, plinko, blackjack,
mines, tower, and baccarat. All games play against the house in coins."""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ScrimBot

COIN = "🪙"
Bet = app_commands.Range[int, 10, 250_000]

CARD_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
CARD_SUITS = ["♠", "♥", "♦", "♣"]


def draw_card() -> str:
    return random.choice(CARD_RANKS) + random.choice(CARD_SUITS)


def card_rank(card: str) -> str:
    return card[:-1]


def bj_value(hand: list[str]) -> int:
    total, aces = 0, 0
    for card in hand:
        rank = card_rank(card)
        if rank == "A":
            total += 11
            aces += 1
        elif rank in ("J", "Q", "K", "10"):
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def baccarat_value(hand: list[str]) -> int:
    total = 0
    for card in hand:
        rank = card_rank(card)
        if rank == "A":
            total += 1
        elif rank in ("10", "J", "Q", "K"):
            total += 0
        else:
            total += int(rank)
    return total % 10


async def take_bet(bot: ScrimBot, interaction: discord.Interaction, amount: int) -> bool:
    if await bot.db.try_spend(interaction.guild_id, interaction.user.id, amount):
        return True
    econ = await bot.db.get_econ(interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(
        f"You only have **{econ['balance']:,}** {COIN}.", ephemeral=True
    )
    return False


def result_line(delta: int) -> str:
    if delta > 0:
        return f"**+{delta:,}** {COIN}"
    if delta < 0:
        return f"**{delta:,}** {COIN}"
    return f"**±0** {COIN} (push)"


# ---------------------------------------------------------------------------
# Blackjack
# ---------------------------------------------------------------------------


class OwnedGameView(discord.ui.View):
    """A game view only its owner may interact with."""

    user_id: int

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Not your game — start your own!", ephemeral=True
            )
            return False
        return True


class BlackjackView(OwnedGameView):
    def __init__(self, bot: ScrimBot, guild_id: int, user_id: int, bet: int) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.player = [draw_card(), draw_card()]
        self.dealer = [draw_card(), draw_card()]
        self.done = False

    def embed(self, reveal: bool = False, footer: str | None = None) -> discord.Embed:
        embed = discord.Embed(title="🃏 Blackjack", color=discord.Color.dark_green())
        dealer = (
            " ".join(self.dealer) + f"  (**{bj_value(self.dealer)}**)"
            if reveal
            else f"{self.dealer[0]} 🂠"
        )
        embed.add_field(name="Dealer", value=dealer, inline=False)
        embed.add_field(
            name="You",
            value=" ".join(self.player) + f"  (**{bj_value(self.player)}**)",
            inline=False,
        )
        embed.add_field(name="Bet", value=f"{self.bet:,} {COIN}", inline=False)
        if footer:
            embed.set_footer(text=footer)
        return embed

    async def finish(self, interaction: discord.Interaction) -> None:
        self.done = True
        for child in self.children:
            child.disabled = True
        player_total = bj_value(self.player)
        while bj_value(self.dealer) < 17:
            self.dealer.append(draw_card())
        dealer_total = bj_value(self.dealer)

        if player_total > 21:
            delta = -self.bet
            note = "Bust!"
        elif dealer_total > 21 or player_total > dealer_total:
            is_blackjack = player_total == 21 and len(self.player) == 2
            payout = math.floor(self.bet * 2.5) if is_blackjack else self.bet * 2
            await self.bot.db.add_coins(self.guild_id, self.user_id, payout)
            delta = payout - self.bet
            note = "Blackjack!" if is_blackjack else "You win!"
        elif player_total == dealer_total:
            await self.bot.db.add_coins(self.guild_id, self.user_id, self.bet)
            delta = 0
            note = "Push."
        else:
            delta = -self.bet
            note = "Dealer wins."
        await interaction.response.edit_message(
            embed=self.embed(reveal=True, footer=f"{note} {result_line(delta)}"
                             .replace("*", "")),
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.player.append(draw_card())
        if bj_value(self.player) > 21:
            await self.finish(interaction)
            return
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.success)
    async def stand(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.finish(interaction)

    async def on_timeout(self) -> None:
        if not self.done:  # treat walking away as a stand-off: refund
            await self.bot.db.add_coins(self.guild_id, self.user_id, self.bet)


# ---------------------------------------------------------------------------
# Mines
# ---------------------------------------------------------------------------


class MinesView(OwnedGameView):
    SIZE = 20  # 4 rows of 5 tiles; row 4 is the cashout row

    def __init__(
        self, bot: ScrimBot, guild_id: int, user_id: int, bet: int, mines: int
    ) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.mines = set(random.sample(range(self.SIZE), mines))
        self.revealed: set[int] = set()
        self.done = False
        self.multiplier = 1.0
        for i in range(self.SIZE):
            button = discord.ui.Button(
                label="​", emoji="🟦", row=i // 5,
                style=discord.ButtonStyle.secondary, custom_id=f"tile:{i}",
            )
            button.callback = self.make_tile_callback(i, button)
            self.add_item(button)
        cashout = discord.ui.Button(
            label="Cash out", emoji="💰", row=4, style=discord.ButtonStyle.success
        )
        cashout.callback = self.cashout
        self.add_item(cashout)

    def payout(self) -> int:
        return math.floor(self.bet * self.multiplier * 0.97)

    def embed(self, footer: str | None = None) -> discord.Embed:
        embed = discord.Embed(title="💣 Mines", color=discord.Color.dark_teal())
        embed.add_field(name="Bet", value=f"{self.bet:,} {COIN}", inline=True)
        embed.add_field(name="Mines", value=str(len(self.mines)), inline=True)
        embed.add_field(
            name="Cash out now",
            value=f"{self.payout():,} {COIN} (×{self.multiplier * 0.97:.2f})",
            inline=True,
        )
        if footer:
            embed.set_footer(text=footer)
        return embed

    def reveal_all(self) -> None:
        for child in self.children:
            child.disabled = True
            cid = getattr(child, "custom_id", "") or ""
            if cid.startswith("tile:"):
                idx = int(cid.split(":")[1])
                if idx in self.mines:
                    child.emoji = "💣"
                    child.style = discord.ButtonStyle.danger
                elif idx in self.revealed:
                    child.emoji = "✅"
                    child.style = discord.ButtonStyle.success

    def make_tile_callback(self, idx: int, button: discord.ui.Button):
        async def callback(interaction: discord.Interaction) -> None:
            if self.done:
                return
            if idx in self.mines:
                self.done = True
                self.reveal_all()
                await interaction.response.edit_message(
                    embed=self.embed(footer=f"BOOM! You lose {self.bet:,} coins."),
                    view=self,
                )
                self.stop()
                return
            self.revealed.add(idx)
            tiles_left = self.SIZE - len(self.revealed) + 1
            safe_left = tiles_left - len(self.mines)
            self.multiplier *= tiles_left / safe_left
            button.disabled = True
            button.emoji = "✅"
            button.style = discord.ButtonStyle.success
            if len(self.revealed) == self.SIZE - len(self.mines):
                await self.cashout(interaction)
                return
            await interaction.response.edit_message(embed=self.embed(), view=self)

        return callback

    async def cashout(self, interaction: discord.Interaction) -> None:
        if self.done:
            return
        if not self.revealed:
            await interaction.response.send_message(
                "Reveal at least one tile first.", ephemeral=True
            )
            return
        self.done = True
        payout = self.payout()
        await self.bot.db.add_coins(self.guild_id, self.user_id, payout)
        self.reveal_all()
        await interaction.response.edit_message(
            embed=self.embed(footer=f"Cashed out {payout:,} coins ({result_line(payout - self.bet).replace('*', '')})"),
            view=self,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if not self.done:
            self.done = True
            if self.revealed:
                await self.bot.db.add_coins(self.guild_id, self.user_id, self.payout())
            else:
                await self.bot.db.add_coins(self.guild_id, self.user_id, self.bet)


# ---------------------------------------------------------------------------
# Tower
# ---------------------------------------------------------------------------


class TowerView(OwnedGameView):
    FLOORS = 8
    STEP = 1.5 * 0.97  # one of three tiles is a bomb

    def __init__(self, bot: ScrimBot, guild_id: int, user_id: int, bet: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.floor = 0
        self.multiplier = 1.0
        self.done = False
        for i in range(3):
            button = discord.ui.Button(
                label=f"Door {i + 1}", emoji="🚪", style=discord.ButtonStyle.primary, row=0
            )
            button.callback = self.make_pick_callback()
            self.add_item(button)
        cashout = discord.ui.Button(
            label="Cash out", emoji="💰", style=discord.ButtonStyle.success, row=1
        )
        cashout.callback = self.cashout
        self.add_item(cashout)

    def payout(self) -> int:
        return math.floor(self.bet * self.multiplier)

    def embed(self, footer: str | None = None) -> discord.Embed:
        tower = "\n".join(
            ("🟩" if f < self.floor else "⬜") * 3
            for f in reversed(range(self.FLOORS))
        )
        embed = discord.Embed(
            title="🗼 Tower", description=tower, color=discord.Color.purple()
        )
        embed.add_field(name="Bet", value=f"{self.bet:,} {COIN}", inline=True)
        embed.add_field(name="Floor", value=f"{self.floor}/{self.FLOORS}", inline=True)
        embed.add_field(name="Cash out now", value=f"{self.payout():,} {COIN}", inline=True)
        if footer:
            embed.set_footer(text=footer)
        return embed

    def make_pick_callback(self):
        async def callback(interaction: discord.Interaction) -> None:
            if self.done:
                return
            if random.random() < 1 / 3:
                self.done = True
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(
                    embed=self.embed(footer=f"💥 Bomb! You lose {self.bet:,} coins."),
                    view=self,
                )
                self.stop()
                return
            self.floor += 1
            self.multiplier *= self.STEP
            if self.floor == self.FLOORS:
                await self.cashout(interaction)
                return
            await interaction.response.edit_message(embed=self.embed(), view=self)

        return callback

    async def cashout(self, interaction: discord.Interaction) -> None:
        if self.done:
            return
        if self.floor == 0:
            await interaction.response.send_message(
                "Climb at least one floor first.", ephemeral=True
            )
            return
        self.done = True
        payout = self.payout()
        await self.bot.db.add_coins(self.guild_id, self.user_id, payout)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=self.embed(footer=f"Cashed out {payout:,} coins!"), view=self
        )
        self.stop()

    async def on_timeout(self) -> None:
        if not self.done:
            self.done = True
            amount = self.payout() if self.floor else self.bet
            await self.bot.db.add_coins(self.guild_id, self.user_id, amount)


# ---------------------------------------------------------------------------
# The cog
# ---------------------------------------------------------------------------


class Casino(commands.Cog):
    """Games of chance. The house edge is small but real."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    @app_commands.command(name="coinflip", description="Flip a coin — 2x or nothing")
    @app_commands.describe(amount="Coins to bet", side="Your call")
    @app_commands.choices(
        side=[
            app_commands.Choice(name="Heads", value="heads"),
            app_commands.Choice(name="Tails", value="tails"),
        ]
    )
    async def coinflip(
        self, interaction: discord.Interaction, amount: Bet, side: app_commands.Choice[str]
    ) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        landed = random.choice(["heads", "tails"])
        if landed == side.value:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount * 2)
            outcome = f"you win {result_line(amount)}!"
        else:
            outcome = f"you lose {result_line(-amount)}."
        await interaction.response.send_message(
            f"🪙 The coin lands on **{landed}** — {outcome}"
        )

    @app_commands.command(name="dice", description="Roll 2d6 vs the house — 2x on a win")
    @app_commands.describe(amount="Coins to bet")
    async def dice(self, interaction: discord.Interaction, amount: Bet) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        you = random.randint(1, 6) + random.randint(1, 6)
        house = random.randint(1, 6) + random.randint(1, 6)
        if you > house:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount * 2)
            outcome = f"you win {result_line(amount)}!"
        elif you == house:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
            outcome = "push — bet refunded."
        else:
            outcome = f"you lose {result_line(-amount)}."
        await interaction.response.send_message(
            f"🎲 You roll **{you}**, the house rolls **{house}** — {outcome}"
        )

    @app_commands.command(name="rps", description="Rock, paper, scissors — 2x on a win")
    @app_commands.describe(amount="Coins to bet", choice="Your throw")
    @app_commands.choices(
        choice=[
            app_commands.Choice(name="Rock 🪨", value="rock"),
            app_commands.Choice(name="Paper 📄", value="paper"),
            app_commands.Choice(name="Scissors ✂️", value="scissors"),
        ]
    )
    async def rps(
        self, interaction: discord.Interaction, amount: Bet, choice: app_commands.Choice[str]
    ) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        emoji = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
        beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
        house = random.choice(list(beats))
        if beats[choice.value] == house:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount * 2)
            outcome = f"you win {result_line(amount)}!"
        elif choice.value == house:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
            outcome = "tie — bet refunded."
        else:
            outcome = f"you lose {result_line(-amount)}."
        await interaction.response.send_message(
            f"{emoji[choice.value]} vs {emoji[house]} — {outcome}"
        )

    @app_commands.command(name="slots", description="Spin the slot machine")
    @app_commands.describe(amount="Coins to bet")
    async def slots(self, interaction: discord.Interaction, amount: Bet) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        symbols = ["🍒", "🍋", "🍇", "🔔", "💎", "7️⃣"]
        weights = [30, 25, 20, 14, 8, 3]
        reels = random.choices(symbols, weights=weights, k=3)
        line = " | ".join(reels)
        if reels[0] == reels[1] == reels[2]:
            mult = {"7️⃣": 25, "💎": 10}.get(reels[0], 5)
            payout = amount * mult
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, payout)
            outcome = f"**JACKPOT ×{mult}** — {result_line(payout - amount)}!"
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            payout = amount * 2
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, payout)
            outcome = f"pair pays ×2 — {result_line(amount)}!"
        else:
            outcome = f"no luck — {result_line(-amount)}."
        await interaction.response.send_message(f"🎰 [ {line} ] — {outcome}")

    @app_commands.command(name="roulette", description="Spin the wheel")
    @app_commands.describe(
        amount="Coins to bet",
        bet_on="What you're betting on (colors/parity pay 2x, a number pays 36x)",
        number="Only if betting on a number (0-36)",
    )
    @app_commands.choices(
        bet_on=[
            app_commands.Choice(name="Red", value="red"),
            app_commands.Choice(name="Black", value="black"),
            app_commands.Choice(name="Even", value="even"),
            app_commands.Choice(name="Odd", value="odd"),
            app_commands.Choice(name="Single number (36x)", value="number"),
        ]
    )
    async def roulette(
        self,
        interaction: discord.Interaction,
        amount: Bet,
        bet_on: app_commands.Choice[str],
        number: app_commands.Range[int, 0, 36] | None = None,
    ) -> None:
        if bet_on.value == "number" and number is None:
            await interaction.response.send_message(
                "Pick a `number` between 0 and 36 for a number bet.", ephemeral=True
            )
            return
        if not await take_bet(self.bot, interaction, amount):
            return
        reds = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        spun = random.randint(0, 36)
        color = "🟢" if spun == 0 else ("🔴" if spun in reds else "⚫")
        won = (
            (bet_on.value == "red" and spun in reds)
            or (bet_on.value == "black" and spun != 0 and spun not in reds)
            or (bet_on.value == "even" and spun != 0 and spun % 2 == 0)
            or (bet_on.value == "odd" and spun % 2 == 1)
            or (bet_on.value == "number" and spun == number)
        )
        if won:
            mult = 36 if bet_on.value == "number" else 2
            payout = amount * mult
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, payout)
            outcome = f"you win {result_line(payout - amount)}!"
        else:
            outcome = f"you lose {result_line(-amount)}."
        await interaction.response.send_message(
            f"🎡 The ball lands on {color} **{spun}** — {outcome}"
        )

    PLINKO_TABLES = {
        # 9 buckets for 8 rows; edges are rare (2/256), the center is common (70/256)
        "low": [3, 1.5, 1.1, 0.9, 0.7, 0.9, 1.1, 1.5, 3],
        "medium": [7, 2.5, 1.3, 0.8, 0.4, 0.8, 1.3, 2.5, 7],
        "high": [15, 4, 1.5, 0.5, 0.2, 0.5, 1.5, 4, 15],
        "extreme": [60, 3, 0.8, 0.2, 0, 0.2, 0.8, 3, 60],
    }

    @app_commands.command(name="plinko", description="Drop a ball down the plinko board")
    @app_commands.describe(amount="Coins to bet", risk="Higher risk: bigger edges, brutal middle")
    @app_commands.choices(
        risk=[
            app_commands.Choice(name="🟢 Low (max 3x)", value="low"),
            app_commands.Choice(name="🟡 Medium (max 7x)", value="medium"),
            app_commands.Choice(name="🟠 High (max 15x)", value="high"),
            app_commands.Choice(name="🔴 Extreme (max 60x)", value="extreme"),
        ]
    )
    async def plinko(
        self,
        interaction: discord.Interaction,
        amount: Bet,
        risk: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        risk_key = risk.value if risk else "medium"
        mults = self.PLINKO_TABLES[risk_key]
        bucket = sum(random.randint(0, 1) for _ in range(8))
        mult = mults[bucket]
        payout = math.floor(amount * mult)
        if payout:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, payout)
        row = " ".join(
            f"[{m}x]" if i == bucket else f" {m}x " for i, m in enumerate(mults)
        )
        await interaction.response.send_message(
            f"🔻 **Plinko ({risk_key})** — the ball bounces into **×{mult}**!\n"
            f"`{row}`\n{result_line(payout - amount)}"
        )

    @app_commands.command(name="blackjack", description="Play a hand of blackjack")
    @app_commands.describe(amount="Coins to bet")
    async def blackjack(self, interaction: discord.Interaction, amount: Bet) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        view = BlackjackView(self.bot, interaction.guild_id, interaction.user.id, amount)
        await interaction.response.send_message(
            embed=view.embed(), view=view, ephemeral=True
        )

    @app_commands.command(name="mines", description="Reveal tiles, dodge the mines, cash out")
    @app_commands.describe(amount="Coins to bet", mines="How many mines on the board (1-10)")
    async def mines(
        self,
        interaction: discord.Interaction,
        amount: Bet,
        mines: app_commands.Range[int, 1, 10] = 3,
    ) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        view = MinesView(self.bot, interaction.guild_id, interaction.user.id, amount, mines)
        await interaction.response.send_message(
            embed=view.embed(), view=view, ephemeral=True
        )

    @app_commands.command(name="tower", description="Climb the tower — 1 in 3 doors is a bomb")
    @app_commands.describe(amount="Coins to bet")
    async def tower(self, interaction: discord.Interaction, amount: Bet) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        view = TowerView(self.bot, interaction.guild_id, interaction.user.id, amount)
        await interaction.response.send_message(
            embed=view.embed(), view=view, ephemeral=True
        )

    @app_commands.command(name="baccarat", description="Player, banker, or tie?")
    @app_commands.describe(amount="Coins to bet", bet_on="Which hand you're backing")
    @app_commands.choices(
        bet_on=[
            app_commands.Choice(name="Player (2x)", value="player"),
            app_commands.Choice(name="Banker (1.95x)", value="banker"),
            app_commands.Choice(name="Tie (9x)", value="tie"),
        ]
    )
    async def baccarat(
        self, interaction: discord.Interaction, amount: Bet, bet_on: app_commands.Choice[str]
    ) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        player = [draw_card(), draw_card()]
        banker = [draw_card(), draw_card()]
        pv, bv = baccarat_value(player), baccarat_value(banker)

        if pv < 8 and bv < 8:  # no natural — third-card rules
            player_third = None
            if pv <= 5:
                player.append(draw_card())
                player_third = baccarat_value([player[2]])
                pv = baccarat_value(player)
            if player_third is None:
                if bv <= 5:
                    banker.append(draw_card())
            else:
                draw = (
                    bv <= 2
                    or (bv == 3 and player_third != 8)
                    or (bv == 4 and 2 <= player_third <= 7)
                    or (bv == 5 and 4 <= player_third <= 7)
                    or (bv == 6 and 6 <= player_third <= 7)
                )
                if draw:
                    banker.append(draw_card())
            bv = baccarat_value(banker)

        winner = "tie" if pv == bv else ("player" if pv > bv else "banker")
        if bet_on.value == winner:
            payout = {
                "player": amount * 2,
                "banker": math.floor(amount * 1.95),
                "tie": amount * 9,
            }[winner]
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, payout)
            outcome = f"you win {result_line(payout - amount)}!"
        elif winner == "tie":
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
            outcome = "tie — bet refunded."
        else:
            outcome = f"you lose {result_line(-amount)}."
        await interaction.response.send_message(
            f"🎴 Player: {' '.join(player)} (**{pv}**) · "
            f"Banker: {' '.join(banker)} (**{bv}**) — **{winner.upper()}** wins. {outcome}"
        )


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(Casino(bot))
