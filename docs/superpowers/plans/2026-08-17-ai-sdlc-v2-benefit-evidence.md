# AI-SDLC 2.0 Benefit Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce an auditable 15-run stage-level AI-SDLC 2.0 benefit benchmark, publish only externally verified results, and integrate a dependency-free Engineering Evidence experience into the existing offline product site.

**Architecture:** A Python standard-library benchmark controller freezes five arm configurations, three isolated fixtures, a 33-attempt atomic provider ledger, sealed external evaluators, raw receipts, and a deterministic summary. The product site consumes only a generated static evidence fragment whose values are parity-checked against the hash-bound summary and closed-world manifest; no browser-time fetch or backend is introduced. Provider execution is a later gated task and cannot start until all deterministic contracts, fixture leakage checks, arm isolation checks, dry-runs, and independent protocol reviews pass.

**Tech Stack:** Python 3.11 standard library, pytest, Codex CLI JSONL, AI-SDLC v2.0.0 CLI from tag `737bda39e05c53450e180a20581b7b7a70db9cf0`, Superpowers v6.3.0 repo-scoped namespace adaptation, HTML5, existing classic JavaScript and CSS, existing Chromium acceptance runner.

**Spec:** `docs/product-site/research/2026-08-17-ai-sdlc-v2-benefit-benchmark-spec.md`

## Global Constraints

- Run exactly five arms `P`, `S`, `A00`, `A10`, `A11` against exactly three fixtures and preserve all 15 pre-registered run units.
- Treat `A00` and `A10` as research-only ablations; never describe them as public AI-SDLC modes.
- Label `A00` and `A10` as `harness-enforced ablation`; v2.0.0 has no official Loop/expert feature flag.
- Treat `S` as the Superpowers 6.3.0 single-Agent repo-skill namespace adaptation with `multi_agent=false`; publish the namespace-only diff and do not claim an exact plugin install or full multi-Agent SDD.
- Limit every fixture to one AI-SDLC stage Loop: Design Contract for fixture 1 and Implementation for fixtures 2–3. Browser checks remain external evaluation, and Local PR Review is out of scope.
- Freeze AI-SDLC at commit `737bda39e05c53450e180a20581b7b7a70db9cf0` and Superpowers v6.3.0 at peeled commit `b36e0829c6d0140e93cfef2ca599b1b07d4a7797`.
- Use `gpt-5.6-sol` with requested `model_reasoning_effort=high`, the exact Codex 0.147.0 binary identity, identical writer tool rights and a 1800-second writer hard timeout. Codex CLI exposes no reliable per-run output-token hard cap, so record actual usage and never claim token-budget or total-compute matching. A11 experts/rereviews have 900-second timeouts and every added token/second is treatment cost.
- Cap logical Provider session attempts at 33 with at most three pre-output technical retries and zero content retries; do not describe this as a count of internal HTTP requests.
- Pre-register the normal 19-attempt topology and the 26-attempt worst allowed topology; reject any unexpected writer/expert/rereview shape as `budget_exhausted`. Writer repair occurs inside the still-active original writer session through a runner callback.
- Do not start any Provider call until Tasks 1–4 are committed, reviewed and green.
- Keep sealed evaluators and Gold outside every Provider-readable workspace.
- Retain failures, timeouts, `needs_user`, expert failures and invalid completion in the denominator.
- Do not publish old synthetic percentages or old competition-arm results as v2.0.0 evidence.
- Preserve the existing offline double-click contract, native video behavior, five accepted viewports and no-runtime-network boundary.
- Every task requires TDD for executable behavior, a task review, and a clean commit before the next task.
- Do not push, publish, or merge without explicit user authority.

---

### Task 1: Freeze protocol, schemas and deterministic validator

**Files:**
- Create: `benchmarks/ai-sdlc-v2-benefits/protocol.json`
- Create: `benchmarks/ai-sdlc-v2-benefits/schemas/run-receipt.schema.json`
- Create: `benchmarks/ai-sdlc-v2-benefits/schemas/summary.schema.json`
- Create: `src/ai_sdlc/benefit_benchmark.py`
- Create: `scripts/ai_sdlc_v2_benefit_benchmark.py`
- Create: `tests/unit/test_benefit_benchmark.py`

