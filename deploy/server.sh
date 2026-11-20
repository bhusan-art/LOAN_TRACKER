#!/usr/bin/env bash
# Ledgerly on a Linux server (Debian 12/13 or Ubuntu 22.04/24.04, e.g. a Proxmox VM).
#
#   sudo ./deploy/server.sh install              first time: Docker, Tailscale https, .env, app, backups
#   sudo ./deploy/server.sh update               after a merge on GitHub: pull, rebuild, restart
#   sudo ./deploy/server.sh status               containers, health, https address, last backup
#   sudo ./deploy/server.sh create-user NAME     a sign-in (prints the authenticator setup key)
#   sudo ./deploy/server.sh backup               encrypted backup now (also runs nightly at 02:30)
#   sudo ./deploy/server.sh import DUMP [DOCS]   move data from the Windows PC (REPLACES server data)
#   sudo ./deploy/server.sh logs                 follow the app logs (Ctrl+C to stop)
#
# Run it from the cloned repository (README "Linux server"). Safe to run install again.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DEFAULT_PORT=8765

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!!  %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31mxx  %s\033[0m\n' "$*" >&2; exit 1; }

need_root() { [ "$(id -u)" -eq 0 ] || die "Run with sudo: sudo $0 $*"; }

# ---- .env helpers (KEY=value lines; values written here never contain / & or spaces) ----
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 || true; }
env_set() {
  if grep -q "^$1=" .env; then sed -i "s/^$1=.*/$1=$2/" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}
port() { local p; p="$(env_get APP_PORT)"; echo "${p:-$DEFAULT_PORT}"; }
dc() { docker compose "$@"; }

wait_healthy() {
  for _ in $(seq 1 90); do
    if curl -fsS "http://127.0.0.1:$(port)/healthz" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  dc logs --tail 40 app >&2
  die "The app did not come up on port $(port). Logs are above."
}

https_name() { tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // empty' | sed 's/\.$//'; }

# ---------------------------------------------------------------------------- install
install_packages() {
  say "System packages and time zone"
  . /etc/os-release
  case "$ID" in debian|ubuntu) ;; *) die "This script supports Debian and Ubuntu (found: $ID)." ;; esac
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -yq ca-certificates curl git gnupg jq ufw p7zip-full unattended-upgrades qemu-guest-agent >/dev/null
  systemctl enable --now qemu-guest-agent >/dev/null 2>&1 || true   # lets Proxmox shut down / back up cleanly
  timedatectl set-timezone Asia/Kolkata || true                    # nightly backup at 02:30 IST
  dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true   # security updates

  if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    say "Docker (official repository)"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
  fi
  systemctl enable --now docker >/dev/null   # containers restart by themselves after a reboot

  if ! command -v tailscale >/dev/null; then
    say "Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
}

make_env() {
  if [ -f .env ]; then
    say "Keeping the existing .env"
  else
    say "Creating .env with new random passwords"
    cp .env.example .env
    local pw; pw="$(openssl rand -hex 24)"
    env_set POSTGRES_PASSWORD "$pw"
    sed -i "s/change-me@/$pw@/" .env                       # the two DATABASE_URL lines
    env_set SECRET_KEY "$(openssl rand -hex 32)"
    env_set APP_PORT "$DEFAULT_PORT"
    env_set SECURE_COOKIES 1                               # only reachable over https (Tailscale)
  fi
  [ -n "$(env_get APP_PORT)" ] || env_set APP_PORT "$DEFAULT_PORT"
  [ -n "$(env_get BACKUP_PASSPHRASE)" ] || env_set BACKUP_PASSPHRASE "$(openssl rand -hex 24)"

  if [ -z "$(env_get DOCUMENT_KEY)" ] && [ -t 0 ]; then
    echo
    echo "Moving from the Windows PC? Paste its DOCUMENT_KEY (from its .env) so the documents"
    read -r -p "open here too. Or press Enter to create a new key: " key
    key="${key#DOCUMENT_KEY=}"
    if [ -n "$key" ]; then
      case "$key" in *[!A-Za-z0-9_=-]*) die "That does not look like a DOCUMENT_KEY." ;; esac
      env_set DOCUMENT_KEY "$key"
    fi
  fi
  chmod 600 .env
}

start_app() {
  say "Building and starting Ledgerly (the first build takes a few minutes)"
  dc up -d --build
  wait_healthy
  if [ -z "$(env_get DOCUMENT_KEY)" ]; then
    local line
    line="$(dc exec -T app python -m loan_tracker.cli gen-doc-key | grep -m1 '^DOCUMENT_KEY=')" \
      || die "Could not create DOCUMENT_KEY."
    env_set DOCUMENT_KEY "${line#DOCUMENT_KEY=}"
    dc up -d --force-recreate app scheduler
    wait_healthy
  fi
}

setup_tailscale() {
  say "Tailscale: private https address"
  if ! tailscale status >/dev/null 2>&1; then
    echo "Open the link below and sign in with your Tailscale account to add this server."
    tailscale up
  fi
  echo "If it asks to enable HTTPS, open the link it prints (admin console: DNS -> HTTPS Certificates)."
  tailscale serve --bg "$(port)"
}

setup_firewall() {
  say "Firewall: SSH and Tailscale only"
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow OpenSSH >/dev/null
  ufw allow in on tailscale0 >/dev/null
  ufw --force enable >/dev/null
}

