# AI-SDLC 2.0 Benefit Benchmark Task 1 Remediation Plan

> This linear addendum replaces further patching of Task 1 after three failed fix rounds. It does not authorize Provider execution and does not change the frozen benchmark method.

**Base:** `970a6209543ce0804be05f64b25353d8dc7ccf6a`

**Invariant:** Provider calls remain `0`. Fixture commitment remains explicitly pending until Task 2, and every reservation/completion path must reject that state.

## Task 11 — Rebuild protocol lock and Provider ledger as one state machine

**Files:**

- Modify `benchmarks/ai-sdlc-v2-benefits/protocol.json`
- Modify `src/ai_sdlc/benefit_benchmark.py`
- Modify `scripts/ai_sdlc_v2_benefit_benchmark.py`
- Modify `tests/unit/test_benefit_benchmark.py`
- Modify `tests/unit/test_cli_commands.py` only for the minimal CLI regression required by a changed core signature

**Required RED cases:**

- Every execution-lock field drifts independently; each drift is rejected.
- A pending fixture commitment reaches the real reservation API and is rejected before a ledger mutation.
- A corrupt/unknown/missing attempt field is rejected on ledger load.
- Primary/Cross-risk expert before a completed writer Candidate is rejected.
- `completed + content_produced=false` is rejected for writer and expert.
- Rereview binds the first Finding, same-writer repair and a new Candidate digest; reusing the old Candidate or changing the parent chain is rejected.
- Two simultaneous reservations receive unique IDs and both persist; completion is covered by the same transaction lock.
- A writer can expose `candidate_ready/review_pending` without becoming terminal; experts depend on that checkpoint. The writer attempt becomes terminal only after required rereview/Close or a terminal failure.

**Implementation boundary:**

- Make protocol (or its exact digest plus loaded object) mandatory for reservation and completion.
- Define one closed persisted-attempt shape with kind-specific required fields and a transition table.
- Whenever a core signature changes, update the minimal CLI adapter and its regression test in the same commit so no intermediate commit has a broken CLI. Defer only full round-trip hardening to Task 1C.
- Do not edit receipt/summary schemas in this task.

**Gate:** focused tests, Ruff, diff check, one focused commit, independent review PASS.

## Task 12 — Rebuild receipt, summary and Provider-output validation

**Files:**

- Modify `benchmarks/ai-sdlc-v2-benefits/schemas/run-receipt.schema.json`
- Modify `benchmarks/ai-sdlc-v2-benefits/schemas/summary.schema.json`
- Modify `src/ai_sdlc/benefit_benchmark.py`
- Modify `scripts/ai_sdlc_v2_benefit_benchmark.py`
- Modify `tests/unit/test_benefit_benchmark.py`
- Modify `tests/unit/test_cli_commands.py` only for the minimal CLI regression required by a changed core signature

**Required RED cases:**

- Receipt with an unknown field, missing/corrupt ledger, malformed digest or mismatched canonical run fails even when protocol is supplied.
- A11 `completed` with placeholder callbacks, empty child session, missing commands, missing snapshot/input/raw-output digests, missing tree proof or broken writer→Finding→repair→new Candidate→rereview→Close order fails.
- Receipt-to-ledger closure is exact per run: missing, extra, duplicate or cross-run writer/expert/rereview/retry attempts fail. Per-attempt status, content, session and token usage must equal the receipt aggregate so expert/retry cost cannot be omitted.
- Summary with `metrics={}` fails. Each of the three preregistered metrics is required with exact arms, `n=3`, value domain, signed delta and direction; every run ID and receipt digest appears exactly once.
- Provider schema rejects non-string/invalid regex, bool numeric operands, NaN/Infinity in constraints/const/enum and arrays without typed items.
- Evidence scanning rejects embedded POSIX/Windows/UNC/case-insensitive file URI paths and CLI/env/header secrets while allowing explicit `REDACTED` values.

**Implementation boundary:**

- `verify_receipt` always runs closed JSON Schema, then protocol binding, then real ledger binding.
- `verify_summary` always runs closed JSON Schema before semantic parity checks.
- Update the minimal CLI adapter and regression whenever a core signature changes; leave only full argument/round-trip hardening to Task 1C.

**Gate:** focused tests, both schemas parse, Ruff, diff check, one focused commit, independent review PASS.

## Task 13 — Harden full CLI round trips after the minimal adapters stay green

**Files:**

- Modify `scripts/ai_sdlc_v2_benefit_benchmark.py`
- Modify `tests/unit/test_benefit_benchmark.py`
- Modify `tests/unit/test_cli_commands.py` only if the existing CLI registry contract requires it

**Required RED cases:**

- `reserve-attempt` and `complete-attempt` require `--protocol`; pending protocol exits non-zero and leaves no ledger mutation.
- Expert/retry/rereview flags round-trip into named core fields.
- `verify-receipt` requires and actually loads both `--protocol` and `--ledger`; missing/corrupt/mismatched files exit non-zero.
- `verify-summary` rejects empty metrics and a false protocol digest.
- CLI output distinguishes structural protocol validity from `execution_ready=false`; no pending state is reported as runnable.

**Gate:** focused and related CLI regression, Ruff, offline negative matrix, diff check, one focused commit, independent review PASS.

## Task 1 final gate

- A fresh reviewer replays every Critical/Important counterexample from the two failed rereviews against the final exact HEAD.
- Run the Task 1 focused and related CLI suites, Ruff, schema parsing and `git diff --check`.
- Confirm worktree clean and Provider attempt count still `0`.
- Only then mark original Task 1 complete and generate the Task 2 brief.