**Interfaces:**
- `load_protocol(path: Path) -> BenchmarkProtocol`
- `validate_protocol(protocol: BenchmarkProtocol, repo_root: Path) -> list[BenchmarkIssue]`
- `validate_provider_output_schema(schema: Mapping[str, object]) -> list[BenchmarkIssue]`
- `reserve_provider_attempt(ledger_path: Path, request: AttemptRequest) -> AttemptReservation`
- `record_provider_completion(ledger_path: Path, completion: AttemptCompletion) -> None`
- CLI `validate`, `reserve-attempt`, `complete-attempt`, `verify-receipt`, `verify-summary`.

- [ ] Write a failing test that rejects any arm list other than `P,S,A00,A10,A11`, any fixture list other than the three spec IDs, a run matrix other than 15 unique `(arm, fixture)` pairs, or a schedule whose mean position is not exactly 3 for every arm.
- [ ] Run `uv run pytest -q tests/unit/test_benefit_benchmark.py` and verify the failure is caused by the missing module.
- [ ] Implement immutable dataclasses and JSON parsing with unknown-field rejection; make the test pass.
- [ ] Write failing tests for attempt 34 rejection, a fourth technical retry, content retry, duplicate run replacement, and completion without a prior reservation.
- [ ] Write failing topology tests that reject any plan other than 19 normal logical sessions, at most four expert rereviews (23), and at most three pre-output retries (26); the remaining seven slots cannot authorize new roles or replacement writers.
- [ ] Implement atomic ledger updates using a same-directory temporary file, `fsync`, and `os.replace`; a reservation increments `attempts_started` before any command can be returned.
- [ ] Write failing receipt tests for missing failures, absolute paths, secrets, missing digests, mismatched candidate tree, malformed token usage, omitted setup/init/review/evaluation time, an automated intent/approval event counted as human, unbalanced additive timing, or A11 Close without complete expert callback evidence.
- [ ] Write recursive output-schema tests that reject `const`/`enum`/object/array/string/number constraints without explicit compatible `type`, unsupported keywords and unbounded additional properties before any Provider reservation.
- [ ] Implement schema and semantic validation, then run the focused test file green.
- [ ] Run `uv run ruff check src/ai_sdlc/benefit_benchmark.py scripts/ai_sdlc_v2_benefit_benchmark.py tests/unit/test_benefit_benchmark.py` and `git diff --check`.
- [ ] Commit as `feat: freeze v2 benefit benchmark protocol`.

### Task 2: Re-freeze three fixtures and sealed external evaluators

**Files:**
- Create: `benchmarks/ai-sdlc-v2-benefits/fixtures/requirement-contract-ambiguity/public/**`
- Create: `benchmarks/ai-sdlc-v2-benefits/fixtures/frontend-recovery-delivery/public/**`
- Create: `benchmarks/ai-sdlc-v2-benefits/fixtures/multi-tenant-security-review/public/**`
- Create: `benchmarks/ai-sdlc-v2-benefits/fixtures/manifest.json`
- Create: `benchmarks/ai-sdlc-v2-benefits/fixtures/sealed-commitments.json`
- Create: `src/ai_sdlc/benefit_benchmark_fixtures.py`
- Create: `tests/unit/test_benefit_benchmark_fixtures.py`
- Runtime only, never tracked before matrix completion: `<protected-evaluator-root>/<lock-id>/**`

**Interfaces:**
- `prepare_fixture(fixture_id: str, destination: Path) -> PreparedFixture`
- `evaluate_fixture(fixture_id: str, candidate: Path, sealed_root: Path) -> EvaluationResult`
- `scan_candidate_for_sealed_leak(candidate: Path, sealed_manifest: Path) -> list[BenchmarkIssue]`

