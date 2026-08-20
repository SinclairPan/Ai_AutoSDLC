# PR4 Retained-Capability Reconnection and Physical Deletion Plan

**Goal:** Reconnect the retained frontend delivery and release paths to the five-Loop product, prove
that retired runtime families have zero production callers, then physically delete those families from
source distributions and installed packages.

**Base:** `ffdd850de1806b75b349db9242d2597438f6ff95`

**Base tree:** `2e6475e903b82b836cead3264de82c2cd12b2588`

**Design:** `docs/superpowers/specs/2026-08-20-pr4-reconnect-delete-design.md`

## Global execution contract

- This is one PR with exactly two ordered product stages: A reconnects retained capabilities; B
  physically deletes retired capabilities.
- Do not start B until the Stage A hard gate is green on a frozen candidate.
- Every behavior change starts with a focused failing regression.
- Frontend Evidence Loop remains the sole frontend close authority; do not add another state machine.
- Preserve two-expert/one-repair review bounds, Local PR exact-tree review, and advisory slimming.
- Preserve generic Continuity handoff. Delete only Program-specific handoff chains.
- Preserve public release identity, self-development constraints, online/offline installers, offline
  bundles, update notices, and self-update behavior.
- Do not build a replacement Program, Graph, Telemetry, Provenance, AgentOps, proof, authority,
  side-by-side runtime, pointer, background service, or fifth governance PR.
- A deleted command must be absent and return exit 2; hidden compatibility is not completion.
- A deleted module must be absent from wheel, sdist, sdist-built wheel, and offline bundle; an
  unregistered source file is not completion.
- Only frozen-scope, reproducible Critical/Important findings may consume the bounded repair pass.

## Stage A — reconnect retained delivery paths

### Task A1 — freeze RED contracts and inventory deletion candidates

Files:

- Create `tests/architecture/test_pr4_product_boundary.py`
- Create `tests/integration/test_frontend_delivery_normal_path.py`
- Create `tests/integration/test_pr4_fresh_project_noise.py`
- Modify `tests/integration/test_cli_loop.py`
- Modify `tests/unit/test_frontend_evidence_loop.py`
- Create `scripts/check_retired_runtime_inbound.py`

RED contracts:

1. Freeze the expectation that the complete new Loop frontend path succeeds while `ProgramService`
   cannot be imported and `program` is not registered. The baseline is RED because the new Loop commands
   do not exist and retained paths still have Program callers; A2/A3 must make the same test GREEN without
   changing its expectation. Use an import/call sentinel plus the architecture scan to prove independence.
2. The future normal path is frozen as:

   ```text
   loop frontend-evidence solution-confirm
   loop frontend-evidence apply
   loop frontend-evidence capture
   loop frontend-evidence baseline
   loop frontend-evidence start/review/close
   ```

3. Frontend commands must not import or call host-runtime, page-ui-schema, generation-constraints,
   quality-platform, provider-runtime handoff, Program manifest, proof, or governance materializers.
4. Frontend Next text, doctor, README, browser runner, Loop E2E, and Windows/POSIX E2E must not emit
   `program ...` commands.
5. Fresh Node, Java, and Python fixtures must fail if init/run/status/doctor/verify create
   program-manifest, telemetry, provenance, AgentOps, proof, archive, or authority artifacts.
6. Read-only commands must fail if they change HEAD, index, worktree bytes, Loop outcome, checkpoint,
   or review outcome.
7. The inbound scanner reports each retired family and distinguishes production callers from tests,
   docs, deletion allowlists, and historical plan records. It must not report zero by excluding all
   `src/ai_sdlc` callers.

No production implementation is changed in A1.

### Task A2 — extract the minimal frontend delivery service

Files:

- Create `src/ai_sdlc/core/frontend_delivery_service.py`
- Modify `src/ai_sdlc/core/managed_delivery_apply.py` only for project-fact inputs
- Modify `src/ai_sdlc/core/frontend_browser_gate_runtime.py`
- Modify `src/ai_sdlc/core/frontend_visual_a11y_evidence_provider.py`
- Modify `src/ai_sdlc/core/frontend_delivery_truth.py` or replace it with a narrower retained model
- Modify only retained models among:
  - `src/ai_sdlc/models/frontend_solution_confirmation.py`
  - `src/ai_sdlc/models/frontend_managed_delivery.py`
  - `src/ai_sdlc/models/frontend_browser_gate.py`
