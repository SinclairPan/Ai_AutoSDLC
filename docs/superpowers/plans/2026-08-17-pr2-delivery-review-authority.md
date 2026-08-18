# PR2 Unique Delivery and Review Authority Implementation Plan

**Goal:** Make the five Loops the only delivery truth, require a bounded independent expert outcome
before Close, execute quality commands instead of accepting strings, and bind Local PR Review to the
exact committed tree.

**Base:** `cf91572e5877d54dba963b4b28c067f7f084aa3f`

**Design:** `docs/superpowers/specs/2026-08-17-pr2-delivery-review-authority-design.md`

## Global constraints

- Test first: each task starts with a focused failing regression and ends with its focused suite.
- Retain only independent local pre-commit review, advisory slimming, and bounded experts for five Loops.
- One primary plus at most one cross-risk expert; at most one repair re-review.
- No authority/store/CAS/proof/certificate/session/quorum/score/Graph/learning/background service.
- No PR3 update/rule/status work and no PR4 physical deletion.
- `run` stays read-only and never commits or writes checkpoint/Telemetry.
- Only frozen-scope reproducible Critical/Important may consume the single repair pass.

## Task 1 — deterministic experts and minimal models

Files:

- Modify `src/ai_sdlc/core/review_kernel.py`
- Create `src/ai_sdlc/core/loop_review_models.py`
- Modify `tests/unit/test_review_kernel.py`
- Create `tests/unit/test_loop_review_service.py`

RED tests:

- exact primary role for all five Loop types;
- fixed cross-risk priority and no duplicate role;
- at most two roles and no third expert;
- outcome round only 1/2;
- forbidden credential fields rejected by `extra="forbid"`.

Implementation:

- add `expert_roles` and `expert_reasons` to `ReviewInput`, included in digest;
- keep `review_kernel.py` pure/read-only;
- add minimal `LoopReviewOutcome` and `ReviewStatusOverlay` in the new model module.

Verify:

```text
uv run pytest -q tests/unit/test_review_kernel.py tests/unit/test_loop_review_service.py
uv run ruff check src/ai_sdlc/core/review_kernel.py src/ai_sdlc/core/loop_review_models.py tests/unit/test_review_kernel.py tests/unit/test_loop_review_service.py
```

## Task 2 — two immutable review rounds in existing Loop directories

Files:

- Create `src/ai_sdlc/core/loop_review_service.py`
- Modify `src/ai_sdlc/cli/loop_review_cmd.py`
- Modify `src/ai_sdlc/cli/loop_cmd.py`
- Modify `src/ai_sdlc/core/loop_status.py`
- Modify `tests/unit/test_loop_review_service.py`
- Modify `tests/integration/test_cli_loop_review.py`

RED tests:

- digest without outcome returns `review-result-missing`;
- missing selected expert, wrong role and third expert block;
- actionable round 1 requires changed substantive input before round 2;
- completed clean round 1 cannot be re-recorded;
- completed round 2 cannot be re-recorded for advisory or actionable findings;
- failed round 1/2 can retry only the same round and same digest;
- stale failed retry leaves prior bytes unchanged;
- third round returns `review-round-limit`.

Implementation:

- resolve canonical existing Loop/review directory; do not create a pointer/store;
- write only `review-outcome-round-1.json` or `review-outcome-round-2.json` atomically;
- re-read destination immediately before replace;
- never overwrite completed; only replace failed with matching round/digest;
- add `loop review-record`; derive status overlay without mutating business artifacts;
- exclude outcome and mutable loop status bytes from substantive digest after identity validation.

Verify:

```text
uv run pytest -q tests/unit/test_loop_review_service.py tests/integration/test_cli_loop_review.py
```

## Task 3 — require outcomes at all five Close transitions

Files:

- Modify `src/ai_sdlc/core/requirement_loop.py`
- Modify `src/ai_sdlc/core/design_contract_loop.py`
- Modify `src/ai_sdlc/core/implementation_loop.py`
- Modify `src/ai_sdlc/core/frontend_evidence_loop.py`
- Modify `src/ai_sdlc/core/pr_review_service.py`
- Modify the corresponding five unit test files

For every Loop add concrete tests proving:

- matching digest without outcome still blocks;
- failed/actionable/stale/wrong-role outcome blocks;
- advisory-only or clean current outcome allows Close;
- validation runs again in the Close write epoch.

Replace digest-only Close calls with `validate_review_outcome_for_close`. Do not add a generic
parallel Close authority and do not write findings to an issue store.

