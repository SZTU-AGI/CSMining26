# Workspace Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Physically organize the root-level workspace materials and reports while preserving all repositories, worktrees, runtime artifacts, content integrity, and Git boundaries.

**Architecture:** Move only the nine root-level project artifacts covered by the design into new `materials` and `reports` taxonomy directories. Use a SHA-256 manifest generated before relocation and verify it after relocation. Add navigational documentation outside repositories plus an explicit Git-relationship document inside Task2.

**Tech Stack:** PowerShell 7 file commands, `Get-FileHash` SHA-256, Git, Markdown, Python pytest.

---

## File Structure

- Create: `D:\Data\Desktop\contest (2)\README.md` — Workspace entry point and repository map.
- Create: `D:\Data\Desktop\contest (2)\materials\source\` — Original inputs.
- Create: `D:\Data\Desktop\contest (2)\materials\mineru\` — Generated regulation extracts.
- Create: `D:\Data\Desktop\contest (2)\reports\README.md` — Report and dashboard index.
- Create: `D:\Data\Desktop\contest (2)\reports\stage\` — Stage reports.
- Create: `D:\Data\Desktop\contest (2)\reports\analysis\` — Cross-case analysis.
- Create: `D:\Data\Desktop\contest (2)\reports\dashboards\` — Scoreboard files.
- Create: `D:\Data\Desktop\contest (2)\contest\contest\Task2\docs\project-map\REPOSITORY_RELATIONSHIP.md` — Remote, branch, and worktree operating guide.
- Create: `D:\Data\Desktop\contest (2)\contest\contest\Task2\docs\project-map\workspace-artifact-manifest.sha256` — SHA-256 evidence for moved source artifacts.
- Modify: `D:\Data\Desktop\contest (2)\contest\contest\Task2\docs\superpowers\plans\2026-08-03-workspace-organization.md` — Mark completed plan steps after execution.

### Task 1: Record source artifact integrity before moving

**Files:**
- Create: `D:\Data\Desktop\contest (2)\contest\contest\Task2\docs\project-map\workspace-artifact-manifest.sha256`

- [x] **Step 1: Define the exact root-level move set**

Use only these files; do not include `.claude`, `_review`, `contest`, `.env`, or any directory:

```text
agreement.json
checkingpoints_all_elements_onesheet_翻译.xlsx
MinerU_1-Export Control (Plants and Plant Products)Rules 2021__20260724024758.json
MinerU_html_1-Export_Control_(Plants_and_Plant_Products)Rules_2021_2080251967198760960.html
MinerU_markdown_1-Export_Control_(Plants_and_Plant_Products)Rules_2021_2080485091979599872.md
report/2026-08-03-stage-report.md
report/2026-08-03-cross-case-systemic-cps.md
scoreboard.html
scoreboard.json
```

- [x] **Step 2: Record each file SHA-256 and intended destination**

Run from `D:\Data\Desktop\contest (2)`:

```powershell
$items = @(
  @{ Source='agreement.json'; Destination='materials/source/agreement.json' },
  @{ Source='checkingpoints_all_elements_onesheet_翻译.xlsx'; Destination='materials/source/checkingpoints_all_elements_onesheet_翻译.xlsx' },
  @{ Source='MinerU_1-Export Control (Plants and Plant Products)Rules 2021__20260724024758.json'; Destination='materials/mineru/MinerU_1-Export Control (Plants and Plant Products)Rules 2021__20260724024758.json' },
  @{ Source='MinerU_html_1-Export_Control_(Plants_and_Plant_Products)Rules_2021_2080251967198760960.html'; Destination='materials/mineru/MinerU_html_1-Export_Control_(Plants_and_Plant_Products)Rules_2021_2080251967198760960.html' },
  @{ Source='MinerU_markdown_1-Export_Control_(Plants_and_Plant_Products)Rules_2021_2080485091979599872.md'; Destination='materials/mineru/MinerU_markdown_1-Export_Control_(Plants_and_Plant_Products)Rules_2021_2080485091979599872.md' },
  @{ Source='report/2026-08-03-stage-report.md'; Destination='reports/stage/2026-08-03-stage-report.md' },
  @{ Source='report/2026-08-03-cross-case-systemic-cps.md'; Destination='reports/analysis/2026-08-03-cross-case-systemic-cps.md' },
  @{ Source='scoreboard.html'; Destination='reports/dashboards/scoreboard.html' },
  @{ Source='scoreboard.json'; Destination='reports/dashboards/scoreboard.json' }
)
```

Write one line per item as `<SHA256>  <destination>` into the manifest file.

- [x] **Step 3: Verify the manifest has nine non-empty records**

Run:

```powershell
(Get-Content 'D:\Data\Desktop\contest (2)\contest\contest\Task2\docs\project-map\workspace-artifact-manifest.sha256').Count
```

Expected: `9`.

### Task 2: Move the root-level artifacts into the approved taxonomy

**Files:**
- Create: `D:\Data\Desktop\contest (2)\materials\source\`
- Create: `D:\Data\Desktop\contest (2)\materials\mineru\`
- Create: `D:\Data\Desktop\contest (2)\reports\stage\`
- Create: `D:\Data\Desktop\contest (2)\reports\analysis\`
- Create: `D:\Data\Desktop\contest (2)\reports\dashboards\`
- Modify: the nine source files from Task 1 by relocation only

- [x] **Step 1: Create the five destination directories**

Run:

```powershell
New-Item -ItemType Directory -Force -Path `
  'D:\Data\Desktop\contest (2)\materials\source', `
  'D:\Data\Desktop\contest (2)\materials\mineru', `
  'D:\Data\Desktop\contest (2)\reports\stage', `
  'D:\Data\Desktop\contest (2)\reports\analysis', `
  'D:\Data\Desktop\contest (2)\reports\dashboards'
```

