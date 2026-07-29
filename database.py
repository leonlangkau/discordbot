"""SQLite persistence layer for the scrim bot."""

from __future__ import annotations

import aiosqlite

DB_PATH = "scrimbot.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scrims (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    creator_id    INTEGER NOT NULL,
    game          TEXT    NOT NULL,
    team_size     INTEGER NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'open',  -- open | live | finished | cancelled
    category_id   INTEGER,
    lobby_channel_id INTEGER,
    announce_channel_id INTEGER,
    announce_message_id INTEGER,
    team_a_score  INTEGER,
    team_b_score  INTEGER,
    scheduled_for TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scrim_players (
    scrim_id  INTEGER NOT NULL REFERENCES scrims(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    team      TEXT    NOT NULL DEFAULT 'unassigned',  -- a | b | unassigned
    ready     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scrim_id, user_id)
);

CREATE TABLE IF NOT EXISTS player_stats (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    wins     INTEGER NOT NULL DEFAULT 0,
    losses   INTEGER NOT NULL DEFAULT 0,
    draws    INTEGER NOT NULL DEFAULT 0,
    played   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id            INTEGER PRIMARY KEY,
    scrim_role_id       INTEGER,
    announce_channel_id INTEGER,
    max_open_scrims     INTEGER NOT NULL DEFAULT 5
);