Verify the five focused test files together.

## Task 4 — shared executable quality evidence

Files:

- Create `src/ai_sdlc/core/quality_command.py`
- Create `tests/unit/test_quality_command.py`

RED tests cover direct argv execution, nonzero exit, timeout, cwd escape, source mutation, untracked
content, clean-repo identity, bounded output and shell-metacharacter non-interpretation.

Implement `QualityCommandResult`, deterministic source identity and `subprocess.run(argv,
shell=False)`. Inherit the caller environment so enterprise proxies, package managers, credentials,
private dependencies and `GIT_SSH_COMMAND` keep working. Remove only repository-redirection variables:
`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`,
`GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`, and `GIT_REPLACE_REF_BASE`. Do not remove proxy,
SSH, credential, package-manager, or ordinary Git configuration variables. Set a timeout and never
import Telemetry.

Add regressions proving `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, package-manager variables and
`GIT_SSH_COMMAND` reach the child unchanged, while each repository-redirection variable is absent and
cannot change the repository whose source digest is being verified.

## Task 5 — Implementation executable evidence

Files:

- Modify `src/ai_sdlc/core/implementation_models.py`
- Modify `src/ai_sdlc/core/implementation_loop.py`
- Modify `src/ai_sdlc/cli/loop_cmd.py`
- Modify `tests/unit/test_implementation_loop.py`
- Modify `tests/integration/test_cli_loop.py`

Add `loop implementation verify --task-id ... --cwd ... -- <argv...>`. Keep old strings readable but
never sufficient. Require a successful current-source result for each required DONE task and include the
typed evidence in the dynamic expert input.

## Task 6 — Local PR exact staged/commit tree

Files:

- Modify source snapshot, PR review models/source/pack/provider/service and CLI modules
- Modify source snapshot, provider, service and CLI PR review tests

RED tests cover:

- `git write-tree` stored in snapshot and review pack;
- missing reviewer and any reviewer mutation remain blockers;
- extra staged/unstaged file makes review stale;
- exact manual commit can Close;
- different commit tree or wrong/multiple parent blocks;
- hook-modified tree blocks without history rollback;
- old evidence strings cannot satisfy Close.

Make `local-staged` the default delivery source. Add `pr-review verify` using Task 4. Add explicit
`pr-review commit --message` with no auto-add and normal hooks. Persist enough pack identity so exact
manual commit does not self-invalidate the review input. Keep range/patch/SCM diagnostic-only.

## Task 7 — read-only five-Loop router

Files:

- Create `src/ai_sdlc/core/loop_router.py`
- Modify `src/ai_sdlc/cli/run_cmd.py`
- Create `tests/unit/test_loop_router.py`
- Modify `tests/integration/test_cli_run.py`
- Modify `tests/architecture/test_review_kernel_cutover.py`

RED tests cover no Loop, one active, predecessor chain, unrelated active Loops and all closed. Every
case must preserve HEAD, index, worktree and checkpoint bytes. Remove imports/construction of
`SDLCRunner`; retain legacy option parsing but return a migration blocker instead of executing it.

## Task 8 — automatic Agent flow and anti-bloat boundary

Files:

- Modify Codex, Claude Code, Cursor and VS Code adapter guidance
- Modify product contract, v2 migration and self-development docs
- Modify adapter and architecture tests

Each adapter must instruct the host Agent to consume `expert_roles`, use one independent context per
role, call `review-record`, stop after round 2, and not ask the human to trigger experts manually.

Architecture tests must prove:

- review kernel has no writes/subprocess/network/store;
- outcome models have no authority/certificate/session/quorum/score fields;
- outcome files are limited to two fixed names in existing Loop directories;
- slimming is absent from status/Close/commit paths;
- retired review subsystem inventory remains physically absent.

## Task 9 — integrated acceptance and bounded review

Run focused PR2 tests, then:

```text
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run ai-sdlc verify constraints
git diff --check
```

Run existing Node, Java and Python project fixtures through the five Loop route, executable verification
and Local PR close. Confirm no AI-SDLC self-release rule leaks into them.

Freeze HEAD/tree/base/path set/dirty state. Start two independent local reviewers on the same frozen
candidate: product-boundary/correctness and delivery/regression. Permit one focused repair for
PR-caused Critical/Important only; rerun focused plus full verification. Then push and open a Draft PR.

## Stop condition

Stop immediately when the above acceptance passes. Do not add update/rules/status improvements,
Program/Telemetry deletion, release work, or any fifth governance subsystem.
