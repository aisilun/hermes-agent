# ASL Hermes 定制分支

本仓库的 `aisilun/hermes-agent` 分支只承载 ASL 生产所需、尚未被 Hermes 官方正式版等价覆盖的最小 host capability（宿主能力）。机器可读的唯一候选合同是 [`governance/asl-fork-release.json`](governance/asl-fork-release.json)。

## 当前状态

- package version（候选包版本）：`0.19.1+asl.1`
- planned release tag（计划发布标签）：`v0.19.1-asl.1`（未创建、未授权）
- production branch（生产分支）：`asl/production`
- source status（源码状态）：`candidate`（候选）
- 当前 official source version（正式源码版本）：`0.19.0+asl.3`
- 当前正式 Tag：`v0.19.0-asl.3`
- review gate（审核门）：`trusted-xiaomu-single-review`，状态 `pending`（待审）
- merge（合并）：未授权
- Tag／Release：未授权
- production activation（生产激活）：未授权
- Fleet apply（全量应用）：未授权

本候选只允许完成隔离源码重建、作者自检、push（推送）、PR、exact-head CI（精确头提交持续集成）并停在稳定 PR 等待小沐单审。它不允许合并、创建 Tag/Release、安装到 `default` profile（默认配置档案）、重启 Gateway（网关）、恢复 Cron、执行数据库迁移或 Fleet apply。

## 来源绑定

| 层 | 固定值 | 含义 |
|---|---|---|
| 上游正式 Tag | `v2026.7.30` | Hermes 官方 v0.19.1 的不可变来源 |
| 上游正式 commit（提交） | `cc4cab2f592e60a197e796506de9168f74baf3ea` | 本候选的官方基线 |
| 上一 ASL 正式 commit | `d01f138cf889ed95e7b7ff3785b89db55c52e828` | `0.19.0+asl.3`回滚与差分基线 |
| turn-gate historical base（历史基点） | `0bd82a8a84595720ea1f14b103aeb81ca3cc50ef` | #74529 与现 ASL 补丁谱系的共同基点 |
| turn-gate source head（来源头） | `0e1031a9ff05d0c0d2f44f2148b80a33ca9d3561` | 宿主回合闸门来源 |
| privileged delegation source（特权委托来源） | `d83815b361dd88e5e126fcece06ab6fe15290027` | 原 PR #8 的安全补丁来源 |
| 上游贡献线 | `NousResearch/hermes-agent#74529` | 长期官方贡献支线，不阻断 ASL 自控主线 |
| 最新 upstream/main 观察值 | `e444d165807f489b5c1ab8e4a612c8d09c2e67a2` | 2026-08-01 只读观察；未纳入，不追逐移动目标 |

候选不是在 live root（现运行源码树）上执行 `hermes update`，也不是把 ASL `.3`整树覆盖到官方 v0.19.1。构建方式是以官方 `cc4cab2…`文件树为基线，使用共同基点完成三方语义重放，再逐项验证官方已覆盖、部分覆盖和未覆盖的能力。

## 最小补丁队列

| 队列项 | 来源 | 官方 v0.19.1 覆盖 | 候选决策 |
|---|---|---|---|
| host turn gate（宿主回合闸门） | `0e1031a…` | 未覆盖 | 保留 |
| fork source/update channel（分支仓源码／更新通道） | `4f6b6fad…`、`0c5b671…` | 不适用 | 保留 `aisilun/hermes-agent` + `asl/production`固定通道 |
| runtime authorization safeguards（运行时授权防护） | `7ad87b9c…` | 部分覆盖 | 语义重放 Codex 上限、Kanban 粘性阻塞、飞书安全与压缩回归 |
| ASL production CI（生产分支持续集成） | `16f97e2d…` | 不适用 | 保留 `asl/production` push／手工触发与 PR base 感知 |
| macOS no-start install（禁止自动启动安装） | `9e152bcd…` | 部分覆盖 | 保留 no-start 与 runtime marker ignore（运行时标记忽略规则）；不重新引入 marker 文件 |
| Kanban privileged delegation（特权配置档案委托防护） | `d83815b…` | 未覆盖 | 保留 create／assign／claim／dispatcher／spawn 全链 fail-closed（失败关闭） |

## 官方基线直接继承的能力

