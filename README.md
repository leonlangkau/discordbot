# CS2 Scrim Bot

A Discord bot that runs **CS2 scrims on your own server** — it creates private match lobbies with their own team channels in Discord, and hooks into your CS2 dedicated server (e.g. your HvH VPS) via RCON.

When someone creates a scrim, the bot spins up a dedicated category containing:

```
🎮 Scrim #1 — CS2 (de_mirage)
├── #lobby          (shared text channel)
├── #team-a         (team text channel)
├── #team-b         (team text channel)
├── 🔊 Lobby VC
├── 🔵 Team A VC
└── 🔴 Team B VC
```

The channels are private — only players who join the scrim (via buttons on the announcement) can see them. When the scrim finishes or is cancelled, the channels are automatically deleted.

## Features — fully button-driven GUI

- **`/scrim panel`** — post a persistent **CS2 Scrim Hub** embed with a `🎮 Create Scrim` button
- **Creation wizard** — clicking Create Scrim (or `/scrim create`) opens a private embed with dropdowns: pick the **map** (Mirage, Dust II, Inferno, …), **team size** (1v1–10v10), and **start time**, then hit ✅ Create
- **Your server, wired in** — configure your CS2 server once with `/scrimconfig server`; when a match starts the bot posts the `connect ip:port; password …` line in the private lobby, and if RCON is configured it switches the server to the picked map automatically
- **🖥️ Connect Info button** on live scrims (players only) and **`/scrimconfig rcon`** to run any RCON command from Discord
- **Join / Ready / Leave buttons** on the announcement embed, with live-updating rosters (all buttons survive bot restarts)
- **Host controls on the embed** — 🏁 Start Match, 🏆 Report Score (popup form for the scores), 🗑️ Cancel; visible to everyone but only usable by the host or moderators
- Score reporting records stats and automatically deletes the scrim channels
- **`/stats`**, **`/leaderboard`**, **`/history`** — full profiles with W/L, Elo rank (Silver → Global Elite), XP level, and coins
- **`/scrimconfig`** — admin settings: announcement channel, ping role, max concurrent scrims
- SQLite persistence — scrims, rosters, stats, economy, and config survive restarts

## Economy & casino

- **Coins** — `/balance`, `/daily` (250, 20h), `/weekly` (2,000, 7d), `/work` (50–150, 1h), `/pay`; playing scrims pays coins and XP, winning pays more; chatting drips +5 coins/min
- **Grinding** — `/beg` (5 min), `/fish` (10 min, can catch inventory items), `/postmeme` (15 min, up to 350), `/crime` (30 min, high risk/reward), `/rob @user` (1h — steal 10–25% of their wallet or pay them damages when caught)
- **Elo** — team-based Elo (K=32) moves with every reported scrim; ranks from Silver to Global Elite
- **XP & levels** — chat and scrims grant XP, with level-up announcements
- **Casino (rendered PNG graphics)** — `/slots`, `/blackjack`, `/mines` (5×5 board), `/plinko`, `/roulette` draw real images; `/dice`, `/rps`, `/coinflip`, `/tower`, `/baccarat` too. Plinko has 4 risk levels with big side multipliers (up to **90x**). In the betting window, instant games (slots, plinko, roulette, dice, coinflip, rps, baccarat) **replay in the same message** instead of spamming new ones.
- **Scrim betting** — `/bet` on Team A/B while a scrim is open; pays 2x, refunded on draws and cancels; players can't bet on their own match
- **Duels** — `/duel @user wager` creates a 1v1 scrim with both wagers escrowed; winner takes the pot on the reported score
- **Shop** — `/shop` with a buy menu; admins stock roles via `/shopadmin add`, grant coins with `/shopadmin give`
- **Giveaways** — `/giveaway` with a join button and automatic timed draw; coin prizes pay out automatically
- **Server status** — `/statuschannel` posts an auto-updating embed (map, players, online state) polled from your CS2 server via RCON every 5 minutes

## Security