- [x] **Step 2: Move the exact files in the manifest map**

Run a single PowerShell `Move-Item -LiteralPath` command per manifest item.
Before each move, require the source to exist and the destination to not yet
exist. Do not use globs.

- [x] **Step 3: Verify all nine source paths are absent and destinations exist**

For each map entry, check `Test-Path Source` is `False` and
`Test-Path Destination` is `True`.

### Task 3: Add workspace and Git relationship documentation

**Files:**
- Create: `D:\Data\Desktop\contest (2)\README.md`
- Create: `D:\Data\Desktop\contest (2)\reports\README.md`
- Create: `D:\Data\Desktop\contest (2)\contest\contest\Task2\docs\project-map\REPOSITORY_RELATIONSHIP.md`

- [x] **Step 1: Write the root README**

Document the top-level layout, distinguish source inputs from generated
MinerU output and experimental reports, list the three Git repositories, and
state that `.claude` is tooling state rather than project source.

- [x] **Step 2: Write the reports README**

Index the stage, analysis, and dashboard directories; identify
`automatic_retrieval` as the source of the 11-case stage scan and state that
reports are evidence snapshots rather than a final 100-case submission.

- [x] **Step 3: Write the Task2 repository relationship document**

Record these verified remotes:

```text
origin  https://github.com/passionworkeer/freca-task2-audit.git
sztu    https://github.com/SZTU-AGI/CSMining26.git
```

State that `Task2` is its own Git repository; `contest/contest` is not its
parent Git history. Explain that `feature/direct-llm-experiments` is a linked
worktree and must be addressed through `git -C Task2 worktree list`, not moved
or copied. Mention that public anonymous GitHub access did not expose the two
repository pages, so permissions/visibility must be checked with an authorized
GitHub session before administrative changes.

### Task 4: Verify content integrity and repository safety

**Files:**
- Test: `D:\Data\Desktop\contest (2)\contest\contest\Task2\docs\project-map\workspace-artifact-manifest.sha256`

- [x] **Step 1: Recompute hashes for the nine destinations**

For every manifest record, run `Get-FileHash -Algorithm SHA256` on its
destination and compare it with the manifest hash. Expected: nine exact
matches.

- [x] **Step 2: Confirm repository changes are scoped**

Run `git status --short --branch` in:

```text
D:\Data\Desktop\contest (2)\contest\contest
D:\Data\Desktop\contest (2)\contest\contest\Task2
D:\Data\Desktop\contest (2)\_review\csmining26
```

Expected: outer and review repositories remain unchanged. Task2 only contains
the new organization documentation plus the pre-existing untracked
`docs/ARCHITECTURE_DESIGN.md`.

- [x] **Step 3: Run Task2 regression tests**

Run:

```powershell
& 'D:\Data\Desktop\contest (2)\contest\contest\Task2\.venv\Scripts\python.exe' -m pytest -q
```

Expected: all tests pass.

- [x] **Step 4: Commit only Task2-owned organization documentation**

Stage only:

```text
docs/project-map/REPOSITORY_RELATIONSHIP.md
docs/project-map/workspace-artifact-manifest.sha256
docs/superpowers/plans/2026-08-03-workspace-organization.md
```

Commit message:

```text
docs: map workspace artifacts and git relationships
```

Do not stage `docs/ARCHITECTURE_DESIGN.md`.
