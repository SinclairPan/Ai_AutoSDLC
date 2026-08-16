# Review Kernel Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax (`- [ ]`) for tracking.

**Goal:** Replace the oversized stage-review/Lean/release-authority subsystems with exactly three retained product capabilities: an independent local pre-commit reviewer, advisory-only code-slimming feedback, and one ephemeral dynamic-expert review of each of the five existing Loop results.

**Architecture:** Keep the five existing Loop reducers/writers and the existing local PR provider isolation boundary. Add one pure, read-only review-input builder plus adapter guidance: the active AI agent reads a stage's already-written substantive result, creates at most two fresh read-only expert contexts, resolves findings in the normal writer workflow, rechecks that the source result did not drift, and only then invokes the existing close command. No review result, digest, PASS token, session, history, pointer, ledger, or certificate is persisted or consumed by close.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, pytest, existing Loop artifacts, existing AI-SDLC adapter guidance, Git, Ruff.

## Global Constraints

- Implement in two PRs only: PR1 performs an atomic runtime cutover and must pass the complete existing test suite; PR2 physically deletes the now-unreachable legacy implementation, tests, and documentation and must also pass the complete suite.
- Start PR1 from protected `main` at implementation time. Start PR2 only from merged PR1.
- Do not revive or merge WorkItem 010. Do not add workflow dispatches, remote authority calls, release/tag/assets/certificate operations, databases, stores, pointers, leases, background workers, or required checks.
- `review_kernel.py` is pure and read-only. It may parse existing artifacts and compute an ephemeral digest for before/after drift detection in the active conversation; it may not import `LoopArtifactStore`, a Loop writer/store, subprocess/provider/model code, or network code.
- There is no `review_kernel_store.py`, no `review-input.json`, no `review-execution.json`, and no `prepare -> record -> close` protocol. Close functions never load a review output or digest.
- `ReviewExecution` exists only in memory/conversation. It contains execution status, ephemeral roles, and findings; it contains no input digest, verdict, pass, close, authorization, certificate, session, score, or reusable identity.
- The active AI adapter performs expert orchestration automatically. The user is not asked to select reviewers, manage artifacts, approve a panel, or adjudicate ordinary reviewer disagreement.
- Expert findings are applied by the normal stage writer/implementation agent to the substantive result, then the normal Loop check/reducer is rerun. Experts never edit files or write Loop state. On expert timeout/unavailability/invalid output, the active agent does not call close and reports the existing Loop as still needing review; it never treats failure as zero findings.
- Code-slimming output is always advisory. Missing analysis, analysis failure, thresholds, or accepted complexity cannot block a Loop, commit, PR, or release.
- Local PR review retains exact reviewed HEAD/index/staged-diff binding, an independent subprocess/context, and before/after no-mutation checks. Local PR Review's cross-stage expert review is terminal and is never reviewed again.
- PR1 deletes the protected-base test baseline/lineage authority and its negative-delta/preflight protocol. Standard CI continues to bind each candidate cell's actual collection to its own JUnit result and to require every configured cell; intentional removal of retired tests is allowed without a lineage mapping.
- Each PR gets one three-expert review and at most one focused repair re-review. A second unresolved Critical/Important/regression is Delivery No-Go; do not broaden the PR or ask the user to referee ordinary review findings.
- Use repository source commands (`uv run ...`). Never use a globally installed package as the controlling implementation.

---

## PR1 — Atomic Runtime Cutover

### Task 0: Replace monotonic test lineage with candidate-local execution evidence

**Files:**

- Modify: `scripts/ci_static_assurance.py`
- Modify: `tests/unit/test_ci_static_assurance.py`
- Modify: `.github/workflows/compatibility-gate.yml`
- Modify: `tests/integration/test_github_workflows.py`
- Delete: `.github/ci/test-baseline.json`
- Delete: `.github/ci/test-lineage.json`

- [ ] Add RED behavior tests proving that a candidate may intentionally remove collected node IDs without any rename mapping, while duplicate cells, a missing cell, a manifest/commit mismatch, duplicate or missing JUnit cases, failures, errors, and missing evidence still fail.
- [ ] Add a RED workflow test requiring no protected-base checkout, `authority-check`, `baseline-preflight`, baseline/lineage artifact, `validate-lineage`, `verify-transition`, or `decide-mode`; require candidate-local `collect`, `cell-evidence`, and `aggregate` plus the unchanged fast suite, OS/Python matrix, and Windows shell smoke.
- [ ] Run the two RED files and confirm the failures are caused by the existing monotonic authority:

```powershell
uv run pytest -q tests/unit/test_ci_static_assurance.py tests/integration/test_github_workflows.py
```

