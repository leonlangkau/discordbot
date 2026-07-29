"""Password-protected web admin panel served from inside the bot.

Runs an aiohttp server (aiohttp ships with discord.py, no extra dep) on
WEB_PORT. Requires PANEL_PASSWORD — if it is unset the panel refuses to
start, so admin controls are never exposed unauthenticated. Sessions are
cookie-based with per-session CSRF tokens.
"""

from __future__ import annotations

import html
import os
import secrets
import time
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from bot import ScrimBot

# session token -> {"csrf": str, "expires": float}
SESSIONS: dict[str, dict] = {}
SESSION_TTL = 12 * 3600
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}


def esc(value) -> str:
    return html.escape(str(value))


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {"csrf": secrets.token_urlsafe(24), "expires": time.time() + SESSION_TTL}
    return token


def valid_session(request: web.Request) -> dict | None:
    token = request.cookies.get("panel_session")
    if not token:
        return None
    session = SESSIONS.get(token)
    if not session or session["expires"] < time.time():
        SESSIONS.pop(token, None)
        return None
    return session


def page(title: str, body: str, bot: ScrimBot) -> web.Response:
    name = esc(bot.user) if bot.user else "Scrim Bot"
    doc = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — Admin Panel</title>
<style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;font:15px/1.5 system-ui,sans-serif;background:#0f1117;color:#e6e8ee}}
a{{color:#7aa2f7;text-decoration:none}}a:hover{{text-decoration:underline}}
header{{background:#161923;padding:14px 22px;border-bottom:1px solid #232838;
display:flex;justify-content:space-between;align-items:center}}
header .brand{{font-weight:700}}
main{{max-width:960px;margin:24px auto;padding:0 18px}}
.card{{background:#161923;border:1px solid #232838;border-radius:12px;padding:18px 20px;margin:0 0 18px}}
.card h2{{margin:0 0 12px;font-size:17px}}
table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid #232838}}
th{{color:#9aa2b4;font-weight:600;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.stat{{background:#1b1f2b;border-radius:10px;padding:14px}}
.stat b{{display:block;font-size:24px}}
.stat span{{color:#9aa2b4;font-size:13px}}
input,select,button{{font:inherit;padding:8px 11px;border-radius:8px;border:1px solid #2c3244;
background:#1b1f2b;color:#e6e8ee}}
button{{background:#3b5bdb;border-color:#3b5bdb;cursor:pointer;font-weight:600}}
button:hover{{background:#4c6ef5}}
button.danger{{background:#c92a2a;border-color:#c92a2a}}
form.row{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.pill{{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600}}
.on{{background:#1c3a24;color:#69db7c}}.off{{background:#3a1c1c;color:#ff8787}}
.muted{{color:#9aa2b4}}
</style></head><body>
<header><span class="brand">🛡️ {name} — Admin</span>
<span><a href="/dashboard">Dashboard</a> &nbsp; <a href="/logout">Log out</a></span></header>
<main>{body}</main></body></html>"""
    return web.Response(text=doc, content_type="text/html")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_login_page(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    error = request.query.get("error")
    banner = '<p class="off pill">Wrong password</p>' if error else ""
    body = f"""<div class="card" style="max-width:380px;margin:60px auto">
<h2>Admin login</h2>{banner}
<form class="row" method="post" action="/login">
<input type="password" name="password" placeholder="Panel password" autofocus
 style="flex:1" required>
<button type="submit">Enter</button></form></div>"""
    return page("Login", body, bot)


async def handle_login(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    password = os.getenv("PANEL_PASSWORD", "")
    ip = request.headers.get("X-Forwarded-For", request.remote or "?").split(",")[0].strip()

    now = time.time()
    attempts = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if now - t < 300]
    if len(attempts) >= 8:
        return web.Response(text="Too many attempts. Wait a few minutes.", status=429)

    data = await request.post()
    supplied = str(data.get("password", ""))
    if password and secrets.compare_digest(supplied, password):
        _LOGIN_ATTEMPTS.pop(ip, None)
        token = new_session()
        response = web.HTTPFound("/dashboard")
        response.set_cookie(
            "panel_session", token, max_age=SESSION_TTL, httponly=True, samesite="Lax"
        )
        return response
    attempts.append(now)
    _LOGIN_ATTEMPTS[ip] = attempts
    return web.HTTPFound("/login?error=1")


async def handle_logout(request: web.Request) -> web.Response:
    token = request.cookies.get("panel_session")
    SESSIONS.pop(token, None)
    response = web.HTTPFound("/login")
    response.del_cookie("panel_session")
    return response


async def handle_dashboard(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    uptime = int(time.time() - request.app["start_time"])
    h, m = uptime // 3600, (uptime % 3600) // 60

    total_scrims = 0
    guild_rows = ""
    for guild in bot.guilds:
        scrims = await bot.db.list_scrims(guild.id, ("open", "live"))
        total_scrims += len(scrims)
        guild_rows += (
            f"<tr><td><a href='/guild/{guild.id}'>{esc(guild.name)}</a></td>"
            f"<td>{guild.member_count}</td><td>{len(scrims)}</td>"
            f"<td class='muted'>{guild.id}</td></tr>"
        )
    if not guild_rows:
        guild_rows = "<tr><td colspan='4' class='muted'>Bot is in no servers yet.</td></tr>"

    body = f"""<div class="card"><h2>Overview</h2>
<div class="grid">
<div class="stat"><b>{len(bot.guilds)}</b><span>servers</span></div>
<div class="stat"><b>{sum(g.member_count or 0 for g in bot.guilds)}</b><span>members</span></div>
<div class="stat"><b>{total_scrims}</b><span>active scrims</span></div>
<div class="stat"><b>{h}h {m}m</b><span>uptime</span></div>
</div></div>
<div class="card"><h2>Servers</h2>
<table><tr><th>Server</th><th>Members</th><th>Active scrims</th><th>ID</th></tr>
{guild_rows}</table></div>"""
    return page("Dashboard", body, bot)


async def handle_guild(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    session = request.app["session"]
    gid = int(request.match_info["gid"])
    guild = bot.get_guild(gid)
    if guild is None:
        return page("Not found", "<div class='card'>Server not found.</div>", bot)

    config = await bot.db.get_config(gid)
    antinuke_on = bool(config and config["antinuke"])
    csrf = session["csrf"]

    # Economy leaderboard
    coins = await bot.db.coins_leaderboard(gid, limit=15)
    coin_rows = "".join(
        f"<tr><td>{esc(getattr(guild.get_member(r['user_id']), 'display_name', r['user_id']))}"
        f"</td><td>{r['balance']:,}</td><td class='muted'>{r['user_id']}</td></tr>"
        for r in coins
    ) or "<tr><td colspan='3' class='muted'>No wallets yet.</td></tr>"

    # Active scrims
    scrims = await bot.db.list_scrims(gid, ("open", "live"))
    scrim_rows = "".join(
        f"<tr><td>#{s['id']}</td><td>{esc(s['game'])} {esc(s['map'] or '')}</td>"
        f"<td>{esc(s['status'])}</td><td>{s['team_size']}v{s['team_size']}</td></tr>"
        for s in scrims
    ) or "<tr><td colspan='4' class='muted'>No active scrims.</td></tr>"

    body = f"""<p><a href="/dashboard">← Dashboard</a></p>
<div class="card"><h2>{esc(guild.name)}</h2>
<p class="muted">{guild.member_count} members · ID {gid}</p></div>

<div class="card"><h2>Security</h2>
<p>Anti-nuke: <span class="pill {'on' if antinuke_on else 'off'}">
{'ENABLED' if antinuke_on else 'DISABLED'}</span></p>
<form class="row" method="post" action="/guild/{gid}/antinuke">
<input type="hidden" name="csrf" value="{esc(csrf)}">
<input type="hidden" name="value" value="{0 if antinuke_on else 1}">
<button type="submit" class="{'danger' if antinuke_on else ''}">
{'Disable' if antinuke_on else 'Enable'} anti-nuke</button></form></div>

<div class="card"><h2>Adjust coins</h2>
<form class="row" method="post" action="/guild/{gid}/coins">
<input type="hidden" name="csrf" value="{esc(csrf)}">
<input name="user_id" placeholder="User ID" required>
<input name="amount" type="number" placeholder="Amount (− to take)" required>
<button type="submit">Apply</button></form>
<p class="muted">Right-click a user in Discord → Copy User ID.</p></div>

<div class="card"><h2>Top balances</h2>
<table><tr><th>Player</th><th>Coins</th><th>ID</th></tr>{coin_rows}</table></div>

<div class="card"><h2>Active scrims</h2>
<table><tr><th>#</th><th>Game / map</th><th>Status</th><th>Format</th></tr>{scrim_rows}</table></div>"""
    return page(esc(guild.name), body, bot)


async def handle_coins(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    session = request.app["session"]
    gid = int(request.match_info["gid"])
    data = await request.post()
    if not secrets.compare_digest(str(data.get("csrf", "")), session["csrf"]):
        return web.Response(text="Bad CSRF token", status=403)
    try:
        user_id = int(str(data["user_id"]).strip())
        amount = int(str(data["amount"]).strip())
    except (KeyError, ValueError):
        return web.HTTPFound(f"/guild/{gid}")
    await bot.db.add_coins(gid, user_id, amount)
    return web.HTTPFound(f"/guild/{gid}")


async def handle_antinuke(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    session = request.app["session"]
    gid = int(request.match_info["gid"])
    data = await request.post()
    if not secrets.compare_digest(str(data.get("csrf", "")), session["csrf"]):
        return web.Response(text="Bad CSRF token", status=403)
    value = 1 if str(data.get("value")) == "1" else 0
    await bot.db.set_config(gid, antinuke=value)
    return web.HTTPFound(f"/guild/{gid}")


# ---------------------------------------------------------------------------
# Auth middleware & server lifecycle
# ---------------------------------------------------------------------------

PUBLIC_PATHS = {"/login", "/health"}


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path in PUBLIC_PATHS:
        return await handler(request)
    session = valid_session(request)
    if session is None:
        return web.HTTPFound("/login")
    request.app["session"] = session
    return await handler(request)


def build_app(bot: ScrimBot) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app["bot"] = bot
    app["start_time"] = time.time()
    app.add_routes(
        [
            web.get("/health", handle_health),
            web.get("/", handle_dashboard),
            web.get("/login", handle_login_page),
            web.post("/login", handle_login),
            web.get("/logout", handle_logout),
            web.get("/dashboard", handle_dashboard),
            web.get("/guild/{gid}", handle_guild),
            web.post("/guild/{gid}/coins", handle_coins),
            web.post("/guild/{gid}/antinuke", handle_antinuke),
        ]
    )
    return app


async def start_panel(bot: ScrimBot):
    """Start the web panel if PANEL_PASSWORD is configured. Returns the runner."""
    import logging

    log = logging.getLogger("scrimbot")
    if not os.getenv("PANEL_PASSWORD"):
        log.warning(
            "Web admin panel disabled: set PANEL_PASSWORD (and WEB_PORT) to enable it."
        )
        return None
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT") or os.getenv("SERVER_PORT") or 8080)
    runner = web.AppRunner(build_app(bot))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("Web admin panel listening on %s:%s", host, port)
    return runner
