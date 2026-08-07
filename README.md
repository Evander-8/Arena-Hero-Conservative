# Arena Hero Conservative

这是一个使用官方 `arena-hero` Python SDK 的资源优先型策略。它每个 Turn
读取当前完整状态并提交一份完整计划：Worker 优先寻找当前可见资源并回 Core，
Core 自动在经济与防御之间生产单位；Vanguard/Ranger 驻守 Core 周边，只在自卫
或 Core 警戒时开火。没有可见资源时，Worker 会按稳定顺序做有限探路，不依赖
过期的雾区记忆。

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

- 正常生产顺序：`4 Worker → 1 Vanguard → 6 Worker → 1 Ranger → 8 Worker → 2 Ranger`；
- 开局无敌情时允许用初始 5 资源直接购买第 2 个 Worker，不再强制保留 5 资源；
- 单位若已在本 Tick 离开 Core 格，不再被误判为阻塞生产；新生 Vanguard/Ranger
  会主动前往守卫位，不会长期堵住生产格；
- 收到 `CORE_DAMAGED` 或发现可在短期内攻击 Core 的敌人后进入 8 Tick 警戒，
  优先 Core 回血、补盾、补齐 Vanguard/Ranger，并把战斗单位收缩到 Core 周边；
- Worker 遇敌优先远离威胁，Core 警戒时同时远离 Core，避免把敌人带回基地；
- 前期不主动拾取 Champion Beacon。只有经济、防卫、Core 状态和资源储备都达到
  安全阈值且当前无可见敌人时，才会机会性拾取。

## 安装

需要 Python 3.11 或更高版本：

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

如果本机没有 `uv`，也可以先创建 Python 虚拟环境，再在激活后运行
`python -m pip install -r requirements.txt`。不要直接向 macOS 或 uv 管理的
系统 Python 安装依赖；这会触发 PEP 668 的 `externally-managed-environment`。

如果运行时出现 `connecting through a SOCKS proxy requires python-socks`，重新执行
`uv pip install -r requirements.txt` 即可安装 SOCKS 支持。如果当前网络不需要代理，
也可以在本次运行前临时清除代理变量：

```bash
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u http_proxy -u https_proxy python tactic.py
```

## 运行

通过环境变量提供 API key（不会写入代码或日志）：

```bash
ARENA_HERO_API_KEY='your-api-key' python tactic.py
```

也可以直接运行后按提示输入 key：

```bash
python tactic.py
```

策略默认连接生产 API，使用 SDK 自带的 WebSocket 重连和安全重试。它会将人口
目标控制在 11：最多 8 个 Worker、1 个 Vanguard 和 2 个 Ranger。这是面向长期
资源积累与低冲突生存的确定性策略；共享永久世界中的敌方行为不可预测，因此不把
它描述为绝对最优。

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

端口被占用时会依次尝试后续 9 个端口。也可以指定端口或关闭面板：

```bash
ARENA_HERO_DASHBOARD_PORT=9000 python tactic.py
ARENA_HERO_DASHBOARD=0 python tactic.py
```

## 检查

离线策略测试：

```bash
python -m unittest discover -s tests -v
```

语法检查：

```bash
python -m compileall -q .
```
