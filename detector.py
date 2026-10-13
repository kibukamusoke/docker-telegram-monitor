"""Docker issue detector: GET-only API calls, bounded log reads, allowlisted alerts."""
import collections
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import urllib.request

STATE = Path('/state/state.json')
LOGS = Path('/host-containers')


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


def docker_get(path):
    connection = DockerConnection('localhost', timeout=5)
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        if response.status != 200:
            raise OSError('Docker GET failed')
        return json.loads(response.read())
    finally:
        connection.close()


PATTERNS = {
    'http_502': r"(?:msg=\"'502 Bad Gateway' caused by:|\b502 Bad Gateway\b)",
    'http_503_504': r'\b(?:503 Service Unavailable|504 Gateway Timeout)\b',
    'network_unreachable': r'\b(?:no route to host|network is unreachable)\b',
    'connection_refused': r'\b(?:connection refused|ECONNREFUSED)\b',
    'connection_reset': r'\b(?:connection reset by peer|ECONNRESET)\b',
    'timeout': r'\b(?:i/o timeout|ETIMEDOUT|context deadline exceeded|connection timed out)\b',
    'dns_failure': r'\b(?:EAI_AGAIN|ENOTFOUND|no such host|temporary failure in name resolution)\b',
    'database_connection': r'(?:unable to connect to (?:the )?database|MongoServerSelectionError|access denied for user|too many connections|connection pool exhausted)',
    'out_of_memory': r'\b(?:out of memory|JavaScript heap out of memory|OOMKilled)\b',
    'disk_full': r'\b(?:no space left on device|ENOSPC)\b',
    'router_collision': r'Router defined multiple times with different configurations',
    'tls_failure': r'(?:TLS handshake error|x509:|certificate has expired|certificate verify failed)',
}


def classify(raw, enabled):
    try:
        record = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(record, dict) or not isinstance(record.get('log'), str):
        return []
    text = record['log']
    try:
        access = json.loads(text)
    except ValueError:
        access = None
    if isinstance(access, dict) and 'DownstreamStatus' in access:
        status = str(access['DownstreamStatus'])
        category = 'http_502' if status == '502' else 'http_503_504' if status in ('503', '504') else None
        return [category] if category in enabled else []
    # Traefik debug requests can contain user-controlled headers and secrets.
    if 'Request=' in text and not re.search(r'msg="\'(?:502|504) ', text):
        return []
    text = text.split(' Request=', 1)[0]
    return [name for name, pattern in PATTERNS.items()
            if name in enabled and re.search(pattern, text, re.IGNORECASE)]


def selected(name, config):
    return (name not in config['exclude_containers'] and
            (not config['include_containers'] or name in config['include_containers']))


def restart_burst(history, now, increment, window, threshold):
    history[:] = [item for item in history if item[0] >= now - window]
    if increment > 0:
        history.append([now, increment])
    return sum(item[1] for item in history) >= threshold


def send(text):
    token = Path('/run/secrets/telegram_token').read_text().strip()
    chat = Path('/run/secrets/telegram_chat_id').read_text().strip()
    request = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=json.dumps({'chat_id': chat, 'text': text}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return bool(json.load(response).get('ok'))
    except Exception:
        # Exceptions may contain the token-bearing URL; never print them.
        print('Telegram delivery failed; queued alerts will retry.', flush=True)
        return False


def persist(state):
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(state))
    temp.replace(STATE)


