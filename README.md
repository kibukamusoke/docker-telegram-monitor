# Docker problem detector

Source project lives on the laptop. Deploy a copy on the Linux Docker server;
the setup discovers that server's Docker storage path. Nothing has been deployed
or tested against your server by the author.

## Build and push from the laptop

```sh
./deploy.sh
```

Validates Python/config, runs tests, builds amd64 with Apple's `container` CLI,
and pushes to `tracksolconnect.tms1:8830/docker_problem_detector:0.1` over HTTP.
Override the image/tag with `IMAGE=tracksolconnect.tms1:8830/docker_problem_detector:0.2 ./deploy.sh`.
Secrets are excluded from the build context. This publishes an image; it does
not start or update containers on the server.

## Deploy

Copy docker-compose.yml, config.json and setup.sh to any server folder, then enter that folder as root. Python runs inside the image; no host Python installation is needed:

```sh
bash setup.sh
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml run --rm detector python -u /app/detector.py --test
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml ps
docker logs --tail 30 docker-problem-detector
```

Requires Bash, Docker Compose, a Telegram bot token, and your
numeric chat ID. Create the bot with @BotFather and send /start to it first.
Setup prompts for the token without echoing it. Proceed with `up` only after
the Telegram test arrives. Expect healthy status after about 30 seconds.

Edit `config.json` before deployment. Empty `include_containers` monitors all
containers except exclusions. To limit scope, use e.g. `["traefik2", "tracksolerp2",
"portainer"]`. Add intentionally retired/maintenance containers to exclusions.
Restart the detector after configuration changes. Thresholds are configurable.
If using a different tag, set `DETECTOR_IMAGE` in the server's `.env` file.
The server Docker daemon must already allow your HTTP registry. Updates use
`docker compose -f docker-compose.yml pull` followed by `docker compose -f docker-compose.yml up -d`. Detector code is now
bundled in the image; configuration remains a local file mount.

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

Remove: `docker compose -f docker-compose.yml down` (keeps state). Delete local secrets separately.
Tests: `python3 -m unittest -v test_detector.py`.
# docker-telegram-monitor
