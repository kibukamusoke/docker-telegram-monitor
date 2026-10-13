import getpass
import os
from pathlib import Path
import re
import socket
import subprocess

os.umask(0o077)
root = Path(subprocess.check_output(
    ["docker", "info", "--format", "{{.DockerRootDir}}"], text=True).strip())
if not (root / "containers").is_dir():
    raise SystemExit("Run this on the Linux Docker server, as root.")
token = getpass.getpass("Telegram bot token (hidden): ").strip()
chat = input("Numeric Telegram chat ID: ").strip()
if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-?[0-9]+", chat):
    raise SystemExit("Unexpected token or numeric chat ID format.")
Path("secrets").mkdir(exist_ok=True, mode=0o700)
for name, value in (("telegram_token", token), ("telegram_chat_id", chat)):
    p = Path("secrets") / name
    p.write_text(value)
    p.chmod(0o600)
server = re.sub(r"[^A-Za-z0-9_.-]", "_", socket.gethostname())
Path(".env").write_text(f"DOCKER_CONTAINERS_DIR={root / 'containers'}\nSERVER_NAME={server}\n")
print("Saved local configuration. Token was not printed.")
