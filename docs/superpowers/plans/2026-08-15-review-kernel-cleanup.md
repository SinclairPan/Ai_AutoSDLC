# Review Kernel Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax (`- [ ]`) for tracking.

**Goal:** Replace the oversized stage-review/Lean/release-authority subsystems with the three approved product capabilities: one independent local pre-commit reviewer, advisory-only code-slimming feedback, and one ephemeral dynamic-expert review of each of the five existing Loop results.

**Architecture:** Keep `LoopRun`, `LoopRound`, each Loop's existing reducer/writer, and the existing local PR provider isolation boundary. Add a small review kernel that only freezes current-round inputs and validates expert findings; the active AI adapter selects and runs at most two fresh read-only expert contexts, then records one current-round result. Remove every parallel close authority, session, ledger, certificate, activation, optimization, and blocking Lean path instead of wrapping or disabling it.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, pytest, existing `LoopArtifactStore`, existing AI-SDLC adapter guidance, Git, Ruff.

## Global Constraints

- Implement in two PRs only: PR1 performs an atomic runtime cutover; PR2 physically deletes the unreachable legacy implementation and documentation.
- Begin PR1 from the protected `main` commit current at implementation start. Begin PR2 only from merged PR1.
- Do not revive or merge WorkItem 010. Do not add workflow dispatches, remote authority calls, release/tag/assets/certificate operations, databases, global stores, pointers, leases, background workers, or required checks.
- Review experts never decide `closed`, `passed`, `needs_fix`, or `needs_user`. They return only execution status, ephemeral roles, and findings; the existing Loop reducer remains the only state authority.
- Review storage is limited to fixed `review-input.json` and `review-execution.json` files inside the existing current Loop run directory. A new round overwrites these files; there is no review history, session, ledger, replay, or cross-round identity.
- Dynamic experts are orchestrated by the active AI adapter in fresh read-only contexts. Core Python must not hard-code Codex, Claude, a provider registry, model routing, quorum, scoring, or learning.
- Code-slimming output is always advisory. Missing analysis, analysis failure, thresholds, or an accepted exception cannot block a Loop, commit, PR, or release.
- Local PR review keeps exact reviewed HEAD/index/staged-diff binding, an independent subprocess/context, and before/after no-mutation checks. It emits `PASS` or findings only.
- Each PR gets one three-expert review and at most one focused repair re-review. A second unresolved Critical/Important/regression is Delivery No-Go; do not broaden the PR.
- Use repository source commands: `uv run ...`. Run narrow tests before broad tests. Never use the global installed package as the controlling implementation.

---

## PR1 — Atomic Runtime Cutover

### Task 1: Freeze the retained product contracts with failing tests

**Files:**

- Create: `tests/unit/test_review_kernel.py`
- Create: `tests/unit/test_review_kernel_store.py`
- Create: `tests/unit/test_slimming_advice.py`
- Create: `tests/architecture/test_review_kernel_boundaries.py`
- Modify: `tests/unit/test_pr_review_provider.py`
- Modify: `tests/unit/test_requirement_loop.py`
- Modify: `tests/unit/test_design_contract_loop.py`
- Modify: `tests/unit/test_implementation_loop.py`
- Modify: `tests/unit/test_frontend_evidence_loop.py`
- Modify: `tests/unit/test_pr_review_service.py`

- [ ] Add model-contract tests requiring exactly these public kernel models and fields:

```python
ReviewInput(
    loop_id="loop-1",
    loop_type="requirement",
    round_number=1,
    input_digest="a" * 64,
    artifact_paths=[".ai-sdlc/loops/requirement/loop-1/report.json"],
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
    input_digest="a" * 64,
    roles=["API compatibility reviewer"],
    role_reasons={"API compatibility reviewer": "The result changes a public schema."},
    findings=[],
)
```