- [ ] Write failing tests that require each public fixture to initialize as a clean Git repo, match every visible command's frozen expected exit/signature, fail at least one sealed criterion, and have a stable manifest digest. For frontend RED, the expected non-zero test and exact failure signature make the baseline self-check pass.
- [ ] Extract only old T1/T2 inputs and evaluator logic from their frozen Git objects; rewrite provenance to v2 benchmark paths and do not copy old outcomes, prompts, wrappers or receipts.
- [ ] Remove method leakage before freezing: delete the old T1 sentence naming the missing AC, replace the source comment that reveals the defect/fix, expose any scored frontend standard equally to all arms, and remove T2 four-role/majority/veto/operator wording.
- [ ] Build the new requirement/design fixture with a literal weighted rubric and frozen intent/approval map only in the protected evaluator root; tracked files contain their schema and commitments, not plaintext criteria, weights or answers.
- [ ] Replace live clarification/approval continuations with a deterministic local service: one public `question_id` taxonomy, one sealed-but-identical answer mapping, and `approval_request(run_id, approval_type, proposal_digest)` transactions for all arms. Log automated service events, never human events, and keep them inside the original Provider session. Unknown questions or invalid proposal digests return the same unresolved/revise result.
- [ ] Add held-out T2 tenant/time/action/audit variants that never enter a tracked Provider-readable Git object; bind only their sealed manifest digest into the public protocol.
- [ ] Add new T1 held-out consecutive-failure/recovery, delayed-response race, rapid double-submit and malformed-response variants that never enter a tracked Provider-readable Git object; old T1 tests alone cannot contribute sealed credit.
- [ ] Add one method-neutral `input-contract.json` per fixture and a zero-Provider canonical pre-state builder. Fixture 1 must produce a frozen Requirement; fixtures 2–3 must produce frozen Requirement plus closed Design Contract. A normalized parity test must prove the A-arm state contains no semantics absent from P/S inputs.
- [ ] Give all five frontend runs the same public frozen solution target and valid method-neutral program manifest. For A arms, leave confirmation pending so the live writer must run `solution-confirm --dry-run`, submit the actual proposal digest to the approval service, then execute; record this as governance, not human work.
- [ ] Materialize sealed plaintext only under the protected evaluator root and write only its commitment to the tracked protocol. The deterministic intent service runs outside the Provider sandbox and exposes only one answer per logged `question_id` request.
- [ ] Write failing leakage tests for a sealed filename, sealed SHA, rubric phrase, direct path, symlink, hardlink, environment variable, other-arm result and `--add-dir` exposure.
- [ ] Implement OS-level deny-read isolation around every Provider process and external post-process evaluation. A known canary read must fail under the exact launch profile; path secrecy alone is not accepted.
- [ ] Run visible and sealed baseline checks twice from fresh copies and assert identical result JSON.
- [ ] Run focused tests, Ruff and `git diff --check`.
- [ ] Commit as `test: add sealed v2 benefit fixtures`.

### Task 3: Build five arm environments and instruction-isolation proofs

**Files:**
- Create: `benchmarks/ai-sdlc-v2-benefits/arms/common-agent-contract.md`
- Create: `benchmarks/ai-sdlc-v2-benefits/arms/P/**`
- Create: `benchmarks/ai-sdlc-v2-benefits/arms/S/**`
- Create: `benchmarks/ai-sdlc-v2-benefits/arms/A00/**`
- Create: `benchmarks/ai-sdlc-v2-benefits/arms/A10/**`
- Create: `benchmarks/ai-sdlc-v2-benefits/arms/A11/**`
- Create: `benchmarks/ai-sdlc-v2-benefits/arms/manifest.json`
- Create: `src/ai_sdlc/benefit_benchmark_arms.py`
- Create: `tests/unit/test_benefit_benchmark_arms.py`

**Interfaces:**
- `prepare_arm(arm_id: str, fixture: PreparedFixture, destination: Path) -> PreparedArm`
- `inspect_instruction_sources(prepared: PreparedArm) -> InstructionInventory`
- `build_codex_command(prepared: PreparedArm, reservation: AttemptReservation) -> list[str]`

`PreparedArm` must freeze `provider_cwd` as the relative path `benchmark-task/`, its tree digest, and the expected resolved instruction chain.

