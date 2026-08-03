# Task2 仓库、远程与组织关系

## 仓库边界

`Task2` 是独立 Git 仓库，位于 `contest/contest/Task2`。虽然它在外层 `contest/contest` 的目录树中，但两者的提交历史、分支、忽略规则和远程均独立；不要在外层仓库提交 Task2 文件，也不要假定外层分支代表 Task2 分支。

| 本地目录 | Git 身份 | 用途 |
|---|---|---|
| `contest/contest` | 外层项目仓库 | 项目级文档与外围资产 |
| `contest/contest/Task2` | FRECA Task2 仓库 | 审计流水线实现、测试、运行配置 |
| `_review/csmining26` | 独立参考仓库 | 上游代码对照 |

## Task2 远程

| 名称 | URL | 归属/角色 |
|---|---|---|
| `origin` | `https://github.com/passionworkeer/freca-task2-audit.git` | Task2 项目远程 |
| `sztu` | `https://github.com/SZTU-AGI/CSMining26.git` | `SZTU-AGI` GitHub 组织远程 |

`SZTU-AGI` 是 GitHub 组织。匿名网页访问并未公开展示上述两个仓库页面；在调整组织权限、仓库可见性、团队成员或保护规则前，应使用具备授权的 GitHub 会话再次确认实际权限和仓库状态。

## 分支和工作树

主检出通常位于 `Task2` 目录。当前重要实验分支为 `feature/direct-llm-experiments`，其链接工作树位于：

```text
Task2/.worktrees/feature-direct-llm-experiments
```

该工作树携带 direct-LLM 实验代码和结果。必须通过以下命令查看或操作它：

```powershell
git -C 'D:\Data\Desktop\contest (2)\contest\contest\Task2' worktree list
git -C 'D:\Data\Desktop\contest (2)\contest\contest\Task2\.worktrees\feature-direct-llm-experiments' status --short --branch
```

不要移动、复制、压缩后删除或手工清理 `.worktrees`。如确实需要移除工作树，先确认分支已推送、运行产物已有独立归档，并使用 `git worktree remove`。

## 日常操作规则

1. 任何改动前，在目标仓库目录运行 `git status --short --branch`。
2. 使用 `git -C <目标目录>`，避免从工作区根目录误在错误仓库中执行 Git。
3. 运行 Task2 测试时，工作目录必须为 `Task2`：`& .\.venv\Scripts\python.exe -m pytest -q`。从工作区根目录运行 pytest 会错误收集 `_review/csmining26` 的测试。
4. 不提交 `.env` 或运行期凭据；只提交经审查的代码、文档和可复现配置。
5. 推送前分别确认 `origin` 与 `sztu` 是否都应接收该提交，不能因为配置了两个远程而默认双推。
