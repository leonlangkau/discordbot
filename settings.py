"""Server connection settings, resolved from guild config with env fallbacks.

Secrets never need to live in files or the database: SERVER_HOST,
SERVER_PORT, SERVER_PASSWORD, and RCON_PASSWORD can all be provided as
environment variables (e.g. panel secrets on a bot host). Values set in
Discord via /scrimconfig server take precedence per guild.
"""

from __future__ import annotations

import os


def server_settings(config) -> dict | None:
    """Merge the guild's stored server config with environment defaults.

    Returns None when no server is configured anywhere.
    """

    def stored(key: str):
        return config[key] if config is not None and config[key] else None

    host = stored("server_host") or os.getenv("SERVER_HOST")
    if not host:
        return None
    try:
        env_port = int(os.getenv("SERVER_PORT", "27015"))
    except ValueError:
        env_port = 27015
    return {
        "host": host,
        "port": stored("server_port") or env_port,
        "password": stored("server_password") or os.getenv("SERVER_PASSWORD"),
        "rcon_password": stored("rcon_password") or os.getenv("RCON_PASSWORD"),
    }
