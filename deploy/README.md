# Linux Server Deployment

This is the supported Debian/Ubuntu cloud deployment path. It keeps exactly one
tactic process running under `systemd`, stores the API key outside the Git
checkout, persists the exploration map and operator statistics, and keeps the
Dashboard bound to loopback. The current tactic grows the living fleet until
population 30, which gives the Core `max(10, 30 * 5) = 150` resource capacity.
One Vanguard and one Ranger stay near the Core; the remaining combat units
explore, fight visible enemies, pursue visible enemy Cores, and collect the
Beacon when safe.

## Server layout

| Path | Purpose |
|---|---|
| `/opt/arena-hero-conservative` | Git checkout and Python virtual environment |
| `/var/lib/arena-hero-conservative` | Persistent map and operator statistics |
| `/etc/arena-hero-conservative/arena-hero.env` | API key and runtime settings |
| `/etc/systemd/system/arena-hero.service` | Long-running service definition |

Do not run a second tactic process with the same Arena Hero account. All Agent
clients share one plan slot, so another process can replace this service's plan.

## 1. Install prerequisites

The server needs Git and Python 3.11 or newer. On Debian or Ubuntu:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv ca-certificates
python3 -c 'import sys; assert sys.version_info >= (3, 11), sys.version'
```

If the version check fails, install a newer Python from the server
distribution before continuing.

Optional connectivity checks:

```bash
getent hosts api.arenahero.io
curl --fail --silent --show-error --max-time 15 https://api.arenahero.io/health || true
```

Do not disable the host firewall to make the tactic work. Allow outbound HTTPS
and keep the Dashboard port private.

## 2. Clone and install

```bash
sudo useradd --system \
  --home-dir /opt/arena-hero-conservative \
  --shell /usr/sbin/nologin arena-hero

sudo git clone \
  https://github.com/Evander-8/Arena-Hero-Conservative.git \
  /opt/arena-hero-conservative

sudo chown -R arena-hero:arena-hero /opt/arena-hero-conservative

sudo -u arena-hero python3 -m venv \
  /opt/arena-hero-conservative/.venv

sudo -u arena-hero \
  /opt/arena-hero-conservative/.venv/bin/python -m pip install \
  -r /opt/arena-hero-conservative/requirements.txt

sudo -u arena-hero \
  /opt/arena-hero-conservative/.venv/bin/python -m pip check
sudo -u arena-hero \
  /opt/arena-hero-conservative/.venv/bin/python -m unittest discover \
  -s /opt/arena-hero-conservative/tests
```

## 3. Configure the API key

```bash
sudo install -d -m 0750 \
  -o root -g arena-hero /etc/arena-hero-conservative

sudo install -m 0640 \
  -o root -g arena-hero \
  /opt/arena-hero-conservative/deploy/arena-hero.env.example \
  /etc/arena-hero-conservative/arena-hero.env

sudoedit /etc/arena-hero-conservative/arena-hero.env
```

Replace only `replace-with-your-api-key`. Do not put the real key in the Git
repository or in a shell command that will remain in history.

## 4. Install and start the service

```bash
sudo install -m 0644 \
  /opt/arena-hero-conservative/deploy/arena-hero.service \
  /etc/systemd/system/arena-hero.service

sudo systemctl daemon-reload
sudo systemctl enable --now arena-hero.service
sudo systemctl status arena-hero.service --no-pager
```

Follow live logs with:

```bash
sudo journalctl -u arena-hero.service -f
```

The logs should show accepted Tick submissions and a dashboard URL on
`127.0.0.1` without printing the API key.

Verify the local API from the server:

```bash
curl --fail --silent http://127.0.0.1:8765/api/state | python3 -m json.tool | head -80
```

Look for `runtime.status` equal to `connected`, a growing `sequence`, and
`acceptedSubmissions` greater than zero. A second tactic process for the same
Arena Hero account can overwrite the Agent plan, so stop duplicate processes
before interpreting stale Dashboard data.

## 5. Open the dashboard securely

Keep TCP port `8765` closed to the public Internet. From the local computer,
create an SSH tunnel:

```bash
ssh -L 8765:127.0.0.1:8765 your-user@your-server
```

Then open `http://127.0.0.1:8765` locally.

### Optional Nginx access

For a domain, install Nginx and password protection:

```bash
sudo apt install -y nginx apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd-arena-hero your-dashboard-user
sudo cp \
  /opt/arena-hero-conservative/deploy/nginx-arena-hero.conf \
  /etc/nginx/sites-available/arena-hero
sudoedit /etc/nginx/sites-available/arena-hero
sudo ln -s /etc/nginx/sites-available/arena-hero \
  /etc/nginx/sites-enabled/arena-hero
sudo nginx -t
sudo systemctl reload nginx
```

Replace `arena.example.com` before enabling the configuration. Add HTTPS before
using the dashboard over an untrusted network.

For HTTPS with Certbot:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d arena.example.com
```

Keep Basic Auth enabled even after adding TLS.

## Update an existing deployment

Use this section when the project, virtual environment, API key file, and
`arena-hero.service` already exist on the cloud server. Do not run the initial
`useradd`, `git clone`, or `python3 -m venv` commands again.

The commands below assume the existing layout shown above. If the previous
deployment used another checkout path, replace `/opt/arena-hero-conservative`
consistently in every command.

### Offline update when the server cannot reach GitHub

Create the release archive on the development machine. Run this from the
repository root in PowerShell:

```powershell
$release = "arena-hero-release-$(Get-Date -Format yyyyMMdd-HHmmss).tar.gz"
tar --exclude=.git --exclude=.venv --exclude=__pycache__ `
    --exclude=.env --exclude='*.log' `
    --exclude='.arena-hero-dashboard-*.json*' `
    -czf $release .
```

Upload the archive to the existing server with any available file-transfer
method. `scp` is an example when SSH is reachable:

```bash
scp arena-hero-release-YYYYMMDD-HHMMSS.tar.gz \
  your-user@your-server:/tmp/
```

The archive contains source, tests, requirements, and deployment templates. It
does not contain `.git`, `.venv`, `.env`, API keys, logs, or persistent runtime
state.

On the server, first verify the archive and stop the old service:

```bash
release=/tmp/arena-hero-release-YYYYMMDD-HHMMSS.tar.gz
tar -tzf "$release" | head -40
sudo systemctl stop arena-hero.service
```

Back up the existing code and persistent state before replacing anything:

```bash
sudo install -d -m 0750 -o root -g arena-hero /var/backups
sudo tar -C /opt -czf \
  /var/backups/arena-hero-code-$(date +%Y%m%d-%H%M%S).tgz \
  arena-hero-conservative
sudo tar -C /var/lib -czf \
  /var/backups/arena-hero-state-$(date +%Y%m%d-%H%M%S).tgz \
  arena-hero-conservative
```

Replace only the application source. This preserves the existing `.venv`, the
server-side `.env` location, and `/var/lib/arena-hero-conservative`:

```bash
sudo mv /opt/arena-hero-conservative \
  /opt/arena-hero-conservative.previous
sudo install -d -m 0750 -o arena-hero -g arena-hero \
  /opt/arena-hero-conservative
sudo tar -xzf "$release" -C /opt/arena-hero-conservative
sudo chown -R arena-hero:arena-hero /opt/arena-hero-conservative
```

Restore the existing virtual environment from the previous directory, then
remove the temporary old checkout after verification:

```bash
sudo mv /opt/arena-hero-conservative.previous/.venv \
  /opt/arena-hero-conservative/.venv
sudo chown -R arena-hero:arena-hero /opt/arena-hero-conservative/.venv
```

Install any changed dependencies and run offline checks. These commands do not
need GitHub; they use the configured Python package index or an existing local
package cache:

```bash
sudo -u arena-hero .venv/bin/python -m pip install -r requirements.txt
sudo -u arena-hero .venv/bin/python -m pip check
sudo -u arena-hero .venv/bin/python -m unittest discover -s tests
sudo -u arena-hero .venv/bin/python -m py_compile tactic.py dashboard.py
```

If dependency installation also cannot reach a package index, do not delete the
old `.venv`; copy the already-installed `.venv` back and confirm that the new
`requirements.txt` is compatible before starting.

The service reads `ARENA_HERO_API_KEY` from the systemd `EnvironmentFile`.
`python-dotenv` is only a convenience for local `.env` runs; the tactic does not
require it when started by systemd. If an older server environment is missing
that optional package, the service can still start normally.

Reinstall the service template only if it changed, then start the new version:

```bash
sudo install -m 0644 \
  /opt/arena-hero-conservative/deploy/arena-hero.service \
  /etc/systemd/system/arena-hero.service
sudo systemctl daemon-reload
sudo systemctl start arena-hero.service
sudo systemctl status arena-hero.service --no-pager
sudo journalctl -u arena-hero.service -n 80 --no-pager
curl --fail --silent http://127.0.0.1:8765/api/state | python3 -m json.tool | head -80
```

Only after the new service is connected and submitting Ticks should you remove
the previous checkout:

```bash
sudo rm -rf /opt/arena-hero-conservative.previous
```

The old checkout is recoverable from the code backup until that cleanup command
is run. Never remove `/var/lib/arena-hero-conservative`, because it contains the
exploration map and operator statistics.

The archive update is the preferred method when outbound GitHub access is
blocked. It does not require changing the server firewall or proxy.

### 1. Check the current service and revision

```bash
cd /opt/arena-hero-conservative
sudo systemctl status arena-hero.service --no-pager
sudo -u arena-hero git rev-parse --short HEAD
sudo -u arena-hero git status --short
```

The working tree should be clean before pulling. Do not overwrite local changes
on the server without reviewing them first.

### 2. Stop the old tactic cleanly

```bash
sudo systemctl stop arena-hero.service
sudo systemctl is-active arena-hero.service || true
```

Stopping the service prevents two tactic processes from sharing the same Agent
slot during the update. It does not reset the Arena Hero world or delete the
persistent map/statistics.

### 3. Back up persistent state and record the old revision

```bash
cd /opt/arena-hero-conservative
sudo -u arena-hero git rev-parse --short HEAD
sudo install -d -m 0750 -o root -g arena-hero /var/backups
sudo tar -C /var/lib -czf \
  /var/backups/arena-hero-state-$(date +%Y%m%d-%H%M%S).tgz \
  arena-hero-conservative
```

### 4. Pull the new version and synchronize dependencies

```bash
sudo -u arena-hero git pull --ff-only
sudo -u arena-hero .venv/bin/python -m pip install -r requirements.txt
sudo -u arena-hero .venv/bin/python -m pip check
```

The existing `.venv` is reused. `pip install -r` only changes packages required
by the new version; it does not recreate the environment.

### 5. Test before starting

```bash
sudo -u arena-hero .venv/bin/python -m unittest discover -s tests
sudo -u arena-hero .venv/bin/python -m py_compile tactic.py dashboard.py
```

### 6. Start the updated service and verify it

```bash
sudo systemctl restart arena-hero.service
sudo systemctl status arena-hero.service --no-pager
sudo journalctl -u arena-hero.service -n 80 --no-pager
curl --fail --silent http://127.0.0.1:8765/api/state | python3 -m json.tool | head -80
```

Check that `runtime.status` is `connected`, `acceptedSubmissions` increases,
and there is only one process:

```bash
sudo systemctl is-active arena-hero.service
pgrep -af 'tactic.py'
```

The service itself starts both the tactic and the local Dashboard. Do not run
`python tactic.py` manually while the systemd service is active.

### Roll back if verification fails

If the new revision is bad, roll back code without deleting state:

```bash
sudo -u arena-hero git log --oneline -5
sudo -u arena-hero git reset --hard <known-good-commit>
sudo -u arena-hero .venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart arena-hero.service
```

After rollback, repeat the test and verification commands above. Only reset to
an explicitly chosen known-good commit. Never reset
`/var/lib/arena-hero-conservative`.

## Common checks

```bash
sudo systemctl is-active arena-hero.service
sudo journalctl -u arena-hero.service -n 100 --no-pager
curl --fail http://127.0.0.1:8765/api/state
```

If port `8765` is occupied, the service fails instead of selecting a fallback
port. Inspect and stop the duplicate process, or change
`ARENA_HERO_DASHBOARD_PORT` in the systemd environment file and update the SSH
tunnel/Nginx upstream consistently.

If `runtime.status` is `disconnected`, check DNS, outbound HTTPS, proxy
environment variables, API key file permissions, and the service journal. Do
not print the API key while debugging. If a proxy is configured, keep the
`python-socks` dependency installed.

If the Dashboard page loads but live updates stop behind Nginx, confirm the
supplied Nginx configuration has buffering disabled and a long read timeout for
the `/api/events` stream.
