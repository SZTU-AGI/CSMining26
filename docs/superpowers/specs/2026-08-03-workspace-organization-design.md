# Workspace Organization Design

## Goal

Make the `contest (2)` workspace easy to navigate without moving any Git
repository, virtual environment, worktree, or runtime experiment output.

## Scope

The workspace root will expose three stable areas:

```text
contest (2)/
├─ README.md
├─ materials/
│  ├─ source/
│  └─ mineru/
├─ reports/
│  ├─ stage/
│  ├─ analysis/
│  └─ dashboards/
├─ contest/contest/
└─ _review/csmining26/
```

`materials/source` holds human-supplied inputs currently stored at the
workspace root: the agreement JSON and translated checkpoint spreadsheet.
`materials/mineru` holds the generated JSON, HTML, and Markdown export of the
Export Control Rules. `reports` holds the stage report, cross-case analysis,
and scoreboard files.

## Repository Boundaries

The following directories are independent Git repositories and remain at their
current paths:

1. `contest/contest` is the outer project repository.
2. `contest/contest/Task2` is the FRECA Task2 implementation repository.
3. `_review/csmining26` is an upstream reference checkout.

The Task2 repository also owns `.worktrees/feature-direct-llm-experiments`.
It is an active linked worktree and must not be moved or treated as an archive.

## Git Relationship Documentation

The organization document will record that Task2 has two remotes: `origin`
(`passionworkeer/freca-task2-audit`) and `sztu`
(`SZTU-AGI/CSMining26`). It will explain that the current direct-LLM branch is
published to both remotes, while avoiding claims about private repository
visibility that cannot be verified from an anonymous session.

## Compatibility and Safety

1. Do not move `.git`, `.worktrees`, `.venv`, `build`, `src`, `scripts`,
   `tests`, or `extracted`.
2. Use Git-aware moves only for files tracked by a repository; root-level
   files are outside the repositories and may be moved normally.
3. Preserve every filename and file content. Add README files that provide
   both the new paths and the ownership/data-provenance distinction.
4. Do not inspect or reproduce secrets from `.env` or runtime configuration.
5. Do not push, change remotes, or mutate GitHub organizations.

## Deliverables

1. A root `README.md` with the workspace map, repository boundary table, and
   Git remote / organization relationship.
2. A `reports/README.md` explaining report provenance and dashboard usage.
3. `Task2/docs/project-map/REPOSITORY_RELATIONSHIP.md` describing remotes,
   worktrees, branches, and operating rules.
4. Physical relocation of the root-level inputs, MinerU exports, reports, and
   scoreboard artifacts into the specified directories.

## Acceptance Checks

1. The new paths exist and the original root-level copies no longer exist.
2. File SHA-256 values before and after relocation match.
3. `git status --short` confirms no unintended changes in all three
   repositories, except the explicitly added Task2 documentation.
4. Task2 test suite continues to pass after the move.