- [ ] Write failing tests proving `P` has no `.ai-sdlc` or methodology repo skills; `S` has only the frozen Superpowers v6.3.0 adaptation; AI-SDLC arms have no Superpowers path. Inventory unavoidable Codex base/global instructions and installed plugins/skills, require the same digest across arms, and fail on any global AI-SDLC/Superpowers/methodology contamination.
- [ ] Build `P` with the common benchmark contract only.
- [ ] Vendor the Superpowers v6.3.0 single-Agent dependency closure into `S/.agents/skills`; mechanically rewrite only `superpowers:<name>` references to existing `$<name>` repo-skill names, publish the full diff/SHA, require explicit `$using-superpowers` activation, and machine-prove every referenced skill exists plus `multi_agent=false`.
- [ ] Expose the same intent/approval client to every arm. For S, require its brainstorming proposal to call the digest-bound approval transaction inside the same writer session; for all A frontend runs require the solution-confirm proposal transaction. Missing or stale approval is a run failure, never an invitation for an unbudgeted continuation.
- [ ] Build AI-SDLC from the exact v2.0.0 Git object into a shared read-only runtime and execute real `init` in each AI-SDLC run repo; record setup duration and bytes.
- [ ] Apply the Task 2 zero-Provider canonical pre-state after `init`, validate its requirement/design lifecycle and digest, and reject any run that would need an unplanned upstream Loop or contains semantics not present in the common input contract.
- [ ] For every A-arm frontend run, require the in-session `solution-confirm` dry-run/approval-service/execute sequence before implementation; prove the selected stack equals the public target and no frontend code changed before the approval receipt.
- [ ] Write failing tests proving every A arm root `AGENTS.md` equals the stock v2 adapter bytes and only A00/A10 have a nested `AGENTS.override.md`; mutation outside the exact allowlisted additions invalidates the arm.
- [ ] Write failing launch tests proving repo-root CWD or missing/mismatched `-C` is rejected. Under the exact provider CWD, A00/A10 resolve stock root+nested overlay, A11 resolves only stock root, and P/S resolve no AI-SDLC instruction.
- [ ] Implement separate hash-bound harness overlays: A00 adds only skip-scenario-Loop/expert instructions; A10 adds only keep-one-Loop/disable-runner-expert instructions; A11 has no ablation overlay and retains the exact managed review guidance.
- [ ] Implement the A11 runner-side expert contract required by the stock adapter: the live writer calls `await-bounded-review`, runner reads the frozen `loop review` snapshot, derives at most Primary plus justified Cross-risk, launches isolated `--sandbox read-only` child sessions, and returns structured Findings to the same writer callback. Persist role/reason/parent-child/Finding/repair/rereview digests. Do not treat Loop Close as proof that an expert ran.
- [ ] Keep the writer `codex exec --ephemeral` process alive through review and repair callbacks; never launch a replacement writer. Expert/rereview sessions are distinct ephemeral read-only processes with an output schema and cannot modify the parent.
- [ ] Require the still-active original writer to execute A10/A11 Close with the reviewed loop ID and `--expect-review-digest`. Persist argv/exit, expected/actual digest and callback preconditions; verifier rejects early/runner-owned Close or Close after expert failure/conflict.
- [ ] Persist per-expert proof: actual `loop review --expect-digest --read-path` argv/exit, snapshot and input byte digests, raw structured output/JSONL, unique child session, sandbox profile, and parent tree digest before/after proving read-only isolation. Validator must reject missing evidence, >2 roles, unbound Findings, repair without a new digest/unique rereview, expert failure followed by Close, or parent mutation.
- [ ] Add deterministic conflict detection for mutually exclusive Findings. Conflict yields terminal `needs_operator`, no repair/Close and no live user prompt; the sealed evaluator still scores the current Candidate and the website shows the unresolved branch.
- [ ] Write failing command tests for implicit model selection, missing `--json`, persistent session mode, writable sealed roots, network drift and mismatched reasoning effort.
- [ ] Implement one explicit OS deny-read wrapper plus writer command `codex exec --ephemeral --json --ignore-user-config --strict-config --model gpt-5.6-sol -c model_reasoning_effort=\"high\" --sandbox workspace-write -C <run-root>/benchmark-task`; set subprocess cwd to the same directory and reject disagreement. Expert variants change only to `--sandbox read-only` plus output schema. Persist relative provider CWD/tree digest, exact argv and resolved chain; verify requested model/effort from strict config and any available JSONL metadata. If served effort is not attested, report `served_effort_attestation=not_available`.
- [ ] Run focused tests, instruction inventory validation, Ruff and `git diff --check`.
- [ ] Commit as `feat: isolate v2 benchmark arms`.