- [ ] Remove baseline/lineage schemas, builders, transition/preflight/mode commands, protected-base comparison, negative-delta enforcement, and allowed-skip authority. Keep only candidate-local collection manifest construction, JUnit cell evidence, and complete-cell aggregation; ordinary pytest skips remain recorded but are not converted into a second policy store.
- [ ] Simplify Compatibility Gate so draft PRs run the fixed fast gate and Ready/push/merge-group/scheduled/manual runs execute the existing full matrix. Full aggregation must use only candidate code and candidate artifacts and must not read a trusted-base checkout.
- [ ] Run GREEN and the workflow parser tests:

```powershell
uv run pytest -q tests/unit/test_ci_static_assurance.py tests/integration/test_github_workflows.py
uv run ruff check scripts/ci_static_assurance.py tests/unit/test_ci_static_assurance.py tests/integration/test_github_workflows.py
```

- [ ] Commit the ordinary-CI cutover before any product deletion:

```powershell
git add -A scripts/ci_static_assurance.py tests/unit/test_ci_static_assurance.py .github/workflows/compatibility-gate.yml tests/integration/test_github_workflows.py .github/ci/test-baseline.json .github/ci/test-lineage.json
git commit -m "refactor(ci): allow intentional test removal"
```

### Task 1: Freeze the minimal product boundary with failing tests

**Files:**

- Create: `tests/unit/test_review_kernel.py`
- Create: `tests/architecture/test_review_kernel_boundaries.py`
- Modify: `tests/unit/test_pr_review_provider.py`
- Modify: `tests/unit/test_requirement_loop.py`
- Modify: `tests/unit/test_design_contract_loop.py`
- Modify: `tests/unit/test_implementation_loop.py`
- Modify: `tests/unit/test_frontend_evidence_loop.py`
- Modify: `tests/unit/test_pr_review_service.py`
- Modify: `tests/integration/test_cli_loop.py`
- Modify: `tests/unit/test_ide_adapter.py`

- [ ] Add model tests for exactly these pure public values:

```python
ReviewInput(
    loop_id="loop-1",
    loop_type="requirement",
    round_number=1,
    input_digest="a" * 64,
    artifact_paths=[".ai-sdlc/loops/requirement/loop-1/requirement-brief.md"],
    upstream_context_paths=[],
    risk_signals=["public-api"],
    role_brief="Choose one primary expert and at most one cross-risk expert.",
)

ReviewFinding(
    severity="important",
    role="API compatibility reviewer",
    location="spec.md:42",
    summary="The response contract removes a required field.",
    recommendation="Restore the field or update the accepted contract.",
)

ReviewExecution(
    status="completed",
    roles=["API compatibility reviewer"],
    role_reasons={"API compatibility reviewer": "The result changes a public schema."},
    findings=[],
)
```

- [ ] Assert `ReviewExecution` rejects more than two roles, duplicate roles, findings attributed to absent roles, `completed` without a role, `failed` without `failure_kind/failure_reason`, and every extra field including `input_digest`, `verdict`, `passed`, `closed`, `certificate`, `session_id`, `quorum`, and `score`.
- [ ] Add five subject-mapping tests using the repository's real current artifacts:
  - Requirement: `requirement-brief.md` plus `acceptance-checklist.md`, never `requirement-freeze.json`;
  - Design Contract: `design-contract-report.json` and `.md`, never the close artifact;
  - Implementation: `implementation-report.json`/`.md` plus `verification-evidence.json`;
  - Frontend Evidence: `frontend-evidence-snapshot.json` plus report JSON/Markdown;
  - Local PR Review: the pre-close Review Pack, Findings, `resolution.yaml`, verification evidence, and current HEAD/index/staged diff; never `final-report.md`, which is generated only by the existing close writer after review, and never a second `LoopRun`.
- [ ] Assert the read-only input builder never creates/modifies a file and that calling it twice on unchanged artifacts gives the same digest; changing an artifact changes the digest; its own stdout/output is never part of the digest.
- [ ] Preserve/add local reviewer tests for HEAD drift, staged-diff drift, timeout followed by mutation, ignored-file mutation, reviewer-created commit, and an unrelated pre-existing unstaged file excluded from the staged result identity.
- [ ] Add architecture tests that fail if `review_kernel.py` imports any writer/store, `LoopArtifactStore`, stage-specific Loop module, `stage_review`, `lean_code`, subprocess/provider/model/network code, or exposes persistence/close/authorization symbols.
- [ ] Add adapter-guidance tests requiring all five stages, fresh independent expert contexts, at most two roles, automatic handling without user review, one re-review maximum, drift recheck, failure-not-clean behavior, and Local PR terminal behavior.
- [ ] Run the actual files containing the new RED tests:

```powershell
uv run pytest -q tests/unit/test_review_kernel.py tests/architecture/test_review_kernel_boundaries.py tests/unit/test_pr_review_provider.py tests/unit/test_requirement_loop.py tests/unit/test_design_contract_loop.py tests/unit/test_implementation_loop.py tests/unit/test_frontend_evidence_loop.py tests/unit/test_pr_review_service.py tests/integration/test_cli_loop.py tests/unit/test_ide_adapter.py
```