- [ ] Assert `ReviewExecution` rejects more than two roles, duplicate roles, findings attributed to absent roles, `completed` without a role, `failed` without `failure_kind/failure_reason`, and every extra field including `verdict`, `passed`, `closed`, `certificate`, `session_id`, `quorum`, and `score`.
- [ ] Add five parametrized Loop tests showing that a current matching completed review is consumed by Requirement, Design Contract, Implementation, Frontend Evidence, and Local PR Review.
- [ ] Add five negative cases: missing review, stale digest, malformed review, explicit execution failure, and actionable findings. Assert each existing Loop reducer chooses its own existing non-closed state and that no kernel API writes Loop state.
- [ ] Preserve/add local reviewer tests for HEAD drift, staged-diff drift, timeout followed by mutation, ignored-file mutation, reviewer-created commit, and an unrelated pre-existing unstaged file excluded from the staged result identity.
- [ ] Add architecture tests that fail if `review_kernel*.py` imports `requirement_loop`, `design_contract_loop`, `implementation_loop`, `frontend_evidence_loop`, `pr_review_service`, any writer/store module, `stage_review`, or `lean_code`.
- [ ] Run the new tests and confirm RED because the minimal kernel/store do not exist and the old close authority is still reachable:

```powershell
uv run pytest -q tests/unit/test_review_kernel.py tests/unit/test_review_kernel_store.py tests/unit/test_slimming_advice.py tests/architecture/test_review_kernel_boundaries.py
```

- [ ] Commit the RED contract tests:

```powershell
git add tests/unit/test_review_kernel.py tests/unit/test_review_kernel_store.py tests/unit/test_slimming_advice.py tests/architecture/test_review_kernel_boundaries.py tests/unit/test_pr_review_provider.py tests/unit/test_requirement_loop.py tests/unit/test_design_contract_loop.py tests/unit/test_implementation_loop.py tests/unit/test_frontend_evidence_loop.py tests/unit/test_pr_review_service.py
git commit -m "test: freeze minimal review product contracts"
```

### Task 2: Implement the minimal review models, digest, and current-round storage

**Files:**

- Create: `src/ai_sdlc/core/review_kernel.py`
- Create: `src/ai_sdlc/core/review_kernel_store.py`
- Modify: `src/ai_sdlc/core/loop_artifacts.py`
- Modify: `tests/unit/test_review_kernel.py`
- Modify: `tests/unit/test_review_kernel_store.py`

- [ ] Implement `ReviewInput`, `ReviewFinding`, and `ReviewExecution` in `review_kernel.py` using `ConfigDict(extra="forbid")` and the validation rules from Task 1. Use only `critical`, `important`, `minor`, and `advisory` severities; the model must not expose a verdict.
- [ ] Implement pure `build_review_input(...)` and `compute_review_input_digest(...)`. The digest is SHA-256 over canonical JSON containing Loop type/id/round plus repository-relative artifact path, size, and raw-byte SHA-256 for the current result and explicitly required upstream context. Reject missing, non-regular, escaping, unstable, or duplicate paths.
- [ ] Implement fixed current-run paths in `review_kernel_store.py`:

```python
def review_input_path(root: Path, loop_run: LoopRun) -> Path:
    return LoopArtifactStore(root).loop_run_dir(
        loop_run.loop_id,
        loop_type=str(loop_run.loop_type),
    ) / "review-input.json"

def review_execution_path(root: Path, loop_run: LoopRun) -> Path:
    return review_input_path(root, loop_run).with_name("review-execution.json")
```

- [ ] Implement `prepare_current_review(...)` to atomically replace the fixed input and remove a stale execution. Implement `record_current_review(...)` to re-read/recompute the input, require the same digest, and atomically replace the fixed execution. Implement `load_current_review(...)` to fail closed on absence, schema damage, execution failure, or digest mismatch.
- [ ] Do not add a current-review pointer, review ID, history directory, append-only log, timestamp authorization, or `ReviewRecord` model.
- [ ] Add the two fixed paths to the existing current `LoopRound.output_artifacts` only through the calling Loop writer; the kernel/store module itself must not import or mutate a Loop writer.
- [ ] Run the focused tests to GREEN:

```powershell
uv run pytest -q tests/unit/test_review_kernel.py tests/unit/test_review_kernel_store.py tests/architecture/test_review_kernel_boundaries.py
uv run ruff check src/ai_sdlc/core/review_kernel.py src/ai_sdlc/core/review_kernel_store.py src/ai_sdlc/core/loop_artifacts.py tests/unit/test_review_kernel.py tests/unit/test_review_kernel_store.py tests/architecture/test_review_kernel_boundaries.py
```

- [ ] Commit:

```powershell
git add src/ai_sdlc/core/review_kernel.py src/ai_sdlc/core/review_kernel_store.py src/ai_sdlc/core/loop_artifacts.py tests/unit/test_review_kernel.py tests/unit/test_review_kernel_store.py tests/architecture/test_review_kernel_boundaries.py
git commit -m "feat: add minimal current-round review kernel"
```