- Modify the minimal retained solution artifact generator/profile adapter only when required
- Modify focused unit tests for these files

Implementation:

- Move behavior, not the Program hierarchy. Do not copy Program manifest, truth ledger, handoff,
  quality-platform, provider patch, proof, or archive models.
- Build a solution snapshot from project facts and an explicit user selection. A recommended option and
  an alternative/custom option remain available; no framework/provider/style pack is a generic default.
- Build the apply request from the confirmed snapshot and live project facts, then call
  `run_managed_delivery_apply`.
- Persist only the confirmation snapshot and apply receipt needed for stale detection and recovery.
- Build browser execution context directly from the snapshot plus apply receipt, then call the existing
  browser runtime and visual/a11y provider.
- Move visual baseline ownership out of `quality-platform` and into a small browser evidence path.
- Bind snapshot, apply receipt, browser bundle, visual/a11y artifact, project root, work item, Loop id,
  source tree and timestamps with existing hashes; do not add digest governance or a proof store.
- A changed snapshot invalidates apply/browser evidence. A changed implementation/evidence tree
  invalidates Frontend Evidence review.
- Solution confirmation before apply, successful apply before capture, and required evidence before Close
  are hard ordering rules.
- Without an existing valid baseline, the first capture is bootstrap/recheck only. `baseline` may create
  the comparison baseline but must not mutate or promote that browser artifact. A second capture must
  compare against the current baseline before review/Close. With a valid baseline, one compare capture is
  allowed. Baseline, implementation, or snapshot changes make older evidence/review stale.

Focused verification:

```text
uv run pytest -q tests/integration/test_frontend_delivery_normal_path.py \
  tests/unit/test_frontend_browser_gate_runtime.py \
  tests/unit/test_frontend_visual_a11y_evidence_provider.py \
  tests/unit/test_frontend_evidence_loop.py
uv run ruff check src/ai_sdlc/core/frontend_delivery_service.py \
  src/ai_sdlc/core/managed_delivery_apply.py \
  src/ai_sdlc/core/frontend_browser_gate_runtime.py \
  tests/integration/test_frontend_delivery_normal_path.py
```

### Task A3 — expose retained actions through the existing Loop namespace

Files:

- Modify `src/ai_sdlc/cli/loop_cmd.py`
- Modify `src/ai_sdlc/core/frontend_evidence_loop.py`
- Modify `src/ai_sdlc/core/frontend_evidence_store.py` only when artifact linkage requires it
- Modify `tests/integration/test_cli_loop.py`
- Modify `tests/unit/test_frontend_evidence_loop.py`
- Modify `tests/integration/test_frontend_delivery_normal_path.py`

Implementation:

- Add thin `solution-confirm`, `apply`, `capture`, and `baseline` commands to
  `loop frontend-evidence`.
- Commands validate arguments, call the frontend service, render Result/Next/Blockers, and return stable
  JSON when requested. They do not own delivery state or Close.
- Existing `start`, `doctor`, `skip`, `status`, and `close` retain their names and review contract.
- Frontend Evidence start consumes the new browser/visual/a11y bundle and freezes the implementation
  snapshot. Close continues to require the independent completed review digest.
- Missing evidence, stale evidence, failed review, third expert, or second repair review remains blocked.
- Explicit skip keeps the existing risk-acceptance semantics and never masquerades as captured evidence.

The no-baseline E2E sequence is fixed as:

```text
solution-confirm -> apply -> capture(bootstrap/recheck) -> baseline ->
capture(compare) -> review-record -> close
```

The valid-baseline sequence is `solution-confirm -> apply -> capture(compare) -> review-record -> close`.
The first bootstrap capture must not Close, and baseline creation must not rewrite it.

The end-to-end test must use the real service path rather than directly writing outcome or evidence files.