### Task 4: Add zero-Provider rehearsal, evaluator determinism and GO gate

**Files:**
- Create: `src/ai_sdlc/benefit_benchmark_runner.py`
- Create: `tests/integration/test_benefit_benchmark_rehearsal.py`
- Create after successful rehearsal: `benchmarks/ai-sdlc-v2-benefits/evidence/preflight-receipt.json`

**Interfaces:**
- `rehearse(protocol_path: Path, output_root: Path) -> PreflightReceipt`
- `serve_intent_and_approval(run: PreparedRun, request: ServiceRequest) -> ServiceResponse`
- `coordinate_bounded_review(run: LiveWriterRun, request: ReviewCallback) -> ReviewCallbackResult`
- CLI `rehearse --provider off --output <new-empty-directory>`.

- [ ] Write a failing integration test requiring all 15 workspaces to prepare without Provider access, remain mutually isolated, expose no sealed bytes, use exact arm instructions, and start with identical fixture trees. Add a Provider-equivalent subprocess canary that knows the exact evaluator, control-source, source `.git`, parent and other-run paths and still cannot read them directly or through links under the final launch profile.
- [ ] Implement the runner state machine `planned -> prepared -> provider_reserved -> provider_running -> evaluated -> terminal`; rehearsal stops at `prepared` and may not reserve an attempt.
- [ ] Add deterministic fake-writer tests for the live callback protocol: proposal-digest approval, writer wait, read-only Primary/Cross-risk dispatch, Finding return to the same writer, new-session rereview, expert failure stop, and original-writer digest-bound Close. Rehearsal uses fakes and remains zero Provider.
- [ ] Add a fake two-expert conflict and prove it stops as `needs_operator`, consumes no hidden approval, emits no Close and remains one of the 15 terminal receipts.
- [ ] Write failing tests for dependency/network mutation, dirty base repo, non-empty output, stale run ID, evaluator nondeterminism, missing timeout and unbounded subprocess output.
- [ ] Implement bounded subprocess capture, exact environment allowlist, per-command SHA and deterministic evaluator double-run.
- [ ] Run rehearsal from a new temporary root; verify `provider_attempts_started=0`, 15/15 prepared and source worktree clean.
- [ ] Verify all 15 prepared commands use the identical relative provider CWD and that resolved instruction-chain digests match each arm contract; deliberately replay one command from repo root and require preflight rejection before Provider reservation.
- [ ] Dispatch independent AI-SDLC mechanism and benchmark-fairness reviews on the exact preflight commit. Any Critical/Important result is a NO-GO and must be fixed before Task 5.
- [ ] Run the focused integration tests, all benchmark tests, Ruff and `git diff --check`.
- [ ] Commit as `test: prove v2 benchmark preflight`.

### Task 5: Execute the frozen 15-run matrix under the 33-attempt lock

**Files:**
- Create: `benchmarks/ai-sdlc-v2-benefits/results/raw/<run-id>/receipt.json`
- Create: `benchmarks/ai-sdlc-v2-benefits/results/raw/<run-id>/provider-events.redacted.jsonl`
- Create: `benchmarks/ai-sdlc-v2-benefits/results/raw/<run-id>/commands.json`
- Create: `benchmarks/ai-sdlc-v2-benefits/results/provider-attempt-ledger.json`

**Interfaces:**
- CLI `run-matrix --protocol ... --output ... --authorization <lock>`.