### Task 3: Add agent-orchestrated prepare/record commands without a provider subsystem

**Files:**

- Create: `src/ai_sdlc/cli/loop_review_cmd.py`
- Modify: `src/ai_sdlc/cli/loop_cmd.py`
- Modify: `src/ai_sdlc/adapters/codex/AI-SDLC.md`
- Modify: `src/ai_sdlc/adapters/claude_code/AI-SDLC.md`
- Modify: `src/ai_sdlc/adapters/cursor/rules/ai-sdlc.md`
- Modify: `src/ai_sdlc/adapters/vscode/AI-SDLC.md`
- Create: `tests/integration/test_cli_loop_review.py`
- Modify: `tests/unit/test_ide_adapter.py`

- [ ] Add `loop review prepare` and `loop review record` beneath the existing `loop_app`:

```text
ai-sdlc loop review prepare --type <loop-type> --loop-id <loop-id> --json
ai-sdlc loop review record --type <loop-type> --loop-id <loop-id> --result <execution.json> --json
```

`prepare` writes only the two current-round fixed artifacts through `review_kernel_store`. `record` validates a local `ReviewExecution` JSON file, rebinds it to the current input digest, and replaces the current execution. Neither command chooses a model, invokes a provider, closes a Loop, or changes a Loop status.
- [ ] Emit JSON containing the input path, exact digest, `role_brief`, artifact paths, and a next action that explicitly tells the active AI adapter to create one fresh read-only expert context and at most one independent cross-risk expert context.
- [ ] Update all four adapter guidance files with the same minimal orchestration contract: read `review-input.json`; derive ephemeral role names/focus/reasons from the actual content; run each expert in a fresh context separate from the writer; merge only deduplicated findings; record `failed` on timeout/unavailable/invalid output; never review Local PR Review again after its expert result.
- [ ] State that the adapter performs orchestration automatically while the user remains in the AI conversation. Do not instruct ordinary users to select reviewers, run provider commands, approve a panel, or manage review artifacts.
- [ ] Add integration tests for prepare/record, invalid Loop type/id, stale digest, two fresh role contexts represented in the recorded result, and Local PR Review terminal behavior. Add a command-tree test proving no `provider`, `panel`, `quorum`, `session`, or model-selection option exists.
- [ ] Run:

```powershell
uv run pytest -q tests/integration/test_cli_loop_review.py tests/unit/test_ide_adapter.py
uv run ruff check src/ai_sdlc/cli/loop_review_cmd.py src/ai_sdlc/cli/loop_cmd.py tests/integration/test_cli_loop_review.py tests/unit/test_ide_adapter.py
```

- [ ] Commit:

```powershell
git add src/ai_sdlc/cli/loop_review_cmd.py src/ai_sdlc/cli/loop_cmd.py src/ai_sdlc/adapters/codex/AI-SDLC.md src/ai_sdlc/adapters/claude_code/AI-SDLC.md src/ai_sdlc/adapters/cursor/rules/ai-sdlc.md src/ai_sdlc/adapters/vscode/AI-SDLC.md tests/integration/test_cli_loop_review.py tests/unit/test_ide_adapter.py
git commit -m "feat: orchestrate ephemeral loop experts"
```

### Task 4: Cut Requirement and Design Contract over to findings-only review

**Files:**

- Modify: `src/ai_sdlc/core/requirement_loop.py`
- Modify: `src/ai_sdlc/core/design_contract_loop.py`
- Modify: `src/ai_sdlc/cli/loop_cmd.py`
- Modify: `tests/unit/test_requirement_loop.py`
- Modify: `tests/unit/test_design_contract_loop.py`
- Modify: `tests/integration/test_cli_loop_requirement.py`
- Modify: `tests/integration/test_cli_loop_design_contract.py`

