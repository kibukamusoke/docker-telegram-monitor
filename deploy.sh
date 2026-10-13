#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

IMAGE="${IMAGE:-appleberryd/docker-telegram-monitor:0.1}"

command -v python3 >/dev/null || { echo 'python3 is required.' >&2; exit 1; }
command -v container >/dev/null || { echo 'The container CLI is required.' >&2; exit 1; }

echo 'Validating detector source and configuration...'
python3 -B - <<'PY'
import ast
import json
from pathlib import Path
ast.parse(Path('detector.py').read_text())
json.loads(Path('config.json').read_text())
PY
python3 -B -m unittest -v test_detector.py

echo "Building amd64 image: $IMAGE"
container build --arch amd64 -t "$IMAGE" .

echo "Pushing image: $IMAGE"
container image push --scheme http "$IMAGE"

echo 'Image published. On the server, run docker compose pull followed by docker compose up -d.'