setup_backups() {
  say "Nightly encrypted backup at 02:30"
  chmod +x deploy/backup.sh
  cat > /etc/cron.d/ledgerly-backup <<EOF
# Ledgerly: database + documents, gpg-encrypted, 60 days kept (deploy/server.sh)
30 2 * * * root cd $ROOT && ./deploy/backup.sh >> backups/backup.log 2>&1
EOF
  chmod 644 /etc/cron.d/ledgerly-backup
}

cmd_install() {
  need_root install
  [ -f docker-compose.yml ] || die "Run this from the cloned LOAN_TRACKER folder."
  install_packages
  make_env
  start_app
  setup_tailscale
  setup_firewall
  setup_backups
  local name; name="$(https_name)"
  say "Done"
  cat <<EOF

  Ledgerly is running.
    Address (phone, PC, apps):  https://${name:-<this-server>.<tailnet>.ts.net}
    Only devices signed in to your Tailscale can reach it. Nothing is open to the internet.

  SAVE THESE TWO IN YOUR PASSWORD MANAGER NOW (they are not inside any backup):
    DOCUMENT_KEY=$(env_get DOCUMENT_KEY)
    BACKUP_PASSPHRASE=$(env_get BACKUP_PASSPHRASE)

  Next:
    New start:           sudo ./deploy/server.sh create-user owner
    Moving from Windows: sudo ./deploy/server.sh import <dump.sql> [<documents folder>]
    In the apps: Settings -> Change server -> the address above.
EOF
}

# ---------------------------------------------------------------------------- daily commands
cmd_update() {
  need_root update
  say "Getting the latest code"
  git pull --ff-only
  say "Rebuilding and restarting"
  dc up -d --build
  wait_healthy
  docker image prune -f >/dev/null
  say "Updated. $(git log -1 --format='%h %s')"
}

cmd_status() {
  dc ps
  echo
  if curl -fsS "http://127.0.0.1:$(port)/healthz"; then echo; else warn "The app is not answering on port $(port)."; fi
  echo; tailscale serve status 2>/dev/null || warn "Tailscale serve is not set up."
  echo; echo "Latest backups:"; ls -1t backups/*.gpg 2>/dev/null | head -2 || echo "  none yet"
}

cmd_create_user() {
  need_root create-user
  [ $# -ge 1 ] || die "Usage: $0 create-user NAME"
  dc exec app python -m loan_tracker.cli create-user "$@"
}

cmd_backup() { need_root backup; ./deploy/backup.sh; }

# A .7z from deploy/backup.ps1 is unpacked first (7z asks for the passphrase).
unpack() {
  case "$1" in
    *.7z) local dir; dir="$(mktemp -d)"; 7z x -o"$dir" "$1" >&2 || die "Could not unpack $1"; echo "$dir" ;;
    *)    echo "$1" ;;
  esac
}

cmd_import() {
  need_root import
  [ $# -ge 1 ] || die "Usage: $0 import <loan_tracker_STAMP.sql or .sql.7z> [<loan_tracker_docs_STAMP folder or .7z>]"
  [ -f "$1" ] || die "No file $1"
  [ $# -lt 2 ] || [ -e "$2" ] || die "No folder or file $2"
  local sql docs=""
  sql="$(unpack "$1")"
  [ -d "$sql" ] && sql="$(find "$sql" -type f -name '*.sql' | head -1)"
  [ -f "$sql" ] || die "No .sql file in $1"
  if [ $# -ge 2 ]; then
    docs="$(unpack "$2")"
    case "$2" in   # inside a .7z: the loan_tracker_docs_STAMP folder
      *.7z) docs="$(find "$docs" -mindepth 1 -maxdepth 1 -type d | head -1)" ;;
    esac
    [ -d "$docs" ] || die "Expected a documents folder in $2"
  fi

  cat <<EOF

  This REPLACES everything in the server's database with $(basename "$sql")
  (a backup of the current server data is made first).
  Documents only open if DOCUMENT_KEY in .env is the key from the Windows PC.
EOF
  read -r -p "  Type yes to continue: " ok
  [ "$ok" = yes ] || die "Stopped, nothing changed."

  say "Backing up the current server data"
  ./deploy/backup.sh
  say "Loading the Windows data"
  dc stop app scheduler
  dc exec -T db dropdb -U loan_tracker --if-exists loan_tracker
  dc exec -T db createdb -U loan_tracker loan_tracker
  # Windows PowerShell writes a byte-order mark and CRLF line ends: remove both
  if ! sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' "$sql" \
      | dc exec -T db psql -q -v ON_ERROR_STOP=1 -U loan_tracker -d loan_tracker >/dev/null; then
    die "The import failed (error above); the app is stopped. To go back: the newest files in backups/ (restore steps in deploy/backup.sh)."
  fi
  dc start app scheduler
  wait_healthy
  if [ -n "$docs" ]; then
    say "Copying documents"
    dc cp "$docs/." app:/data/documents/
    dc exec -T -u root app chown -R app:app /data/documents
  fi
  say "Imported. Sign in with the same users as on Windows; the apps must sign in again."
}

[ "${BASH_SOURCE[0]}" = "$0" ] || return 0   # sourced (tests): functions only

case "${1:-}" in
  install)     shift; cmd_install "$@" ;;
  update)      shift; cmd_update "$@" ;;
  status)      shift; cmd_status "$@" ;;
  create-user) shift; cmd_create_user "$@" ;;
  backup)      shift; cmd_backup "$@" ;;
  import)      shift; cmd_import "$@" ;;
  logs)        dc logs -f --tail 100 app ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
