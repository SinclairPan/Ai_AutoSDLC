# Codex Handoff — Directional Benefit Demo

## Goal

Deliver the lightweight 5-arm × 3-task directional benefit runner, fake-Provider rehearsal, preflight evidence, and budget confirmation boundary without any real Provider call.

## Current state

- Audit-grade runtime-canary WIP was archived on `codex/benefit-audit-wip` at `fccdbb262b87529d10f5fc0eb461e86dd54df909` and the product branch was restored to clean `b9bd1c31fd2b1aa405e234d6ecabe0eb7c7e2d96` before this work.
- Frozen directional manifest contains 15 opaque writer runs and exactly 19 predeclared sessions.
- Fake rehearsal prepares 15 real workspaces and records 19 simulated reservations, four findings-only expert events, and three same-writer resume events with zero Provider launches.
- The append-only attempt ledger is read and appended through a locked, owner-bound pinned descriptor; short writes are completed and corrupt, duplicate, overwrite, and symlink cases fail before append.
- Actual r2 authority remains `NO-GO`; the evaluator is labelled only `legacy-directional-evaluator`.
- Preflight and budget request keep token and currency estimates null.

## Changed files

- `src/ai_sdlc/benefit_directional_demo.py`
- `scripts/ai_sdlc_v2_directional_demo.py`
- `benchmarks/ai-sdlc-v2-directional/**`
- `tests/unit/test_benefit_directional_demo.py`
- `tests/integration/test_benefit_directional_rehearsal.py`
- `tests/integration/test_benefit_directional_isolation.py`

## Decisions

- Keep all five arms because P/S/A11 support product comparisons while A00/A10 isolate Loop and expert effects.
- Keep three fixtures as the minimum cross-task directional sample; report `n=3 per arm`, single run per task, no statistical significance or generalization.
- Any 20th session, retry, rereview, unplanned expert, candidate-writing expert, or expert subagent fails before ledger append.
- Model failures are terminal cells; infrastructure failures abort the whole matrix incomplete.
- Website data must show raw paired values and losses, with no cherry-picked winner.

## Verification so far

- Fresh RED: `35 failed`.
- Unit GREEN after expansion and final format: `49 passed`.
- Directional unit + 15-workspace real preparation + isolation: `51 passed`; 15 workspaces, 19 sessions, Provider 0.
- Exact macOS profile canary final system-outside: `1 passed / 0 skipped`.
- Related benchmark/arms/directional suites: `504 passed / 1 existing skip`.
- Full benefit suite system-outside: `718 passed / 2 skipped / 1 transient external Codex Git-ref race`; isolated rerun of that test: `1 passed`.
- Ruff check, Ruff format check, and `git diff --check`: green.
- Task2 protocol/commitment blobs and actual r1/r2/legacy/source/disposition fingerprints: unchanged.
- Formal auth/ledger/results, r3 target/source/disposition, Provider and `codex exec`: 0 / absent.

## Risks / blockers

- No implementation blocker.
- Formal Provider execution remains blocked until the final explicit budget confirmation.

## Next exact steps

1. Review final diff and JSON contracts.
2. Create the single scoped commit on the product branch.
3. Stop before any Provider call and return the budget confirmation fields.
