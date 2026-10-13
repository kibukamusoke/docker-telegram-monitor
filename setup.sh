#!/usr/bin/env bash
# Installs, or updates, docker-telegram-monitor on this Docker host. Run as root:
#   wget -qO- https://raw.githubusercontent.com/kibukamusoke/docker-telegram-monitor/main/setup.sh | sudo bash
# Re-running pulls the latest image and keeps existing config.json, .env and secrets.
set +x
set -euo pipefail
umask 077

REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/kibukamusoke/docker-telegram-monitor/main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/docker-telegram-monitor}"

fail() { echo "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail 'Run as root, e.g. with sudo.'
command -v docker >/dev/null || fail 'Docker is required.'
docker compose version >/dev/null 2>&1 || fail 'The Docker Compose plugin is required.'
docker_root=$(docker info --format '{{.DockerRootDir}}')
if [[ "$docker_root" != /* || ! -d "$docker_root/containers" ]]; then
  fail 'Run this on the Linux Docker host with access to its container directory.'
fi

# Writes to a temp file and renames, so replacing a running setup.sh is safe.
download() {
  if command -v wget >/dev/null; then wget -qO "$2.tmp" "$1"
  elif command -v curl >/dev/null; then curl -fsSL -o "$2.tmp" "$1"
  else fail 'wget or curl is required.'
  fi
  mv "$2.tmp" "$2"
}

mkdir -p "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR"
cd "$INSTALL_DIR"

echo "Installing to $INSTALL_DIR"
download "$REPO_RAW/docker-compose.yml" docker-compose.yml
download "$REPO_RAW/setup.sh" setup.sh
if [[ ! -f config.json ]]; then
  download "$REPO_RAW/config.json" config.json
fi
chmod 644 docker-compose.yml config.json

# Prompts read from the terminal so the script also works when piped into bash.
# TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID may be set instead for unattended installs.
mkdir -p secrets
chmod 700 secrets
ask() {
  [[ -r /dev/tty ]] || fail "No terminal to prompt on; set $1 instead."
  if [[ $3 == secret ]]; then
    IFS= read -r -s -p "$2" "$1" </dev/tty; printf '\n' >/dev/tty
  else
    IFS= read -r -p "$2" "$1" </dev/tty
  fi
}
if [[ ! -s secrets/telegram_token ]]; then
  [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] || ask TELEGRAM_BOT_TOKEN 'Telegram bot token (hidden): ' secret
  [[ "$TELEGRAM_BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || fail 'Unexpected Telegram token format.'
  printf '%s' "$TELEGRAM_BOT_TOKEN" > secrets/telegram_token
fi
if [[ ! -s secrets/telegram_chat_id ]]; then
  [[ -n "${TELEGRAM_CHAT_ID:-}" ]] || ask TELEGRAM_CHAT_ID 'Numeric Telegram chat ID: ' plain
  [[ "$TELEGRAM_CHAT_ID" =~ ^-?[0-9]+$ ]] || fail 'A numeric Telegram chat ID is required.'
  printf '%s' "$TELEGRAM_CHAT_ID" > secrets/telegram_chat_id
fi
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
chmod 600 secrets/telegram_token secrets/telegram_chat_id

if [[ ! -f .env ]]; then
  server_name=$(hostname | tr -c 'A-Za-z0-9_.-' '_')
  # Single-quoted Compose values preserve literal spaces/$ in filesystem paths.
  [[ "$docker_root" != *"'"* && "$docker_root" != *$'\n'* ]] || fail 'Unsupported Docker root path; configure .env manually.'
  printf "DOCKER_CONTAINERS_DIR='%s/containers'\nSERVER_NAME=%s\n" \
    "$docker_root" "$server_name" > .env
fi

compose() { docker compose -f docker-compose.yml "$@"; }

compose pull
echo 'Sending a Telegram test message...'
compose run --rm detector python -u /app/detector.py --test \
  || fail "Telegram test failed. Check $INSTALL_DIR/secrets/, then run this script again."
compose up -d
compose ps

echo
echo "docker-telegram-monitor is running from $INSTALL_DIR."
echo "Logs:   docker logs --tail 30 docker-telegram-monitor"
echo "Update: sudo bash $INSTALL_DIR/setup.sh"