- LSP idle reaper（语言服务器空闲回收器）直接继承官方 v0.19.1；`agent/lsp/manager.py`必须保持官方 blob SHA `7ba1b914f74c3728b97650ade147fa38d4c2bc53`，不再维护重复的 ASL LSP 补丁。
- `.lazy-refresh-incomplete`在官方基线中已不再作为跟踪文件；候选继续保留 ASL 的 `.gitignore`规则，且不得重新引入该 marker（标记文件）。
- 官方 v0.19.1 的 Cron／Gateway 生命周期、更新器、Windows 路径、依赖锁与安全修复均以官方树为准；ASL 只在机器合同列出的路径上叠加必要差异。

## 保留的 ASL 不变量

1. **Host turn gate（宿主回合闸门）**：由 `config.yaml`绑定 required provider（必需提供者），在 Gateway 主／后台入口持有 lease（租约），工具前和输出前重新校验，reload（热重载）后继续 fail-closed。
2. **Codex context cap（上下文上限）**：活动 provider/base URL（提供方／基础地址）优先于通用 endpoint 探测，避免本地 broker（代理）错误使用 1.05M 上限。
3. **Kanban sticky block（看板粘性阻塞）**：创建即 blocked（阻塞）的任务记录阻塞事件，未经显式 unblock/requeue（解除阻塞／重新入队）不得被自动晋级。
4. **Feishu approval safety（飞书审批安全）**：中文风险摘要、smart-deny（智能拒绝）仅本次覆盖，并在同步渲染成功卡片前严格检查 operator allowlist（操作人允许名单）。
5. **Conversation compression recovery（会话压缩恢复）**：压缩失败不丢历史。
6. **macOS no-start（禁止自动启动）**：`--no-start-now --no-start-on-login`完整透传，`RunAtLoad=false`、`KeepAlive=false`，安装时不 bootstrap（加载）服务。
7. **Privileged delegation containment（特权委托遏制）**：`default`是 code-owned privileged profile（代码持有的特权配置档案）；非可信、空来源、旧行、重派回流和 `kanban.default_assignee=default`均在凭据读取和副作用前拒绝。

特权委托补丁不是完整 zero-trust broker（零信任授权代理）。同一 OS 用户直接修改 SQLite／进程环境、跨 profile single-use grant（跨配置档案单次授权）和不可伪造 provenance（来源证明）仍不属于本候选的解决范围。

## 分发与维护责任

- owner（维护责任组织）：`aisilun`
- reconciliation policy（对账策略）：`official-tag-minimal-overlay`（官方 Tag + 最小下游覆盖层）
- 不自动跟随 `upstream/main`
- `asl/production`是唯一默认 install/update/banner/release（安装／更新／启动提示／发布链接）通道；fork 的 `main`不属于 ASL 生产线
- `setup.py`继续禁止 wheel/sdist/PyPI（轮子包／源码包／Python 包索引发布）；分发仍通过 `scripts/install.sh`源码检出
- 任一后续上游移植必须记录来源 commit、冲突决策、受影响测试和新的候选 exact HEAD
- #74529 若上游合并，只进入下一次正式对账，不自动替换已发布的 ASL 运行版本

## PR 前硬门

1. `pyproject.toml`、`hermes_cli.__version__`、`uv.lock`与机器合同均为 `0.19.1+asl.1`。
2. 机器合同列出的 required tests（必测文件）全部通过。
3. LSP manager blob（语言服务器管理器文件对象）与官方 v0.19.1 完全一致。
4. 在临时 `HERMES_HOME`中完成插件发现、配置加载、Gateway 入口、工具前门和输出后门隔离验证。
5. `git diff --check <base>...HEAD`、语法／格式门、secret scan（密钥扫描）与全量 CI 全部绑定 PR exact HEAD。
6. 作者完成全差分自检；稳定 PR 只由小沐单审一次。
7. merge、Tag、Release、production activation、Cron 恢复、数据库迁移和 Fleet apply 继续分闸。

## 回滚边界

候选或后续隔离验收失败时，源码回滚基线是 `0.19.0+asl.3@d01f138cf889ed95e7b7ff3785b89db55c52e828`及其已记录 Tag／Release。不得通过禁用 fail-closed gate 恢复服务。真实生产回滚命令、配置快照和运行根切换只能在后续 production activation（生产激活）授权包中确定。
