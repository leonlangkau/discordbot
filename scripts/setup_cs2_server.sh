#!/usr/bin/env bash
#
# Provisions a CS2 (Counter-Strike 2) dedicated server on a fresh Ubuntu/Debian
# VPS and wires it up for use with this bot's RCON integration (rcon.py).
#
# Run this AS ROOT, ON THE VPS ITSELF (over your own SSH session) — not from
# anywhere else:
#
#   curl -fsSL https://raw.githubusercontent.com/leonlangkau/discordbot/claude/discord-bot-server-setup-hyxdow/scripts/setup_cs2_server.sh -o setup_cs2_server.sh
#   chmod +x setup_cs2_server.sh
#   ./setup_cs2_server.sh
#
# Safe to re-run: it reuses any existing install and RCON password instead of
# starting over, so re-running just to pick up a fix (or to add a GSLT) is fine.
#
# Optional overrides (env vars), e.g. `CS2_PORT=27016 ./setup_cs2_server.sh`:
#   CS2_INSTALL_DIR   Install path                  (default: /home/steam/cs2-ds)
#   CS2_PORT          Game/RCON port                (default: 27015)
#   CS2_MAP           Startup map                   (default: de_mirage)
#   HOSTNAME_CVAR     Server browser name           (default: "CS2 Scrim Server")
#   SERVER_PASSWORD   sv_password to join (blank=none)
#   RCON_PASSWORD     RCON password (auto-generated if unset and not already configured)
#   GSLT              Game Server Login Token — get one free at
#                      https://steamcommunity.com/dev/managegameservers (App ID 730)
#                      after the server is up; without it Valve may limit how long
#                      the server stays publicly joinable. Not required for the
#                      bot's own RCON control or for private/direct-connect play.
#
set -euo pipefail

CS2_INSTALL_DIR="${CS2_INSTALL_DIR:-/home/steam/cs2-ds}"
CS2_PORT="${CS2_PORT:-27015}"
CS2_MAP="${CS2_MAP:-de_mirage}"
HOSTNAME_CVAR="${HOSTNAME_CVAR:-CS2 Scrim Server}"
SERVER_PASSWORD="${SERVER_PASSWORD:-}"
ENV_FILE="/etc/cs2-server.env"
SERVICE_NAME="cs2-server"
STEAM_USER="steam"

log() { printf '\n\033[1;32m==>\033[0m %s\n' "$1"; }
die() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run this as root (e.g. with sudo)."
[[ "$(uname -m)" == "x86_64" ]] || die "CS2's dedicated server requires x86_64; this host is $(uname -m)."
command -v apt-get >/dev/null 2>&1 || die "This script only supports Debian/Ubuntu (apt-get not found). Adapt the package-install steps manually for your distro."

# Reuse a prior run's secrets/config instead of clobbering them.
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi
RCON_PASSWORD="${RCON_PASSWORD:-}"
GSLT="${GSLT:-}"
if [[ -z "$RCON_PASSWORD" ]]; then
  RCON_PASSWORD="$(openssl rand -base64 18 2>/dev/null || head -c18 /dev/urandom | base64)"
fi

AVAIL_KB="$(df -Pk "$(dirname "$CS2_INSTALL_DIR")" 2>/dev/null | awk 'NR==2{print $4}' || echo 0)"
if [[ "${AVAIL_KB:-0}" -lt 78643200 ]]; then # ~75GB
  die "Need ~75GB free for the ~60GB CS2 download; only $((AVAIL_KB/1024/1024))GB free near $CS2_INSTALL_DIR. Free up space and re-run."
fi

log "Creating unprivileged '$STEAM_USER' user (the game server should never run as root)"
id -u "$STEAM_USER" &>/dev/null || useradd -m -s /bin/bash "$STEAM_USER"
mkdir -p "$CS2_INSTALL_DIR"
chown -R "$STEAM_USER:$STEAM_USER" "$(dirname "$CS2_INSTALL_DIR")"

log "Installing dependencies (32-bit libs for steamcmd, curl, openssl)"
dpkg --add-architecture i386
apt-get update -qq
apt-get install -y -qq curl tar openssl ca-certificates >/dev/null
apt-get install -y -qq lib32gcc-s1 lib32stdc++6 >/dev/null 2>&1 || apt-get install -y -qq lib32gcc1 lib32stdc++6 >/dev/null

STEAMCMD_DIR="/home/$STEAM_USER/steamcmd"
if [[ ! -x "$STEAMCMD_DIR/steamcmd.sh" ]]; then
  log "Installing SteamCMD"
  sudo -u "$STEAM_USER" mkdir -p "$STEAMCMD_DIR"
  curl -fsSL "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz" \
    | sudo -u "$STEAM_USER" tar -xz -C "$STEAMCMD_DIR"
fi

log "Downloading/updating the CS2 dedicated server (app 730, anonymous login — this can take 10-30 min for ~60GB)"
sudo -u "$STEAM_USER" "$STEAMCMD_DIR/steamcmd.sh" \
  +force_install_dir "$CS2_INSTALL_DIR" \
  +login anonymous \
  +app_update 730 validate \
  +quit

