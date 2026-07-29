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
"""

# Columns added after the initial release; applied via ALTER TABLE on connect.
MIGRATIONS: dict[str, dict[str, str]] = {
    "scrims": {"map": "TEXT"},
    "guild_config": {
        "server_host": "TEXT",
        "server_port": "INTEGER",
        "server_password": "TEXT",
        "rcon_password": "TEXT",
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

    # ---- config -----------------------------------------------------------

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