- [ ] Replace Requirement's `scope authority -> execute_stage_close -> authority commit` chain with: build/freeze the existing requirement result; prepare/load the matching current review; let the existing requirement reducer map execution failure to `needs_user`, Critical/Important findings to `needs_fix`, and no actionable findings to its existing close writer.
- [ ] Preserve the base Requirement current pointer, artifacts, acceptance-criteria checks, and transition to Design Contract. Remove all scope-authority identity, CAS, commit, recovery, and certificate fields from Requirement command results.
- [ ] Apply the same pattern to Design Contract. Remove design-close transaction/authority preparation and post-write commit while preserving design report/close/current pointer and the Requirement prerequisite.
- [ ] Ensure a Minor/Advisory-only execution does not block either close and remains visible in the current LoopRound result. Ensure missing/stale/failed review cannot appear as a clean pass.
- [ ] Make the first close attempt return the existing `needs_review` state and automatic next action for the AI adapter; the rerun after `review record` is the only close attempt that may call the original writer.
- [ ] Run:

```powershell
uv run pytest -q tests/unit/test_requirement_loop.py tests/unit/test_design_contract_loop.py tests/integration/test_cli_loop_requirement.py tests/integration/test_cli_loop_design_contract.py
rg -n "scope_authority|design_close_authority|execute_stage_close" src/ai_sdlc/core/requirement_loop.py src/ai_sdlc/core/design_contract_loop.py
```

The `rg` command must return no matches.

- [ ] Commit:

```powershell
git add src/ai_sdlc/core/requirement_loop.py src/ai_sdlc/core/design_contract_loop.py src/ai_sdlc/cli/loop_cmd.py tests/unit/test_requirement_loop.py tests/unit/test_design_contract_loop.py tests/integration/test_cli_loop_requirement.py tests/integration/test_cli_loop_design_contract.py
git commit -m "refactor: return requirement and design review to loop reducers"
```

### Task 5: Cut Implementation and Frontend Evidence over and make slimming advisory-only

**Files:**

- Create: `src/ai_sdlc/core/slimming_advice.py`
- Modify: `src/ai_sdlc/core/implementation_models.py`
- Modify: `src/ai_sdlc/core/implementation_loop.py`
- Modify: `src/ai_sdlc/core/frontend_evidence_loop.py`
- Modify: `src/ai_sdlc/core/loop_models.py`
- Modify: `src/ai_sdlc/cli/loop_cmd.py`
- Modify: `tests/unit/test_slimming_advice.py`
- Modify: `tests/unit/test_implementation_loop.py`
- Modify: `tests/unit/test_frontend_evidence_loop.py`
- Modify: `tests/integration/test_cli_loop_implementation.py`
- Modify: `tests/integration/test_cli_loop_frontend_evidence.py`

- [ ] Implement a single-pass `collect_slimming_advice(paths) -> list[SlimmingAdvice]` covering only file length, function length, obvious same-file duplication, unnecessary single-caller wrapper, and mixed responsibility hints. Return `[]` on unavailable analysis and include no status/verdict/waiver/receipt fields.
- [ ] Add `advisories: list[str]` to the existing `ImplementationReport` and command result. Collect advice when writing the report; never include it in `blockers`, `blocker_count`, `status`, `next_action`, or close eligibility.
- [ ] Remove `lean_code_*` imports and blocking `validate_lean_close` from Implementation. Remove `lean-check`, `lean-verify`, `lean-regression`, and Lean No-Go commands and options from `loop implementation`.
- [ ] Remove Lean fields from `LoopPolicyProfile`. A legacy project config containing those fields must be ignored with one migration notice at config load, not read into runtime behavior and not written back.
- [ ] Wire the current review into the existing Implementation reducer/writer using the same missing/stale/failed/actionable rules as Task 4. Keep task completion, verification evidence, WorkItem linkage, and frontend-routing behavior unchanged.
- [ ] Wire Frontend Evidence similarly. Preserve ordinary browser/frontend evidence and skip behavior; remove only stage-review authority/certificate/close-gate code.
- [ ] Add explicit tests that excessive file/function size, advice collection failure, and accepted complexity all leave close eligibility unchanged; a real correctness finding from a dynamic expert still returns through the original reducer.
- [ ] Run:

```powershell
uv run pytest -q tests/unit/test_slimming_advice.py tests/unit/test_implementation_loop.py tests/unit/test_frontend_evidence_loop.py tests/integration/test_cli_loop_implementation.py tests/integration/test_cli_loop_frontend_evidence.py
uv run ai-sdlc loop implementation --help
rg -n "lean-check|lean-verify|lean-regression|lean-no-go|execute_stage_close" src/ai_sdlc/cli/loop_cmd.py src/ai_sdlc/core/implementation_loop.py src/ai_sdlc/core/frontend_evidence_loop.py
```

