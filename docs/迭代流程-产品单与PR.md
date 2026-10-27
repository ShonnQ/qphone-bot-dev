# 迭代流程：产品单（FR / PR）+ Pull Request

> 版本：v1.0 · 2026-10-27 · 正本
> 一句话：**一次迭代 = 一张单（Issue）+ 一条分支 + 一个 PR**；里程碑只做每日总账，唔重复细节。

## 1 两层，唔好混

| 层 | 载体 | 编号 | 记录乜 |
|---|---|---|---|
| 产品单 | GitHub Issue | `FR-YYYYMMDD-N`（功能 / 需求）<br>`PR-YYYYMMDD-N`（缺陷，Problem Report） | 为咩做、范围、验收标准、影响层、实测证据 |
| 改动 | GitHub Pull Request | GitHub 原生 `#N` | 改咗边几个文件、点证明、版本号、回滚方式 |
| 总账 | `docs/里程碑-*.md` | 每日一节 | 当日完成 / 已验证 / 踩过嘅坑 / 架构事实 / 下一步 |

- `PR-` 系 **Problem Report（缺陷）**，同 GitHub Pull Request 唔同一样嘢；沟通时讲清楚。
- GitHub 原生 `#N` 只系链接，Issue 同 PR 共用同一个计数器，所以**产品单号以标题为准**。
- 单号一律由脚本分配，**禁手填**（手填会撞同日序号）。

## 2 开单

```bash
cd ~/projects/qphone-bot-dev
./scripts/new-ticket.sh fr "接管场景分类"          # 功能单
./scripts/new-ticket.sh pr "步骤日志中途停更"       # 缺陷单
./scripts/new-ticket.sh --dry-run fr "只睇单号"
./scripts/new-ticket.sh --body-file /tmp/fr.md fr "标题"   # 自备正文
```

脚本扫 `tickets/INDEX.md` + GitHub 现有 Issue 标题，分配当日下一个序号，`gh issue create` 后自动追账本。

验：`gh issue view N --repo ShonnQ/qphone-bot-dev --json number,title,labels,state,url`，标题必须带单号，账本有对应行。

## 3 落码到合入

```bash
git switch -c feat/FR-20261027-1-接管分类      # 分支名 = 单号 + slug
# 改码 → 真跑实测（禁凭文档推断）
git add <相关文件> && git commit -m "feat(FR-20261027-1): 接管场景分类"
git push -u origin feat/FR-20261027-1-接管分类
gh pr create --repo ShonnQ/qphone-bot-dev --base main \
  --title "feat(FR-20261027-1): 接管场景分类" --body-file /tmp/pr.md
```

PR 正文照 `.github/PULL_REQUEST_TEMPLATE.md`：挂单号（`Closes #N`）+ **实测证据表** + 影响层 + 三处版本号 + 回滚方式。

合入后：
1. `gh issue close N --repo ShonnQ/qphone-bot-dev --comment "交付：<证据 / 版本>，PR #M"`
2. `tickets/INDEX.md` 状态列改 `closed YYYY-MM-DD`、PR 列填 `#M`，commit 推 `main`
3. 里程碑当日节追加一行指向单号 + PR

## 4 纪律

- **先单后码**：冇单、冇分支 = 未开工。产品代码唔准直推 `main`。
- **单个 PR 只做一件事**：一单可以拆多张单 / 多个 PR，但一个 PR 唔好混两件无关改动（回滚会连坐）。
- **实测先过**：PR 正文嘅「点证明」必须系真跑输出（命令 + 返回 / trajectory id / 截图绝对路径），唔准写「已实现」。
- **文档三处同步**：`详细设计.md`（正本）→ `python3 docs/gen_design_html.py` 重生 HTML → 里程碑追加。
- **运行产物唔入库**：`trajectories/` 已在 `.gitignore`；证据用 ID + 摘要写落 PR，唔好塞整个目录。
- **本机 `gh` 有 `libxcrun` 坑**（x86_64 二进制经 Rosetta 调 xcrun shim 失败）：所有 `gh` 命令显式带 `--repo ShonnQ/qphone-bot-dev`，唔好靠 cwd 推断仓库。

## 5 与旧记录嘅关系

2026-10-23 → 10-27 嘅 45 个 commit 系「直推 main」时期嘅历史，唔回溯逐单开单。由 `FR-20261027-1` 起，新迭代一律行单 + PR。
