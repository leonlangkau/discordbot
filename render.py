"""Pillow-based game graphics for the casino.

Every function returns a discord.File ready to attach. Fonts are loaded
from a list of common paths with a graceful fallback, so it works whether
or not the host ships DejaVu/Liberation fonts.
"""

from __future__ import annotations

import io
import os
from functools import lru_cache

import discord
from PIL import Image, ImageDraw, ImageFont

# Palette
BG_TOP = (26, 32, 46)
BG_BOTTOM = (15, 17, 23)
FELT = (30, 78, 54)
FELT_EDGE = (18, 52, 35)
GOLD = (240, 198, 92)
GOLD_DK = (176, 137, 44)
CARD_BG = (245, 245, 240)
CARD_RED = (200, 40, 50)
CARD_BLACK = (30, 32, 40)
WIN = (105, 219, 124)
LOSE = (255, 120, 120)
TEXT = (230, 232, 238)
MUTED = (150, 158, 176)
TILE = (43, 49, 66)
TILE_HI = (60, 68, 92)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
]
FONT_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "DejaVuSans.ttf",
]


@lru_cache(maxsize=32)
def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES if bold else FONT_REGULAR:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _canvas(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), BG_BOTTOM)
    top = Image.new("RGB", (w, h), BG_TOP)
    mask = Image.linear_gradient("L").resize((w, h))
    img = Image.composite(img, top, mask)
    return img


def _center(draw, xy, text, fnt, fill):
    x, y = xy
    l, t, r, b = draw.textbbox((0, 0), text, font=fnt)
    draw.text((x - (r - l) / 2, y - (b - t) / 2 - t), text, font=fnt, fill=fill)


def _to_file(img: Image.Image, name: str) -> discord.File:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return discord.File(buf, filename=name)


def _banner(draw, w, y, text, color):
    draw.rounded_rectangle((24, y, w - 24, y + 46), radius=12, fill=color)
    _center(draw, (w / 2, y + 23), text, font(26), (20, 22, 30))


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------


def slots(reels: list[str], result: str, won: bool) -> discord.File:
    w, h = 520, 320
    img = _canvas(w, h)
    d = ImageDraw.Draw(img)
    _center(d, (w / 2, 40), "🎰  SLOTS", font(34), GOLD)

    cell = 130
    gap = 20
    total = cell * 3 + gap * 2
    x0 = (w - total) / 2
    y0 = 90
    d.rounded_rectangle((x0 - 14, y0 - 14, x0 + total + 14, y0 + cell + 14),
                        radius=18, fill=GOLD_DK)
    d.rounded_rectangle((x0 - 8, y0 - 8, x0 + total + 8, y0 + cell + 8),
                        radius=14, fill=(20, 22, 30))
    for i, sym in enumerate(reels):
        cx = x0 + i * (cell + gap)
        d.rounded_rectangle((cx, y0, cx + cell, y0 + cell), radius=12, fill=CARD_BG)
        _center(d, (cx + cell / 2, y0 + cell / 2), sym, font(74), CARD_BLACK)

    _banner(d, w, 250, result.upper() if won else "NO WIN", WIN if won else LOSE)
    return _to_file(img, "slots.png")


# ---------------------------------------------------------------------------
# Playing cards (blackjack / baccarat)
# ---------------------------------------------------------------------------

SUIT_COLOR = {"♠": CARD_BLACK, "♣": CARD_BLACK, "♥": CARD_RED, "♦": CARD_RED}


def _draw_card(d, x, y, card: str | None, cw=92, ch=128):
    if card is None:  # face-down
        d.rounded_rectangle((x, y, x + cw, y + ch), radius=10, fill=(58, 74, 140))
        d.rounded_rectangle((x + 8, y + 8, x + cw - 8, y + ch - 8), radius=8,
                            outline=(120, 140, 220), width=3)
        return
    rank, suit = card[:-1], card[-1]
    color = SUIT_COLOR.get(suit, CARD_BLACK)
    d.rounded_rectangle((x, y, x + cw, y + ch), radius=10, fill=CARD_BG)
    d.text((x + 9, y + 6), rank, font=font(30), fill=color)
    d.text((x + 9, y + 40), suit, font=font(30), fill=color)
    _center(d, (x + cw / 2, y + ch / 2 + 6), suit, font(52), color)


def cards_table(
    dealer: list[str], player: list[str], dealer_hidden: bool,
    dealer_total, player_total, bet: int, footer: str, color,
) -> discord.File:
    w, h = 560, 400
    img = _canvas(w, h)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((16, 16, w - 16, h - 70), radius=20, fill=FELT, outline=FELT_EDGE, width=4)

    def row(cards, label, total, y, hide_second=False):
        d.text((36, y - 26), f"{label}   {total}", font=font(22), fill=TEXT)
        for i, c in enumerate(cards):
            shown = None if (hide_second and i == 1) else c
            _draw_card(d, 36 + i * 60, y, shown)

    row(dealer, "DEALER", "" if dealer_hidden else dealer_total, 70, dealer_hidden)
    row(player, "YOU", player_total, 230)

    d.text((36, h - 54), f"Bet {bet:,}", font=font(20), fill=GOLD)
    _banner_bottom(d, w, h, footer, color)
    return _to_file(img, "cards.png")