CREATE TABLE IF NOT EXISTS economy (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    balance    INTEGER NOT NULL DEFAULT 0,
    last_daily INTEGER NOT NULL DEFAULT 0,
    last_work  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS scrim_bets (
    scrim_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    team     TEXT    NOT NULL,
    amount   INTEGER NOT NULL,
    PRIMARY KEY (scrim_id, user_id)
);

CREATE TABLE IF NOT EXISTS shop_items (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name     TEXT    NOT NULL,
    price    INTEGER NOT NULL,
    role_id  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS giveaways (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    prize      TEXT    NOT NULL,
    coins      INTEGER NOT NULL DEFAULT 0,
    ends_at    INTEGER NOT NULL,
    ended      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    PRIMARY KEY (giveaway_id, user_id)
);

CREATE TABLE IF NOT EXISTS duels (
    scrim_id      INTEGER PRIMARY KEY,
    guild_id      INTEGER NOT NULL,
    challenger_id INTEGER NOT NULL,
    target_id     INTEGER NOT NULL,
    wager         INTEGER NOT NULL,
    settled       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS antinuke_whitelist (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS member_log (
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    username  TEXT,
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, user_id, joined_at)
);
"""

# Columns added after the initial release; applied via ALTER TABLE on connect.
MIGRATIONS: dict[str, dict[str, str]] = {
    "scrims": {"map": "TEXT"},
    "guild_config": {
        "server_host": "TEXT",
        "server_port": "INTEGER",
        "server_password": "TEXT",
        "rcon_password": "TEXT",
        "status_channel_id": "INTEGER",
        "status_message_id": "INTEGER",
        "log_channel_id": "INTEGER",
        "antinuke": "INTEGER NOT NULL DEFAULT 0",
    },
    "player_stats": {
        "elo": "INTEGER NOT NULL DEFAULT 1000",
        "xp": "INTEGER NOT NULL DEFAULT 0",
    },
}


class Database:
    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            cur = await self._conn.execute(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in await cur.fetchall()}
            for name, decl in columns.items():
                if name not in existing:
                    await self._conn.execute(
                        f'ALTER TABLE {table} ADD COLUMN "{name}" {decl}'
                    )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not connected"
        return self._conn

    # ---- scrims -----------------------------------------------------------

    async def create_scrim(
        self,
        guild_id: int,
        creator_id: int,
        game: str,
        team_size: int,
        scheduled_for: str | None,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO scrims (guild_id, creator_id, game, team_size, scheduled_for)"
            " VALUES (?, ?, ?, ?, ?)",
            (guild_id, creator_id, game, team_size, scheduled_for),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_scrim(self, scrim_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute("SELECT * FROM scrims WHERE id = ?", (scrim_id,))
        return await cur.fetchone()

    async def list_scrims(self, guild_id: int, statuses: tuple[str, ...]) -> list[aiosqlite.Row]:
        marks = ",".join("?" * len(statuses))
        cur = await self.conn.execute(
            f"SELECT * FROM scrims WHERE guild_id = ? AND status IN ({marks}) ORDER BY id",
            (guild_id, *statuses),
        )
        return await cur.fetchall()

    async def update_scrim(self, scrim_id: int, **fields) -> None:
        keys = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE scrims SET {keys} WHERE id = ?", (*fields.values(), scrim_id)
        )
        await self.conn.commit()

    # ---- players ----------------------------------------------------------

    async def add_player(self, scrim_id: int, user_id: int, team: str = "unassigned") -> bool:
        try:
            await self.conn.execute(
                "INSERT INTO scrim_players (scrim_id, user_id, team) VALUES (?, ?, ?)",
                (scrim_id, user_id, team),
            )
        except aiosqlite.IntegrityError:
            return False
        await self.conn.commit()
        return True

    async def remove_player(self, scrim_id: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM scrim_players WHERE scrim_id = ? AND user_id = ?",
            (scrim_id, user_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_players(self, scrim_id: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM scrim_players WHERE scrim_id = ? ORDER BY rowid", (scrim_id,)
        )
        return await cur.fetchall()

    async def set_team(self, scrim_id: int, user_id: int, team: str) -> None:
        await self.conn.execute(
            "UPDATE scrim_players SET team = ? WHERE scrim_id = ? AND user_id = ?",
            (team, scrim_id, user_id),
        )
        await self.conn.commit()

    async def set_ready(self, scrim_id: int, user_id: int, ready: bool) -> bool:
        cur = await self.conn.execute(
            "UPDATE scrim_players SET ready = ? WHERE scrim_id = ? AND user_id = ?",
            (int(ready), scrim_id, user_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # ---- stats ------------------------------------------------------------

    async def record_result(self, guild_id: int, user_id: int, outcome: str) -> None:
        await self.conn.execute(
            "INSERT INTO player_stats (guild_id, user_id) VALUES (?, ?)"
            " ON CONFLICT(guild_id, user_id) DO NOTHING",
            (guild_id, user_id),
        )
        column = {"win": "wins", "loss": "losses", "draw": "draws"}[outcome]
        await self.conn.execute(
            f"UPDATE player_stats SET {column} = {column} + 1, played = played + 1"
            " WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def get_stats(self, guild_id: int, user_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM player_stats WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return await cur.fetchone()

    async def leaderboard(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM player_stats WHERE guild_id = ?"
            " ORDER BY wins DESC, played ASC LIMIT ?",
            (guild_id, limit),
        )
        return await cur.fetchall()

    # ---- economy ----------------------------------------------------------

    async def _ensure_econ(self, guild_id: int, user_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO economy (guild_id, user_id) VALUES (?, ?)"
            " ON CONFLICT(guild_id, user_id) DO NOTHING",
            (guild_id, user_id),
        )

    async def get_econ(self, guild_id: int, user_id: int) -> aiosqlite.Row:
        await self._ensure_econ(guild_id, user_id)
        await self.conn.commit()
        cur = await self.conn.execute(
            "SELECT * FROM economy WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return await cur.fetchone()

    async def add_coins(self, guild_id: int, user_id: int, amount: int) -> None:
        await self._ensure_econ(guild_id, user_id)
        await self.conn.execute(
            "UPDATE economy SET balance = balance + ? WHERE guild_id = ? AND user_id = ?",
            (amount, guild_id, user_id),
        )
        await self.conn.commit()

    async def try_spend(self, guild_id: int, user_id: int, amount: int) -> bool:
        """Atomically deduct coins; False if the balance is insufficient."""
        await self._ensure_econ(guild_id, user_id)
        cur = await self.conn.execute(
            "UPDATE economy SET balance = balance - ?"
            " WHERE guild_id = ? AND user_id = ? AND balance >= ?",
            (amount, guild_id, user_id, amount),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def set_econ_time(self, guild_id: int, user_id: int, field: str, ts: int) -> None:
        assert field in ("last_daily", "last_work")
        await self.conn.execute(
            f"UPDATE economy SET {field} = ? WHERE guild_id = ? AND user_id = ?",
            (ts, guild_id, user_id),
        )
        await self.conn.commit()

    async def coins_leaderboard(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM economy WHERE guild_id = ? ORDER BY balance DESC LIMIT ?",
            (guild_id, limit),
        )
        return await cur.fetchall()

    # ---- scrim bets -------------------------------------------------------

    async def place_bet(self, scrim_id: int, user_id: int, team: str, amount: int) -> bool:
        try:
            await self.conn.execute(
                "INSERT INTO scrim_bets (scrim_id, user_id, team, amount) VALUES (?, ?, ?, ?)",
                (scrim_id, user_id, team, amount),
            )
        except aiosqlite.IntegrityError:
            return False
        await self.conn.commit()
        return True

    async def get_bets(self, scrim_id: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM scrim_bets WHERE scrim_id = ?", (scrim_id,)
        )
        return await cur.fetchall()

    async def clear_bets(self, scrim_id: int) -> None:
        await self.conn.execute("DELETE FROM scrim_bets WHERE scrim_id = ?", (scrim_id,))
        await self.conn.commit()

    # ---- elo / xp ---------------------------------------------------------

    async def _ensure_stats(self, guild_id: int, user_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO player_stats (guild_id, user_id) VALUES (?, ?)"
            " ON CONFLICT(guild_id, user_id) DO NOTHING",
            (guild_id, user_id),
        )

    async def adjust_elo(self, guild_id: int, user_id: int, delta: int) -> None:
        await self._ensure_stats(guild_id, user_id)
        await self.conn.execute(
            "UPDATE player_stats SET elo = MAX(0, elo + ?)"
            " WHERE guild_id = ? AND user_id = ?",
            (delta, guild_id, user_id),
        )
        await self.conn.commit()

    async def add_xp(self, guild_id: int, user_id: int, amount: int) -> tuple[int, int]:
        """Add XP; returns (old_xp, new_xp)."""
        await self._ensure_stats(guild_id, user_id)
        cur = await self.conn.execute(
            "SELECT xp FROM player_stats WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        old = (await cur.fetchone())["xp"]
        await self.conn.execute(
            "UPDATE player_stats SET xp = xp + ? WHERE guild_id = ? AND user_id = ?",
            (amount, guild_id, user_id),
        )
        await self.conn.commit()
        return old, old + amount

    async def stats_leaderboard(
        self, guild_id: int, column: str, limit: int = 10
    ) -> list[aiosqlite.Row]:
        assert column in ("wins", "elo", "xp")
        cur = await self.conn.execute(
            f"SELECT * FROM player_stats WHERE guild_id = ? ORDER BY {column} DESC LIMIT ?",
            (guild_id, limit),
        )
        return await cur.fetchall()

    async def match_history(
        self, guild_id: int, user_id: int, limit: int = 10
    ) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT s.*, p.team AS my_team FROM scrims s"
            " JOIN scrim_players p ON p.scrim_id = s.id"
            " WHERE s.guild_id = ? AND p.user_id = ? AND s.status = 'finished'"
            " ORDER BY s.id DESC LIMIT ?",
            (guild_id, user_id, limit),
        )
        return await cur.fetchall()

    # ---- shop -------------------------------------------------------------

    async def add_shop_item(
        self, guild_id: int, name: str, price: int, role_id: int
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO shop_items (guild_id, name, price, role_id) VALUES (?, ?, ?, ?)",
            (guild_id, name, price, role_id),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def remove_shop_item(self, guild_id: int, item_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_shop_items(self, guild_id: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM shop_items WHERE guild_id = ? ORDER BY price", (guild_id,)
        )
        return await cur.fetchall()

    async def get_shop_item(self, guild_id: int, item_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
        )
        return await cur.fetchone()

    # ---- giveaways --------------------------------------------------------

    async def create_giveaway(
        self, guild_id: int, channel_id: int, prize: str, coins: int, ends_at: int
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO giveaways (guild_id, channel_id, prize, coins, ends_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (guild_id, channel_id, prize, coins, ends_at),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def set_giveaway_message(self, giveaway_id: int, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?", (message_id, giveaway_id)
        )
        await self.conn.commit()

    async def get_giveaway(self, giveaway_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM giveaways WHERE id = ?", (giveaway_id,)
        )
        return await cur.fetchone()

    async def due_giveaways(self, now: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM giveaways WHERE ended = 0 AND ends_at <= ?", (now,)
        )
        return await cur.fetchall()

    async def end_giveaway(self, giveaway_id: int) -> None:
        await self.conn.execute(
            "UPDATE giveaways SET ended = 1 WHERE id = ?", (giveaway_id,)
        )
        await self.conn.commit()

    async def enter_giveaway(self, giveaway_id: int, user_id: int) -> bool:
        try:
            await self.conn.execute(
                "INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
                (giveaway_id, user_id),
            )
        except aiosqlite.IntegrityError:
            return False
        await self.conn.commit()
        return True

    async def giveaway_entry_count(self, giveaway_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS n FROM giveaway_entries WHERE giveaway_id = ?",
            (giveaway_id,),
        )
        return (await cur.fetchone())["n"]

    async def random_giveaway_winner(self, giveaway_id: int) -> int | None:
        cur = await self.conn.execute(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?"
            " ORDER BY RANDOM() LIMIT 1",
            (giveaway_id,),
        )
        row = await cur.fetchone()
        return row["user_id"] if row else None

    # ---- duels ------------------------------------------------------------

    async def create_duel(
        self, scrim_id: int, guild_id: int, challenger_id: int, target_id: int, wager: int
    ) -> None:
        await self.conn.execute(
            "INSERT INTO duels (scrim_id, guild_id, challenger_id, target_id, wager)"
            " VALUES (?, ?, ?, ?, ?)",
            (scrim_id, guild_id, challenger_id, target_id, wager),
        )
        await self.conn.commit()

    async def get_duel(self, scrim_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM duels WHERE scrim_id = ? AND settled = 0", (scrim_id,)
        )
        return await cur.fetchone()

    async def settle_duel(self, scrim_id: int) -> None:
        await self.conn.execute(
            "UPDATE duels SET settled = 1 WHERE scrim_id = ?", (scrim_id,)
        )
        await self.conn.commit()

    # ---- security ---------------------------------------------------------

    async def add_whitelist(self, guild_id: int, user_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO antinuke_whitelist (guild_id, user_id) VALUES (?, ?)"
            " ON CONFLICT DO NOTHING",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def remove_whitelist(self, guild_id: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM antinuke_whitelist WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_whitelist(self, guild_id: int) -> set[int]:
        cur = await self.conn.execute(
            "SELECT user_id FROM antinuke_whitelist WHERE guild_id = ?", (guild_id,)
        )
        return {row["user_id"] for row in await cur.fetchall()}

    async def log_member(self, guild_id: int, user_id: int, username: str) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO member_log (guild_id, user_id, username)"
            " VALUES (?, ?, ?)",
            (guild_id, user_id, username),
        )
        await self.conn.commit()

    # ---- config -----------------------------------------------------------

    async def all_status_configs(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM guild_config WHERE status_channel_id IS NOT NULL"
        )
        return await cur.fetchall()

    async def get_config(self, guild_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        )
        return await cur.fetchone()

    async def set_config(self, guild_id: int, **fields) -> None:
        await self.conn.execute(
            "INSERT INTO guild_config (guild_id) VALUES (?)"
            " ON CONFLICT(guild_id) DO NOTHING",
            (guild_id,),
        )
        if fields:
            keys = ", ".join(f"{k} = ?" for k in fields)
            await self.conn.execute(
                f"UPDATE guild_config SET {keys} WHERE guild_id = ?",
                (*fields.values(), guild_id),
            )
        await self.conn.commit()