### Task A4 — remove Program/Telemetry from ordinary runtime paths

Files:

- Modify `src/ai_sdlc/cli/commands.py`
- Modify `src/ai_sdlc/cli/main.py`
- Modify `src/ai_sdlc/cli/sub_apps.py`
- Modify `src/ai_sdlc/cli/doctor_cmd.py`
- Modify `src/ai_sdlc/cli/verify_cmd.py`
- Modify `src/ai_sdlc/cli/workitem_cmd.py`
- Modify `src/ai_sdlc/core/close_check.py`
- Modify `src/ai_sdlc/core/verify_constraints.py`
- Modify `src/ai_sdlc/models/project.py`
- Modify `src/ai_sdlc/templates/project-config.yaml.j2`
- Add or modify a neutral time/display helper only where ordinary code used Telemetry utilities
- Modify status/doctor/verify/workitem/project-config tests
- Modify root-surface/command-name tests

Implementation:

- Remove retired Program/Telemetry/Provenance/Trace/AgentOps/enterprise/studio/host-runtime imports and
  registration from the root app in Stage A, so ordinary CLI startup cannot eager import those modules.
  Keep their source files until Stage B, but direct invocation must already return exit 2.
- Replace init's `SDLCRunner` safe rehearsal with read-only environment/config/five-Loop routing checks.
- Build doctor from environment, adapter, installation/update, and browser-provider facts only.
- Keep status default compact. Keep `--details`/`--json`, but rebuild them from current project,
  adapter, five-Loop, review and retained frontend facts.
- Make verify constraints decide directly from its report. It must not initialize RuntimeTelemetry or
  write session/event/evidence/evaluation files.
- Remove Program truth, provenance advisory, and old WorkItem release-evidence checks from generic
  close-check. Keep task/log/branch/quality/Loop/local-review checks.
- Remove AgentOps/Telemetry defaults from ProjectConfig and fresh templates. Existing config files with
  extra old keys remain readable but those keys are ignored and never re-emitted.
- Replace generic clock/display uses with narrow neutral utilities. Do not create a telemetry adapter.

Regression requirements:

- Node/Java/Python fixtures remain free of framework release rules and old artifacts.
- status details/JSON do not import retired modules.
- verify constraints remains read-only in user and self-development profiles.
- self-development profile still catches an intentionally broken release identity.

### Task A5 — migrate retained consumers and documentation

Files:

- Modify `README.md`, `USER_GUIDE.zh-CN.md`, `AGENTS.md`, `docs/product-contract.md`, and
  `docs/v2-migration.zh-CN.md` only for verified PR4 behavior
- Modify `scripts/frontend_browser_gate_probe_runner.mjs`
- Modify `scripts/loop_e2e_release_gate.py`
- Modify `scripts/windows_clean_user_e2e.py`
- Modify `scripts/windows_clean_user_e2e_support.py`
- Modify POSIX user-guide/offline consumers when they contain old commands
- Modify `.github/ci/fast-gate-tests.txt`
- Modify consumer-contract tests

Implementation:

- Replace all active `program solution-confirm/managed-delivery-apply/browser-gate-*` guidance with the
  new Loop commands.
- Do not remove generic handoff guidance from AGENTS or runtime adapters.
- Do not advertise AgentOps/Telemetry/Provenance as default product capabilities.
- Keep release/install/self-update guidance unchanged except where a deleted command is referenced.
- E2E fixtures create their own temporary frontend facts and artifacts; they must not rely on repository
  `governance/`, `kernel/`, `providers/`, or `managed/` samples.

### Task A6 — freeze and pass the Stage A hard gate

Run focused suites, then:

```text
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run ai-sdlc verify constraints
uv run ai-sdlc verify constraints --profile self-development
git diff --check
```

Build wheel and sdist. From each fresh installed candidate, run the Node/Java/Python fixtures and the
complete frontend chain. Run release/offline/self-update smokes in proportion to local platform support.

Freeze HEAD/tree/base/path set/dirty state and run exactly two independent local reviews against the same
candidate:

1. product boundary/state-machine review;
2. deletion readiness/delivery regression review.

Required PASS evidence before Stage B:

- retained frontend chain and Frontend Evidence Close are real and independent of Program;
- ordinary runtime paths do not import or write Program/Telemetry/Provenance/AgentOps;
- every proposed Stage B family is unreachable from retained CLI/Loop/release entry roots; self-references
  inside the unregistered retired family are reported separately and do not count as retained inbound;
- fresh distribution E2E is green;
- release/offline/self-update/self-development checks remain green.

If any item is not proven, stay in Stage A. Do not delete first.

Commit the Stage A candidate as:

```text
refactor: reconnect retained delivery paths
```

## Stage B — physically remove retired runtime surfaces

### Task B1 — verify the Stage A cutover and delete retired CLI surfaces

Files:

- Delete retired CLI modules proven unreachable
- Modify `src/ai_sdlc/cli/main.py` and `src/ai_sdlc/cli/sub_apps.py` only for residual retired sets/exports
- Modify CLI command-name/root-surface/docs-consistency tests

First prove Stage A already removed imports, registration, read-only/compatibility entries, and direct
invocation for `program`, `telemetry`, `provenance`, `trace`, `agentops`, `enterprise`, `studio`, and
`host-runtime`. Then physically delete those CLI modules. If `stage`, `gate`, or another seven-stage
surface has zero retained callers, unregister it in a focused cutover test before deleting it in this
batch. Direct invocation must remain exit 2.

Do not remove `handoff`, `loop`, `pr-review`, `self-update`, `verify`, or any normal-path command.

### Task B2 — delete Program, handoff/writeback, and proof/archive families

Delete only after B1 and zero-inbound proof:

- `src/ai_sdlc/cli/program_cmd.py`
- `src/ai_sdlc/core/program_service.py`
- `src/ai_sdlc/models/program.py`
- `templates/program-manifest.example.yaml`
- Program handoff/provider patch/writeback/governance/proof/archive/final-proof models and generators
- corresponding eager exports, tests, docs and fixed CI lists

Do not delete generic Continuity handoff files or Local PR review artifacts.

After each deletion batch:

```text
python scripts/check_retired_runtime_inbound.py --family program
uv run pytest -q <affected retained tests>
uv run python -c "import ai_sdlc.cli.main"
git diff --check
```

### Task B3 — delete Telemetry, Provenance, Trace, and AgentOps

Delete after retained source imports are zero:

- `src/ai_sdlc/telemetry/**`
- `src/ai_sdlc/cli/{telemetry_cmd,provenance_cmd,trace_cmd,agentops_cmd,enterprise_cmd}.py`
- `src/ai_sdlc/core/{agentops_bridge,provenance_gate}.py`
- retired contracts/config/docs/tests and generated artifacts

Re-run fresh init/run/status/doctor/verify/close-check and assert no runtime files are created beyond
their explicit retained contracts. A neutral time helper is allowed; a replacement event pipeline is not.

### Task B4 — delete studio, host-runtime, and unreachable seven-stage execution

Use the inbound scanner separately for each family. Delete only zero-inbound modules among:

- `src/ai_sdlc/studios/**`
- `src/ai_sdlc/cli/host_runtime_cmd.py`
- `src/ai_sdlc/core/host_runtime_manager.py`
- `src/ai_sdlc/models/host_runtime_plan.py`
- old runner/executor/dispatcher/stage CLI, backends, parallel engine and stage YAMLs

Do not delete gates, task acceptance helpers, state loaders, or models still consumed by five Loop,
verify, init, recover, or release flows. The scanner must prove each individual deletion set.

### Task B5 — delete historical frontend baselines and repository noise

Delete frontend model/generator families whose retained source inbound set is zero, including historical
provider expansion/runtime adapter/cross-provider consistency/page-schema/theme/quality-platform/
generation-constraint/patch-writeback chains.

Delete repository-generated roots only after tests use isolated fixtures:

- `governance/`
- `kernel/`
- `providers/`
- `managed/`
- `src/frontend-governance/`

