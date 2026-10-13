#!/usr/bin/env bash
# Installs, or updates, docker-telegram-monitor into the current folder. Run as root:
#   wget -qO- https://raw.githubusercontent.com/kibukamusoke/docker-telegram-monitor/main/setup.sh | sudo bash
# Re-running pulls the latest image and keeps existing config.json, .env and secrets.
# Rotate the Telegram bot token or chat ID:
#   sudo bash setup.sh --rotate-secrets
set +x
set -euo pipefail
umask 077

REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/kibukamusoke/docker-telegram-monitor/main}"
# Piped into bash: the current folder. Run from a saved copy: that copy's folder.
if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
  INSTALL_DIR="${INSTALL_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
else
  INSTALL_DIR="${INSTALL_DIR:-$PWD}"
fi

fail() { echo "$*" >&2; exit 1; }

rotate=0
case "${1:-}" in
  '') ;;
  --rotate-secrets) rotate=1 ;;
  *) fail "Unknown option: $1 (supported: --rotate-secrets)" ;;
esac

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
cd "$INSTALL_DIR"

echo "Installing to $INSTALL_DIR"
download "$REPO_RAW/docker-compose.yml" docker-compose.yml
download "$REPO_RAW/setup.sh" setup.sh
if [[ ! -f config.json ]]; then
  download "$REPO_RAW/config.json" config.json
fi
chmod 644 docker-compose.yml config.json

# Prompts read from the terminal so the script also works when piped into bash.
# TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID may be set instead of answering prompts.
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

old_token=$(cat secrets/telegram_token 2>/dev/null || true)
old_chat=$(cat secrets/telegram_chat_id 2>/dev/null || true)
if (( rotate )); then
  [[ -n "$old_token" && -n "$old_chat" ]] || fail 'Nothing to rotate yet; run without --rotate-secrets to install.'
  if [[ -z "${TELEGRAM_BOT_TOKEN:-}${TELEGRAM_CHAT_ID:-}" ]]; then
    echo 'Rotating Telegram credentials. Leave a value blank to keep the current one.'
    ask TELEGRAM_BOT_TOKEN 'New Telegram bot token (hidden): ' secret
    ask TELEGRAM_CHAT_ID 'New numeric Telegram chat ID: ' plain
  fi
else
  if [[ -z "$old_token" && -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    ask TELEGRAM_BOT_TOKEN 'Telegram bot token (hidden): ' secret
  fi
  if [[ -z "$old_chat" && -z "${TELEGRAM_CHAT_ID:-}" ]]; then
    ask TELEGRAM_CHAT_ID 'Numeric Telegram chat ID: ' plain
  fi
  # Without --rotate-secrets, saved values are never replaced.
  [[ -z "$old_token" ]] || TELEGRAM_BOT_TOKEN=''
  [[ -z "$old_chat" ]] || TELEGRAM_CHAT_ID=''
fi
new_token="${TELEGRAM_BOT_TOKEN:-$old_token}"
new_chat="${TELEGRAM_CHAT_ID:-$old_chat}"
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
[[ "$new_token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || fail 'Unexpected Telegram token format.'
[[ "$new_chat" =~ ^-?[0-9]+$ ]] || fail 'A numeric Telegram chat ID is required.'

# Writes in place, keeping the file a running container has bind-mounted.
write_secrets() {
  printf '%s' "$1" > secrets/telegram_token
  printf '%s' "$2" > secrets/telegram_chat_id
  chmod 600 secrets/telegram_token secrets/telegram_chat_id
}
# Puts back the previous values, or removes new ones so the next run prompts again.
restore_secrets() {
  if [[ -n "$old_token" ]]; then printf '%s' "$old_token" > secrets/telegram_token
  else rm -f secrets/telegram_token; fi
  if [[ -n "$old_chat" ]]; then printf '%s' "$old_chat" > secrets/telegram_chat_id
  else rm -f secrets/telegram_chat_id; fi
}
write_secrets "$new_token" "$new_chat"
unset new_token new_chat

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
if ! compose run --rm detector python -u /app/detector.py --test; then
  restore_secrets
  fail 'Telegram test failed; saved credentials were not changed. Check the token and chat ID, then run this script again.'
fi
unset old_token old_chat
if (( rotate )); then
  compose up -d --force-recreate
else
  compose up -d
fi
compose ps

echo
echo "docker-telegram-monitor is running from $INSTALL_DIR."
echo "Logs:   docker logs --tail 30 docker-telegram-monitor"
echo "Update: sudo bash \"$INSTALL_DIR/setup.sh\""
echo "Rotate: sudo bash \"$INSTALL_DIR/setup.sh\" --rotate-secrets"
