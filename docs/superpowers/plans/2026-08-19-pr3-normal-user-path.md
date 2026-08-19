# PR3 Normal User Path Implementation Plan

**Goal:** Repair the existing CLI update path, surface a bounded current-Loop rule context during
normal Agent execution, and make default status/help understandable without deleting compatibility
surfaces.

**Base:** `f9fe2894475afd76aa04c617acc21369c71c0483`

**Design:** `docs/superpowers/specs/2026-08-19-pr3-normal-user-path-design.md`

## Global constraints

- Test first: every task starts with a focused failing regression and ends with its focused suite.
- Reuse the existing update advisor/cache and PR2 five-Loop route; do not build a second authority.
- Rule context is static, local, read-only, maximum two excerpts, and byte bounded.
- Preserve Local PR Review, advisory slimming, and bounded dynamic experts unchanged.
- No version lock, digest registry, rule platform, daemon, Graph, AgentOps, Telemetry, CAS, proof, or certificate.
- No physical module/command deletion and no PR4 retained-capability migration.
- Only frozen-scope, reproducible Critical/Important may consume the single repair pass.

## Task 1 — freeze the normal-path contracts

Files:

- Modify `tests/integration/test_cli_self_update.py`
- Create `tests/integration/test_cli_update_notice_process.py`
- Modify `tests/integration/test_cli_run.py`
- Modify `tests/unit/test_rules_loader.py`
- Modify `tests/integration/test_cli_status.py`
- Modify `tests/unit/test_cli_commands.py`

RED tests:

- ordinary, Loop, and JSON invocations all consult the update advisor;
- JSON stdout is parseable while stderr contains exactly one stable notice line;
- TTY confirmation updates and replays the exact original command once; decline/offline continues once;
- source/editable runtime never installs or replays;
- `run` returns at most two current-Loop excerpts without calling `rules show`;
- default status exposes only Result/Next/Blockers, while details/JSON remain available;
- top-level help omits the frozen hidden set while direct invocation remains registered.

Tests must assert HEAD, index, worktree, Loop outcome and checkpoint bytes remain unchanged on all
read-only paths. Do not implement production changes in this task.

## Task 2 — one update-notice protocol for human and Agent commands

Files:

- Modify `src/ai_sdlc/cli/main.py`
- Modify `src/ai_sdlc/cli/self_update_cmd.py`
- Modify `tests/integration/test_cli_self_update.py`
- Create `tests/integration/test_cli_update_notice_process.py`
- Modify `tests/unit/test_update_advisor.py` only if an existing cache behavior needs a regression

Implementation:

- remove the global `--json` bypass and pass an explicit human/machine render mode;
- render a deterministic `AI_SDLC_UPDATE_NOTICE {compact-json}` line to stderr for non-TTY/JSON;
- keep TTY confirmation on stderr; decline and discovery failure continue the original command;
- freeze the original executable and argv before installation; on POSIX, replay them after the install
  returns; on Windows, extend `_reexec_windows_launcher_if_needed` so its `ai-sdlc.exe`→module-updater
  `os.execve` carries a one-shot process-environment handoff instead of discarding the business command;
- make the module updater strictly parse and immediately remove that handoff, install, then run the
  updated original executable with exact argv and `shell=False`; the replay child receives a one-shot
  bypass marker that the root callback removes before invoking the business handler;
- propagate the replay exit code; missing/malformed auto-replay handoff, install failure or replay launch
  failure must be nonzero and must never claim the business command completed;
- explicit `self-update` does not create a replay handoff, and no handoff/argv may reach a file, cache,
  log, notice, persistent environment or the final business-handler environment;
- never serialize argv into notice/cache and never replay source/editable/`uv run` runtimes;
- retain existing cache freshness, automatic timeout, failure backoff, explicit-check timeout and
  Release redirect validation.

Process-level tests must use separated stdout/stderr and a disposable installed-runtime fixture. A
CliRunner-only assertion is insufficient for JSON cleanliness or replay behavior. Add a real Windows
installed-launcher regression that traverses `.exe` re-exec: updater once, business handler once and
only after install, exact argv, no recursion, exact child exit propagation, no business execution on
install failure, and consumed handoff/bypass values. A POSIX spawn or platform mock cannot replace it.

Verify:

```text
uv run pytest -q tests/integration/test_cli_self_update.py tests/integration/test_cli_update_notice_process.py tests/unit/test_update_advisor.py
uv run ruff check src/ai_sdlc/cli/main.py src/ai_sdlc/cli/self_update_cmd.py tests/integration/test_cli_self_update.py tests/integration/test_cli_update_notice_process.py tests/unit/test_update_advisor.py
```

## Task 3 — bounded five-Loop rule context

Files:

- Modify `src/ai_sdlc/rules/__init__.py`
- Modify `src/ai_sdlc/rules/prd-guidance.md`
- Modify `src/ai_sdlc/rules/scenario-routing.md`
- Modify `src/ai_sdlc/rules/tdd.md`
- Modify `src/ai_sdlc/rules/debugging.md`
- Modify `src/ai_sdlc/rules/code-review.md`
- Modify `src/ai_sdlc/rules/quality-gate.md`
- Modify `src/ai_sdlc/rules/verification.md`
- Modify `src/ai_sdlc/cli/run_cmd.py`
- Modify `tests/unit/test_rules_loader.py`
- Modify `tests/integration/test_cli_run.py`