The help output must omit all old Lean commands and the `rg` command must return no matches.

- [ ] Commit:

```powershell
git add src/ai_sdlc/core/slimming_advice.py src/ai_sdlc/core/implementation_models.py src/ai_sdlc/core/implementation_loop.py src/ai_sdlc/core/frontend_evidence_loop.py src/ai_sdlc/core/loop_models.py src/ai_sdlc/cli/loop_cmd.py tests/unit/test_slimming_advice.py tests/unit/test_implementation_loop.py tests/unit/test_frontend_evidence_loop.py tests/integration/test_cli_loop_implementation.py tests/integration/test_cli_loop_frontend_evidence.py
git commit -m "refactor: make implementation slimming advisory only"
```

### Task 6: Preserve the independent local reviewer and remove its authority layer

**Files:**

- Modify: `src/ai_sdlc/core/pr_review_models.py`
- Modify: `src/ai_sdlc/core/pr_review_provider.py`
- Modify: `src/ai_sdlc/core/pr_review_service.py`
- Modify: `src/ai_sdlc/cli/pr_review_cmd.py`
- Modify: `tests/unit/test_pr_review_provider.py`
- Modify: `tests/unit/test_pr_review_service.py`
- Modify: `tests/integration/test_cli_pr_review.py`

- [ ] Retain the existing exact staged identity capture and checks in `pr_review_provider.py`: reviewed HEAD, index, staged diff, independent subprocess, dirty-scope handling, timeout, and before/after HEAD/index/worktree mutation guard.
- [ ] Remove stage-review/session/panel/binding/quorum/certificate/attestation/Lean fields from PR review models and reports. The provider result becomes `PASS` or findings plus execution failure; it never yields an authorization token.
- [ ] Make Local PR Review's own result the input to the same review kernel, with the adapter explicitly selecting a cross-stage regression expert. Record that expert result once and terminate; do not generate another review request for the expert aggregation.
- [ ] Remove `pr-review attest`, attestation export, CI certificate export, stale attestation cleanup, locks, and service methods. Keep `doctor`, `start`, `status`, `fix`, `rerun`, and `close` only where they support the retained local review workflow.
- [ ] Add tests proving a reviewer cannot modify tracked, ignored, staged, or commit state; pre-existing unstaged content remains outside staged identity but cannot change during execution; Local PR Review terminates after one expert review; old copied attestation JSON cannot advance the Loop.
- [ ] Run:

```powershell
uv run pytest -q tests/unit/test_pr_review_provider.py tests/unit/test_pr_review_service.py tests/integration/test_cli_pr_review.py
uv run ai-sdlc pr-review --help
rg -n "attest|certificate|stage_review|execute_stage_close|lean_code" src/ai_sdlc/core/pr_review_models.py src/ai_sdlc/core/pr_review_provider.py src/ai_sdlc/core/pr_review_service.py src/ai_sdlc/cli/pr_review_cmd.py
```

The help output must omit `attest`; remaining `rg` matches must be zero or test-only comments removed before commit.

- [ ] Commit:

```powershell
git add src/ai_sdlc/core/pr_review_models.py src/ai_sdlc/core/pr_review_provider.py src/ai_sdlc/core/pr_review_service.py src/ai_sdlc/cli/pr_review_cmd.py tests/unit/test_pr_review_provider.py tests/unit/test_pr_review_service.py tests/integration/test_cli_pr_review.py
git commit -m "refactor: keep local review without close authority"
```

### Task 7: Remove all public legacy entry points and prove PR1 cutover

**Files:**

- Delete: `src/ai_sdlc/cli/activation_cmd.py`
- Delete: `src/ai_sdlc/cli/stage_review_guidance.py`
- Delete: `src/ai_sdlc/cli/optimization_hooks.py`
- Modify: `src/ai_sdlc/cli/main.py`
- Modify: `src/ai_sdlc/cli/verify_cmd.py`
- Modify: `src/ai_sdlc/core/verify_constraints.py`
- Modify: `.github/workflows/compatibility-gate.yml`
- Modify: `.github/workflows/cross-platform-core.yml`
- Delete: `.github/workflows/activation-evidence.yml`
- Delete: `.github/workflows/ci-certificate.yml`
- Delete: `.github/workflows/reviewer-isolation.yml`
- Create: `tests/architecture/test_review_kernel_cutover.py`
- Modify: `tests/integration/test_cli_help.py`
- Modify: `tests/unit/test_verify_constraints.py`