Retain only the minimal solution/apply/browser/visual/a11y/evidence modules proven by Stage A. Scan
production source and distribution members for historical WorkItem ids and retired tokens; the allowed
count in built artifacts is zero.

### Task B6 — delete unreachable tests/docs and enforce distribution membership

Files:

- Delete tests that only assert deleted implementations
- Rewrite high-signal behavior tests to assert retained outcomes and absent commands/modules
- Remove retired user docs; historical implementation plans may remain in the repository only when the
  packaging member contract proves they are not shipped
- Modify `packaging_backend.py` only if required to enforce sdist/wheel membership
- Add distribution and offline-bundle blacklist/allowlist tests

Membership tests must inspect:

1. wheel;
2. sdist;
3. wheel rebuilt from sdist;
4. wheel nested in the offline bundle.

They must allow the five Loop/review/frontend/browser/self-update/rules/adapter/template assets and reject
all retired module families, program manifest templates, `src/frontend-governance`, generated repository
roots and historical WorkItem tokens.

### Task B7 — full final verification and adversarial review

Run:

```text
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run ai-sdlc verify constraints
uv run ai-sdlc verify constraints --profile self-development
git diff --check
```

Build wheel, sdist, sdist-built wheel, and offline bundle. In fresh installations:

- run `ai-sdlc --help` and `python -m ai_sdlc --help`;
- import every packaged module with `pkgutil.walk_packages`;
- prove deleted commands return exit 2 and deleted modules cannot be imported;
- run Node/Java/Python enterprise fixtures;
- run all five Loop and Local PR Review;
- with no baseline, run solution-confirm → apply → capture(bootstrap/recheck) → baseline →
  capture(compare) → review-record → Close, proving the bootstrap artifact cannot Close and baseline does
  not promote it; also run the valid-baseline one-capture path;
- run online/offline install and self-update success/failure recovery;
- run self-development release identity failure detection.

Formal CI must cover macOS, Windows and Linux with supported Python versions, User Guide E2E, Offline
Smoke, Compatibility Gate, Merge Assurance and release-build smoke. Do not weaken, skip or xfail a core
case to reach green.

Freeze the exact candidate and run two independent local reviewers on the same bytes. Permit at most one
focused repair for a PR-caused reproducible Critical/Important, rerun proportionate and full verification,
then commit Stage B as:

```text
refactor!: physically remove retired runtime surfaces
```

Push one Draft PR. Monitor review and required checks. A focused in-scope review/CI failure may be repaired
on the same branch after bounded adversarial decision; a scope/product/release decision must stop for user
input. Do not mark Ready or merge without explicit authority applicable at that time.

## Frozen allowed paths

Stage A allows only:

- the new/retained frontend solution/apply/browser/visual/a11y/evidence modules and models;
- the source-checkout module entry `ai_sdlc/__main__.py` and
  `cli/{main,sub_apps,commands,doctor_cmd,loop_cmd,verify_cmd,workitem_cmd}.py`;
- `core/{close_check,verify_constraints}.py` and a narrow neutral time/display helper;
- `gates/pipeline_gates.py` only to sever its retired frontend summary import while preserving
  the generic verification gate;
- project config model/template;
- README/AGENTS/User Guide/product/migration docs;
- browser/Loop/Windows/POSIX E2E scripts, fast-gate list and directly corresponding tests.

Stage B additionally allows deletion of the frozen retired module families, generated roots, their exclusive
tests/docs/templates and eager exports. `packaging_backend.py`, installer scripts and release workflows may
change only for command migration or exact membership assertions.

Never modify review-kernel semantics, expert counts/rounds, Local PR exact-tree rules, self-update protocol,
dependency/version/lock files, workflow permissions, tags/releases/assets, generic Continuity handoff, or
introduce a replacement platform.

## Stop condition

Stop PR4 when retained capabilities execute through the five-Loop product, the three original P0s remain
closed, update/release/install paths still work, retired implementation is physically absent from source
distributions and offline packages, fresh projects contain no old noise, full verification is green, and
the final frozen candidate passes bounded independent review. Do not release or continue opportunistic
cleanup in this PR.
