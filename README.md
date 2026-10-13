# Docker telegram monitor

Watches the containers on a Linux Docker host and sends a Telegram alert when
something goes wrong: gateway errors in logs, unhealthy or crashed containers,
restart loops, and low disk space. It runs as a single container on the host it
monitors.

Image: `appleberryd/docker-telegram-monitor` (linux/amd64 and linux/arm64).

## Image builds

GitHub Actions ([.github/workflows/docker-publish.yml](.github/workflows/docker-publish.yml))
runs the tests, then builds and pushes the image on every push to `main`. Tags:

- `latest`: the newest build from `main`
- `sha-<commit>`: every build, for pinning or rolling back
- `1.2.0` and `1.2`: when a `v1.2.0` git tag is pushed

The workflow needs the repository secrets `DOCKER_USERNAME` and
`DOCKER_PASSWORD` (a Docker Hub access token with Read & Write scope).

## Install

You need a Linux Docker host with the Docker Compose plugin, plus:

- a Telegram bot token: create the bot with @BotFather, then send `/start` to it
- your numeric Telegram chat ID

On the Docker host, run:

```sh
wget -qO- https://raw.githubusercontent.com/kibukamusoke/docker-telegram-monitor/main/setup.sh | sudo bash
```

The script:

1. installs to `/opt/docker-telegram-monitor`
2. asks for the bot token and chat ID
3. sends a Telegram test message
4. starts the monitor

Python runs inside the image, so the host does not need Python installed.

Check that it is running:

```sh
docker logs --tail 30 docker-telegram-monitor
```

The container should report `healthy` after about 30 seconds.

**Update:** run the same command again, or `sudo bash /opt/docker-telegram-monitor/setup.sh`.
Updating pulls the latest image and keeps your `config.json`, `.env` and secrets.

**Unattended install:** set the secrets as variables instead of answering prompts:

```sh
wget -qO- https://raw.githubusercontent.com/kibukamusoke/docker-telegram-monitor/main/setup.sh \
  | sudo TELEGRAM_BOT_TOKEN='123:abc' TELEGRAM_CHAT_ID='-100123' bash
```

**Change the Telegram details:** delete the file under
`/opt/docker-telegram-monitor/secrets/` and run the script again.

**Uninstall:**

```sh
cd /opt/docker-telegram-monitor && sudo docker compose down -v
sudo rm -rf /opt/docker-telegram-monitor
```

### Settings

`/opt/docker-telegram-monitor/.env` (see [.env.example](.env.example)):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DETECTOR_IMAGE` | `appleberryd/docker-telegram-monitor:latest` | Image to run; pin a `sha-…` or version tag here |
| `SERVER_NAME` | the host name | Name shown in alerts |
| `DOCKER_CONTAINERS_DIR` | found by the script | Docker's container log directory on the host |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Docker API socket |

After editing `.env`, apply the change with
`cd /opt/docker-telegram-monitor && sudo docker compose up -d`.

## Run with `docker run` (no Compose)

Run these commands as root:

```sh
mkdir -p /opt/docker-telegram-monitor/secrets && cd /opt/docker-telegram-monitor
wget -q https://raw.githubusercontent.com/kibukamusoke/docker-telegram-monitor/main/config.json
printf '%s' 'YOUR_BOT_TOKEN' > secrets/telegram_token
printf '%s' 'YOUR_CHAT_ID'   > secrets/telegram_chat_id
chmod 600 secrets/*

docker run -d \
  --name docker-telegram-monitor \
  --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  -e SERVER_NAME="$(hostname)" \
  -v "$PWD/config.json:/app/config.json:ro" \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v "$(docker info --format '{{.DockerRootDir}}')/containers:/host-containers:ro" \
  -v docker-telegram-monitor-state:/state \
  -v "$PWD/secrets/telegram_token:/run/secrets/telegram_token:ro" \
  -v "$PWD/secrets/telegram_chat_id:/run/secrets/telegram_chat_id:ro" \
  --label traefik.enable=false \
  --log-driver local --log-opt max-size=5m --log-opt max-file=2 \
  --health-cmd "python -c \"import pathlib,time; assert time.time()-pathlib.Path('/state/heartbeat').stat().st_mtime < 90\"" \
  --health-interval 30s --health-timeout 5s --health-retries 3 \
  appleberryd/docker-telegram-monitor:latest
```

To send a test message, run
`docker exec docker-telegram-monitor python -u /app/detector.py --test`.

**Update:** `docker pull appleberryd/docker-telegram-monitor:latest`, then run
`docker rm -f docker-telegram-monitor` and run the `docker run` command again.
The state volume keeps read offsets and queued alerts.

## Configuration

Edit `/opt/docker-telegram-monitor/config.json`, then run
`docker restart docker-telegram-monitor`. An empty `include_containers` list
monitors every container except those in the exclusions. To limit scope, use for
example `["traefik2", "tracksolerp2", "portainer"]`. Add containers that are
intentionally retired or in maintenance to the exclusions. Alert thresholds are
also set in this file.

## Checks and alerts

- New JSON-file log signatures: 502/503/504, unreachable/refused/reset connections,
  connection timeouts, DNS failures, database connection failures, memory/disk
  errors, router collisions and TLS errors.
- Docker state: unhealthy, OOMKilled, running-to-stopped transitions, removal
  of a previously running container, and three restarts in five minutes.
- Disk and inode use of the filesystem containing Docker's container directory:
  alerts at 90% by default. Separate volume filesystems are not checked.
- Docker API failure and unreadable expected log files.

First observation establishes restart baselines and skips existing logs; it still
alerts for current unhealthy/OOM state and disk pressure. First new issue alerts
promptly, then queued observations are grouped at most once per five minutes.
One log line may match multiple categories; counts are observations, not unique
failed requests. State conditions alert on entry, not continuously. Intentional
stops of monitored running containers also alert; use exclusions for maintenance.
No routine Telegram messages while quiet. Delivery failure retries after 30
seconds; queued summaries and read offsets survive detector restarts. A rare
crash after Telegram accepted an alert can cause a duplicate on retry.

Messages contain only container names, fixed issue categories, counts and the
latest observation time in UTC. No raw log text, headers, bodies, environment
values or credentials are sent or stored in detector state.

## Access and limits

Uses only Docker API GET requests for inventory and state. **Docker socket
access grants control of the host; a read-only socket mount does not restrict
API operations.** Run only this trusted code and protect the folder. Reads the
Docker container directory through a read-only mount, bypassing your hanging
Docker log API. Docker warns against external interaction with its managed log
files; this workaround does not modify them. See
[Docker JSON-file logging](https://docs.docker.com/engine/logging/drivers/json-file/).
Other drivers retain state checks, but have no log signature coverage.

Currently your Traefik gateway diagnostic is DEBUG output. If you disable DEBUG,
enable JSON access logs to stdout with sensitive headers excluded, or gateway
log detection may be lost. Healthy HTTP access logs are not treated as errors.
Application text signatures are heuristic and can produce false positives.

Discovers recreated containers automatically. Follows live log rotation but can
miss entries rotated away during detector downtime, rapid container replacement,
or failures before a new container is discovered. It cannot detect Cloudflare-only
errors, a dead host, short outages between 30-second state polls, or all possible
application failures. Use an external probe for complete request-path monitoring.
It does not diagnose or repair network issues. Healthcheck reports scan/log-read
failure; Docker does not automatically restart an unhealthy container.

Run the tests locally with `python3 -m unittest -v test_detector.py`.

