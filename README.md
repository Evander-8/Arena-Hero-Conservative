# Arena Hero Conservative

这是一个使用官方 `arena-hero` Python SDK 的资源优先型策略。它每个 Turn
读取当前完整状态并提交一份完整计划：Worker 优先寻找当前可见资源并回 Core，
Core 自动在经济与防御之间生产单位；保留一组 Vanguard/Ranger 守卫 Core，其余
战斗单位沿外环远征、搜索可见敌人和敌方 Core。没有可见资源时，Worker 会按不同
距离和方位分散探路，不依赖过期的雾区记忆。

## 参考策略

资源调度参考了 `VelvetEvening/ArenaHero-nearly-perfect-guide`，并按当前规则重新实现：

- 记住视野外的历史资源，但重新进入真实视野确认为空时立即删除；
- 每个 Turn 按距离重新分配互不重复的资源目标，新发现的近资源可替换旧的远目标；
- 没有资源任务时，按 `12 → 19 → 26 → 32 → 26 → 19` 方环错位搜索；
- 使用已确认的永久障碍和有限 A* 规划下一步；
- Core 满仓时优先腾空生成格。

原仓库的 `4 Worker + 2 Vanguard/1 Ranger` 进攻小队、敌方 Core 远征、SDK
`0.2.8` 和“19 人以下避免维护费”逻辑没有采用。当前 `v0.14` 规则没有人口维护费，
本项目继续使用 SDK `0.2.9` 的动态 `unit_cost()`，并保持资源优先、尽量避战。

另外参考了 Linux DO 的两篇实战复盘：

