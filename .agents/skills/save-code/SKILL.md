---
name: save-code
description: "Safely save code in autoresearch-crypto by reviewing the current Git worktree, confirming commit scope and commit message, pushing the current branch to GitHub, and creating a parent-directory tar.gz runtime archive from committed HEAD. Use when the user says to save code, \u4fdd\u5b58\u4ee3\u7801, \u63d0\u4ea4\u63a8\u9001\u5e76\u6253\u5305, commit/push/archive, or asks for the repository to be committed, pushed, and packaged after changes."
---

# Save Code

## Required Reads

Before committing, pushing, or archiving, read:

- `AGENTS.md`
- `docs/codex/LIVE_GUARD.md`

If the task context is stale or unclear, also skim `docs/codex/CURRENT_STATE.md` and `docs/codex/HANDOFF.md`.

## Workflow

1. Run `git status --short --untracked-files=all` and review the changed files.
2. Confirm the intended commit scope with the user before staging. Do not include unrelated dirty files.
3. Classify the selected scope for live-safety risk:
   - `live_*_quant.py`
   - `dex/live/`
   - `configs/live/`
   - `checkpoints/`
   - `.env`, `.env.*`, credentials, API keys, or exchange routing/configuration
   - order placement, position sizing, leverage, stop-loss, retry, lock, or execution-safety code
   - `dex/strategies/channel_breakout.py`
   - `scripts/research_oracle.py`
4. If the selected scope includes any high-risk path or behavior, stop and require explicit user approval in the current session. The bundled script requires the exact phrase `APPROVE LIVE-RISK SAVE`.
5. Generate a concise commit message from the confirmed scope and ask the user to confirm it.
6. Run the bundled script with the final message and explicit paths where possible:

```powershell
pwsh .agents\skills\save-code\scripts\save-code.ps1 -Message "chore: save current tooling" -Paths @("path\one", "path\two")
```

Use `-DryRun` first when checking scope, high-risk detection, or archive naming:

```powershell
pwsh .agents\skills\save-code\scripts\save-code.ps1 -Message "chore: test save-code dry run" -DryRun
```

## Push Target

Default push target is `origin/<current branch>`. Re-check the current branch every time with `git branch --show-current`; in this checkout it is currently expected to be `origin/dev-2.0`, but do not hard-code that if the branch changed.

Do not push to `upstream` unless the user explicitly asks.

## Archive Rule

The archive must be created from committed `HEAD` using `git archive`, not from the working tree.

Default archive path pattern:

```text
..\autoresearch-crypto-runtime-YYYYMMDD-HHMMSS-<shortsha>.tar.gz
```

Because `git archive HEAD` only packages tracked committed files, it excludes untracked or ignored local material such as `.venv/`, `data/`, `.git/`, `.codegraph/`, `.pytest_cache/`, `.ruff_cache/`, `.uv-cache/`, logs, local reports, and untracked research artifacts. Tracked `research_workspace/` schemas, candidates, documentation, or necessary diagnostics remain included. Secrets and `.env*` files should not be committed and therefore should not enter the archive.

## Default Validation

This save workflow does not run the full test suite by default. Perform lightweight checks:

- Review Git status and selected paths.
- Run `-DryRun` when the scope or risk classification needs confirmation.
- Validate this skill after edits:

```powershell
py -3 C:\Users\81094\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\save-code
```

Only run `uv run ruff check .`, `uv run ruff format .`, or `uv run pytest tests/` when the user asks or the code changes justify the extra time.