# Common "Failed to load steamclient.so" fix.
sudo -u "$STEAM_USER" mkdir -p "/home/$STEAM_USER/.steam/sdk64"
ln -sf "$STEAMCMD_DIR/linux64/steamclient.so" "/home/$STEAM_USER/.steam/sdk64/steamclient.so"

CS2_LAUNCHER="$(find "$CS2_INSTALL_DIR" -maxdepth 2 -iname 'cs2.sh' | head -1)"
[[ -n "$CS2_LAUNCHER" ]] || die "Couldn't find cs2.sh under $CS2_INSTALL_DIR — the steamcmd download may have failed. Check the output above and re-run."

CFG_DIR="$(find "$CS2_INSTALL_DIR" -type d -path '*/csgo/cfg' | head -1)"
[[ -n "$CFG_DIR" ]] || die "Couldn't find the csgo/cfg directory under $CS2_INSTALL_DIR."

log "Writing server.cfg"
[[ -f "$CFG_DIR/server.cfg" ]] && cp "$CFG_DIR/server.cfg" "$CFG_DIR/server.cfg.bak"
cat > "$CFG_DIR/server.cfg" <<EOF
hostname "$HOSTNAME_CVAR"
rcon_password "$RCON_PASSWORD"
sv_password "$SERVER_PASSWORD"
EOF
chown "$STEAM_USER:$STEAM_USER" "$CFG_DIR/server.cfg"

log "Writing $ENV_FILE and start wrapper"
cat > "$ENV_FILE" <<EOF
CS2_INSTALL_DIR=$CS2_INSTALL_DIR
CS2_PORT=$CS2_PORT
CS2_MAP=$CS2_MAP
RCON_PASSWORD=$RCON_PASSWORD
GSLT=$GSLT
EOF
chown root:root "$ENV_FILE"
chmod 600 "$ENV_FILE"

cat > "$CS2_INSTALL_DIR/start.sh" <<'STARTSCRIPT'
#!/usr/bin/env bash
set -euo pipefail
source /etc/cs2-server.env
cd "$CS2_INSTALL_DIR"
LAUNCHER="$(find "$CS2_INSTALL_DIR" -maxdepth 2 -iname 'cs2.sh' | head -1)"
ARGS=(-dedicated -usercon -port "$CS2_PORT" +map "$CS2_MAP" +game_type 0 +game_mode 1)
if [[ -n "${GSLT:-}" ]]; then
  ARGS+=(+sv_setsteamaccount "$GSLT")
fi
exec "$LAUNCHER" "${ARGS[@]}"
STARTSCRIPT
chmod +x "$CS2_INSTALL_DIR/start.sh"
chown "$STEAM_USER:$STEAM_USER" "$CS2_INSTALL_DIR/start.sh"

log "Installing systemd service"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=CS2 Dedicated Server
After=network.target

[Service]
Type=simple
User=$STEAM_USER
WorkingDirectory=$CS2_INSTALL_DIR
EnvironmentFile=-$ENV_FILE
ExecStart=$CS2_INSTALL_DIR/start.sh
Restart=on-failure
RestartSec=5
LimitNOFILE=100000

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  log "ufw is active — opening game ports ($CS2_PORT/tcp, $CS2_PORT/udp, 27020/udp)"
  ufw allow "$CS2_PORT/tcp" >/dev/null
  ufw allow "$CS2_PORT/udp" >/dev/null
  ufw allow 27020/udp >/dev/null
else
  log "ufw isn't active here, so it was left untouched (won't risk locking you out of SSH)."
  echo "  Make sure ${CS2_PORT}/tcp, ${CS2_PORT}/udp and 27020/udp are reachable — via your"
  echo "  own firewall and, if applicable, your VPS provider's network/security-group rules."
fi

sleep 5
PUBLIC_IP="$(curl -fsSL --max-time 5 ifconfig.me || echo '<your-server-ip>')"

echo
echo "============================================================"
if systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "cs2-server is running."
else
  echo "cs2-server did NOT start correctly. Check: journalctl -u $SERVICE_NAME -e"
fi
echo "============================================================"
echo "Install dir     : $CS2_INSTALL_DIR"
echo "Server address  : $PUBLIC_IP:$CS2_PORT"
echo "RCON password   : $RCON_PASSWORD"
echo "Join password   : ${SERVER_PASSWORD:-<none>}"
echo
echo "Logs            : journalctl -u $SERVICE_NAME -f"
echo "Restart         : systemctl restart $SERVICE_NAME"
echo
if [[ -z "$GSLT" ]]; then
  echo "No GSLT set. Get a free one at https://steamcommunity.com/dev/managegameservers"
  echo "(App ID 730), then: echo 'GSLT=your-token' | sudo tee -a $ENV_FILE && sudo systemctl restart $SERVICE_NAME"
  echo
fi
echo "Wire this into the Discord bot with ONE of:"
echo "  - .env:    SERVER_HOST=$PUBLIC_IP SERVER_PORT=$CS2_PORT SERVER_PASSWORD=${SERVER_PASSWORD:-} RCON_PASSWORD=$RCON_PASSWORD"
echo "  - Discord: /scrimconfig server host:$PUBLIC_IP port:$CS2_PORT password:${SERVER_PASSWORD:-\"\"} rcon_password:$RCON_PASSWORD"
echo "============================================================"