- [ ] Remove activation app registration, stage-certificate verification commands, stage review CLI guidance, optimization hooks, and every workflow whose only purpose is activation/certificate/reviewer-isolation authority. Do not add replacement workflows.
- [ ] Remove `verify_constraints` requirements for stage-review history, attestation, certificate, activation, sealed transition, or WorkItem 010. Keep ordinary repository, docs, packaging, Loop, and release consistency constraints.
- [ ] Make `compatibility-gate.yml` and `cross-platform-core.yml` stop selecting deleted tests/commands while retaining their ordinary Python/CLI/build/platform coverage.
- [ ] Implement `test_review_kernel_cutover.py` to assert:
  - `execute_stage_close(` call count is zero outside the still-unremoved legacy directory;
  - production imports from `stage_review` and `lean_code_*` are zero outside the legacy directories scheduled for PR2 deletion;
  - Requirement/Design authority commits and blocking Lean close calls are zero;
  - CLI help omits activation, attest, stage-certificate, promotion, seal, Lean check/verify/regression/No-Go;
  - no new workflow, network authority, global review store, pointer, CAS, lease, or background worker was introduced.
- [ ] Run the PR1 gate:

```powershell
uv run pytest -q tests/unit/test_review_kernel.py tests/unit/test_review_kernel_store.py tests/unit/test_slimming_advice.py tests/unit/test_requirement_loop.py tests/unit/test_design_contract_loop.py tests/unit/test_implementation_loop.py tests/unit/test_frontend_evidence_loop.py tests/unit/test_pr_review_provider.py tests/unit/test_pr_review_service.py tests/integration/test_cli_loop_review.py tests/integration/test_cli_loop_requirement.py tests/integration/test_cli_loop_design_contract.py tests/integration/test_cli_loop_implementation.py tests/integration/test_cli_loop_frontend_evidence.py tests/integration/test_cli_pr_review.py tests/integration/test_cli_help.py tests/architecture/test_review_kernel_boundaries.py tests/architecture/test_review_kernel_cutover.py
uv run ai-sdlc verify constraints
uv run ruff check src/ai_sdlc tests/unit tests/integration tests/architecture
git diff --check
```

- [ ] Record a size report as PR text only, never as a runtime gate:

```powershell
git diff --stat origin/main...HEAD
git diff --numstat origin/main...HEAD
```

- [ ] Commit:

```powershell
git add -A
git commit -m "refactor: cut over to the minimal review kernel"
```

- [ ] Freeze the exact PR1 HEAD and request three independent read-only reviewers: product-boundary, architecture/dependency, and delivery/regression. Require unanimous Ready. Allow one focused repair/re-review only; otherwise declare Delivery No-Go.

---

## PR2 — Physical Deletion and Distribution Cleanup

### Task 8: Freeze the deletion inventory and semantic anti-revival tests

**Files:**

- Create: `tests/architecture/review_kernel_removed_paths.txt`
- Create: `tests/architecture/review_kernel_forbidden_semantics.txt`
- Create: `tests/architecture/test_removed_review_subsystems.py`
- Create: `tests/integration/test_legacy_review_artifacts_ignored.py`

- [ ] Generate `review_kernel_removed_paths.txt` from PR1's tracked tree, then review and commit the exact repository-relative paths for:
  - `src/ai_sdlc/core/stage_review/**`;
  - every root `src/ai_sdlc/core/lean_code*.py`;
  - scope/design close authority, enforce/shadow authority, and stage-review-only helper modules;
  - stage-review/Lean-only tests, scripts, workflows, policies, rules, templates, docs, schemas, fixtures, and packaging copies.
