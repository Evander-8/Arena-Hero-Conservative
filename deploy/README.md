# Linux Server Deployment

This is the supported Debian/Ubuntu cloud deployment path. It keeps exactly one
tactic process running under `systemd`, accepts the API key through the local
Dashboard after startup, persists the exploration map and operator statistics, and keeps the
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
| `/etc/arena-hero-conservative/arena-hero.env` | Dashboard and runtime settings |
| `/etc/systemd/system/arena-hero.service` | Long-running service definition |

Do not run a second tactic process with the same Arena Hero account. All Agent
clients share one plan slot, so another process can replace this service's plan.

## 部署路径选择（先看）

下文的“项目根目录”统一指 `/opt/arena-hero-conservative`，不是 `/root`，也不是
登录后提示符所在的 `~`。只选择一种流程：

- 全新服务器、没有旧项目：按第 1 到第 5 节执行。
- 已有项目、服务器无法连接 GitHub：执行 **Offline update**。
- 已有 Git 仓库、服务器可以连接 GitHub：执行 **Online Git update**。

不要在已有部署上重复执行 `useradd`、`git clone` 或创建 `.venv`。无论选择哪种
流程，Python/systemd 进程每次重启都会清除内存中的 Key；服务启动后要重新打开
Dashboard 提交一次 Key。

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

cd /opt/arena-hero-conservative
sudo -u arena-hero .venv/bin/python -m pip check
sudo -u arena-hero .venv/bin/python -m unittest discover -s tests
```

## 3. Configure runtime settings

```bash
sudo install -d -m 0750 \
  -o root -g arena-hero /etc/arena-hero-conservative

sudo install -m 0640 \
  -o root -g arena-hero \
  /opt/arena-hero-conservative/deploy/arena-hero.env.example \
  /etc/arena-hero-conservative/arena-hero.env

sudoedit /etc/arena-hero-conservative/arena-hero.env
```

Keep `ARENA_HERO_DASHBOARD=1`. Do not put the API Key in this file or in the Git
repository. Submit it through the Dashboard after the service starts; it remains
in the running Python process memory only.

## 4. Install and start the service

```bash
sudo install -m 0644 \
  /opt/arena-hero-conservative/deploy/arena-hero.service \
  /etc/systemd/system/arena-hero.service

sudo systemctl daemon-reload
sudo systemctl enable --now arena-hero.service
sudo systemctl status arena-hero.service --no-pager
```

At this point the service is expected to stay active with Dashboard status
`awaiting-key`. Starting the service does not connect to Arena Hero by itself.

Follow live logs with:

```bash
sudo journalctl -u arena-hero.service -f
```

The logs should show a dashboard URL on `127.0.0.1` and a process waiting for a
page Key. Open the Dashboard, enter the API Key, and submit it once. Accepted
Tick submissions should then appear in the logs.

Verify the local API from the server:

```bash
curl --fail --silent http://127.0.0.1:8765/api/state | python3 -m json.tool | head -80
```

Before submitting the Key, `runtime.status` is `awaiting-key`. After submission,
look for `runtime.status` equal to `connected`, a growing `sequence`, and
`acceptedSubmissions` greater than zero. Closing or refreshing the browser does
not stop the strategy while the service process remains alive, so the Key is not
requested again. A second tactic process for the same
Arena Hero account can overwrite the Agent plan, so stop duplicate processes
before interpreting stale Dashboard data.

## 5. Open the dashboard securely

Keep TCP port `8765` closed to the public Internet. From the local computer,
create an SSH tunnel:

```bash
ssh -L 18765:127.0.0.1:8765 your-user@your-server
```

Then open `http://127.0.0.1:18765` locally. Using local port `18765` avoids a
collision when a local development instance already uses `8765`.

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

Replace `arena.example.com` before enabling the configuration. The `/api/key`
request contains the API Key, so do not submit it through a public HTTP-only
site. Basic Auth is access control, not encryption; add HTTPS first.

For HTTPS with Certbot:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d arena.example.com
```

Keep Basic Auth enabled even after adding TLS.

## 更新现有服务器

服务器已经有项目、`.venv`、配置和依赖时，不需要重新部署环境。在服务器只执行这一
条命令：

```bash
curl -fsSL \
  https://raw.githubusercontent.com/Evander-8/Arena-Hero-Conservative/main/deploy/update-server.sh \
  | sudo bash
```

脚本会直接从
`https://github.com/Evander-8/Arena-Hero-Conservative.git` 获取最新代码，然后启动
`arena-hero.service`。它不会删除已有 `.venv`、
`/etc/arena-hero-conservative/arena-hero.env` 或
`/var/lib/arena-hero-conservative`。如果更新失败，脚本会重新启动原服务。

服务重启后 Dashboard 显示 `awaiting-key` 是正常状态。重新在页面提交一次 Key 后，程序
就会继续发送计划；关闭网页不会停止已经运行的程序。

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

If `runtime.status` is `awaiting-key`, open the Dashboard and submit the Key. If
it is `error`, check DNS, outbound HTTPS, proxy environment variables, SDK
compatibility, and the service journal. Do not print the API Key while
debugging. If a proxy is configured, keep the `python-socks` dependency
installed.

If the Dashboard page loads but live updates stop behind Nginx, confirm the
supplied Nginx configuration has buffering disabled and a long read timeout for
the `/api/events` stream.
