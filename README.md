# Scrim Bot

A Discord bot that creates and manages **scrim servers** — private match lobbies with their own team channels, right inside your Discord server.

When someone runs `/scrim create`, the bot spins up a dedicated category containing:

```
🎮 Scrim #1 — Valorant
├── #lobby          (shared text channel)
├── #team-a         (team text channel)
├── #team-b         (team text channel)
├── 🔊 Lobby VC
├── 🔵 Team A VC
└── 🔴 Team B VC
```

The channels are private — only players who join the scrim (via buttons on the announcement) can see them. When the scrim finishes or is cancelled, the channels are automatically deleted.

## Features — fully button-driven GUI

- **`/scrim panel`** — post a persistent **Scrim Hub** embed with a `🎮 Create Scrim` button
- **Creation wizard** — clicking Create Scrim (or `/scrim create`) opens a private embed with dropdowns: pick the **game** (presets + custom via popup), **team size** (1v1–10v10), and **start time**, then hit ✅ Create
- **Join / Ready / Leave buttons** on the announcement embed, with live-updating rosters (all buttons survive bot restarts)
- **Host controls on the embed** — 🏁 Start Match, 🏆 Report Score (popup form for the scores), 🗑️ Cancel; visible to everyone but only usable by the host or moderators
- Score reporting records stats and automatically deletes the scrim channels
- **`/stats`** and **`/leaderboard`** — per-server win/loss records
- **`/scrimconfig`** — admin settings: announcement channel, ping role, max concurrent scrims
- SQLite persistence — scrims, rosters, stats, and config survive restarts

## Setup

### 1. Create the Discord application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create an application.
2. Under **Bot**, create a bot and copy its token.
3. Enable the **Server Members Intent** (Bot → Privileged Gateway Intents).
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

| Command | Who | What it does |
|---|---|---|
| `/scrim panel` | admins | Post the Scrim Hub panel with the Create Scrim button |
| `/scrim create` | anyone | Open the scrim creation wizard (same as the panel button) |
| `/scrim list` | anyone | List active scrims |
| `/scrim kick <scrim_id> <player>` | host/mod | Remove a player |
| `/stats [player]` | anyone | Show a player's record |
| `/leaderboard` | anyone | Top players by wins |
| `/scrimconfig …` | admins | Announcement channel, ping role, scrim limit |

Starting, reporting, and cancelling scrims all happen through the buttons on the scrim's announcement embed — no IDs to type.

## Project layout

```
bot.py          # entry point, command sync, cog loading
database.py     # aiosqlite persistence (scrims, players, stats, config)
cogs/scrims.py  # scrim lifecycle, channel creation, join/ready buttons
cogs/stats.py   # /stats and /leaderboard
cogs/admin.py   # /scrimconfig admin commands
```