- [ ] Include these explicit external production files when they have no retained caller after PR1: `src/ai_sdlc/core/scope_authority_store.py`, `src/ai_sdlc/core/design_close_artifact_verification.py`, `src/ai_sdlc/core/design_close_authority_store.py`, `src/ai_sdlc/core/design_close_enforce_authority.py`, `src/ai_sdlc/core/design_close_enforce_evidence.py`, `src/ai_sdlc/core/design_close_shadow_authority.py`, `src/ai_sdlc/core/design_scope_authority_transition.py`, and `src/ai_sdlc/core/lean_code_reviewer_authority.py`.
- [ ] Populate `review_kernel_forbidden_semantics.txt` with semantic families rather than only old names: parallel close decision/authorization; persistent reviewer identity; review session/history/ledger/replay; quorum/panel/provider/model routing; activation/promotion/rollback; review certificate/attestation/seal; resource lease/budget; dataset/holdout/learning; blocking code-size/complexity waiver or No-Go.
- [ ] Test both path absence and AST/CLI/schema behavior. Fail if a new module recreates any forbidden semantic family even under a renamed symbol, or if `review_kernel` gains a close verdict/state writer/provider router/history store.
- [ ] Add a legacy-project test containing old review/session/certificate/Lean artifacts under `.ai-sdlc/`. Normal status/run must ignore them, emit one concise deprecation notice, never migrate/replay them, and never treat them as current evidence.
- [ ] Run RED before deletion:

```powershell
uv run pytest -q tests/architecture/test_removed_review_subsystems.py tests/integration/test_legacy_review_artifacts_ignored.py
```

- [ ] Commit the frozen inventory/tests:

```powershell
git add tests/architecture/review_kernel_removed_paths.txt tests/architecture/review_kernel_forbidden_semantics.txt tests/architecture/test_removed_review_subsystems.py tests/integration/test_legacy_review_artifacts_ignored.py
git commit -m "test: freeze removal inventory and anti-revival contract"
```

### Task 9: Delete the unreachable production and test subsystems

**Files:**

- Delete: `src/ai_sdlc/core/stage_review/`
- Delete: every path listed in `tests/architecture/review_kernel_removed_paths.txt`
- Delete: `tests/unit/stage_review/`
- Delete: all listed `tests/unit/test_lean_code*.py` and stage-review/authority-only test modules
- Delete: `scripts/build_activation_evidence.py`
- Delete: `scripts/build_activation_quality_cell.py`
- Delete: `scripts/windows_lean_code_e2e.py`
- Delete: `.ai-sdlc/policies/stage-gate-activation-policy.json`
- Delete: `rules/lean-code.md`
- Delete: `src/ai_sdlc/rules/lean-code.md`
- Modify: `pyproject.toml`
- Modify: `MANIFEST.in`

- [ ] Delete the exact frozen inventory with `git rm`; do not edit legacy files into shims. If a listed module still has a retained caller, stop and move that caller to the minimal kernel/base Loop before deleting it.
- [ ] Remove package-data, test selection, entry point, and manifest references to deleted policies/rules/schemas/workflows/scripts. Keep ordinary rules, frontend evidence, CI, build, and release files.
- [ ] Remove stale imports/exports from all `__init__.py` files. Do not leave lazy imports, fallback imports, `try/except ImportError` compatibility, disabled feature flags, or empty namespace packages.
- [ ] Run:

```powershell
uv run pytest -q tests/architecture/test_removed_review_subsystems.py tests/integration/test_legacy_review_artifacts_ignored.py
uv run python -c "import ai_sdlc; import ai_sdlc.cli.main"
uv run ruff check src/ai_sdlc tests/architecture tests/integration/test_legacy_review_artifacts_ignored.py
git diff --check
```

- [ ] Commit:

```powershell
git add -A
git commit -m "refactor: delete legacy review authority subsystems"
```

### Task 10: Remove obsolete product claims, templates, and initialized copies

**Files:**

- Modify: `README.md`
- Modify: `docs/ai-sdlc-next-stage-trusted-delivery-and-value-activation-prd.zh-CN.md`
- Modify: `docs/framework-defect-backlog.zh-CN.md`
- Modify: `specs/007-framework-defect-truth-closure/spec.md`
- Modify: `src/ai_sdlc/adapters/codex/AI-SDLC.md`
- Modify: `src/ai_sdlc/adapters/claude_code/AI-SDLC.md`
- Modify: `src/ai_sdlc/adapters/cursor/rules/ai-sdlc.md`
- Modify: `src/ai_sdlc/adapters/vscode/AI-SDLC.md`
- Modify: applicable files under `templates/`, `packaging/`, and `.ai-sdlc/` named in the frozen deletion inventory
- Modify: `tests/integration/test_init.py`
- Modify: `tests/integration/test_upgrade.py`
- Create: `tests/integration/test_review_kernel_distribution.py`