class Detector:
    def __init__(self, config, state):
        self.config, self.state = config, state
        self.files = {}
        self.sources = {}
        self.retry_at = 0
        self.last_scan = 0
        self.scan_ok = False

    def event(self, name, category):
        # No arbitrary message text, headers, addresses or request bodies persisted.
        name = re.sub(r'[^A-Za-z0-9_.-]', '_', name)[:100]
        key = name + ': ' + category
        pending = self.state.setdefault('pending', {})
        if key in pending or len(pending) < 100:
            pending[key] = pending.get(key, 0) + 1
        self.state['latest_utc'] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())

    def transition(self, key, active, name, category):
        flags = self.state.setdefault('flags', {})
        if active and not flags.get(key, False):
            self.event(name, category)
        flags[key] = active

    def scan(self, now):
        inventory = docker_get('/containers/json?all=1')
        new_sources = {}
        observed = self.state.setdefault('containers', {})
        seen = set()
        for item in inventory:
            name = item['Names'][0].lstrip('/') if item.get('Names') else item['Id'][:12]
            if not selected(name, self.config):
                continue
            seen.add(name)
            info = docker_get('/containers/' + item['Id'] + '/json')
            runtime = info['State']
            previous = observed.get(name, {})
            # Previously stopped containers are not presumed to be incidents.
            if previous.get('running') and not runtime['Running']:
                self.event(name, 'container_stopped')
            self.transition(name + ':unhealthy', runtime.get('Health', {}).get('Status') == 'unhealthy', name, 'unhealthy')
            self.transition(name + ':oom', bool(runtime.get('OOMKilled')), name, 'oom_killed')
            count = info.get('RestartCount', 0)
            history = previous.get('restarts', [])
            delta = max(0, count - previous.get('count', count)) if previous.get('id') == item['Id'] else 0
            burst = restart_burst(history, now, delta, self.config['restart_window_seconds'], self.config['restart_threshold'])
            self.transition(name + ':burst', burst, name, 'restart_loop')
            observed[name] = {'id': item['Id'], 'running': runtime['Running'], 'count': count, 'restarts': history}
            if runtime['Running']:
                if info.get('HostConfig', {}).get('LogConfig', {}).get('Type') == 'json-file':
                    new_sources[item['Id']] = name
                else:
                    print(f'Log signature checks unavailable for {name}: not json-file.', flush=True)
        for name, old in list(observed.items()):
            if name not in seen and selected(name, self.config) and old.get('running'):
                self.event(name, 'container_removed')
                old['running'] = False
        self.sources = new_sources
        disk = os.statvfs(LOGS)
        disk_percent = 100 * (1 - disk.f_bavail / disk.f_blocks) if disk.f_blocks else 0
        inode_percent = 100 * (1 - disk.f_favail / disk.f_files) if disk.f_files else 0
        self.transition('disk', disk_percent >= self.config['disk_percent'], 'docker_storage', 'disk_pressure')
        self.transition('inode', inode_percent >= self.config['inode_percent'], 'docker_storage', 'inode_pressure')

    def read_logs(self):
        good = True
        offsets = self.state.setdefault('offsets', {})
        for cid, name in self.sources.items():
            path = LOGS / cid / (cid + '-json.log')
            try:
                stat = path.stat()
                identity = [stat.st_dev, stat.st_ino]
                cursor = offsets.get(cid)
                handle = self.files.get(cid)
                if handle is None:
                    handle = path.open('rb')
                    self.files[cid] = handle
                    # First discovery starts at EOF. Restarts resume saved offsets.
                    offset = stat.st_size if cursor is None else cursor['offset'] if cursor['identity'] == identity and cursor['offset'] <= stat.st_size else 0
                    handle.seek(offset)
                    cursor = offsets[cid] = {'identity': identity, 'offset': offset}
                elif cursor['identity'] == identity and stat.st_size < handle.tell():
                    handle.seek(0)
                for _ in range(1000):
                    start = handle.tell()
                    line = handle.readline(1024 * 1024)
                    if not line or not line.endswith(b'\n'):
                        handle.seek(start)
                        break
                    for category in classify(line, self.config['log_categories']):
                        self.event(name, category)
                    cursor['offset'] = handle.tell()
                if cursor['identity'] != identity and handle.tell() >= os.fstat(handle.fileno()).st_size:
                    handle.close()
                    del self.files[cid]
                    offsets[cid] = {'identity': identity, 'offset': 0}
                self.transition(name + ':log', False, name, 'log_unavailable')
            except OSError:
                good = False
                self.transition(name + ':log', True, name, 'log_unavailable')
        for cid in list(self.files):
            if cid not in self.sources:
                self.files.pop(cid).close()
        return good

    def deliver(self, now):
        pending = self.state.get('pending', {})
        if not pending or now < max(self.state.get('last_sent', 0) + self.config['cooldown_seconds'], self.retry_at):
            return
        # Bound message size and drain remaining queued keys on later deliveries.
        batch = list(pending.items())[:20]
        server = re.sub(r'[^A-Za-z0-9_.-]', '_', os.environ.get('SERVER_NAME', 'server'))[:100]
        message = f'Docker issue alert — {server}\nLatest observation: {self.state["latest_utc"]}\n'
        message += '\n'.join(f'{key} ({count} observations)' for key, count in batch)
        if send(message):
            for key, _ in batch:
                del pending[key]
            self.state['last_sent'] = now
            persist(self.state)
            print('Issue alert delivered.', flush=True)
        else:
            self.retry_at = now + 30


def main():
    if '--test' in sys.argv:
        ok = send('Docker telegram monitor: Telegram deployment test.')
        print('Test delivered.' if ok else 'Test FAILED.')
        return 0 if ok else 1
    config = json.loads(Path('/app/config.json').read_text())
    STATE.parent.mkdir(exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    detector = Detector(config, state)
    print('Detector started. No historical log replay on first discovery.', flush=True)
    while True:
        now = time.time()
        if now - detector.last_scan >= 30:
            try:
                detector.scan(now)
                detector.scan_ok = True
                detector.transition('api', False, 'docker', 'api_unavailable')
            except (OSError, ValueError, http.client.HTTPException):
                detector.scan_ok = False
                detector.transition('api', True, 'docker', 'api_unavailable')
                print('Inventory/storage check failed; retrying in 30 seconds.', flush=True)
            detector.last_scan = now
        logs_ok = detector.read_logs()
        persist(state)
        detector.deliver(time.time())
        if detector.scan_ok and logs_ok:
            Path('/state/heartbeat').touch()
        time.sleep(2)


if __name__ == '__main__':
    sys.exit(main())