- [ ] Commit the RED contracts:

```powershell
git add tests/unit/test_review_kernel.py tests/architecture/test_review_kernel_boundaries.py tests/unit/test_pr_review_provider.py tests/unit/test_requirement_loop.py tests/unit/test_design_contract_loop.py tests/unit/test_implementation_loop.py tests/unit/test_frontend_evidence_loop.py tests/unit/test_pr_review_service.py tests/integration/test_cli_loop.py tests/unit/test_ide_adapter.py
git commit -m "test: freeze minimal review product contracts"
```

### Task 2: Implement one pure read-only review-input command

**Files:**

- Create: `src/ai_sdlc/core/review_kernel.py`
- Create: `src/ai_sdlc/cli/loop_review_cmd.py`
- Modify: `src/ai_sdlc/cli/loop_cmd.py`
- Modify: `tests/unit/test_review_kernel.py`
- Modify: `tests/integration/test_cli_loop.py`

- [ ] Implement `ReviewInput`, `ReviewFinding`, and in-memory-only `ReviewExecution` in `review_kernel.py` with `ConfigDict(extra="forbid")` and Task 1 validation.
- [ ] Implement pure `build_review_input(root, *, loop_id, loop_type, round_number, artifact_paths, upstream_context_paths, risk_signals) -> ReviewInput`. It receives already-resolved paths, reads stable regular files, and computes SHA-256 over canonical JSON containing Loop type/id/round plus repository-relative path, size, and raw-byte SHA-256. Reject missing, escaping, duplicate, unstable, or non-regular paths. It does not know how a stage stores or closes a Loop.
- [ ] Implement pure `merge_expert_findings(executions) -> ReviewExecution` to deduplicate identical role/location/summary findings while preserving severity and evidence. It must not decide clean/pass/close or mutate any artifact.
- [ ] Add one read-only command only:

```text
ai-sdlc loop review --type <loop-type> --loop-id <loop-id> --json
ai-sdlc loop review --type <loop-type> --loop-id <loop-id> --expect-digest <sha256> --json
```

`loop_review_cmd.py`, not the kernel, resolves the existing current `LoopRun`/`ReviewRun` and selects the Task 1 paths using the already-existing read APIs. The second form only re-reads those substantive artifacts and exits nonzero on drift. Neither form accepts findings, writes output files, records completion, changes Loop state, invokes a provider/model, or authorizes close.
- [ ] Prove Local PR Review reads the existing `.ai-sdlc/reviews/pr/<review-id>` `ReviewRun` and never creates `.ai-sdlc/loops/local-pr-review/<loop-id>`.
- [ ] Run:

```powershell
uv run pytest -q tests/unit/test_review_kernel.py tests/integration/test_cli_loop.py tests/architecture/test_review_kernel_boundaries.py
uv run ruff check src/ai_sdlc/core/review_kernel.py src/ai_sdlc/cli/loop_review_cmd.py src/ai_sdlc/cli/loop_cmd.py tests/unit/test_review_kernel.py tests/integration/test_cli_loop.py tests/architecture/test_review_kernel_boundaries.py
```

- [ ] Commit:

```powershell
git add src/ai_sdlc/core/review_kernel.py src/ai_sdlc/cli/loop_review_cmd.py src/ai_sdlc/cli/loop_cmd.py tests/unit/test_review_kernel.py tests/integration/test_cli_loop.py tests/architecture/test_review_kernel_boundaries.py
git commit -m "feat: expose read-only loop review inputs"
```

### Task 3: Make dynamic experts an automatic adapter behavior, not a new runtime protocol

**Files:**

- Modify: `src/ai_sdlc/adapters/codex/AI-SDLC.md`
- Modify: `src/ai_sdlc/adapters/claude_code/AI-SDLC.md`
- Modify: `src/ai_sdlc/adapters/cursor/rules/ai-sdlc.md`
- Modify: `src/ai_sdlc/adapters/vscode/AI-SDLC.md`
- Modify: `src/ai_sdlc/integrations/ide_adapter.py`
- Modify: `tests/unit/test_ide_adapter.py`
- Modify: `tests/integration/test_cli_ide_adapter.py`

- [ ] Install the same bounded orchestration rule for every supported adapter:
  1. when a substantive stage result is ready, run the read-only `loop review` command;
  2. choose one primary expert from the actual result content and at most one cross-risk expert;
  3. run each expert in a fresh read-only context separate from the result writer;
  4. if findings exist, the normal writer fixes the underlying result and reruns the normal Loop check; experts never edit;
  5. invoke the existing close/freeze command with the reviewed `--loop-id` and `--expect-review-digest`; Local PR Review also passes `--review-id`, so the close process itself rebuilds and binds the reviewed input;
  6. if the current identity or digest changed, discard the old conclusion and review once more; if experts fail, do not call close and report that the existing Loop still needs review;
  7. only after no actionable finding may the active agent invoke the guarded existing close command;
  8. after Local PR Review's one cross-stage expert pass, stop—no reviewer-of-reviewer.
