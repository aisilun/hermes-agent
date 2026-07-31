# ASL Hermes 定制分支

本仓库的 `aisilun/hermes-agent` 分支用于承载 ASL 生产所需、尚未进入 Hermes 上游正式版的最小 host capability（宿主能力）。机器可读的唯一发布合同是 [`governance/asl-fork-release.json`](governance/asl-fork-release.json)。

## 当前状态

- package version（候选包版本）：`0.19.0+asl.4`
- planned release tag（计划发布标签）：`v0.19.0-asl.4`（未创建、未授权）
- production branch（生产分支）：`asl/production`
- 状态：`candidate`（候选源码）
- official source version（正式源码版本）：`0.19.0+asl.3`
- 当前正式 Tag：`v0.19.0-asl.3`
- 当前正式 GitHub Release：`v0.19.0-asl.3`（绑定该 Tag 并完成正文与状态回读）
- production activation（生产激活）：未授权
- Fleet apply（全量应用）：未授权

该状态允许完成 `.4` 源码、测试、PR、CI、指定账号单审及已授权 merge（合并）。它不授权创建 `.4` Tag/Release，不表示可生产安装，不允许写入 `default` profile（默认配置档案），也不允许重启 Gateway（网关）。

## 来源绑定

| 层 | 固定值 | 含义 |
|---|---|---|
| 上游正式 Tag | `v2026.7.20` | 最近已观察的 Hermes 正式版 |
| 正式 Tag commit | `3ef6bbd201263d354fd83ec55b3c306ded2eb72a` | 正式版基准事实 |
| turn-gate upstream base | `0bd82a8a84595720ea1f14b103aeb81ca3cc50ef` | #74529 开发基线 |
| turn-gate source head | `0e1031a9ff05d0c0d2f44f2148b80a33ca9d3561` | 本正式源码承接的宿主能力源码 |
| 上游贡献线 | `NousResearch/hermes-agent#74529` | 长期官方贡献支线，不阻断 ASL 生产 |
| 最新 upstream/main 观察值 | `cc4cab2f592e60a197e796506de9168f74baf3ea` | 2026-07-31 观察；未吸收，不追逐移动目标 |

曾尝试从上游正式 Tag 仅移植 #74529 两提交，但 `agent/conversation_loop.py`、`agent/tool_executor.py`、`gateway/run.py` 等 9 个生产文件无法按上下文直接应用。当前正式源码因此绑定 #74529 自身的 exact head（精确头提交），而不是手工硬解正式 Tag 冲突，也不是静默带入约 2909 个上游未发布提交。

## 定制范围

已发布的 `.1` 增加 host-enforced outer turn gate（宿主强制外层轮次门）：

- 由 `config.yaml` 绑定 required provider（必需提供者）；
- 在 Gateway 主轮次和后台轮次入口获取 lease（租约）；
- 工具执行前和输出发送前重新校验；
- reload（热重载）后重新发现插件并保持 fail-closed（失败关闭）；
- 为 standalone ASL plugin（独立 ASL 插件）提供宿主边界，但不把 ASL 插件源码写入 Hermes core（核心仓）。

已发布的 `.2` 补丁在 `.1` 上显式承接当前 live tree（现网工作树）的生产相关语义并集：

- Codex OAuth / custom endpoint（自定义端点）按活动 provider/base URL 施加上下文上限，避免错误使用通用 1.05M 上限；
- Kanban 创建即 blocked（阻塞）任务写入粘性阻塞事件，并允许已有 PR 修复任务在明确 requeue/unblock（重新入队/解除阻塞）后继续；
- 飞书原生审批卡使用中文标签、风险摘要和 smart-deny 单次覆盖边界；
- card callback（卡片回调）在同步渲染“已批准/已回答”前先执行严格 operator allowlist（操作人允许名单）检查，未授权时 fail-closed；
- 补齐 conversation compression（会话压缩失败不丢历史）及上述行为的回归测试。

审计判定为已被 `.1` 吸收、因此不重复移植的 live 差异：`gateway/run.py` 活动 provider/base URL 解析，以及 upstream commits `967e078ae46e6e748cc2ca36a88e0d0146904f7a`、`75be8fb463c5159b1d17e46c59f809ce1c06633a`。未纳入 `.2` 的工作态内容：OAuth Keychain broker 支线、GrsAI 第三方图片插件、`workspace/` 与采集临时文件。

`.3` patch release（补丁正式源码）只修复 `.2` 隔离安装 A–C 验收发现的两个发布缺陷：