def _banner_bottom(d, w, h, text, color):
    _center(d, (w - 170, h - 44), text, font(22), color)


# ---------------------------------------------------------------------------
# Mines
# ---------------------------------------------------------------------------


def mines_grid(
    size: int, cols: int, revealed: set[int], mines: set[int],
    reveal_all: bool, multiplier: float, payout: int, footer: str | None,
) -> discord.File:
    rows = -(-size // cols)
    cell, pad = 74, 12
    w = cols * cell + pad * 2
    h = rows * cell + 150
    img = _canvas(w, h)
    d = ImageDraw.Draw(img)
    _center(d, (w / 2, 34), "💣  MINES", font(30), GOLD)
    d.text((pad, 58), f"×{multiplier:.2f}", font=font(22), fill=WIN)
    d.text((w - 160, 58), f"cash {payout:,}", font=font(20), fill=GOLD)

    y0 = 90
    for i in range(size):
        r, c = divmod(i, cols)
        x = pad + c * cell
        y = y0 + r * cell
        box = (x + 4, y + 4, x + cell - 4, y + cell - 4)
        if i in revealed:
            d.rounded_rectangle(box, radius=10, fill=(34, 92, 60))
            _center(d, (x + cell / 2, y + cell / 2), "💎", font(38), TEXT)
        elif reveal_all and i in mines:
            d.rounded_rectangle(box, radius=10, fill=(120, 40, 44))
            _center(d, (x + cell / 2, y + cell / 2), "💣", font(38), TEXT)
        else:
            d.rounded_rectangle(box, radius=10, fill=TILE)
            d.rounded_rectangle(box, radius=10, outline=TILE_HI, width=2)
            # tile number matches the "Tile N" dropdown option
            _center(d, (x + cell / 2, y + cell / 2), str(i + 1), font(22), MUTED)
    if footer:
        _banner(d, w, h - 52, footer, WIN if "Cashed" in footer or "cash" in footer.lower() else LOSE)
    return _to_file(img, "mines.png")


# ---------------------------------------------------------------------------
# Plinko
# ---------------------------------------------------------------------------


def plinko_board(mults: list[float], bucket: int, risk: str, payout: int, bet: int) -> discord.File:
    w, h = 560, 360
    img = _canvas(w, h)
    d = ImageDraw.Draw(img)
    _center(d, (w / 2, 30), f"🔻  PLINKO — {risk.upper()}", font(26), GOLD)

    rows = 8
    top = 66
    peg_area = 210
    for r in range(rows):
        pegs = r + 3
        span = (pegs - 1) * 40
        y = top + r * (peg_area / rows)
        for c in range(pegs):
            x = w / 2 - span / 2 + c * 40
            d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=MUTED)

    n = len(mults)
    bw = (w - 40) / n
    by = top + peg_area + 6
    for i, m in enumerate(mults):
        x = 20 + i * bw
        hit = i == bucket
        base = WIN if m >= 1 else LOSE
        col = GOLD if hit else tuple(int(x * 0.5) for x in base)
        d.rounded_rectangle((x + 3, by, x + bw - 3, by + 54), radius=8, fill=col)
        _center(d, (x + bw / 2, by + 27), f"{m}x", font(19 if len(str(m)) < 4 else 15),
                (20, 22, 30) if hit else TEXT)

    delta = payout - bet
    txt = f"×{mults[bucket]}  →  {'+' if delta >= 0 else ''}{delta:,}"
    _banner(d, w, h - 52, txt, WIN if delta >= 0 else LOSE)
    return _to_file(img, "plinko.png")


# ---------------------------------------------------------------------------
# Roulette
# ---------------------------------------------------------------------------

REDS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def roulette_result(number: int, won: bool, delta: int) -> discord.File:
    w, h = 480, 300
    img = _canvas(w, h)
    d = ImageDraw.Draw(img)
    _center(d, (w / 2, 40), "🎡  ROULETTE", font(30), GOLD)

    if number == 0:
        col, label = (40, 160, 90), "GREEN"
    elif number in REDS:
        col, label = (200, 55, 60), "RED"
    else:
        col, label = (40, 42, 52), "BLACK"

    cx, cy, rad = w / 2, 150, 58
    d.ellipse((cx - rad - 8, cy - rad - 8, cx + rad + 8, cy + rad + 8), fill=GOLD)
    d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=col)
    _center(d, (cx, cy), str(number), font(56), (245, 245, 245))
    _center(d, (cx, cy + rad + 26), label, font(20), MUTED)

    _banner(d, w, h - 52, f"{'+' if delta >= 0 else ''}{delta:,}", WIN if won else LOSE)
    return _to_file(img, "roulette.png")
