# Linux Server Deployment

This deployment keeps one tactic process running under `systemd`. The dashboard
continues to listen on `127.0.0.1`, and the API key is stored outside the Git
checkout.

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

## Update the deployment

```bash
cd /opt/arena-hero-conservative
sudo -u arena-hero git pull --ff-only
sudo -u arena-hero .venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart arena-hero.service
sudo systemctl status arena-hero.service --no-pager
```

## Common checks

```bash
sudo systemctl is-active arena-hero.service
sudo journalctl -u arena-hero.service -n 100 --no-pager
curl --fail http://127.0.0.1:8765/api/state
```

If the dashboard starts on `8766` or another fallback port, another local
process is already using `8765`. Stop the duplicate process instead of exposing
multiple tactic instances.
