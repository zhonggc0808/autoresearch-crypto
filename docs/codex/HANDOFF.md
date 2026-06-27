# Handoff

Last updated: 2026-06-27

## Current Handoff Summary

The repository memory structure has been moved toward repo-versioned memory:

- `AGENTS.md` is now the hard-rule entry point.
- `docs/codex/` stores long-term project memory.
- `.agents/skills/live-change-guard/` stores the first project skill for live-risk preflight.
- `.agents/skills/save-code/` stores the commit, push, and committed-HEAD archive workflow.

No strategy, live runtime, checkpoint, exchange config, oracle, or research artifact behavior was intentionally changed as part of this memory-structure update.

## Latest Tooling Update

Added `save-code` as a repo-level skill for the requested "save code" workflow:

- Review Git status and confirm the intended commit scope.
- Require explicit live-risk approval for protected paths before staging.
- Commit with a confirmed message, push to `origin/<current branch>`, and create `..\autoresearch-crypto-runtime-YYYYMMDD-HHMMSS-<shortsha>.tar.gz` from committed `HEAD`.

This workflow does not change the current ETH strategy baseline or live/demo routing.

## Current Baseline To Load First

Read:

- `docs/codex/CURRENT_STATE.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/DECISIONS.md`

Current baseline:

- `channel_breakout_v2_2_m375_bbm375_1p5`
- main gate TimesFM `exp_0068`
- Moirai2 `exp_0093` is challenger shadow only

## Latest Research Thread

Latest useful evidence is around gate attribution:

- `research_workspace/diagnostics/exp_0092_final_gate_shortlist_compare.md`
- `research_workspace/diagnostics/exp_0129_gate_focused_deep_dive.md`
- `research_workspace/diagnostics/exp_0130_gate_year_dd_slices.md`

Key read:

- TimesFM `exp_0068` remains main.
- Moirai2 `exp_0093` is challenger shadow.
- Moirai `risk_floor=3%` has interesting diagnostic DD repair but is not promoted.

## Modified Files In This Memory Update

- `AGENTS.md`
- `.gitignore`
- `docs/codex/PROJECT_BRIEF.md`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/LIVE_GUARD.md`
- `docs/codex/HANDOFF.md`
- `.agents/skills/live-change-guard/SKILL.md`
- `.agents/skills/live-change-guard/agents/openai.yaml`
- `.agents/skills/save-code/SKILL.md`
- `.agents/skills/save-code/agents/openai.yaml`
- `.agents/skills/save-code/scripts/save-code.ps1`
- `C:\Users\81094\.codex\config.toml` (enabled native Codex memories)

## Next Session Startup Prompt

Use this when starting a new session:

```text
先不要改代码。

请先读取：
- AGENTS.md
- docs/codex/PROJECT_BRIEF.md
- docs/codex/CURRENT_STATE.md
- docs/codex/DECISIONS.md
- docs/codex/EXPERIMENT_LEDGER.md
- docs/codex/LIVE_GUARD.md
- docs/codex/HANDOFF.md

然后输出：
1. 你理解的当前项目状态
2. 当前禁止触碰的文件
3. 最近已否定的方向
4. 本次任务你建议怎么做
5. 哪些地方需要我确认

确认前不要修改任何文件。
```

## End Of Session Prompt

Use this before ending a research or implementation session:

```text
请不要继续改代码。

请根据本轮工作更新：
- docs/codex/CURRENT_STATE.md
- docs/codex/HANDOFF.md

如果有实验结果，更新：
- docs/codex/EXPERIMENT_LEDGER.md
- docs/codex/DECISIONS.md（仅当方向被通过、拒绝、暂停或关闭）

要求：
1. 只记录长期有价值的信息
2. 不写闲聊
3. 明确哪些结论已拒绝
4. 明确下次接着做什么
5. 标出所有被修改的文件
```