- [ ] State explicitly that ordinary users remain in the AI conversation and are not asked to choose experts, run commands, inspect review files, or approve the review. Experts reconcile ordinary disagreement in one bounded re-review; only a product-boundary change returns to the user.
- [ ] Do not add a provider registry, model selector, role catalog/profile, score, quorum, session, finding history, optimizer, or persistence instruction.
- [ ] Test installed/generated adapter files byte-for-byte for the bounded rule and assert they contain no prepare/record/store/certificate/attestation/panel/quorum language.
- [ ] Run:

```powershell
uv run pytest -q tests/unit/test_ide_adapter.py tests/integration/test_cli_ide_adapter.py
uv run ruff check src/ai_sdlc/integrations/ide_adapter.py tests/unit/test_ide_adapter.py tests/integration/test_cli_ide_adapter.py
```

- [ ] Commit:

```powershell
git add src/ai_sdlc/adapters/codex/AI-SDLC.md src/ai_sdlc/adapters/claude_code/AI-SDLC.md src/ai_sdlc/adapters/cursor/rules/ai-sdlc.md src/ai_sdlc/adapters/vscode/AI-SDLC.md src/ai_sdlc/integrations/ide_adapter.py tests/unit/test_ide_adapter.py tests/integration/test_cli_ide_adapter.py
git commit -m "feat: run bounded experts in the active agent"
```

### Task 4: Return Requirement, Design, Implementation, and Frontend close authority to their base Loop writers

**Files:**

- Modify: `src/ai_sdlc/core/requirement_loop.py`
- Modify: `src/ai_sdlc/core/design_contract_loop.py`
- Modify: `src/ai_sdlc/core/implementation_loop.py`
- Modify: `src/ai_sdlc/core/frontend_evidence_loop.py`
- Modify: `src/ai_sdlc/cli/loop_cmd.py`
- Modify: `tests/unit/test_requirement_loop.py`
- Modify: `tests/unit/test_design_contract_loop.py`
- Modify: `tests/unit/test_implementation_loop.py`
- Modify: `tests/unit/test_frontend_evidence_loop.py`
- Modify: `tests/integration/test_cli_loop.py`

- [ ] In Requirement, remove scope-authority preparation/commit and `execute_stage_close`; keep the existing intake/brief/questions/checklist writes, acceptance checks, `requirement-freeze.json`, current pointer, base reducer/writer, and Design Contract next action.
- [ ] In Design Contract, remove design-close authority/transaction/enforce/shadow preparation/commit and `execute_stage_close`; keep the existing input/coverage/report/close/current-pointer artifacts and Requirement prerequisite.
- [ ] In Implementation, remove stage-review close and blocking Lean close from close eligibility; keep tasks/progress/verification/report/close/current pointer and frontend routing.
- [ ] In Frontend Evidence, remove stage-review close/certificate authority while retaining ordinary snapshot/report/close/current pointer, browser evidence, and skip behavior.
- [ ] Do not add a review-completed field or make any serialized review output a close precondition. The automatic adapter behavior in Task 3 is the review workflow; these reducers remain ordinary deterministic state writers.
- [ ] Add tests proving copied old stage-review/certificate/authority artifacts cannot change any Loop transition and that each base writer closes from its own current substantive result exactly as before the authority subsystem was layered on.
- [ ] Run:

```powershell
uv run pytest -q tests/unit/test_requirement_loop.py tests/unit/test_design_contract_loop.py tests/unit/test_implementation_loop.py tests/unit/test_frontend_evidence_loop.py tests/integration/test_cli_loop.py
rg -n "scope_authority|design_close_authority|execute_stage_close|validate_lean_close" src/ai_sdlc/core/requirement_loop.py src/ai_sdlc/core/design_contract_loop.py src/ai_sdlc/core/implementation_loop.py src/ai_sdlc/core/frontend_evidence_loop.py
```

The `rg` command must return no matches.

- [ ] Commit:

```powershell
git add src/ai_sdlc/core/requirement_loop.py src/ai_sdlc/core/design_contract_loop.py src/ai_sdlc/core/implementation_loop.py src/ai_sdlc/core/frontend_evidence_loop.py src/ai_sdlc/cli/loop_cmd.py tests/unit/test_requirement_loop.py tests/unit/test_design_contract_loop.py tests/unit/test_implementation_loop.py tests/unit/test_frontend_evidence_loop.py tests/integration/test_cli_loop.py
git commit -m "refactor: restore base loop close writers"
```

### Task 5: Replace blocking Lean governance with one advisory list

**Files:**

