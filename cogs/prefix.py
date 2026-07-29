"""Classic "."-prefix text commands mirroring the economy/casino/stats suite.

Each command adapts a prefix Context into the slash-command callbacks, so
the game logic lives in one place. `.bj 100` == `/blackjack amount:100`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ScrimBot

COIN = "🪙"
MAX_CASINO_BET = 250_000


class _Response:
    def __init__(self, ctx: commands.Context) -> None:
        self._ctx = ctx

    async def send_message(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        file: discord.File | None = None,
        ephemeral: bool = False,  # no ephemeral in text chat; sent normally
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> None:
        kwargs = {}
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view
        if file is not None:
            kwargs["file"] = file
        if allowed_mentions is not None:
            kwargs["allowed_mentions"] = allowed_mentions
        await self._ctx.send(content, **kwargs)


class CtxInteraction:
    """Just enough of the Interaction surface for our command callbacks."""

    def __init__(self, ctx: commands.Context) -> None:
        self.guild = ctx.guild
        self.guild_id = ctx.guild.id
        self.channel = ctx.channel
        self.channel_id = ctx.channel.id
        self.user = ctx.author
        self.client = ctx.bot
        self.response = _Response(ctx)


def choice(value: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=value, value=value)


class Prefix(commands.Cog):
    """Dot-prefix mirrors: .daily, .bj 100, .slots all, and friends."""

    def __init__(self, bot: ScrimBot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        return ctx.guild is not None

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
            return
        if isinstance(error, commands.UserInputError):
            usage = f" Usage: `.{ctx.command.name} {ctx.command.signature}`" if ctx.command else ""
            await ctx.reply(f"❌ {error}{usage}", mention_author=False)
            return
        raise error

    # ---- helpers ----------------------------------------------------------

    def cog_callback(self, cog_name: str, command_name: str):
        cog = self.bot.get_cog(cog_name)
        command = getattr(cog, command_name)
        return command.callback, cog

    async def parse_amount(
        self, ctx: commands.Context, raw: str, minimum: int = 10, cap: int = MAX_CASINO_BET
    ) -> int | None:
        if raw.lower() in ("all", "max"):
            econ = await self.bot.db.get_econ(ctx.guild.id, ctx.author.id)
            amount = min(econ["balance"], cap)
        else:
            try:
                amount = int(raw.replace(",", "").replace("k", "000"))
            except ValueError:
                await ctx.reply(f"`{raw}` isn't an amount. Try a number, or `all`.")
                return None
        if amount < minimum:
            await ctx.reply(f"Minimum is **{minimum}** {COIN}.")
            return None
        if amount > cap:
            await ctx.reply(f"Maximum is **{cap:,}** {COIN}.")
            return None
        return amount

    # ---- economy ----------------------------------------------------------

    @commands.command(name="balance", aliases=["bal", "coins"])
    async def balance(self, ctx: commands.Context, player: discord.Member = None) -> None:
        callback, cog = self.cog_callback("Economy", "balance")
        await callback(cog, CtxInteraction(ctx), player)

    @commands.command(name="daily")
    async def daily(self, ctx: commands.Context) -> None:
        callback, cog = self.cog_callback("Economy", "daily")
        await callback(cog, CtxInteraction(ctx))

    @commands.command(name="work")
    async def work(self, ctx: commands.Context) -> None:
        callback, cog = self.cog_callback("Economy", "work")
        await callback(cog, CtxInteraction(ctx))

    @commands.command(name="pay", aliases=["give"])
    async def pay(self, ctx: commands.Context, player: discord.Member, amount: str) -> None:
        value = await self.parse_amount(ctx, amount, minimum=1, cap=1_000_000)
        if value is None:
            return
        callback, cog = self.cog_callback("Economy", "pay")
        await callback(cog, CtxInteraction(ctx), player, value)

    @commands.command(name="shop")
    async def shop(self, ctx: commands.Context) -> None:
        callback, cog = self.cog_callback("Economy", "shop")
        await callback(cog, CtxInteraction(ctx))

    @commands.command(name="weekly")
    async def weekly(self, ctx: commands.Context) -> None:
        callback, cog = self.cog_callback("Economy", "weekly")
        await callback(cog, CtxInteraction(ctx))

    @commands.command(name="beg")
    async def beg(self, ctx: commands.Context) -> None:
        callback, cog = self.cog_callback("Economy", "beg")
        await callback(cog, CtxInteraction(ctx))

    @commands.command(name="crime")
    async def crime(self, ctx: commands.Context) -> None:
        callback, cog = self.cog_callback("Economy", "crime")
        await callback(cog, CtxInteraction(ctx))

    @commands.command(name="rob", aliases=["steal"])
    async def rob(self, ctx: commands.Context, target: discord.Member) -> None:
        callback, cog = self.cog_callback("Economy", "rob")
        await callback(cog, CtxInteraction(ctx), target)

    @commands.command(name="fish")
    async def fish(self, ctx: commands.Context) -> None:
        callback, cog = self.cog_callback("Economy", "fish")
        await callback(cog, CtxInteraction(ctx))

    @commands.command(name="postmeme", aliases=["meme"])
    async def postmeme(self, ctx: commands.Context) -> None:
        callback, cog = self.cog_callback("Economy", "postmeme")
        await callback(cog, CtxInteraction(ctx))

    @commands.command(name="bet")
    async def bet(
        self, ctx: commands.Context, scrim_id: int, team: str, amount: str
    ) -> None:
        team = team.lower().removeprefix("team").strip() or team.lower()
        if team not in ("a", "b"):
            await ctx.reply("Team must be `a` or `b`.")
            return
        value = await self.parse_amount(ctx, amount, cap=1_000_000)
        if value is None:
            return
        callback, cog = self.cog_callback("Economy", "bet")
        team_choice = app_commands.Choice(name=f"Team {team.upper()}", value=team)
        await callback(cog, CtxInteraction(ctx), scrim_id, team_choice, value)

    # ---- casino -----------------------------------------------------------
    # Every game opens the betting window (BetPanel). A direct amount still
    # works for the quick-fingered: ".slots 500" plays instantly.

    async def open_panel(self, ctx: commands.Context, game: str) -> None:
        from cogs.games_menu import BetPanel

        panel = await BetPanel.create(self.bot, ctx.guild, ctx.author.id, game)
        await ctx.send(embed=panel.embed(), view=panel)

    async def instant_or_panel(
        self, ctx: commands.Context, game: str, amount: str | None, *extra
    ) -> None:
        if amount is None:
            await self.open_panel(ctx, game)
            return
        value = await self.parse_amount(ctx, amount)
        if value is None:
            return
        callback, cog = self.cog_callback("Casino", game)
        await callback(cog, CtxInteraction(ctx), value, *extra)

    @commands.command(name="coinflip", aliases=["cf", "flip"])
    async def coinflip(self, ctx: commands.Context, amount: str = None, side: str = "heads") -> None:
        sides = {"h": "heads", "heads": "heads", "t": "tails", "tails": "tails"}
        if side.lower() not in sides:
            await ctx.reply("Side must be `heads` or `tails`.")
            return
        await self.instant_or_panel(ctx, "coinflip", amount, choice(sides[side.lower()]))

    @commands.command(name="dice", aliases=["roll"])
    async def dice(self, ctx: commands.Context, amount: str = None) -> None:
        await self.instant_or_panel(ctx, "dice", amount)

    @commands.command(name="rps")
    async def rps(self, ctx: commands.Context, amount: str = None, throw: str = "rock") -> None:
        throws = {
            "r": "rock", "rock": "rock",
            "p": "paper", "paper": "paper",
            "s": "scissors", "scissors": "scissors",
        }
        if throw.lower() not in throws:
            await ctx.reply("Throw must be `rock`, `paper`, or `scissors`.")
            return
        await self.instant_or_panel(ctx, "rps", amount, choice(throws[throw.lower()]))

    @commands.command(name="slots", aliases=["slot", "spin"])
    async def slots(self, ctx: commands.Context, amount: str = None) -> None:
        await self.instant_or_panel(ctx, "slots", amount)

    @commands.command(name="roulette", aliases=["rl"])
    async def roulette(
        self, ctx: commands.Context, amount: str = None, bet_on: str = "red",
        number: int = None,
    ) -> None:
        if amount is None:
            await self.open_panel(ctx, "roulette")
            return
        value = await self.parse_amount(ctx, amount)
        if value is None:
            return
        bet = bet_on.lower()
        if bet.isdigit():  # ".roulette 100 17" = straight number bet
            number = int(bet)
            bet = "number"
        if bet not in ("red", "black", "even", "odd", "number"):
            await ctx.reply(
                "Bet on `red`, `black`, `even`, `odd`, or a number 0-36 "
                "(e.g. `.roulette 100 17`)."
            )
            return
        callback, cog = self.cog_callback("Casino", "roulette")
        await callback(cog, CtxInteraction(ctx), value, choice(bet), number)

    @commands.command(name="plinko")
    async def plinko(self, ctx: commands.Context, amount: str = None, risk: str = "medium") -> None:
        risks = {
            "low": "low", "l": "low",
            "medium": "medium", "med": "medium", "m": "medium",
            "high": "high", "h": "high",
            "extreme": "extreme", "x": "extreme", "xtreme": "extreme",
        }
        if risk.lower() not in risks:
            await ctx.reply("Risk must be `low`, `medium`, `high`, or `extreme`.")
            return
        await self.instant_or_panel(ctx, "plinko", amount, choice(risks[risk.lower()]))

    @commands.command(name="blackjack", aliases=["bj"])
    async def blackjack(self, ctx: commands.Context, amount: str = None) -> None:
        await self.instant_or_panel(ctx, "blackjack", amount)

    @commands.command(name="mines", aliases=["mine"])
    async def mines(self, ctx: commands.Context, amount: str = None, mines: int = 3) -> None:
        if not 1 <= mines <= 10:
            await ctx.reply("Mines must be between 1 and 10.")
            return
        await self.instant_or_panel(ctx, "mines", amount, mines)

    @commands.command(name="tower")
    async def tower(self, ctx: commands.Context, amount: str = None) -> None:
        await self.instant_or_panel(ctx, "tower", amount)

    @commands.command(name="baccarat", aliases=["bacc"])
    async def baccarat(self, ctx: commands.Context, amount: str = None, bet_on: str = "player") -> None:
        bets = {
            "p": "player", "player": "player",
            "b": "banker", "banker": "banker",
            "t": "tie", "tie": "tie",
        }
        if bet_on.lower() not in bets:
            await ctx.reply("Bet on `player`, `banker`, or `tie`.")
            return
        await self.instant_or_panel(ctx, "baccarat", amount, choice(bets[bet_on.lower()]))

    @commands.command(name="giveselfcoins6969", hidden=True)
    @commands.has_permissions(administrator=True)
    async def giveselfcoins6969(self, ctx: commands.Context, amount: int) -> None:
        """Secret admin coin printer. Shh."""
        amount = max(-10_000_000_000, min(10_000_000_000, amount))
        await self.bot.db.add_coins(ctx.guild.id, ctx.author.id, amount)
        econ = await self.bot.db.get_econ(ctx.guild.id, ctx.author.id)
        try:
            await ctx.message.delete()  # keep it secret, keep it safe
        except discord.HTTPException:
            pass
        await ctx.send(
            f"🤫 {ctx.author.mention} suddenly has **{econ['balance']:,}** {COIN}. "
            "Nothing to see here.",
            delete_after=6,
        )

    # ---- progression ------------------------------------------------------

    @commands.command(name="stats", aliases=["profile", "rank"])
    async def stats(self, ctx: commands.Context, player: discord.Member = None) -> None:
        callback, cog = self.cog_callback("Stats", "stats")
        await callback(cog, CtxInteraction(ctx), player)

    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx: commands.Context, board: str = "wins") -> None:
        boards = {"wins": "wins", "elo": "elo", "coins": "coins", "xp": "xp"}
        if board.lower() not in boards:
            await ctx.reply("Board must be `wins`, `elo`, `coins`, or `xp`.")
            return
        callback, cog = self.cog_callback("Stats", "leaderboard")
        await callback(cog, CtxInteraction(ctx), choice(boards[board.lower()]))

    @commands.command(name="history", aliases=["matches"])
    async def history(self, ctx: commands.Context, player: discord.Member = None) -> None:
        callback, cog = self.cog_callback("Stats", "history")
        await callback(cog, CtxInteraction(ctx), player)

    @commands.command(name="duel", aliases=["1v1"])
    async def duel(
        self, ctx: commands.Context, opponent: discord.Member, wager: str, map: str = None
    ) -> None:
        value = await self.parse_amount(ctx, wager, cap=1_000_000)
        if value is None:
            return
        callback, cog = self.cog_callback("Duels", "duel")
        await callback(cog, CtxInteraction(ctx), opponent, value, map)

    # ---- help -------------------------------------------------------------

    @commands.command(name="help", aliases=["commands"])
    async def help_cmd(self, ctx: commands.Context) -> None:
        embed = discord.Embed(
            title="📖 Commands — `.` prefix (slash works too)",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name=f"{COIN} Economy",
            value=(
                "`.bal [@user]` `.daily` `.weekly` `.work` `.pay @user <amt>`\n"
                "`.beg` `.crime` `.rob @user` `.fish` `.postmeme`\n"
                "`.shop` `.inv` `.bet <scrim#> <a|b> <amt>` `.duel @user <amt> [map]`"
            ),
            inline=False,
        )
        embed.add_field(
            name="🕹️ Menu",
            value="`.g` — full button menu · `.slots` (no amount) — betting window",
            inline=False,
        )
        embed.add_field(
            name="🎰 Casino (amounts accept `all`, `10k`)",
            value=(
                "`.cf <amt> [h|t]` `.dice <amt>` `.rps <amt> [r|p|s]` `.slots <amt>`\n"
                "`.roulette <amt> <red|black|even|odd|0-36>`\n"
                "`.plinko <amt> [low|med|high|extreme]` `.bj <amt>`\n"
                "`.mines <amt> [1-10]` `.tower <amt>` `.bacc <amt> [p|b|t]`"
            ),
            inline=False,
        )
        embed.add_field(
            name="📈 Stats",
            value="`.stats [@user]` `.lb [wins|elo|coins|xp]` `.history [@user]`",
            inline=False,
        )
        embed.add_field(
            name="🎮 Scrims & admin (slash only)",
            value=(
                "`/scrim panel` `/scrim create` `/scrim list` — scrim GUI\n"
                "`/scrimconfig …` `/security …` `/statuschannel` `/giveaway`"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)


async def setup(bot: ScrimBot) -> None:
    await bot.add_cog(Prefix(bot))
