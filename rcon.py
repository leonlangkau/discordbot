"""Minimal async Source RCON client (works with CS2 dedicated servers)."""

from __future__ import annotations

import asyncio
import struct

SERVERDATA_AUTH = 3
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_RESPONSE_VALUE = 0


class RconError(Exception):
    pass


class RconAuthError(RconError):
    pass


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0

    async def __aenter__(self) -> "RconClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        except (OSError, asyncio.TimeoutError) as e:
            raise RconError(f"Could not connect to {self.host}:{self.port}: {e}") from e

        auth_id = await self._send(SERVERDATA_AUTH, self.password)
        # The server replies with an empty RESPONSE_VALUE, then AUTH_RESPONSE.
        while True:
            packet_id, packet_type, _ = await self._recv()
            if packet_type == SERVERDATA_AUTH_RESPONSE:
                if packet_id == -1:
                    raise RconAuthError("RCON password rejected by the server.")
                if packet_id == auth_id:
                    return

    async def command(self, cmd: str) -> str:
        if self._writer is None:
            raise RconError("Not connected.")
        request_id = await self._send(SERVERDATA_EXECCOMMAND, cmd)
        body_parts: list[str] = []
        while True:
            packet_id, packet_type, body = await self._recv()
            if packet_type == SERVERDATA_RESPONSE_VALUE and packet_id == request_id:
                body_parts.append(body)
                # CS2 answers in a single packet for typical commands; drain
                # by checking whether more data is immediately available.
                if not self._reader._buffer:  # noqa: SLF001 - best-effort drain
                    return "".join(body_parts)

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
            self._reader = None

    # ---- wire format ------------------------------------------------------

    async def _send(self, packet_type: int, body: str) -> int:
        self._request_id += 1
        payload = (
            struct.pack("<ii", self._request_id, packet_type)
            + body.encode("utf-8")
            + b"\x00\x00"
        )
        self._writer.write(struct.pack("<i", len(payload)) + payload)
        await asyncio.wait_for(self._writer.drain(), self.timeout)
        return self._request_id

    async def _recv(self) -> tuple[int, int, str]:
        try:
            raw_len = await asyncio.wait_for(self._reader.readexactly(4), self.timeout)
            (length,) = struct.unpack("<i", raw_len)
            payload = await asyncio.wait_for(
                self._reader.readexactly(length), self.timeout
            )
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError) as e:
            raise RconError(f"Connection lost while reading response: {e}") from e
        packet_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:-2].decode("utf-8", errors="replace")
        return packet_id, packet_type, body


async def run_command(
    host: str, port: int, password: str, cmd: str, timeout: float = 5.0
) -> str:
    """One-shot convenience wrapper: connect, run, close."""
    async with RconClient(host, port, password, timeout) as client:
        return await client.command(cmd)