- **Join/leave logging** — `/security logchannel` picks a channel; every joiner gets a full profile embed (ID, username, display name, account age with new-account warning, badges, avatar, banner, bot flag) and leavers are logged with their roles; joins are also recorded in the database
- **Anti-nuke** — `/security antinuke enabled:true` watches the audit log for bursts of destructive actions (3+ channel/role deletions or 4+ bans/kicks within 60s) by any member **or bot** and immediately bans the offender (or strips all their roles if banning isn't possible), then reports in the log channel
- **Whitelist** — `/security whitelist` marks trusted admins that anti-nuke ignores; the server owner and the bot itself are always exempt
- `/security status` shows the full configuration
- Needs extra bot permissions: **View Audit Log** and **Ban Members** for full protection

## Web admin panel

A password-protected browser dashboard runs from inside the bot. Set `PANEL_PASSWORD` to enable it (it refuses to start without one) and `WEB_PORT` to your host's allocated port. On bot-hosting.net that's the address shown on your server — e.g. `fi15.bot-hosting.net:26152`, so set `WEB_PORT=26152`.

From the panel you can:
- See an overview: servers, members, active scrims, uptime
- Per server: view top coin balances and active scrims
- **Grant or take coins** from any user (by ID)
- **Toggle anti-nuke** on/off

Security: cookie sessions with a 12h expiry, per-session CSRF tokens on every action, login rate-limiting, and it never starts unauthenticated. Use a strong `PANEL_PASSWORD` — anyone with it can adjust coins.

## Setup

### 1. Create the Discord application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create an application.
2. Under **Bot**, create a bot and copy its token.
3. Enable the **Server Members Intent** and the **Message Content Intent** (Bot → Privileged Gateway Intents — message content is needed for the `.` prefix commands).
4. Invite the bot with the **`bot`** and **`applications.commands`** scopes and these permissions: *Manage Channels, View Channels, Send Messages, Embed Links, Mention Everyone*.

### 2. Run the bot

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your token into .env
python bot.py
```

Set `GUILD_ID` in `.env` to your server's ID during development so slash commands appear instantly (global sync can take up to an hour).

## Commands

Economy, casino, scrim, and stats commands work **both** as slash commands and with the classic `.` prefix. `.help` lists everything. Admin/config commands are slash-only.

**Scrim menu.** `.s` (or `/scrims`) opens a button menu: **🎮 Create Scrim** (opens the map/size/time wizard in the same message), **🔄 Refresh**, **📌 Post Hub Panel** (server managers only), and a **🔗 Jump to a scrim** dropdown. Active scrims are listed live with clickable links to their sign-up posts.

**Game menu & betting windows.** `.g` (or `/games`) opens a button menu with **all 10 games pressable on the front screen** (two rows: Slots, Flip, Dice, RPS, Plinko / Mines, Tower, BJ, Baccarat, Roulette) plus Economy, Item Shop, Stats and Inventory. Pressing a game turns that same message into its betting window, with **↩️ Menu** to go back — no extra messages. Typing a game name alone, like `.slots` or `.blackjack`, opens a **betting window**: a styled embed with **½ / ×2** to adjust the bet, **✏️ Amount** for an exact figure, **💰 All In** (with a max-bet confirmation), a dropdown for game options (coinflip side, plinko risk, roulette target…), and a **▶ Play** button. Power users can still bet inline: `.slots 500`, `.roulette 100 17`, `.bj 1k`.

**Item shop.** Pepe-bot-style flex items from a 🌭 Hotdog (500) up to the 🍆 **Golden Dildo** (10,000,000,000), plus 📦 Mystery Boxes that open instantly for coins or a random item. Items land in your `.inv`. **Custom emojis:** upload a server emoji whose name matches an item key (e.g. `:rare_pepe:`, `:golden_dildo:`, `:smug_pepe:`, `:pepe_crown:`, `:golden_pepe:`, `:mystery_box:`, `:lambo:`, `:chain:`) and the bot uses it automatically — otherwise it falls back to a unicode emoji.

| Command | Who | What it does |
|---|---|---|
| `/scrim panel` | admins | Post the Scrim Hub panel with the Create Scrim button |
| `/scrim create` | anyone | Open the scrim creation wizard (same as the panel button) |
| `/scrim list` | anyone | List active scrims |
| `/scrim kick <scrim_id> <player>` | host/mod | Remove a player |
| `/stats [player]` | anyone | Show a player's record |
| `/leaderboard` | anyone | Top players by wins |
| `/scrimconfig server <host> [port] [password] [rcon_password]` | admins | Point the bot at your CS2 server |
| `/scrimconfig rcon <command>` | admins | Run an RCON command on the server |
| `/scrimconfig …` | admins | Announcement channel, ping role, scrim limit |
| `/balance` `/daily` `/work` `/pay` | anyone | Economy basics |
| `/coinflip` `/dice` `/rps` `/slots` `/roulette` `/plinko` `/blackjack` `/mines` `/tower` `/baccarat` | anyone | Casino games |
| `/bet <scrim_id> <team> <amount>` | anyone | Bet on an open scrim (2x) |
| `/duel <opponent> <wager> [map]` | anyone | 1v1 for a coin pot |
| `/history [player]` | anyone | Recent scrim results |
| `/shop`, `/shopadmin …` | anyone / admins | Coin shop for roles |
| `/giveaway <prize> <minutes> [coins]` | mods | Timed giveaway with join button |
| `/statuschannel [channel]` | admins | Live CS2 server status embed |

Starting, reporting, and cancelling scrims all happen through the buttons on the scrim's announcement embed — no IDs to type.

## Project layout

```
bot.py          # entry point, command sync, cog loading
database.py     # aiosqlite persistence (scrims, players, stats, config)
rcon.py         # minimal async Source RCON client (no extra deps)
cogs/scrims.py    # scrim lifecycle, channel creation, join/ready buttons, payouts
cogs/stats.py     # profiles, Elo ranks, XP levels, leaderboards, history
cogs/economy.py   # coins, daily/work/pay, shop, scrim betting
cogs/casino.py    # all ten casino games
cogs/duels.py     # 1v1 wager duels
cogs/giveaways.py # timed giveaways
cogs/status.py    # live server status embed
cogs/admin.py     # /scrimconfig admin commands (server, rcon, channels, limits)
cogs/security.py  # join/leave logging + anti-nuke
cogs/prefix.py    # classic "." text commands
webpanel.py       # password-protected web admin panel
```

## Configuration via environment variables

Every secret can be supplied as a plain environment variable — ideal for hosting panels (bot-hosting.net, Railway, Docker, …) where you set secrets in the dashboard instead of files. A `.env` file works too, but is optional.

| Variable | Required | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | yes | Bot token |
| `GUILD_ID` | no | Sync slash commands instantly to one server |
| `SERVER_HOST` | no | Default CS2 server IP/hostname |
| `SERVER_PORT` | no | Default CS2 server port (default 27015) |
| `SERVER_PASSWORD` | no | Default join password (`sv_password`) |
| `RCON_PASSWORD` | no | Default RCON password |
| `PANEL_PASSWORD` | no | Enables the web admin panel; required to start it |
| `WEB_PORT` | no | Port for the web panel (your host's allocated port) |
| `WEB_HOST` | no | Bind address for the web panel (default `0.0.0.0`) |

Anything set per guild with `/scrimconfig server` overrides the environment defaults for that guild.

## Wiring up your CS2 server

Don't have a CS2 dedicated server yet? `scripts/setup_cs2_server.sh` provisions one on a fresh Ubuntu/Debian VPS — SteamCMD install, a non-root `steam` user, `server.cfg` with a generated RCON password, and a systemd service. Copy it to your VPS and run it there as root:

```bash
curl -fsSL https://raw.githubusercontent.com/leonlangkau/discordbot/claude/discord-bot-server-setup-hyxdow/scripts/setup_cs2_server.sh -o setup_cs2_server.sh
chmod +x setup_cs2_server.sh
./setup_cs2_server.sh
```

It prints the server address, RCON password, and the exact `.env`/`/scrimconfig` values to paste below when it's done. See the comments at the top of the script for overridable options (port, map, join password, GSLT).

On your VPS, make sure RCON is enabled in your server config (`rcon_password "..."` in `server.cfg`, and the RCON port — same as the game port — reachable over TCP from wherever the bot runs). Then either set the `SERVER_*`/`RCON_PASSWORD` environment variables above, or configure it in Discord:

```
/scrimconfig server host:1.2.3.4 port:27015 password:scrimpw rcon_password:yourrconpw
```

The bot verifies the RCON connection immediately. From then on, starting a scrim auto-loads the picked map, and players get the connect line in their private lobby (only scrim participants can see it).