- Create: `src/ai_sdlc/core/slimming_advice.py`
- Modify: `src/ai_sdlc/core/implementation_models.py`
- Modify: `src/ai_sdlc/core/implementation_loop.py`
- Modify: `src/ai_sdlc/core/implementation_store.py`
- Modify: `src/ai_sdlc/core/close_check.py`
- Modify: `src/ai_sdlc/core/pr_review_pack.py`
- Modify: `src/ai_sdlc/cli/loop_cmd.py`
- Modify: `tests/unit/test_implementation_loop.py`
- Modify: `tests/unit/test_close_check.py`
- Modify: `tests/unit/test_pr_review_pack.py`
- Create: `tests/unit/test_slimming_advice.py`
- Delete: `tests/integration/test_cli_lean_code.py`
- Modify: `tests/integration/test_cli_rules.py`

- [ ] Implement a single-pass `collect_slimming_advice(paths) -> list[SlimmingAdvice]` covering only file length, function length, obvious same-file duplication, unnecessary single-caller wrapper, and mixed-responsibility hints. It returns advice text/evidence only; no status, verdict, exception, waiver, receipt, history, or policy lifecycle.
- [ ] Add `advisories: list[str]` to the existing `ImplementationReport` and command result. Collect advice when writing the report; never include it in blockers, blocker count, status, next action, close eligibility, Local PR verdict, or release checks.
- [ ] Remove automatic `lean-code` quality-profile injection from `implementation_store.py`; remove Lean closure checks from `close_check.py`; remove Lean capture/binding from `pr_review_pack.py`; remove Lean runtime imports from all three.
- [ ] Remove `lean-check`, `lean-verify`, `lean-regression`, and Lean No-Go commands/options/imports from `loop implementation` and delete the CLI integration test for those retired commands.
- [ ] Keep old `LoopPolicyProfile` Lean fields inert during PR1 so legacy unit tests/configs still parse; remove the fields and compatibility loader in PR2 after legacy modules/tests are deleted.
- [ ] Add explicit tests that excessive size, advice collector failure, and accepted complexity all leave close eligibility unchanged; a genuine correctness/security/authorization finding remains a normal defect independent of slimming.
- [ ] Run:

```powershell
uv run pytest -q tests/unit/test_slimming_advice.py tests/unit/test_implementation_loop.py tests/unit/test_close_check.py tests/unit/test_pr_review_pack.py tests/integration/test_cli_loop.py tests/integration/test_cli_rules.py
uv run ai-sdlc loop implementation --help
rg -n "lean-check|lean-verify|lean-regression|lean-no-go" src/ai_sdlc/cli/loop_cmd.py
```

The help output and `rg` result must contain no retired commands.

- [ ] Commit:

```powershell
git add -A src/ai_sdlc/core/slimming_advice.py src/ai_sdlc/core/implementation_models.py src/ai_sdlc/core/implementation_loop.py src/ai_sdlc/core/implementation_store.py src/ai_sdlc/core/close_check.py src/ai_sdlc/core/pr_review_pack.py src/ai_sdlc/cli/loop_cmd.py tests/unit/test_slimming_advice.py tests/unit/test_implementation_loop.py tests/unit/test_close_check.py tests/unit/test_pr_review_pack.py tests/integration/test_cli_lean_code.py tests/integration/test_cli_rules.py
git commit -m "refactor: make code slimming advisory only"
```

### Task 6: Preserve the independent local reviewer and remove its authority layer

**Files:**

- Modify: `src/ai_sdlc/core/pr_review_models.py`
- Modify: `src/ai_sdlc/core/pr_review_provider.py`
- Modify: `src/ai_sdlc/core/pr_review_service.py`
- Modify: `src/ai_sdlc/cli/pr_review_cmd.py`
- Modify: `src/ai_sdlc/core/loop_status.py`
- Modify: `tests/unit/test_pr_review_provider.py`
- Modify: `tests/unit/test_pr_review_service.py`
- Modify: `tests/integration/test_cli_pr_review.py`
- Delete: `tests/integration/test_stage_review_attestation.py`

- [ ] Keep `ReviewRun` and `.ai-sdlc/reviews/pr/<review-id>` as the existing Local PR workflow; do not migrate or duplicate it into a generic `LoopRun`.
- [ ] Retain exact staged identity and safeguards in `pr_review_provider.py`: reviewed HEAD, index, staged diff, independent subprocess/context, dirty scope, timeout, and before/after HEAD/index/worktree mutation guard.
- [ ] Remove stage-review session/panel/binding/quorum/certificate/attestation and Lean fields from new reports/service behavior. If removing legacy model fields would break untouched PR1 tests, leave the fields parse-only and unused until Task 9; do not write new values.
- [ ] Remove `pr-review attest`, attestation export, CI certificate export, stale attestation cleanup, attestation locks, and `execute_stage_close` service calls. Keep only commands supporting the retained local review (`doctor`, `start`, `status`, `fix`, `rerun`, `close`).
- [ ] Make `loop_status.py` report Local PR status from the existing `ReviewRun` only. Before close, the active adapter performs one terminal cross-stage expert pass over Review Pack, Findings, `resolution.yaml`, verification evidence, and current HEAD/index/staged diff; the existing close writer then generates `final-report.md`. No state field records that pass.
- [ ] Test that a reviewer cannot modify tracked, ignored, staged, or commit state; pre-existing unstaged content is outside staged identity but cannot change during execution; old copied attestation/certificate files never affect close/status; CLI help omits `attest`.
- [ ] Run:

