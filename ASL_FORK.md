# ASL Hermes v0.20.0 Host Gate 薄覆盖层正式源码

本文件描述 `aisilun/hermes-agent`在 NousResearch Hermes official（官方）`v2026.8.3`之上的单层、可退役 Host Gate thin overlay（宿主门禁薄覆盖层）。机器可读合同为 [`governance/asl-fork-release.json`](governance/asl-fork-release.json)。

## 当前发布状态

- upstream package version（上游包版本）：`0.20.0`
- ASL release version（ASL 发布版本）：`0.20.0-asl.1`
- planned annotated Tag（计划附注标签）：`v0.20.0-asl.1`
- source status（源码状态）：`official-source-ready`
- base branch（基准分支）：`asl/upstream-v2026.8.3`
- distribution（分发）：固定 Tag／commit 的 source checkout（源码检出），`assets=0`
- release-only PR、审核和合并对象：合并后实时回读，当前不预造
- production activation（生产激活）、Gateway restart（网关重启）、Profile/Fleet apply（配置档案／全量应用）及数据库迁移：全部未授权

```text
release_only_pr=pending
release_reviewed_head=pending
release_review_id=pending
release_merge_commit=pending
tag_created=false
github_release_created=false
production_activation_authorized=false
gateway_restart_authorized=false
profile_apply_authorized=false
fleet_apply_authorized=false
database_migration_authorized=false
```

## 不可变上游来源

```text
upstream_repository=NousResearch/hermes-agent
upstream_version=0.20.0
upstream_tag=v2026.8.3
upstream_tag_object=7de39e700d2c329e15d32eb0b96e2f7cdd9fbdb2
upstream_commit=3c27eb6234bf91b8ceee9e9071591b31e9b148cb
upstream_tree=b217767ccb994605dad522e693fa1b4cdbc2f352
```

本薄覆盖层不改写 `pyproject.toml`、`hermes_cli.__version__`或官方日期；`0.20.0-asl.1`只作为 ASL Release／Tag 身份，避免把官方 package version（包版本）伪造成新的上游版本。

## PR #12 来源闭包

```text
source_pr=12
source_reviewed_head=70a2fd89eb9c9da594968c1d552eaf918a82bda1
source_review_id=4867149067
source_review_state=APPROVED
source_pr_tree=e0a5957f695448ec971abfc9505d794aab3dc53b
source_merge_commit=c0872e8b27bf4232dc353d6a0d43d6a132033744
source_merge_tree=e0a5957f695448ec971abfc9505d794aab3dc53b
source_pr_ci_run=31010701357
source_pr_check_runs=25 success + 12 skipped + 0 failed
source_author_self_check=150 passed
source_post_merge_ci=CI_NOT_CONFIGURED
```

reviewed tree（已审文件树）与 squash merge tree（压缩合并文件树）完全一致。目标分支没有 push CI，也没有 `workflow_dispatch（手工工作流）`触发器；因此 release-only merge 后必须从该 exact merge commit 建 detached worktree（分离工作树）并重跑完整发布矩阵，在 Release 正文写明 `CI_NOT_CONFIGURED`。不能用 PR HEAD 的旧 CI 冒充 post-merge CI（合并后持续集成）。

## 薄覆盖层边界

Host API 保持 policy-neutral（政策中立）：

- Hermes core 只定义通用 turn gate（轮次门）、observation（观察）与 child environment（子进程环境）合同；
- ASL generation／lease／challenge／ACK、审批 authority（授权权威）、Skill ACK 和业务策略留在 Standards Extension Pack；
- 保留 extensionless Python wrapper lifecycle guard（无扩展名 Python 封装器生命周期保护）；
- 不向 Hermes core 写入 ASL 群聊、负责人、项目或业务字段；
- 当 NousResearch 正式 Release 提供并验证等价能力后，本覆盖层必须退役。

Extension Pack 正式资产必须分别绑定：

1. 本 Release 的 peeled commit（剥离后提交）；
2. Standards `asl-standards-v2.16.0` peeled commit；
3. 资产内 manifest（清单）与逐文件 SHA-256；
4. Release 下载后摘要。

## 分发与授权边界

本 Release 仅允许：

- 创建并回读 release-only PR；
- 在 exact merge commit 上创建 annotated Tag；
- 创建 source-only GitHub Release；
- 从远端 Tag／source archive 做 clean-room（洁净环境）导入验证。

本 Release **不授权**：

- 安装或替换任何 live runtime（实时运行时）；
- 修改任何 `HERMES_HOME`、Profile 配置或凭据；
- 加载 LaunchAgent、启动 coordinator、切换 `current`；
- Gateway restart、真实飞书双连接或 live smoke；
- Profile/Fleet apply、项目 runtime lock 迁移、数据库迁移或 production deploy（生产部署）。

这些动作从 G3 开始，必须使用新的 exact plan（精确计划）和单次回执。