- macOS `hermes gateway install --no-start-now --no-start-on-login` 现在把参数完整传入 `launchd`，生成 `RunAtLoad=false`、`KeepAlive=false` 的 plist，安装时不 bootstrap（加载）服务；后续 plist 刷新保持该显式策略，不会静默恢复自动启动；
- `.lazy-refresh-incomplete` 被定义为 runtime recovery marker（运行时恢复标记），从 Git 跟踪树移除并写入 `.gitignore`，不再污染固定 Tag 的源码安装。

`.4` candidate（候选止血版）只增加 Kanban privileged delegation containment（特权委托止血）：

- `default`被定义为 code-owned privileged profile（代码持有的特权配置档案），不能由 agent 可编辑配置关闭；
- create、assign/reassign、`kanban.default_assignee`、ready/review claim、dispatcher 与 `_default_spawn`共用同一 fail-closed 规则；任何从非 `default`／空 assignee 转入 `default`的 assign/reassign 均拒绝，`kanban.default_assignee=default`也一律禁用；
- 非 `default`、`worker`、空来源和 legacy row（旧行）指向 `default`时，在 spawn、凭据读取、Gateway 动作与远端写入前拒绝；旧行被置为 `blocked`，标准 `block_kind=capability`，并记录 `policy=privileged_delegation`；
- CLI 把所有 task（任务）的 `created_by`绑定到 active profile（活动配置档案），拒绝任何不一致的 `--created-by`伪造，防止先伪造来源、再二次 reassign（重派）到特权目标；`kanban_create`工具使用相同的 runtime active-profile resolver（运行时活动配置档案解析器），即使 default 会话未导出 `HERMES_PROFILE`也能保留合法自来源；
- `default`直接创建 `assignee=default`、`default`创建非特权 task，以及非特权 profile 之间的正常路由保持可用；任务一旦 handoff（交接）离开 `default`，不能靠 reassign 回流，须由 `default`新建直接任务。

该止血版不是完整 zero-trust broker（零信任授权代理）：legacy `created_by`与本机环境变量仍不是密码学证明，同一 OS 用户直接修改 SQLite／进程环境不属于 `.4`已解决边界。受治理的跨 profile single-use grant（单次授权）、side-effect authorization（副作用授权）与不可伪造 provenance（来源证明）仍须后续独立设计；在此之前，仅创建阶段的 `default` self-origin（自来源）任务可直接指向 `default`，其他入站委托和 reassign（重派）回流均拒绝。

本候选源码不包含：独立 ASL 权限插件、真实 Feishu approval（飞书审批）配置、生产密钥、数据库迁移、生产部署或 Fleet apply。

## 维护责任

- owner（维护责任组织）：`aisilun`
- 上游同步策略：`explicit-tested-port-only`（仅显式、经过测试的移植）
- 不自动跟随 `upstream/main`
- `asl/production` 是唯一默认 install/update/banner/release（安装、更新、启动提示与发布链接）通道；fork 的旧 `main` 不属于生产线
- 稳定 PR 以 `0bd82a8a84595720ea1f14b103aeb81ca3cc50ef` 初始化的 `asl/production` 为 base（基线），避免把 fork `main` 落后的 4065 个上游提交混入单审
- 任一上游移植都必须记录源 commit、冲突决策、受影响测试和候选新 SHA
- 上游 #74529 若合并，只作为后续回归基线；不得自动替换已发布的 ASL 源码安装

## 发布前硬门

1. 机器合同与 `pyproject.toml`、`hermes_cli.__version__`、`uv.lock` 版本一致。
2. 合同声明的 6 个 turn-gate 测试文件与 5 个 fork/update/security 测试文件全部通过。
3. 在临时 `HERMES_HOME` 中完成插件发现、配置加载、Gateway 入口、工具前门和输出后门隔离验证。
4. 保留 `setup.py` 对 wheel/sdist/PyPI（轮子包/源码包/Python 包索引发布）的官方禁令；通过 `scripts/install.sh` 从 `aisilun/hermes-agent` 的 `asl/production` 固定 Tag 和 commit 源码检出，并在全新 venv（虚拟环境）与临时 `HERMES_HOME` 中完成安装 smoke test（冒烟测试）。Windows ZIP fallback（回退更新）也必须绑定同一 fork branch（分支），不得回落到官方 `main`。
5. PR 的 exact HEAD 通过 CI，并由指定的小沐账号单审。
6. merge（合并）、Tag、Release、production activation、Fleet apply 分闸；Tag/Release 只固化 GitHub canonical source（规范源码），不表示 production activation 或 Fleet apply 已授权。

## 计划回滚边界

`.4` 候选或后续发布若验收失败，只允许回滚到 `0.19.0+asl.3` 的已记录 commit、Tag 与源码归档 SHA-256，并恢复原 `config.yaml`；不得通过禁用 fail-closed gate（失败关闭门）来恢复服务。生产回滚方案仍须在后续生产激活授权包中绑定具体来源摘要和恢复命令。