- [ ] Rewrite product documentation to name only the three retained capabilities and the five Loop stages. Mark Shadow/Enforce, Activation, close certificates, reviewer sessions/ledgers, offline optimization, resource governance, and blocking Lean governance as deleted—not postponed, disabled, or future work.
- [ ] Resolve any concurrent/user-owned changes in the three listed PRD/spec files by rebasing and manually preserving unrelated content. Never overwrite a dirty main-worktree copy.
- [ ] Remove generated/init templates that install old rules, policies, commands, workflows, or artifacts. Fresh init must install only the minimal agent guidance; upgrade must leave legacy artifacts untouched/ignored and print one deprecation notice.
- [ ] Build wheel and sdist, unpack both, and assert the frozen path list and forbidden public strings are absent while `review_kernel.py`, adapter guidance, base Loop modules, and ordinary frontend evidence are present.
- [ ] Add a fresh-init integration test that exercises Requirement, Design Contract, Implementation, Frontend Evidence, and Local PR Review guidance without creating a stage-review session, authority pointer, certificate, attestation, Lean receipt, or activation policy.
- [ ] Run:

```powershell
uv run pytest -q tests/integration/test_init.py tests/integration/test_upgrade.py tests/integration/test_review_kernel_distribution.py
uv build
uv run python -m build --sdist --wheel
```

- [ ] Commit:

```powershell
git add -A
git commit -m "docs: publish the minimal review product boundary"
```

### Task 11: Verify the retained product end to end and close PR2

**Files:**

- Modify only if a failing retained-behavior test identifies a regression; do not add scope.

- [ ] Run the exact retained-capability suite:

```powershell
uv run pytest -q tests/unit/test_review_kernel.py tests/unit/test_review_kernel_store.py tests/unit/test_slimming_advice.py tests/unit/test_requirement_loop.py tests/unit/test_design_contract_loop.py tests/unit/test_implementation_loop.py tests/unit/test_frontend_evidence_loop.py tests/unit/test_pr_review_provider.py tests/unit/test_pr_review_service.py tests/integration/test_cli_loop_review.py tests/integration/test_cli_loop_requirement.py tests/integration/test_cli_loop_design_contract.py tests/integration/test_cli_loop_implementation.py tests/integration/test_cli_loop_frontend_evidence.py tests/integration/test_cli_pr_review.py tests/integration/test_cli_help.py tests/integration/test_init.py tests/integration/test_upgrade.py tests/integration/test_review_kernel_distribution.py tests/integration/test_legacy_review_artifacts_ignored.py tests/architecture/test_review_kernel_boundaries.py tests/architecture/test_review_kernel_cutover.py tests/architecture/test_removed_review_subsystems.py
```

- [ ] Run repository gates:

```powershell
uv run pytest -q
uv run ai-sdlc verify constraints
uv run ruff check .
git diff --check
git status --short
```

- [ ] Assert the three retained behaviors manually from a disposable initialized project:
  1. local pre-commit review runs in an independent read-only process/context and detects mutation;
  2. excessive code size produces advice but does not block close;
  3. each of the five Loop results produces one current-round dynamic expert review, with at most two fresh roles and no recursive Local PR review.
- [ ] Assert all deletion invariants:

```powershell
rg -n "stage_review|StageReviewSession|FindingLedger|StageCloseCertificate|activation|attest|ci-certificate|whole-tree seal|lean-check|lean-verify|lean-regression|LeanNoGo|OfflineOptimization|ResourceGovernor|Holdout|quorum|panel solver" src tests .github scripts rules docs templates packaging .ai-sdlc
```

Classify every remaining match. It must be either the deletion-contract/design history in `docs/superpowers/`, an anti-revival test fixture, or an ordinary unrelated use of the English word; there must be no runtime, user command, template, workflow, policy, or active product claim.

- [ ] Record net deletion as an advisory delivery report:

```powershell
git diff --stat origin/main...HEAD
git diff --numstat origin/main...HEAD
git ls-files | Measure-Object
```

- [ ] Freeze exact PR2 HEAD/tree and request the same three independent reviewers. Require unanimous Ready and at most one focused repair/re-review. Do not ask the user to adjudicate ordinary review disagreements; reviewers must reconcile against the approved deletion contract. Ask the user only if a proposed resolution changes one of the three retained product capabilities.

- [ ] After unanimous Ready, hand off the two reviewed PRs for the repository's normal push/PR/required-check/merge protocol. Do not dispatch a release or recreate WorkItem 010 as part of this cleanup.