- [Arena Hero 鼠鼠玩家游玩分享](https://linux.do/t/topic/2692947)：采用资源点与
  Worker 一对一最近匹配、侦察目标持续追踪、避免无任务 Worker 立即原路折返；
- [Arena Hero 游戏有点上头，分享下游玩体会](https://linux.do/t/topic/2690941)：
  采用先补经济再建防卫、战斗单位发现资源后交给 Worker、Core 受击后召回守卫。

帖子中“20 人口后每 Tick 扣资源，资源不足扣 Core 血量”的旧版本经验不适用于
当前 `v0.14`。现规则没有维护费；第 21 个单位开始只会提高之后的购买价格。

## 当前生产与生存策略

- 正常状态下先补 4 Worker 和基础 Vanguard/Ranger 防卫；此后 Worker 少于当前人口
  一半时优先补 Worker，最多生产到 14 个，避免战斗单位增长挤压经济；
- 开局无敌情时允许用初始 5 资源直接购买第 2 个 Worker，不再强制保留 5 资源；
- 单位若已在本 Tick 离开 Core 格，不再被误判为阻塞生产；新生 Vanguard/Ranger
  会主动前往守卫位，不会长期堵住生产格；
- 收到 `CORE_DAMAGED` 或发现可在短期内攻击 Core 的敌人后进入 8 Tick 警戒，
  优先 Core 回血、补盾、补齐 Vanguard/Ranger，并把战斗单位收缩到 Core 周边；
- Worker 遇敌优先远离威胁，Core 警戒时同时远离 Core，避免把敌人带回基地；
- 前期不主动拾取 Champion Beacon。只有经济、防卫、Core 状态和资源储备都达到
  安全阈值且当前无可见敌人时，才会机会性拾取。

## 本地部署

### 1. 安装

需要 Python 3.11 或更高版本：

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

如果本机没有 `uv`，也可以先创建 Python 虚拟环境，再在激活后运行
`python -m pip install -r requirements.txt`。不要直接向 macOS 或 uv 管理的
系统 Python 安装依赖；这会触发 PEP 668 的 `externally-managed-environment`。

Windows PowerShell 首次部署：

```powershell
py -3 -c "import sys; assert sys.version_info >= (3, 11), sys.version"
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

后续测试和启动也建议使用 `.venv\Scripts\python.exe`，避免调用到系统中
其他版本的 Python。

如果运行时出现 `connecting through a SOCKS proxy requires python-socks`，重新执行
`uv pip install -r requirements.txt` 即可安装 SOCKS 支持。如果当前网络不需要代理，
也可以在本次运行前临时清除代理变量：

```bash
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u http_proxy -u https_proxy python tactic.py
```

### 2. 启动

启动时不需要在终端或 `.env` 中填写 API Key，直接运行策略：

```bash
.venv/bin/python tactic.py
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe tactic.py
```

启动后打开 Dashboard，在页面的 API Key 输入框提交 Key，提交成功后才会连接
Arena Hero 并发送计划。Key 只保存在当前 Python 进程内，不写入 `.env`、日志或
Dashboard 状态接口。关闭或刷新浏览器页面不会停止已经启动的策略；只有停止或
重启 Python/systemd 服务后才需要再次打开页面填写 Key。

Windows 建议以前台方式运行。保持 PowerShell 窗口开启：

```powershell
cd F:\APP\Arena-Hero-Conservative
.\.venv\Scripts\python.exe tactic.py
```

### 3. 停止与重启（Windows）

前台运行时优先在原 PowerShell 窗口按 `Ctrl+C`。如果窗口已经丢失或曾重复启动，
请使用管理员 PowerShell 清理所有监听 `8765` 的进程树。不要使用 `$pid` 变量名，
因为 `$PID` 是 PowerShell 内置只读变量：

```powershell
$ownerPids = @(
  Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
)
$ownerPids | ForEach-Object { taskkill /PID $_ /T /F }

Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
```

最后一条命令没有输出才表示端口已经完全释放。然后重新执行启动命令；进程重启后
内存中的 Key 会被清除，需要在 Dashboard 页面重新提交。

策略默认连接生产 API，使用 SDK 自带的 WebSocket 重连和安全重试。当前生产目标是
人口 30，即 Core 资源容量达到 `30 × 5 = 150` 后停止购买；人口未满时会在 Worker、
Vanguard、Ranger 之间按防守缺口、经济需求和远征编队动态分配。每个编队保留 1 个
Vanguard 和 1 个 Ranger 在 Core 附近巡逻，其余战斗单位远程探索、接敌、攻击可见敌方
Core，并在低血量或威胁过多时撤回。共享永久世界中的敌方行为不可预测，因此不把它
描述为绝对最优。

同一个 Arena Hero 账号只能运行一个战术进程。Agent 计划按账号共享，第二个进程
会覆盖第一个进程的计划；本地测试前应先停止云服务器上的 `arena-hero.service`，
测试结束后再恢复云端服务。

战斗单位没有脱战自动回血。受伤的 Vanguard/Ranger 会先分配一个单位进入 Core
治疗位，其他伤员在 Core 视野内的独立候诊格等待；Core 每次最多容纳 Core 加一个
单位。治疗需要 Core 资源，资源不足时伤员会主动腾出 Core，让 Worker 能够存入资源。
满血守卫不会因为候诊逻辑长期占位；如果 Dashboard 中同一单位连续多个 Tick 都是
`action: null`，应优先检查障碍、友军拥堵和是否启动了重复进程。

## 实时面板

运行策略后，终端会输出本地面板地址，默认是：

```text
http://127.0.0.1:8765
```

面板实时显示策略连接状态、Tick、提交次数、资源/人口、Core 状态和 Worker
采集统计。地图会将探索、障碍和资源记忆保存到不会提交 Git 的
`.arena-hero-dashboard-map.json`，脚本重启后继续累积，并区分当前视野、障碍、
已知资源、己方单位、可见敌方和 Worker 的资源/探索目标；可见资源使用实心晶体，雾区资源记忆
使用空心晶体，并提供可定位的资源坐标列表。地图坐标方向与官方界面一致：X 向右、
Y 向下；支持拖拽、缩放、复位和单位/资源定位。

右侧栏常驻显示官方面板对应的 15 项“操作员统计”指标。策略会先尝试用当前 API Key
读取 Arena Hero 的 `/api/v1/me/stats` 完整生涯数据；如果服务端只接受网页登录
Cookie，则自动改为根据私有结算事件在本地持续累计。降级数据会明确标注“本地持续
累计”，并保存到不会提交 Git 的 `.arena-hero-dashboard-stats.json`。统计和地图状态
都不包含 API Key。地图和右侧编队共用四套不同的 2.5D 机体模型：Core 是移动
指挥堡垒，Worker 是采集车，先锋是盾装重机体，游侠是长枪侦察机体。可见敌方会
按其单位类型显示对应的锈红敌对型号，并带敌对三角标、红色名称条和 HP 状态；未知
敌方类型显示为通用掠夺者机体。
服务只监听本机地址，状态数据不包含 API key。

Arena Hero 官网过去的探索历史保存在官网域名自己的 IndexedDB 中，本地 Python
进程无法自动读取。首次升级到此版本时，本地面板不能补回升级前只存在于官网的历史；
从本版本开始，策略实际观察到的探索范围会在重启后持续保留。

Dashboard 使用固定端口，默认始终为 `8765`。端口被占用时程序会明确报错并
终止启动，不会自动切换到其他端口。也可以显式指定另一个固定端口：

```bash
ARENA_HERO_DASHBOARD_PORT=9000 python tactic.py
```

页面 Key 模式要求 Dashboard 保持启用；设置 `ARENA_HERO_DASHBOARD=0` 会直接退出。

本地面板也可以直接检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/state
```

确认返回的 `runtime.status` 为 `connected`，并且 `runtime.acceptedSubmissions`
持续增加。端口被占用时不要再启动第二个 `tactic.py`，先查找并停止旧进程。

## 服务器部署

第一次使用本项目时，必须先完成服务器初始化，不能直接执行“更新代码”的命令。首次
部署包含以下步骤：

1. 在本地从 GitHub 获取代码，生成离线压缩包并上传服务器。
2. 在服务器安装 Python，创建 `arena-hero` 运行用户和项目目录。
3. 解压代码，创建 `.venv` 并安装 `requirements.txt`。
4. 安装环境配置和 `arena-hero.service`，然后启动服务。
5. 通过 SSH 隧道打开 Dashboard 并提交 API Key。

服务器已经完成首次部署后，后续更新才可以只覆盖代码并重启服务。更新时会保留
`.venv`、`/etc/arena-hero-conservative/arena-hero.env` 和
`/var/lib/arena-hero-conservative`。

完整的首次部署命令、更新命令、Dashboard 访问和排错说明见
[`deploy/README.md`](deploy/README.md)。Dashboard 应监听 `127.0.0.1`，不要直接把
端口 `8765` 暴露到公网。

## 检查

离线策略测试：

```bash
python -m unittest discover -s tests -v
```

语法检查：

```bash
python -m compileall -q .
```

依赖检查：

```bash
python -m pip check
```