```powershell
uv run pytest -q tests/unit/test_pr_review_provider.py tests/unit/test_pr_review_service.py tests/integration/test_cli_pr_review.py
uv run ai-sdlc pr-review --help
rg -n "attest|certificate|execute_stage_close|lean_code" src/ai_sdlc/core/pr_review_provider.py src/ai_sdlc/core/pr_review_service.py src/ai_sdlc/cli/pr_review_cmd.py
```

- [ ] Commit:

```powershell
git add -A src/ai_sdlc/core/pr_review_models.py src/ai_sdlc/core/pr_review_provider.py src/ai_sdlc/core/pr_review_service.py src/ai_sdlc/cli/pr_review_cmd.py src/ai_sdlc/core/loop_status.py tests/unit/test_pr_review_provider.py tests/unit/test_pr_review_service.py tests/integration/test_cli_pr_review.py tests/integration/test_stage_review_attestation.py
git commit -m "refactor: keep local review without close authority"
```

### Task 7: Remove public legacy entry points, fix every external caller, and make PR1 fully green

**Files:**

- Delete: `src/ai_sdlc/cli/activation_cmd.py`
- Delete: `src/ai_sdlc/cli/stage_review_guidance.py`
- Delete: `src/ai_sdlc/cli/optimization_hooks.py`
- Modify: `src/ai_sdlc/cli/main.py`
- Modify: `src/ai_sdlc/cli/run_cmd.py`
- Modify: `src/ai_sdlc/cli/verify_cmd.py`
- Modify: `src/ai_sdlc/core/verify_constraints.py`
- Modify: `.github/workflows/compatibility-gate.yml`
- Modify: `.github/workflows/cross-platform-core.yml`
- Delete: `.github/workflows/activation-evidence.yml`
- Delete: `.github/workflows/ci-certificate.yml`
- Delete: `.github/workflows/reviewer-isolation.yml`
- Delete: `tests/integration/test_cli_activation.py`
- Delete: `tests/integration/test_canonical_stage_review_executor.py`
- Delete: `tests/integration/test_stage_review_shadow.py`
- Delete: `tests/integration/test_stage_review_shadow_planning.py`
- Delete: `tests/e2e/stage_review/test_codex_permission_profile_backend.py`
- Delete: `tests/e2e/test_clean_user_stage_gate.py`
- Delete: `tests/unit/test_cli_stage_review_guidance.py`
- Delete: `tests/unit/test_cli_optimization_hooks.py`
- Modify: `tests/integration/test_cli_run.py`
- Modify: `tests/integration/test_cli_verify_constraints.py`
- Modify: `tests/integration/test_github_workflows.py`
- Create: `tests/architecture/test_review_kernel_cutover.py`

- [ ] Remove activation registration, stage-certificate verification commands, stage-review CLI guidance, and optimization hooks. Remove all callers/imports before deleting the modules, including `tests/unit/test_cli_stage_review_guidance.py` and `tests/unit/test_cli_optimization_hooks.py`.
- [ ] Remove `verify_constraints` requirements for stage-review history, attestation, certificate, activation, sealed transition, or WorkItem 010. Keep ordinary repository, docs, packaging, Loop, and release consistency checks.
- [ ] Remove workflows whose sole purpose is activation/certificate/reviewer-isolation authority. Update ordinary workflow tests and selectors without weakening the existing full test jobs.
- [ ] Delete integration/e2e tests that assert removed public behavior. Keep all legacy unit tests whose implementation remains in PR1; they must continue passing until PR2 removes both code and tests.
- [ ] Add a cutover architecture test asserting six `execute_stage_close` calls are gone, external runtime imports from `stage_review`/`lean_code_*` are zero outside directories scheduled for PR2, scope/design authority commits and blocking Lean close are zero, CLI help omits retired commands, and no new store/pointer/workflow/network authority exists.
- [ ] Run the full PR1 suite, not a selected substitute:

```powershell
uv run pytest -q
uv run ai-sdlc verify constraints
uv run ruff check .
git diff --check
if (Test-Path .github/ci/test-baseline.json) { throw "legacy test baseline still exists" }
if (Test-Path .github/ci/test-lineage.json) { throw "legacy test lineage still exists" }
uv run ai-sdlc --help
uv run ai-sdlc loop --help
uv run ai-sdlc pr-review --help
```

- [ ] Record LOC/file/delete-add metrics only in PR text:

```powershell
git diff --stat origin/main...HEAD
git diff --numstat origin/main...HEAD
```

- [ ] Commit:

```powershell
git add -A
git commit -m "refactor: cut over to the minimal review kernel"
```

- [ ] Freeze exact PR1 HEAD/tree and request three independent read-only reviewers (product boundary, architecture/dependency, delivery/regression). Require unanimous Ready and allow one focused repair/re-review only; otherwise declare Delivery No-Go.

---

## PR2 — Physical Deletion and Distribution Cleanup

### Task 8: Freeze exact deletion paths and anti-revival architecture tests

**Files:**

- Create: `tests/architecture/review_kernel_removed_paths.txt`
- Create: `tests/architecture/test_removed_review_subsystems.py`
- Create: `tests/integration/test_legacy_review_artifacts_ignored.py`

- [ ] Generate and manually review `review_kernel_removed_paths.txt` from PR1's tracked tree. It must list every exact path under:
  - `src/ai_sdlc/core/stage_review/**`;
  - root `src/ai_sdlc/core/lean_code*.py`;
  - stage-review/Lean-only unit tests and fixtures;
  - retired scripts, policies, rules, schemas, templates, package-data copies, and docs.
- [ ] Include these external modules if PR1 leaves no retained caller: `scope_authority_store.py`, `design_close_artifact_verification.py`, `design_close_authority_store.py`, `design_close_enforce_authority.py`, `design_close_enforce_evidence.py`, `design_close_shadow_authority.py`, `design_scope_authority_transition.py`, and `lean_code_reviewer_authority.py`.
- [ ] In `test_removed_review_subsystems.py`, assert exact path absence after deletion and AST/public-model boundaries that prevent renamed reintroduction: `review_kernel.py` cannot write/import stores, decide Loop status, run providers/models/network, persist roles/findings/history, or expose certificate/session/panel/quorum/score/authorization fields; slimming cannot affect blockers/status/close.
- [ ] Add a legacy-project test containing old session/certificate/Lean artifacts under `.ai-sdlc/`. Normal `status`/`run` must ignore them, never migrate/replay them, and emit at most one concise deprecation notice.
- [ ] Run RED before deletion:

```powershell
uv run pytest -q tests/architecture/test_removed_review_subsystems.py tests/integration/test_legacy_review_artifacts_ignored.py
```

- [ ] Commit:

```powershell
git add tests/architecture/review_kernel_removed_paths.txt tests/architecture/test_removed_review_subsystems.py tests/integration/test_legacy_review_artifacts_ignored.py
git commit -m "test: freeze removed review subsystem paths"
```

### Task 9: Delete the unreachable implementation and remove inert legacy fields

**Files:**

- Delete: every path in `tests/architecture/review_kernel_removed_paths.txt`
- Delete: `src/ai_sdlc/core/stage_review/`
- Delete: all root `src/ai_sdlc/core/lean_code*.py`
- Delete: `tests/unit/stage_review/`
- Delete: all listed `tests/unit/test_lean_code*.py`, fixtures, and test support modules
- Delete: `scripts/build_activation_evidence.py`
- Delete: `scripts/build_activation_quality_cell.py`
- Delete: `scripts/windows_lean_code_e2e.py`
- Delete: `.ai-sdlc/policies/stage-gate-activation-policy.json`
- Delete: `rules/lean-code.md`
- Delete: `src/ai_sdlc/rules/lean-code.md`
- Modify: `src/ai_sdlc/core/loop_models.py`
- Modify: `src/ai_sdlc/core/loop_policy.py`
- Modify: `src/ai_sdlc/core/pr_review_models.py`
- Modify: `src/ai_sdlc/rules/__init__.py`
- Modify: `packaging_backend.py`
- Modify: `pyproject.toml`
- Modify: `tests/unit/test_loop_policy.py`
- Modify: `tests/unit/test_packaging_backend.py`
- Modify: `tests/unit/test_packaging_config.py`

- [ ] Delete the frozen inventory with `git rm`; do not edit legacy files into stubs, shims, lazy imports, or disabled feature flags.
- [ ] Remove inert Lean fields from `LoopPolicyProfile` and `ReviewRun`. In `loop_policy.py`, strip only the known retired Lean keys before strict Pydantic validation and emit one deprecation notice; keep unknown unrelated keys fail-closed. Never write the retired keys back.
- [ ] Remove Lean rule registration and all package-data/build assertions for deleted rules/policies/schemas. Keep ordinary rules and the new adapter guidance.
- [ ] Remove stale exports/imports. If a frozen path still has a retained caller, stop and move the caller to a retained base module before deletion; do not add a compatibility import.
- [ ] Run:

```powershell
uv run pytest -q tests/architecture/test_removed_review_subsystems.py tests/integration/test_legacy_review_artifacts_ignored.py tests/unit/test_loop_policy.py tests/unit/test_packaging_backend.py tests/unit/test_packaging_config.py
uv run python -c "import ai_sdlc; import ai_sdlc.cli.main"
uv run ruff check src/ai_sdlc tests/architecture tests/integration/test_legacy_review_artifacts_ignored.py tests/unit/test_loop_policy.py tests/unit/test_packaging_backend.py tests/unit/test_packaging_config.py
git diff --check
```