RED tests cover the exact five-Loop mapping, implementation needs-fix substitution, maximum two
excerpts, total UTF-8 byte limit, duplicate/missing marker rejection, unknown Loop fail-closed, and no
full-file fallback.

Implementation:

- replace normal-path use of the old seven-stage hint map with an explicit five-Loop map;
- add one short marked normal-path excerpt to each selected existing rule file;
- expose names plus exact excerpts from `run`; add `run --json` with the same route and rule context;
- keep `rules` compatibility commands able to read full files, but do not reference them in normal
  Agent instructions;
- use only existing structured Loop status/task flags, never keyword scanning or model selection.

## Task 4 — compact Agent adapters and generic rules

Files:

- Modify `AGENTS.md`
- Modify `src/ai_sdlc/adapters/codex/AI-SDLC.md`
- Modify `src/ai_sdlc/adapters/claude_code/AI-SDLC.md`
- Modify `src/ai_sdlc/adapters/cursor/rules/ai-sdlc.md`
- Modify `src/ai_sdlc/adapters/vscode/AI-SDLC.md`
- Modify `src/ai_sdlc/rules/pipeline.md`
- Modify `tests/integration/test_cli_adapter.py`
- Modify `tests/unit/test_ide_adapter.py`
- Modify `tests/architecture/test_review_kernel_cutover.py`

Replace fixed framework/provider/style-pack prescriptions with a short product-neutral solution
confirmation contract. Tell host Agents to consume the rule context returned by `run`, execute PR2's
expert roles in independent contexts, and keep slimming advisory. Keep the root repository's local
self-development protocol explicitly local; prevent it from entering packaged templates.

Architecture regressions scan generic adapter/rule outputs for competition, AI-SDLC release,
historical WorkItem, fixed PrimeVue/Vue2 and retired seven-stage instructions. They also prove no
new network/write/store/Agent runtime was introduced by the rule selector.

## Task 5 — minimal default status with preserved diagnostics

Files:

- Modify `src/ai_sdlc/cli/commands.py`
- Modify `src/ai_sdlc/cli/run_cmd.py` only to share the existing renderer without duplicating truth
- Modify `tests/integration/test_cli_status.py`
- Modify `tests/integration/test_cli_run.py`

Default `status` must call the review-aware five-Loop router and render exactly Result, Next and
Blockers without adapter/project writes. Move the existing large human table behind `--details`.
Preserve the existing `--json` diagnostic contract and reject `--details --json` together. Do not
delete status builders or Telemetry models in PR3.

## Task 6 — compress top-level help without deleting commands

Files:

- Modify `src/ai_sdlc/cli/main.py`
- Modify `src/ai_sdlc/__main__.py`
- Modify `tests/unit/test_cli_commands.py`
- Modify `tests/integration/test_user_guide_contract.py`

Keep visible only `init`, `adopt`, `doctor`, `status`, `recover`, `run`, `adapter`, `workitem`,
`verify`, `loop`, `pr-review`, and `self-update`. Register the frozen advanced/history set with
Typer `hidden=True`; prove every hidden command remains directly invocable and its module remains
importable. Update fallback help and user-facing guidance to match. Do not remove files or command
implementations.

## Task 7 — enterprise normal-path acceptance

Files:

- Create `tests/integration/test_normal_user_path.py`
- Modify `README.md`
- Modify `USER_GUIDE.zh-CN.md`
- Modify `docs/product-contract.md`
- Modify `docs/v2-migration.zh-CN.md`

Create small Node, Java and Python project fixtures. For each, prove:

- ordinary and JSON commands keep their stdout/exit contract with update discovery available/offline;
- `run` returns only the current Loop's maximum-two rule context;
- no fixed frontend provider, framework release, competition, historical WorkItem, Telemetry/proof or
  unrelated rule-library text appears;
- default status is read-only and limited to Result/Next/Blockers;
- help contains the frozen visible set and hidden commands still work when explicitly invoked.

Document only verified behavior and the human/Agent upgrade interaction. Do not advertise PR4 deletion
before it exists.

## Task 8 — full verification and bounded adversarial review

Run all focused PR3 tests, then:

```text
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run ai-sdlc verify constraints
git diff --check
```

Build wheel and sdist, run a fresh installed CLI subprocess, and exercise macOS/Linux/Windows smoke
contracts including separated stdout/stderr. The Windows smoke must use the generated console `.exe`
and prove the real launcher→module updater→updated launcher replay branch, not a mocked platform check.
Re-run the Node/Java/Python fixtures from the built distribution, not only the source checkout.

Freeze HEAD/tree/base/path set/dirty state. Start exactly two independent local reviewers on the same
candidate: product-boundary/correctness and delivery/regression. Permit one focused repair for a
PR-caused Critical/Important only, then rerun focused and full verification before pushing a Draft PR.

## Stop condition

Stop when update notices cover human, Agent and JSON paths; `run` exposes bounded current-Loop rules;
status/help are compact; enterprise fixtures and full verification pass. Do not begin PR4 deletion,
release work, or a fifth governance PR.
