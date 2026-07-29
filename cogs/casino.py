"""Casino games: coinflip, dice, RPS, slots, roulette, plinko, blackjack,
mines, tower, and baccarat. Visual games render PNG images (Pillow); if
Pillow is unavailable they fall back to text so nothing breaks.

Instant games (slots, dice, coinflip, rps, roulette, plinko, baccarat) can
be dealt into a betting window in place — pass panel=<BetPanel> and the
result is edited into that message instead of posting a new one.
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

try:  # graphics are optional — degrade to text if Pillow is missing
    import render
except Exception:  # pragma: no cover
    render = None

if TYPE_CHECKING:
    from bot import ScrimBot

COIN = "🪙"
Bet = app_commands.Range[int, 10, 250_000]

CARD_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
CARD_SUITS = ["♠", "♥", "♦", "♣"]

# Games that can render into a betting window instead of a new message.
INSTANT_GAMES = {"slots", "dice", "coinflip", "rps", "roulette", "plinko", "baccarat"}


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


WIN_COLOR = (105, 219, 124)
LOSE_COLOR = (255, 120, 120)


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

    def image(self, reveal: bool, footer: str, color) -> discord.File | None:
        if render is None:
            return None
        return render.cards_table(
            self.dealer, self.player, dealer_hidden=not reveal,
            dealer_total=bj_value(self.dealer), player_total=bj_value(self.player),
            bet=self.bet, footer=footer, color=color,
        )

    def make_embed(self, reveal: bool = False, footer: str | None = None,
                   has_image: bool = True) -> discord.Embed:
        embed = discord.Embed(title="🃏 Blackjack", color=discord.Color.dark_green())
        if has_image:
            embed.set_image(url="attachment://cards.png")
        else:  # text fallback
            dealer = (" ".join(self.dealer) + f"  (**{bj_value(self.dealer)}**)"
                      if reveal else f"{self.dealer[0]} 🂠")
            embed.add_field(name="Dealer", value=dealer, inline=False)
            embed.add_field(name="You",
                            value=" ".join(self.player) + f"  (**{bj_value(self.player)}**)",
                            inline=False)
            embed.add_field(name="Bet", value=f"{self.bet:,} {COIN}", inline=False)
        if footer:
            embed.set_footer(text=footer)
        return embed

    async def _edit(self, interaction, reveal, footer, color) -> None:
        file = self.image(reveal, footer or "", color)
        embed = self.make_embed(reveal=reveal, footer=footer, has_image=file is not None)
        kwargs = {"embed": embed, "view": self}
        if file is not None:
            kwargs["attachments"] = [file]
        await interaction.response.edit_message(**kwargs)

    async def finish(self, interaction: discord.Interaction) -> None:
        self.done = True
        for child in self.children:
            child.disabled = True
        player_total = bj_value(self.player)
        while bj_value(self.dealer) < 17:
            self.dealer.append(draw_card())
        dealer_total = bj_value(self.dealer)

        if player_total > 21:
            delta, note, color = -self.bet, "Bust!", LOSE_COLOR
        elif dealer_total > 21 or player_total > dealer_total:
            is_bj = player_total == 21 and len(self.player) == 2
            payout = math.floor(self.bet * 2.5) if is_bj else self.bet * 2
            await self.bot.db.add_coins(self.guild_id, self.user_id, payout)
            delta, note, color = payout - self.bet, ("Blackjack!" if is_bj else "You win!"), WIN_COLOR
        elif player_total == dealer_total:
            await self.bot.db.add_coins(self.guild_id, self.user_id, self.bet)
            delta, note, color = 0, "Push.", (230, 232, 238)
        else:
            delta, note, color = -self.bet, "Dealer wins.", LOSE_COLOR
        footer = f"{note} {result_line(delta)}".replace("*", "")
        await self._edit(interaction, True, footer, color)
        self.stop()

    @discord.ui.button(label="Hit", emoji="🃏", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.player.append(draw_card())
        if bj_value(self.player) >= 21:  # bust or 21 auto-stands
            await self.finish(interaction)
            return
        await self._edit(interaction, False, "Your move — Hit or Stand?", (230, 232, 238))

    @discord.ui.button(label="Stand", emoji="✋", style=discord.ButtonStyle.success)
    async def stand(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.finish(interaction)

    async def on_timeout(self) -> None:
        if not self.done:  # walking away refunds
            await self.bot.db.add_coins(self.guild_id, self.user_id, self.bet)


# ---------------------------------------------------------------------------
# Mines — 5x5, image board, tile picker + cash out
# ---------------------------------------------------------------------------


class MinesView(OwnedGameView):
    COLS = 5
    SIZE = 25

    def __init__(
        self, bot: ScrimBot, guild_id: int, user_id: int, bet: int, mines: int
    ) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.mines = set(random.sample(range(self.SIZE), min(mines, self.SIZE - 1)))
        self.revealed: set[int] = set()
        self.done = False
        self.multiplier = 1.0
        self.build()

    def payout(self) -> int:
        return math.floor(self.bet * self.multiplier * 0.97)

    def image(self, reveal_all: bool, footer: str | None) -> discord.File | None:
        if render is None:
            return None
        return render.mines_grid(
            self.SIZE, self.COLS, self.revealed, self.mines, reveal_all,
            self.multiplier * 0.97, self.payout(), footer,
        )

    def make_embed(self, footer: str | None = None, has_image: bool = True) -> discord.Embed:
        embed = discord.Embed(title="💣 Mines", color=discord.Color.dark_teal())
        if has_image:
            embed.set_image(url="attachment://mines.png")
        else:
            embed.add_field(name="Mines", value=str(len(self.mines)), inline=True)
            embed.add_field(name="Revealed", value=str(len(self.revealed)), inline=True)
            embed.add_field(name="Cash out", value=f"{self.payout():,} {COIN}", inline=True)
        if footer:
            embed.set_footer(text=footer)
        return embed

    def build(self) -> None:
        self.clear_items()
        remaining = [i for i in range(self.SIZE) if i not in self.revealed]
        if remaining and not self.done:
            select = discord.ui.Select(
                placeholder="🔎 Reveal a tile…",
                options=[
                    discord.SelectOption(label=f"Tile {i + 1}", value=str(i))
                    for i in remaining
                ][:25],
                row=0,
            )
            select.callback = self._pick(select)
            self.add_item(select)
        cash = discord.ui.Button(
            label=f"Cash out ({self.payout():,})", emoji="💰",
            style=discord.ButtonStyle.success, row=1,
            disabled=self.done or not self.revealed,
        )
        cash.callback = self.cashout
        self.add_item(cash)

    async def _edit(self, interaction, reveal_all, footer) -> None:
        file = self.image(reveal_all, footer)
        embed = self.make_embed(footer=footer, has_image=file is not None)
        kwargs = {"embed": embed, "view": self}
        if file is not None:
            kwargs["attachments"] = [file]
        await interaction.response.edit_message(**kwargs)

    def _pick(self, select: discord.ui.Select):
        async def callback(interaction: discord.Interaction) -> None:
            if self.done:
                return
            idx = int(select.values[0])
            if idx in self.mines:
                self.done = True
                self.build()
                await self._edit(interaction, True, f"💥 BOOM! You lose {self.bet:,} coins.")
                self.stop()
                return
            self.revealed.add(idx)
            tiles_left = self.SIZE - len(self.revealed) + 1
            safe_left = tiles_left - len(self.mines)
            self.multiplier *= tiles_left / safe_left
            if len(self.revealed) == self.SIZE - len(self.mines):
                await self.cashout(interaction)
                return
            self.build()
            await self._edit(interaction, False, None)

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
        self.build()
        await self._edit(
            interaction, True,
            f"💰 Cashed out {payout:,} coins ({result_line(payout - self.bet).replace('*', '')})",
        )
        self.stop()

    async def on_timeout(self) -> None:
        if not self.done:
            self.done = True
            payout = self.payout() if self.revealed else self.bet
            await self.bot.db.add_coins(self.guild_id, self.user_id, payout)


# ---------------------------------------------------------------------------
# Tower (unchanged, text/emoji tower is fine)
# ---------------------------------------------------------------------------


class TowerView(OwnedGameView):
    FLOORS = 8
    STEP = 1.5 * 0.97  # one of three doors is a bomb

    def __init__(self, bot: ScrimBot, guild_id: int, user_id: int, bet: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.floor = 0
        self.multiplier = 1.0
        self.done = False
        for _ in range(3):
            button = discord.ui.Button(
                label="Door", emoji="🚪", style=discord.ButtonStyle.primary, row=0
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
        embed = discord.Embed(title="🗼 Tower", description=tower, color=discord.Color.purple())
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

    PLINKO_TABLES = {
        # 9 buckets for 8 rows; edges are rare (1/256 each), center common (70/256)
        "low": [10, 2, 1.1, 0.7, 0.5, 0.7, 1.1, 2, 10],
        "medium": [25, 2.5, 1.2, 0.5, 0.3, 0.5, 1.2, 2.5, 25],
        "high": [50, 3, 0.8, 0.3, 0.15, 0.3, 0.8, 3, 50],
        "extreme": [90, 2, 0.3, 0.1, 0, 0.1, 0.3, 2, 90],
    }

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    async def _deliver(
        self, interaction: discord.Interaction, content: str, *, panel=None, file=None
    ) -> None:
        """Send a result, or edit it into a betting window when panel is given."""
        if panel is not None:
            econ = await self.bot.db.get_econ(interaction.guild_id, interaction.user.id)
            panel.balance = econ["balance"]
            panel.build()
            embed = panel.result_embed(content, file)
            await interaction.response.edit_message(
                embed=embed, attachments=[file] if file else [], view=panel
            )
        elif file is not None:
            await interaction.response.send_message(content, file=file)
        else:
            await interaction.response.send_message(content)

    # ---- instant games (logic in _do_*, so panel stays off slash sigs) ---

    async def run_from_panel(self, game, interaction, panel, bet, option, number):
        """Dispatch a betting-window Play to the matching game helper."""
        if game == "slots":
            await self._do_slots(interaction, bet, panel)
        elif game == "dice":
            await self._do_dice(interaction, bet, panel)
        elif game == "coinflip":
            await self._do_coinflip(interaction, bet, option, panel)
        elif game == "rps":
            await self._do_rps(interaction, bet, option, panel)
        elif game == "roulette":
            await self._do_roulette(interaction, bet, option, number, panel)
        elif game == "plinko":
            await self._do_plinko(interaction, bet, option, panel)
        elif game == "baccarat":
            await self._do_baccarat(interaction, bet, option, panel)

    async def _do_coinflip(self, interaction, amount, side, panel=None):
        if not await take_bet(self.bot, interaction, amount):
            return
        landed = random.choice(["heads", "tails"])
        if landed == side:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount * 2)
            outcome = f"you win {result_line(amount)}!"
        else:
            outcome = f"you lose {result_line(-amount)}."
        await self._deliver(
            interaction, f"\U0001fa99 The coin lands on **{landed}** \u2014 {outcome}", panel=panel
        )

    async def _do_dice(self, interaction, amount, panel=None):
        if not await take_bet(self.bot, interaction, amount):
            return
        you = random.randint(1, 6) + random.randint(1, 6)
        house = random.randint(1, 6) + random.randint(1, 6)
        if you > house:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount * 2)
            outcome = f"you win {result_line(amount)}!"
        elif you == house:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
            outcome = "push \u2014 bet refunded."
        else:
            outcome = f"you lose {result_line(-amount)}."
        await self._deliver(
            interaction, f"\U0001f3b2 You roll **{you}**, the house rolls **{house}** \u2014 {outcome}",
            panel=panel,
        )

    async def _do_rps(self, interaction, amount, throw, panel=None):
        if not await take_bet(self.bot, interaction, amount):
            return
        emoji = {"rock": "\U0001faa8", "paper": "\U0001f4c4", "scissors": "\u2702\ufe0f"}
        beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
        house = random.choice(list(beats))
        if beats[throw] == house:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount * 2)
            outcome = f"you win {result_line(amount)}!"
        elif throw == house:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
            outcome = "tie \u2014 bet refunded."
        else:
            outcome = f"you lose {result_line(-amount)}."
        await self._deliver(
            interaction, f"{emoji[throw]} vs {emoji[house]} \u2014 {outcome}", panel=panel
        )

    async def _do_slots(self, interaction, amount, panel=None):
        if not await take_bet(self.bot, interaction, amount):
            return
        symbols = ["\U0001f352", "\U0001f34b", "\U0001f347", "\U0001f514", "\U0001f48e", "7\ufe0f\u20e3"]
        weights = [30, 25, 20, 14, 8, 3]
        reels = random.choices(symbols, weights=weights, k=3)
        if reels[0] == reels[1] == reels[2]:
            mult = {"7\ufe0f\u20e3": 25, "\U0001f48e": 10}.get(reels[0], 5)
            payout, won, label = amount * mult, True, f"JACKPOT \u00d7{mult}"
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            payout, won, label = amount * 2, True, "Pair \u00d72"
        else:
            payout, won, label = 0, False, "No win"
        if payout:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, payout)
        file = render.slots(reels, label, won) if render else None
        content = f"\U0001f3b0 [ {' | '.join(reels)} ] \u2014 **{label}** {result_line(payout - amount)}"
        await self._deliver(interaction, content, panel=panel, file=file)

    async def _do_roulette(self, interaction, amount, bet_on, number, panel=None):
        if bet_on == "number" and number is None:
            await interaction.response.send_message(
                "Pick a `number` between 0 and 36 for a number bet.", ephemeral=True
            )
            return
        if not await take_bet(self.bot, interaction, amount):
            return
        reds = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        spun = random.randint(0, 36)
        won = (
            (bet_on == "red" and spun in reds)
            or (bet_on == "black" and spun != 0 and spun not in reds)
            or (bet_on == "even" and spun != 0 and spun % 2 == 0)
            or (bet_on == "odd" and spun % 2 == 1)
            or (bet_on == "number" and spun == number)
        )
        if won:
            mult = 36 if bet_on == "number" else 2
            payout = amount * mult
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, payout)
            outcome = f"you win {result_line(payout - amount)}!"
        else:
            payout = 0
            outcome = f"you lose {result_line(-amount)}."
        file = render.roulette_result(spun, won, payout - amount) if render else None
        await self._deliver(
            interaction, f"\U0001f3a1 The ball lands on **{spun}** \u2014 {outcome}",
            panel=panel, file=file,
        )

    async def _do_plinko(self, interaction, amount, risk, panel=None):
        if not await take_bet(self.bot, interaction, amount):
            return
        risk_key = risk or "medium"
        mults = self.PLINKO_TABLES[risk_key]
        bucket = sum(random.randint(0, 1) for _ in range(8))
        mult = mults[bucket]
        payout = math.floor(amount * mult)
        if payout:
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, payout)
        file = render.plinko_board(mults, bucket, risk_key, payout, amount) if render else None
        content = f"\U0001f53b **Plinko ({risk_key})** \u2014 the ball drops into **\u00d7{mult}** {result_line(payout - amount)}"
        await self._deliver(interaction, content, panel=panel, file=file)

    async def _do_baccarat(self, interaction, amount, bet_on, panel=None):
        if not await take_bet(self.bot, interaction, amount):
            return
        player = [draw_card(), draw_card()]
        banker = [draw_card(), draw_card()]
        pv, bv = baccarat_value(player), baccarat_value(banker)
        if pv < 8 and bv < 8:
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
        if bet_on == winner:
            payout = {"player": amount * 2, "banker": math.floor(amount * 1.95),
                      "tie": amount * 9}[winner]
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, payout)
            outcome = f"you win {result_line(payout - amount)}!"
        elif winner == "tie":
            await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
            outcome = "tie \u2014 bet refunded."
        else:
            outcome = f"you lose {result_line(-amount)}."
        await self._deliver(
            interaction,
            f"\U0001f3b4 Player {' '.join(player)} (**{pv}**) \u00b7 Banker {' '.join(banker)} "
            f"(**{bv}**) \u2014 **{winner.upper()}** wins. {outcome}",
            panel=panel,
        )

    # ---- instant game slash/prefix wrappers -------------------------------

    @app_commands.command(name="coinflip", description="Flip a coin \u2014 2x or nothing")
    @app_commands.describe(amount="Coins to bet", side="Your call")
    @app_commands.choices(side=[
        app_commands.Choice(name="Heads", value="heads"),
        app_commands.Choice(name="Tails", value="tails"),
    ])
    async def coinflip(self, interaction, amount: Bet, side: app_commands.Choice[str]):
        await self._do_coinflip(interaction, amount, side.value)

    @app_commands.command(name="dice", description="Roll 2d6 vs the house \u2014 2x on a win")
    @app_commands.describe(amount="Coins to bet")
    async def dice(self, interaction, amount: Bet):
        await self._do_dice(interaction, amount)

    @app_commands.command(name="rps", description="Rock, paper, scissors \u2014 2x on a win")
    @app_commands.describe(amount="Coins to bet", choice="Your throw")
    @app_commands.choices(choice=[
        app_commands.Choice(name="Rock", value="rock"),
        app_commands.Choice(name="Paper", value="paper"),
        app_commands.Choice(name="Scissors", value="scissors"),
    ])
    async def rps(self, interaction, amount: Bet, choice: app_commands.Choice[str]):
        await self._do_rps(interaction, amount, choice.value)

    @app_commands.command(name="slots", description="Spin the slot machine")
    @app_commands.describe(amount="Coins to bet")
    async def slots(self, interaction, amount: Bet):
        await self._do_slots(interaction, amount)

    @app_commands.command(name="roulette", description="Spin the wheel")
    @app_commands.describe(amount="Coins to bet", bet_on="What you bet on", number="Number 0-36")
    @app_commands.choices(bet_on=[
        app_commands.Choice(name="Red", value="red"),
        app_commands.Choice(name="Black", value="black"),
        app_commands.Choice(name="Even", value="even"),
        app_commands.Choice(name="Odd", value="odd"),
        app_commands.Choice(name="Single number (36x)", value="number"),
    ])
    async def roulette(self, interaction, amount: Bet, bet_on: app_commands.Choice[str],
                       number: app_commands.Range[int, 0, 36] | None = None):
        await self._do_roulette(interaction, amount, bet_on.value, number)

    @app_commands.command(name="plinko", description="Drop a ball down the plinko board")
    @app_commands.describe(amount="Coins to bet", risk="Higher risk: bigger edges")
    @app_commands.choices(risk=[
        app_commands.Choice(name="\U0001f7e2 Low (max 10x)", value="low"),
        app_commands.Choice(name="\U0001f7e1 Medium (max 25x)", value="medium"),
        app_commands.Choice(name="\U0001f7e0 High (max 50x)", value="high"),
        app_commands.Choice(name="\U0001f534 Extreme (max 90x)", value="extreme"),
    ])
    async def plinko(self, interaction, amount: Bet,
                     risk: app_commands.Choice[str] | None = None):
        await self._do_plinko(interaction, amount, risk.value if risk else None)

    @app_commands.command(name="baccarat", description="Player, banker, or tie?")
    @app_commands.describe(amount="Coins to bet", bet_on="Which hand you're backing")
    @app_commands.choices(bet_on=[
        app_commands.Choice(name="Player (2x)", value="player"),
        app_commands.Choice(name="Banker (1.95x)", value="banker"),
        app_commands.Choice(name="Tie (9x)", value="tie"),
    ])
    async def baccarat(self, interaction, amount: Bet, bet_on: app_commands.Choice[str]):
        await self._do_baccarat(interaction, amount, bet_on.value)

    # ---- interactive games (own message) ----------------------------------

    @app_commands.command(name="blackjack", description="Play a hand of blackjack")
    @app_commands.describe(amount="Coins to bet")
    async def blackjack(self, interaction: discord.Interaction, amount: Bet) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        view = BlackjackView(self.bot, interaction.guild_id, interaction.user.id, amount)

        if bj_value(view.player) == 21:  # natural
            view.done = True
            while bj_value(view.dealer) < 17:
                view.dealer.append(draw_card())
            if bj_value(view.dealer) == 21 and len(view.dealer) == 2:
                await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, amount)
                note, delta, color = "Both have blackjack — push.", 0, (230, 232, 238)
            else:
                payout = math.floor(amount * 2.5)
                await self.bot.db.add_coins(interaction.guild_id, interaction.user.id, payout)
                note, delta, color = "BLACKJACK! Instant win!", payout - amount, WIN_COLOR
            footer = f"{note} {result_line(delta)}".replace("*", "")
            file = view.image(True, footer, color)
            embed = view.make_embed(reveal=True, footer=footer, has_image=file is not None)
            await interaction.response.send_message(
                embed=embed, file=file, ephemeral=True
            ) if file else await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        footer = "Your move — Hit or Stand?"
        file = view.image(False, footer, (230, 232, 238))
        embed = view.make_embed(footer=footer, has_image=file is not None)
        if file:
            await interaction.response.send_message(
                embed=embed, file=file, view=view, ephemeral=True
            )
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="mines", description="Reveal tiles, dodge the mines, cash out")
    @app_commands.describe(amount="Coins to bet", mines="How many mines on the 5×5 board (1-24)")
    async def mines(
        self, interaction: discord.Interaction, amount: Bet,
        mines: app_commands.Range[int, 1, 24] = 3,
    ) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        view = MinesView(self.bot, interaction.guild_id, interaction.user.id, amount, mines)
        file = view.image(False, None)
        embed = view.make_embed(has_image=file is not None)
        if file:
            await interaction.response.send_message(
                embed=embed, file=file, view=view, ephemeral=True
            )
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="tower", description="Climb the tower — 1 in 3 doors is a bomb")
    @app_commands.describe(amount="Coins to bet")
    async def tower(self, interaction: discord.Interaction, amount: Bet) -> None:
        if not await take_bet(self.bot, interaction, amount):
            return
        view = TowerView(self.bot, interaction.guild_id, interaction.user.id, amount)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(Casino(bot))