- [ ] Commit:

```powershell
git add -A
git commit -m "refactor: delete legacy review authority subsystems"
```

### Task 10: Remove obsolete claims/templates, verify distribution, and close PR2

**Files:**

- Modify: `README.md`
- Modify: `docs/product-contract.md`
- Modify: `docs/pull-request-checklist.zh.md`
- Modify: `docs/framework-defect-backlog.zh-CN.md`
- Modify: `src/ai_sdlc/adapters/codex/AI-SDLC.md`
- Modify: `src/ai_sdlc/adapters/claude_code/AI-SDLC.md`
- Modify: `src/ai_sdlc/adapters/cursor/rules/ai-sdlc.md`
- Modify: `src/ai_sdlc/adapters/vscode/AI-SDLC.md`
- Modify: applicable tracked files under `templates/`, `packaging/`, and `.ai-sdlc/` listed by Task 8
- Modify: `tests/integration/test_cli_init.py`
- Modify: `tests/integration/test_cli_rules.py`
- Modify: `tests/integration/test_github_workflows.py`
- Create: `tests/integration/test_review_kernel_distribution.py`

- [ ] Rewrite product docs to name only the three retained capabilities and five Loop stages. Mark Shadow/Enforce, Activation, close certificates, review sessions/ledgers, offline optimization, resource governance, and blocking Lean governance as deleted—not postponed, disabled, or future work.
- [ ] Do not touch untracked or user-owned PRD/spec files that are absent from the implementation branch. If these tracked docs acquire concurrent edits before implementation, rebase and preserve unrelated content rather than overwriting it.
- [ ] Remove init/templates that install old rules, policies, commands, workflows, or artifacts. Fresh init installs only minimal agent guidance; re-init/upgrade leaves legacy artifacts untouched/ignored and prints at most one deprecation notice.
- [ ] Run `uv build`, unpack wheel and sdist in a temporary directory, and assert all frozen removed paths/public commands are absent while `review_kernel.py`, adapter guidance, base Loop modules, local PR reviewer, ordinary frontend evidence, ordinary CI, build, and release resources remain.
- [ ] Add a fresh-init integration test covering the installed review guidance without creating stage-review sessions, authority pointers, certificates, attestations, Lean receipts, or activation policies.
- [ ] Run the exact retained-capability suite, then the complete suite:

```powershell
uv run pytest -q tests/unit/test_review_kernel.py tests/unit/test_slimming_advice.py tests/unit/test_requirement_loop.py tests/unit/test_design_contract_loop.py tests/unit/test_implementation_loop.py tests/unit/test_frontend_evidence_loop.py tests/unit/test_pr_review_provider.py tests/unit/test_pr_review_service.py tests/integration/test_cli_loop.py tests/integration/test_cli_pr_review.py tests/integration/test_cli_init.py tests/integration/test_cli_rules.py tests/integration/test_github_workflows.py tests/integration/test_review_kernel_distribution.py tests/integration/test_legacy_review_artifacts_ignored.py tests/architecture/test_review_kernel_boundaries.py tests/architecture/test_review_kernel_cutover.py tests/architecture/test_removed_review_subsystems.py
uv run pytest -q
uv run ai-sdlc verify constraints
uv run ruff check .
uv build
git diff --check
git status --short
```

- [ ] Manually verify in one disposable initialized project:
  1. local pre-commit review runs in an independent read-only process/context and detects mutation;
  2. excessive code size produces advice but does not block close;
  3. each of the five substantive Loop results produces one fresh dynamic-expert review with at most two roles, while Local PR review terminates after its one cross-stage review.
- [ ] Classify all residual matches; only the approved deletion/design history under `docs/superpowers/`, anti-revival test fixtures, and unrelated ordinary wording may remain:

```powershell
rg -n "stage_review|StageReviewSession|FindingLedger|StageCloseCertificate|activation|attest|ci-certificate|whole-tree seal|lean-check|lean-verify|lean-regression|LeanNoGo|OfflineOptimization|ResourceGovernor|Holdout|quorum|panel solver" src tests .github scripts rules docs templates packaging .ai-sdlc
```

- [ ] Record net deletion as advisory PR evidence only:

```powershell
git diff --stat origin/main...HEAD
git diff --numstat origin/main...HEAD
```

- [ ] Freeze exact PR2 HEAD/tree and request the same three independent reviewers. Require unanimous Ready and at most one focused repair/re-review. Reviewers reconcile ordinary disagreement against the approved deletion contract; the user is contacted only if a proposed resolution changes one of the three retained product capabilities.
- [ ] After unanimous Ready, hand off the two reviewed PRs to the repository's normal push/PR/required-check/merge protocol. Do not dispatch a release or recreate WorkItem 010 as part of this cleanup.