- [ ] Freeze an authorization lock containing exact protocol SHA, code commit, model, effort, 33-attempt cap, three-technical-retry cap and zero-content-retry rule.
- [ ] Freeze and verify the call topology: 19 normal logical sessions; at most 23 after all permitted rereviews; at most 26 including three pre-output retries; seven remaining attempts are safety margin and cannot authorize an unregistered shape.
- [ ] Re-run Task 4 rehearsal against the exact execution commit; abort if any digest differs.
- [ ] Execute the 15 units in the frozen position-balanced five-round order. Before every writer, expert or rereview `codex exec`, reserve one logical attempt atomically; on any post-output failure, record terminal failure and continue to the next pre-registered run without retry. Same-session writer repair is part of the original writer attempt and all additional token/time remains in that run's cost.
- [ ] For pre-output technical failure only, classify evidence first, then reserve a retry if both retry and global caps permit.
- [ ] After every run, execute the external sealed evaluator, write the terminal receipt, redact provider events, hash all artifacts and delete the writable candidate workspace only after its tree and command outputs are captured.
- [ ] After matrix completion, verify there are exactly 15 terminal receipts, no replacement run ID, `attempts_started <= 33`, no content retries, and source worktree clean.
- [ ] Commit raw immutable results as `evidence: record v2 benefit benchmark runs`.

### Task 6: Calculate transparent comparisons and freeze the evidence bundle

**Files:**
- Create: `src/ai_sdlc/benefit_benchmark_summary.py`
- Create: `tests/unit/test_benefit_benchmark_summary.py`
- Create: `benchmarks/ai-sdlc-v2-benefits/results/summary.json`
- Create: `benchmarks/ai-sdlc-v2-benefits/results/summary.csv`
- Create: `benchmarks/ai-sdlc-v2-benefits/EVIDENCE-BOUNDARIES.zh-CN.md`
- Create after all 15 terminal receipts: `benchmarks/ai-sdlc-v2-benefits/results/evaluator-disclosure/**`
- Create: `benchmarks/ai-sdlc-v2-benefits/SHA256SUMS`

**Interfaces:**
- `summarize(receipts: Sequence[RunReceipt]) -> BenchmarkSummary`
- `render_csv(summary: BenchmarkSummary) -> str`
- `render_site_evidence(summary_path: Path, destination: Path) -> SiteEvidenceManifest`
- CLI `summarize` and `verify-bundle`.

- [ ] Write failing tests with hand-calculated literal fixtures for medians, min/max, raw counts, invalid completion, N/A precision, failure retention and the three pre-registered comparisons.
- [ ] Implement the minimal calculator; do not add significance, confidence or production extrapolation. Preserve setup, initialization, Provider, governance, expert and evaluation time/tokens as adjacent additive fields.
- [ ] Write mutation tests proving removal of a failed run, conversion of N/A to zero, hidden governance time, or HTML-friendly rounding before calculation invalidates the summary.
- [ ] Generate summary JSON/CSV from all 15 receipts and build a sorted closed-world SHA manifest.
- [ ] Only after verifying all 15 receipts are terminal, disclose the sealed plaintext, prove every file against the pre-run commitments, and include a one-command external recomputation path.
- [ ] Add deterministic site rendering in this same module; it must reject missing/hidden failed runs, wrong research-arm labels, HTML-friendly pre-rounding, missing A11 actual topology events, or any output that does not preserve the frozen run order.
- [ ] Run independent method review and AI-SDLC practice review on raw receipts plus calculator output; fix and regenerate on any Critical/Important finding.
- [ ] Run benchmark unit/integration tests, bundle verifier, Ruff and `git diff --check`.
- [ ] Commit as `feat: publish verified v2 benefit evidence`.

### Task 7: Integrate Engineering Evidence into the offline product site

