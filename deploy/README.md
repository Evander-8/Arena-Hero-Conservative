# Linux 云服务器部署

项目在 Debian/Ubuntu 上通过 `systemd` 长期运行。第一次使用本项目时，请完整执行
“首次部署”；只有服务器已经存在项目、`.venv`、配置和依赖时，才执行“更新已有服务器”。

Dashboard 默认只监听 `127.0.0.1:8765`。API Key 在服务启动后从页面提交，不写入代码、
压缩包或环境文件。

## 服务器目录

| 路径 | 用途 |
|---|---|
| `/opt/arena-hero-conservative` | 项目代码和 `.venv` |
| `/etc/arena-hero-conservative/arena-hero.env` | Dashboard 与状态目录配置 |
| `/var/lib/arena-hero-conservative` | 探索地图和统计数据 |
| `/etc/systemd/system/arena-hero.service` | systemd 服务 |

同一 Arena Hero 账号只能运行一个策略进程，否则多个进程会互相覆盖计划。

## 首次部署

本节面向从未部署过本项目的新服务器。

### 1. 在本地获取代码并打包

在本地 PowerShell 中执行：

```powershell
git clone https://github.com/Evander-8/Arena-Hero-Conservative.git
cd Arena-Hero-Conservative

$release = "arena-hero-release-$(Get-Date -Format yyyyMMdd-HHmmss).tar.gz"
tar --exclude=.git --exclude=.venv --exclude=__pycache__ `
    --exclude=.env --exclude='*.log' `
    --exclude='.arena-hero-dashboard-*.json*' `
    --exclude='arena-hero-release-*.tar.gz' `
    -czf $release .

scp $release "root@服务器IP:/tmp/"
```

压缩包包含代码、测试、依赖声明和部署模板，不包含 `.git`、`.venv`、API Key、日志或
运行状态。

### 2. 安装系统依赖并解压项目

登录服务器后执行：

```bash
apt update
apt install -y python3 python3-venv ca-certificates

useradd --system \
  --home-dir /opt/arena-hero-conservative \
  --shell /usr/sbin/nologin arena-hero

install -d -m 0750 -o arena-hero -g arena-hero \
  /opt/arena-hero-conservative

release=$(ls -1t /tmp/arena-hero-release-*.tar.gz | head -n 1)
test -n "$release" && test -f "$release"
tar -xzf "$release" -C /opt/arena-hero-conservative
chown -R arena-hero:arena-hero /opt/arena-hero-conservative
```

### 3. 创建虚拟环境并安装 Python 依赖

```bash
sudo -u arena-hero python3 -m venv \
  /opt/arena-hero-conservative/.venv

sudo -u arena-hero \
  /opt/arena-hero-conservative/.venv/bin/python -m pip install \
  -r /opt/arena-hero-conservative/requirements.txt

cd /opt/arena-hero-conservative
sudo -u arena-hero .venv/bin/python -m pip check
sudo -u arena-hero .venv/bin/python -m py_compile tactic.py dashboard.py
```

### 4. 安装运行配置和 systemd 服务

```bash
install -d -m 0750 \
  -o root -g arena-hero /etc/arena-hero-conservative

install -m 0640 \
  -o root -g arena-hero \
  /opt/arena-hero-conservative/deploy/arena-hero.env.example \
  /etc/arena-hero-conservative/arena-hero.env

install -m 0644 \
  /opt/arena-hero-conservative/deploy/arena-hero.service \
  /etc/systemd/system/arena-hero.service

systemctl daemon-reload
systemctl enable --now arena-hero.service
systemctl status arena-hero.service --no-pager
```

显示 `active (running)` 即表示服务启动成功。首次启动时 Dashboard 状态为
`awaiting-key`，这是正常状态。

### 5. 打开 Dashboard 并提交 Key

不要把服务器的 `8765` 端口直接暴露到公网。在本地电脑建立 SSH 隧道：

```bash
ssh -L 18765:127.0.0.1:8765 root@服务器IP
```

在本地打开 `http://127.0.0.1:18765`，填写 API Key。状态变为 `connected` 后程序开始
发送计划。关闭网页不会停止程序；只有服务重新启动后才需要再次填写 Key。

## 更新已有服务器

本节只适用于已经完成首次部署，并且以下文件都存在的服务器：

```text
/opt/arena-hero-conservative/.venv/bin/python
/etc/arena-hero-conservative/arena-hero.env
/etc/systemd/system/arena-hero.service
```

### 1. 本地重新打包并上传

进入本地项目根目录，执行与首次部署相同的打包命令：

```powershell
$release = "arena-hero-release-$(Get-Date -Format yyyyMMdd-HHmmss).tar.gz"
tar --exclude=.git --exclude=.venv --exclude=__pycache__ `
    --exclude=.env --exclude='*.log' `
    --exclude='.arena-hero-dashboard-*.json*' `
    --exclude='arena-hero-release-*.tar.gz' `
    -czf $release .

scp $release "root@服务器IP:/tmp/"
```

### 2. 服务器覆盖代码并启动

```bash
set -e

release=$(ls -1t /tmp/arena-hero-release-*.tar.gz | head -n 1)
test -n "$release" && test -f "$release"
test -x /opt/arena-hero-conservative/.venv/bin/python
tar -tzf "$release" >/dev/null

systemctl stop arena-hero.service
tar -xzf "$release" -C /opt/arena-hero-conservative
chown -R arena-hero:arena-hero /opt/arena-hero-conservative

cd /opt/arena-hero-conservative
sudo -u arena-hero .venv/bin/python -m py_compile tactic.py dashboard.py
sudo -u arena-hero .venv/bin/python -m unittest discover -s tests
sudo -u arena-hero .venv/bin/python -m pip check

install -m 0644 deploy/arena-hero.service \
  /etc/systemd/system/arena-hero.service
systemctl daemon-reload
systemctl reset-failed arena-hero.service
systemctl restart arena-hero.service
systemctl status arena-hero.service --no-pager
```

更新只覆盖项目代码，不删除已有 `.venv`、
`/etc/arena-hero-conservative/arena-hero.env` 或
`/var/lib/arena-hero-conservative`。`set -e` 会在压缩包校验、语法检查、测试或依赖检查
失败时立即停止，不会用已知有问题的代码启动服务。服务重新启动后，需要在 Dashboard
再提交一次 Key。

## 使用域名访问 Dashboard

如果使用域名反向代理，应启用 HTTPS 和访问认证。仓库中的
`deploy/nginx-arena-hero.conf` 可作为 Nginx 配置模板。不要通过公网 HTTP 明文提交
API Key。

## 检查与排错

```bash
systemctl is-active arena-hero.service
journalctl -u arena-hero.service -n 100 --no-pager
curl --fail http://127.0.0.1:8765/api/state
```

- `runtime.status=awaiting-key`：打开 Dashboard 提交 Key。
- `runtime.status=connected`：程序已连接，确认 `acceptedSubmissions` 持续增加。
- `can't open file ... tactic.py`：代码没有完整解压，重新上传压缩包并覆盖解压。
- `ModuleNotFoundError`：先 `cd /opt/arena-hero-conservative`，再使用 `.venv/bin/python`
  执行检查。
- 端口 `8765` 被占用：停止重复的 `tactic.py` 进程，不要同时运行手动进程和 systemd
  服务。

服务器必须允许程序访问 `api.arenahero.io:443`。服务器无法访问 GitHub 不影响离线部署
和更新，但首次安装 Python 依赖时仍需访问可用的 Python 软件源。