**Files:**
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/engineering-evidence.html`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/evidence/**`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/index.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/downloads-docs.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/site.css`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Modify: `scripts/validate_offline_product_site.py`
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Consumes: Task 6 `render_site_evidence(summary_path, destination)`.
- Validator parity between visible values and `summary.json`.

- [ ] Write failing site tests for the sixth nav item `Engineering Evidence`, four evidence tabs, sample scope, all failures, governance/setup/expert overhead, comparison boundaries, A11 actual topology trace and the evidence bundle link.
- [ ] Require the exact six-item navigation order `AI-SDLC 2.0`, `Loop Engineering`, `Dynamic Expert Review`, `Platform Capabilities`, `Engineering Evidence`, `Downloads & Docs` on all top-level pages.
- [ ] Write failing parity tests that mutate one HTML value, hide one failed run, remove `n`, or replace an A00/A10 research label with a product-mode label.
- [ ] Add negative-copy tests rejecting claims of saved human time/steps, total-compute matching, full-SDLC effectiveness, exact Superpowers plugin installation or served model/effort attestation not present in receipts.
- [ ] Implement a static generator that copies only public/redacted evidence and emits semantic HTML; JavaScript only enhances tabs and never supplies the sole copy of a result.
- [ ] Reuse the existing generic tab implementation without changing `site.js`; reject any runtime `fetch`, dynamic import or second result source.
- [ ] Add a compact homepage evidence rail with exactly the pre-registered metrics: `external_verified_delivery_count` for P/S/A11, `median_weighted_ac_coverage` for A00/A10, and `sum_severe_defect_escape_count` for A10/A11. Preserve zero and adverse outcomes with neutral/negative wording; values and CTAs map one-to-one to the three measured comparisons.
- [ ] Add compact deep links on Loop, Expert and Platform pages; add the offline evidence bundle on Downloads & Docs.
- [ ] Implement responsive laptop/mobile layouts without a full-width slide-strip treatment; charts must retain adjacent numeric tables and text alternatives.
- [ ] Keep the 15-run raw table, failure table and cost table visible after the Tab workspace; on small screens only each table wrapper may scroll horizontally, never the page root.
- [ ] Render one actual topology trace per A11 run, including roles/reasons, parent-child bindings, Findings, repair, rereview, conflicts and human nodes; render “未观察到” for absent events and protect the trace with summary parity mutations.
- [ ] Run site tests, full validator with guide parity and evidence parity, Node syntax checks and `git diff --check`.
- [ ] Commit as `feat: add engineering evidence to offline site`.

### Task 8: Browser acceptance and three-expert exact-hash final review

**Files:**
- Modify: `scripts/run_offline_product_site_browser_acceptance.mjs`
- Modify: `docs/product-site/design/qa/interaction-verification.md`
- Create: `docs/product-site/design/qa/engineering-evidence-*.png`
- Create: `docs/product-site/design/qa/reviewers/benefit-methodology.md`
- Create: `docs/product-site/design/qa/reviewers/ai-sdlc-mechanism.md`
- Create: `docs/product-site/design/qa/reviewers/evaluator-clarity.md`
- Modify: `docs/product-site/design/qa/final-adversarial-review.md`
- Modify: `docs/product-site/design/qa/package-manifest.sha256`

- [ ] Write failing browser-runner receipt tests for six pages, four evidence tabs, back/forward/reload, keyboard Arrow/Home/End, 1440×900, 1366×768, 1280×800, 1024×768, 390×844, no-JS readability, zero horizontal overflow and evidence/download links.
- [ ] Extend the existing browser runner and capture fresh screenshots; run from `file://` and assert zero console/page/remote-runtime errors.
- [ ] Freeze an exact reviewed product commit and run full repo tests, site tests, Ruff, Node syntax, validator, benchmark bundle verifier, manifest verifier and fresh-clone browser acceptance.
- [ ] Dispatch three independent reviewers on the same exact hash: benchmark methodology, AI-SDLC v2 mechanism truth, and evaluator-facing clarity/visual delivery.
- [ ] Fix every Critical/Important finding with TDD, create a new exact baseline and repeat all three reviews; do not reuse verdicts from an earlier hash.
- [ ] Record reviewer identity, input hashes and raw outputs; final attestation commits may change only reviewer/final-record files.
- [ ] Run final fresh-clone verification and confirm tracked worktree clean.
- [ ] Commit as `evidence: attest v2 benefit product site`.
