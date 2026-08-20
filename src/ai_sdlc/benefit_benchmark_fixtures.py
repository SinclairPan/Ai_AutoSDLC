"""Public fixtures and sealed-evaluator boundaries for the v2 benefit benchmark."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from ai_sdlc.benefit_benchmark import BenchmarkIssue

FIXTURE_IDS = (
    "requirement-contract-ambiguity",
    "frontend-recovery-delivery",
    "multi-tenant-security-review",
)
ARMS = ("P", "S", "A00", "A10", "A11")
_BENCHMARK_ROOT = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "ai-sdlc-v2-benefits"
)
_FIXTURE_ROOT = _BENCHMARK_ROOT / "fixtures"
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
EVALUATOR_PYTHON = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
_RUNTIME_IDENTITY_KEYS = {
    "schema",
    "path",
    "sha256",
    "version",
    "implementation",
    "cache_tag",
    "device",
    "inode",
    "uid",
    "gid",
    "mode",
    "nlink",
    "size",
}
_RUNTIME_COMMITMENT_KEYS = {
    "schema",
    "lock_id",
    "source_head",
    "source_tree_sha",
    "materializer_sha256",
    "source_bundle_sha256",
    "fixture_manifest_sha256",
    "fixture_tree_sha256",
    "evidence_contract_sha256",
    "sealed_manifest_sha256",
    "intent_map_sha256",
    "fixture_payloads",
    "source_root_tree_sha256",
    "evaluator_python_runtime",
    "evaluator_python_runtime_sha256",
}
_SEALED_PHRASE_MARKERS = (
    "SEALED_RUBRIC_PHRASE",
    "consecutive-failure-recovery-answer",
    "delayed-response-race-answer",
    "rapid-double-submit-answer",
    "malformed-response-answer",
    "tenant-time-action-audit-answer",
)
_DETERMINISTIC_GIT_ENV = {
    "GIT_AUTHOR_NAME": "AI-SDLC Fixture Builder",
    "GIT_AUTHOR_EMAIL": "fixture@invalid.example",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
    "GIT_COMMITTER_NAME": "AI-SDLC Fixture Builder",
    "GIT_COMMITTER_EMAIL": "fixture@invalid.example",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    "TZ": "UTC",
}


@dataclass(frozen=True)
class VisibleCommand:
    command_id: str
    argv: tuple[str, ...]
    cwd: str
    expected_exit_code: int
    expected_signature: str
    signature_stream: str


@dataclass(frozen=True)
class FixtureManifestEntry:
    fixture_id: str
    public_root: str
    public_tree_sha256: str
    input_contract_sha256: str
    provenance_commit: str
    provenance_paths: tuple[str, ...]
    visible_commands: tuple[VisibleCommand, ...]


@dataclass(frozen=True)
class FixtureManifest:
    schema: str
    fixture_ids: tuple[str, ...]
    fixtures: tuple[FixtureManifestEntry, ...]
    canonical_sha256: str
    evidence_contract_template_sha256: str
    canonical_bytes: bytes


@dataclass(frozen=True)
class VisibleCommandResult:
    command_id: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    stdout_sha256: str
    stderr_sha256: str
    expected_exit_code: int
    expected_signature: str
    matches_expected: bool


@dataclass(frozen=True)
class PreparedFixture:
    fixture_id: str
    root: Path
    public_tree_sha256: str
    initial_commit: str
    visible_results: tuple[VisibleCommandResult, ...]


@dataclass(frozen=True)
class EvaluationResult:
    fixture_id: str
    external_verified_delivery: bool
    weighted_ac_coverage: float
    severe_defect_escape_count: int
    satisfied_criteria: tuple[str, ...]
    failed_criteria: tuple[str, ...]
    result_sha256: str
    root_cause_count: int = 0
    finding_true_positive_count: int = 0
    finding_false_positive_count: int = 0
    finding_false_negative_count: int = 0
    finding_precision: float | None = None
    finding_recall: float | None = None
    severe_finding_miss_count: int = 0


@dataclass(frozen=True)
class ProviderIsolationProfile:
    run_root: Path
    sealed_root: Path
    control_root: Path
    raw_results_root: Path
    protected_roots: tuple[Path, ...]
    other_run_roots: tuple[Path, ...]
    argv: tuple[str, ...]
    environment: Mapping[str, str]
    sandbox_text: str
    issues: tuple[BenchmarkIssue, ...]
    executable: bool


@dataclass(frozen=True)
class IsolationProbeResult:
    direct: bool
    parent: bool
    symlink: bool
    hardlink: bool
    environment: bool
    other_run: bool
    add_dir: bool
    protected_root_results: tuple[tuple[str, bool], ...] = ()


class EvaluatorNoGoError(RuntimeError):
    """Fail one complete evaluation when its trusted infrastructure is uncertain."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_BROWSER_PROGRAM_KEYS = {"schema", "scenarios"}
_BROWSER_SCENARIO_KEYS = {"id", "loader", "confirmer", "actions", "assertions"}
_BROWSER_OUTCOME_KEYS = {
    "resolve": {"type", "value"},
    "reject": {"type"},
    "deferred": {"type", "key"},
}
_BROWSER_ACTION_KEYS = {
    "load": {"op", "handle", "await"},
    "retry": {"op", "handle", "await"},
    "resolve-load": {"op", "key", "value"},
    "await": {"op", "handle"},
    "render": {"op", "filter"},
    "checkpoint": {"op", "name"},
    "confirm": {"op", "risk_id", "handle", "await"},
    "release-confirms": {"op"},
    "await-all": {"op", "handles"},
}
_BROWSER_ASSERTION_KINDS = {
    "json-equal",
    "json-length",
    "dom-text-contains",
    "dom-count",
    "dom-present",
    "console-empty",
    "basic-a11y",
}


def validate_frontend_browser_program(value: object) -> tuple[str, ...]:
    """Validate a closed, data-only browser program without knowing its scenarios."""
    program = _closed_object(value, _BROWSER_PROGRAM_KEYS, "browser program")
    if program["schema"] != "ai-sdlc-v2-frontend-browser-program/v1":
        raise ValueError("browser program schema is invalid")
    scenarios = program["scenarios"]
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("browser program scenarios are invalid")
    identifiers: list[str] = []
    for raw_scenario in scenarios:
        scenario = _closed_object(
            raw_scenario, _BROWSER_SCENARIO_KEYS, "browser scenario"
        )
        identifier = scenario["id"]
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("browser scenario id is invalid")
        identifiers.append(identifier)
        loader = _closed_object(scenario["loader"], {"outcomes"}, "browser loader")
        outcomes = loader["outcomes"]
        if not isinstance(outcomes, list) or not outcomes:
            raise ValueError("browser loader outcomes are invalid")
        for raw_outcome in outcomes:
            if not isinstance(raw_outcome, Mapping):
                raise ValueError("browser loader outcome is invalid")
            outcome_type = raw_outcome.get("type")
            if outcome_type not in _BROWSER_OUTCOME_KEYS:
                raise ValueError("browser loader outcome type is invalid")
            _closed_object(
                raw_outcome,
                _BROWSER_OUTCOME_KEYS[str(outcome_type)],
                "browser loader outcome",
            )
            if outcome_type == "deferred" and (
                not isinstance(raw_outcome["key"], str) or not raw_outcome["key"]
            ):
                raise ValueError("browser deferred outcome key is invalid")
        confirmer = _closed_object(scenario["confirmer"], {"mode"}, "browser confirmer")
        if confirmer["mode"] not in {"immediate", "deferred"}:
            raise ValueError("browser confirmer mode is invalid")
        actions = scenario["actions"]
        if not isinstance(actions, list) or not actions:
            raise ValueError("browser actions are invalid")
        deferred_keys = {
            str(outcome["key"]) for outcome in outcomes if outcome["type"] == "deferred"
        }
        if len(deferred_keys) != sum(
            outcome["type"] == "deferred" for outcome in outcomes
        ):
            raise ValueError("browser deferred outcome keys are duplicated")
        handles: set[str] = set()
        resolved_keys: set[str] = set()
        checkpoints: set[str] = set()
        loader_calls = 0
        for raw_action in actions:
            if not isinstance(raw_action, Mapping):
                raise ValueError("browser action is invalid")
            operation = raw_action.get("op")
            if operation not in _BROWSER_ACTION_KEYS:
                raise ValueError("browser action operation is invalid")
            action = _closed_object(
                raw_action,
                _BROWSER_ACTION_KEYS[str(operation)],
                "browser action",
            )
            for key in {"handle", "key", "name", "risk_id"} & set(action):
                if not isinstance(action[key], str) or not action[key]:
                    raise ValueError("browser action identifier is invalid")
            if "await" in action and not isinstance(action["await"], bool):
                raise ValueError("browser action await flag is invalid")
            if "handles" in action and (
                not isinstance(action["handles"], list)
                or not action["handles"]
                or not all(isinstance(item, str) and item for item in action["handles"])
            ):
                raise ValueError("browser action handles are invalid")
            if operation == "render" and not isinstance(action["filter"], str):
                raise ValueError("browser render filter is invalid")
            if operation in {"load", "retry", "confirm"}:
                handle = str(action["handle"])
                if handle in handles:
                    raise ValueError("browser action handles are duplicated")
                handles.add(handle)
                if operation in {"load", "retry"}:
                    loader_calls += 1
            elif operation == "resolve-load":
                key = str(action["key"])
                if key not in deferred_keys or key in resolved_keys:
                    raise ValueError("browser deferred resolution is invalid")
                resolved_keys.add(key)
            elif operation == "await" and action["handle"] not in handles:
                raise ValueError("browser awaited handle is unknown")
            elif operation == "await-all" and any(
                handle not in handles for handle in action["handles"]
            ):
                raise ValueError("browser awaited handles are unknown")
            elif operation == "checkpoint":
                name = str(action["name"])
                if name in checkpoints:
                    raise ValueError("browser checkpoint names are duplicated")
                checkpoints.add(name)
            elif operation == "release-confirms" and confirmer["mode"] != "deferred":
                raise ValueError("browser confirm release mode is invalid")
        if loader_calls != len(outcomes) or resolved_keys != deferred_keys:
            raise ValueError("browser loader program is incomplete")
        assertions = scenario["assertions"]
        if not isinstance(assertions, list) or not assertions:
            raise ValueError("browser assertions are invalid")
        assertion_ids: list[str] = []
        for raw_assertion in assertions:
            assertion = _closed_object(
                raw_assertion,
                {"id", "kind", "target", "expected", "expose_as"},
                "browser assertion",
            )
            if (
                not isinstance(assertion["id"], str)
                or not assertion["id"]
                or assertion["kind"] not in _BROWSER_ASSERTION_KINDS
                or assertion["expose_as"] is not None
                and (
                    not isinstance(assertion["expose_as"], str)
                    or not assertion["expose_as"]
                )
            ):
                raise ValueError("browser assertion fields are invalid")
            kind = str(assertion["kind"])
            target = assertion["target"]
            expected = assertion["expected"]
            if kind in {"json-equal", "json-length"} and (
                not isinstance(target, list)
                or not target
                or not all(
                    (isinstance(item, str) and item)
                    or isinstance(item, int)
                    and not isinstance(item, bool)
                    and item >= 0
                    for item in target
                )
            ):
                raise ValueError("browser JSON assertion target is invalid")
            if kind in {"dom-text-contains", "dom-count", "dom-present"} and (
                not isinstance(target, str) or not target
            ):
                raise ValueError("browser DOM assertion target is invalid")
            if kind == "json-length" and (
                isinstance(expected, bool)
                or not isinstance(expected, int)
                or expected < 0
            ):
                raise ValueError("browser length assertion is invalid")
            if kind == "dom-text-contains" and (
                not isinstance(expected, list)
                or not expected
                or not all(isinstance(item, str) and item for item in expected)
            ):
                raise ValueError("browser text assertion is invalid")
            if kind == "dom-count" and (
                isinstance(expected, bool)
                or not isinstance(expected, int)
                or expected < 0
            ):
                raise ValueError("browser count assertion is invalid")
            if kind in {"dom-present", "basic-a11y"} and not isinstance(expected, bool):
                raise ValueError("browser boolean assertion is invalid")
            if kind == "console-empty" and expected != []:
                raise ValueError("browser console assertion is invalid")
            assertion_ids.append(assertion["id"])
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("browser assertion ids are duplicated")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("browser scenario ids are duplicated")
    return tuple(identifiers)


def _browser_interpreter_document() -> str:
    return r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>浏览器验收</title><link rel="icon" href="data:,"></head>
<body><main aria-labelledby="title"><h1 id="title">交付工作台</h1><nav aria-label="数据筛选"><button type="button">全部</button></nav><section id="workspace" aria-live="polite"></section></main><pre id="result">{"pending":true}</pre>
<script type="module">
import { createRiskController } from "../src/release-state.mjs";
const errors=[];
window.addEventListener("error",event=>errors.push(event.message));
window.addEventListener("unhandledrejection",event=>errors.push(String(event.reason)));
const program=await (await fetch("../browser-program.json",{cache:"no-store"})).json();
const identifier=new URL(location.href).searchParams.get("scenario");
const scenario=program.scenarios.find(item=>item.id===identifier);
if(!scenario) throw new Error("unknown scenario");
const outcomes=[...scenario.loader.outcomes];
const deferredLoads=new Map();
const confirmResolvers=[];
const handles=new Map();
const snapshots={};
let loadCalls=0;
let confirmCalls=0;
const loader=()=>{
  loadCalls+=1;
  const outcome=outcomes.shift();
  if(!outcome) return Promise.reject(new Error("missing outcome"));
  if(outcome.type==="reject") return Promise.reject(new Error("unavailable"));
  if(outcome.type==="deferred") return new Promise((resolve,reject)=>deferredLoads.set(outcome.key,{resolve,reject}));
  return Promise.resolve(outcome.value);
};
const confirmer=()=>{
  confirmCalls+=1;
  if(scenario.confirmer.mode==="immediate") return Promise.resolve(true);
  return new Promise(resolve=>confirmResolvers.push(resolve));
};
const controller=createRiskController(loader,confirmer);
function render(filter="all"){
  const workspace=document.querySelector("#workspace");
  const risks=Array.isArray(controller.state.risks)?controller.state.risks.filter(item=>filter==="all"||item.level===filter):[];
  workspace.innerHTML=controller.state.error
    ? `<div role="alert">${controller.state.error}<button type="button" data-retry>重试</button></div>`
    : `<table><caption>交付数据</caption><thead><tr><th>名称</th><th>服务</th><th>等级</th><th>负责人</th><th>状态</th></tr></thead><tbody>${risks.map(item=>`<tr data-item="${item.id}"><td>${item.name}</td><td>${item.service}</td><td>${item.level}</td><td>${item.owner}</td><td><button type="button" data-confirm="${item.id}" ${item.confirmed?"disabled":""}>确认</button></td></tr>`).join("")}</tbody></table>`;
}
for(const action of scenario.actions){
  if(action.op==="load"||action.op==="retry"){
    const promise=action.op==="load"?controller.load():controller.retry();
    handles.set(action.handle,promise);
    if(action.await) await promise;
  }else if(action.op==="resolve-load"){
    const deferred=deferredLoads.get(action.key);
    if(!deferred) throw new Error("unknown deferred load");
    deferred.resolve(action.value);
  }else if(action.op==="await"){
    await handles.get(action.handle);
  }else if(action.op==="render"){
    render(action.filter);
  }else if(action.op==="checkpoint"){
    snapshots[action.name]={state:JSON.parse(JSON.stringify(controller.state))};
  }else if(action.op==="confirm"){
    const promise=controller.confirm(action.risk_id);
    handles.set(action.handle,promise);
    if(action.await) await promise;
  }else if(action.op==="release-confirms"){
    for(const resolve of confirmResolvers.splice(0)) resolve(true);
  }else if(action.op==="await-all"){
    await Promise.all(action.handles.map(handle=>handles.get(handle)));
  }
}
const basicAccessibility=Boolean(document.querySelector("main[aria-labelledby]")&&document.querySelector("nav[aria-label]")&&(document.querySelector("table caption")||document.querySelector("[role=alert]")));
const context={state:controller.state,snapshots,load_calls:loadCalls,confirm_calls:confirmCalls,console_errors:errors,basic_accessibility:basicAccessibility};
const at=(root,path)=>path.reduce((value,part)=>value?.[part],root);
const same=(left,right)=>JSON.stringify(left)===JSON.stringify(right);
const behaviorChecks={};
const assertionResults={};
for(const assertion of scenario.assertions){
  let passed=false;
  if(assertion.kind==="json-equal") passed=same(at(context,assertion.target),assertion.expected);
  else if(assertion.kind==="json-length") passed=at(context,assertion.target)?.length===assertion.expected;
  else if(assertion.kind==="dom-text-contains") passed=assertion.expected.every(value=>document.querySelector(assertion.target)?.textContent?.includes(value));
  else if(assertion.kind==="dom-count") passed=document.querySelectorAll(assertion.target).length===assertion.expected;
  else if(assertion.kind==="dom-present") passed=Boolean(document.querySelector(assertion.target))===assertion.expected;
  else if(assertion.kind==="console-empty") passed=errors.length===0;
  else if(assertion.kind==="basic-a11y") passed=basicAccessibility===assertion.expected;
  assertionResults[assertion.id]=passed;
  if(assertion.expose_as) behaviorChecks[assertion.expose_as]=passed;
}
document.querySelector("#result").textContent=JSON.stringify({scenario:scenario.id,passed:Object.values(assertionResults).every(Boolean),load_calls:loadCalls,confirm_calls:confirmCalls,console_errors:errors,basic_accessibility:basicAccessibility,behavior_checks:behaviorChecks,assertions:assertionResults});
</script></body></html>"""


def run_frontend_browser_e2e(
    task_root: Path, browser_program: Mapping[str, object]
) -> Mapping[str, object]:
    """Execute only the supplied sealed data program in a real browser."""
    scenario_ids = validate_frontend_browser_program(browser_program)
    root = task_root.resolve(strict=True)
    release_state = root / "src" / "release-state.mjs"
    if not release_state.is_file():
        raise ValueError("frontend release-state module is missing")
    browser = Path(
        os.environ.get(
            "AI_SDLC_BENCHMARK_BROWSER",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
    )
    if not browser.is_file():
        raise RuntimeError("frozen browser executable is unavailable")
    evaluator_workspace = tempfile.TemporaryDirectory(
        prefix="ai-sdlc-frontend-evaluator-"
    )
    evaluator_root = Path(evaluator_workspace.name)
    (evaluator_root / "tests").mkdir()
    (evaluator_root / "src").mkdir()
    (evaluator_root / "tests" / "browser-interpreter.html").write_text(
        _browser_interpreter_document(), encoding="utf-8"
    )
    (evaluator_root / "browser-program.json").write_bytes(
        json.dumps(
            browser_program,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    shutil.copy2(release_state, evaluator_root / "src" / "release-state.mjs")

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: QuietHandler(
            *args, directory=str(evaluator_root), **kwargs
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    observed: dict[str, bool] = {}
    console_errors: list[str] = []
    accessibility: list[bool] = []
    behavior_checks: dict[str, bool] = {}
    try:
        playwright_module = os.environ.get("AI_SDLC_BENCHMARK_PLAYWRIGHT_MODULE")
        if playwright_module:
            module = Path(playwright_module).resolve(strict=True)
            adapter = r"""
import { pathToFileURL } from "node:url";
const [modulePath,browserPath,baseUrl,rawScenarios]=process.argv.slice(1);
const { chromium }=await import(pathToFileURL(modulePath).href);
const browser=await chromium.launch({executablePath:browserPath,headless:true});
const outputs=[];
try {
  for (const scenario of JSON.parse(rawScenarios)) {
    const page=await browser.newPage();
    const browserErrors=[];
    page.on("console",message=>{if(message.type()==="error") browserErrors.push(message.text());});
    page.on("pageerror",error=>browserErrors.push(error.message));
    await page.goto(`${baseUrl}/tests/browser-interpreter.html?scenario=${encodeURIComponent(scenario)}`,{waitUntil:"networkidle"});
    await page.waitForFunction(()=>!document.querySelector("#result")?.textContent?.includes('"pending":true'),null,{timeout:10000});
    const payload=JSON.parse(await page.locator("#result").textContent());
    payload.console_errors=[...(payload.console_errors||[]),...browserErrors];
    outputs.push(payload);
    await page.close();
  }
} finally { await browser.close(); }
console.log(JSON.stringify(outputs));
"""
            completed = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    adapter,
                    str(module),
                    str(browser),
                    f"http://127.0.0.1:{server.server_port}",
                    json.dumps(scenario_ids),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError("Playwright browser acceptance process failed")
            payloads = json.loads(completed.stdout)
            if not isinstance(payloads, list) or len(payloads) != len(scenario_ids):
                raise RuntimeError("Playwright browser acceptance output is invalid")
            for scenario, payload in zip(scenario_ids, payloads, strict=True):
                if not isinstance(payload, Mapping):
                    raise RuntimeError("Playwright browser scenario output is invalid")
                observed[scenario] = payload.get("passed") is True
                accessibility.append(payload.get("basic_accessibility") is True)
                errors = payload.get("console_errors")
                if isinstance(errors, list):
                    console_errors.extend(str(item) for item in errors)
                checks = payload.get("behavior_checks")
                if isinstance(checks, Mapping):
                    behavior_checks.update(
                        {str(key): value is True for key, value in checks.items()}
                    )
            return {
                "executed_with_real_browser": True,
                "same_origin": True,
                "scenarios": observed,
                "console_errors": console_errors,
                "basic_accessibility": all(accessibility),
                "behavior_checks": behavior_checks,
            }
        with tempfile.TemporaryDirectory(prefix="ai-sdlc-browser-profile-") as profile:
            for scenario in scenario_ids:
                url = (
                    f"http://127.0.0.1:{server.server_port}/tests/browser-interpreter.html"
                    f"?scenario={quote(scenario)}"
                )
                completed = subprocess.run(
                    [
                        str(browser),
                        "--headless",
                        f"--user-data-dir={profile}",
                        "--disable-gpu",
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-default-apps",
                        "--disable-extensions",
                        "--disable-sync",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--no-proxy-server",
                        "--proxy-bypass-list=*",
                        "--virtual-time-budget=3000",
                        "--dump-dom",
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                match = re.search(
                    r'<pre id="result">([^<]+)</pre>',
                    completed.stdout,
                    flags=re.DOTALL,
                )
                if completed.returncode != 0 or match is None:
                    raise RuntimeError("real-browser acceptance process failed")
                payload = json.loads(
                    match.group(1)
                    .replace("&quot;", '"')
                    .replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                )
                observed[scenario] = payload.get("passed") is True
                accessibility.append(payload.get("basic_accessibility") is True)
                errors = payload.get("console_errors")
                if isinstance(errors, list):
                    console_errors.extend(str(item) for item in errors)
                checks = payload.get("behavior_checks")
                if isinstance(checks, Mapping):
                    behavior_checks.update(
                        {str(key): value is True for key, value in checks.items()}
                    )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        evaluator_workspace.cleanup()
    return {
        "executed_with_real_browser": True,
        "same_origin": True,
        "scenarios": observed,
        "console_errors": console_errors,
        "basic_accessibility": all(accessibility),
        "behavior_checks": behavior_checks,
    }


def _closed_object(value: object, keys: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{label} must be a closed object")
    return value


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("fixture paths must be non-empty POSIX relative paths")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("fixture paths must stay inside the public root")
    return value


def _digest_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def evaluator_runtime_identity_sha256(identity: Mapping[str, object]) -> str:
    """Return the canonical commitment for one closed runtime identity."""
    if set(identity) != _RUNTIME_IDENTITY_KEYS:
        raise EvaluatorNoGoError("runtime-identity")
    return sha256(_canonical_json_bytes(identity)).hexdigest()


def evaluator_python_runtime_identity(
    *,
    runtime_path: Path | None = None,
    forbidden_roots: Sequence[Path] = (),
    expected_sha256: str | None = None,
) -> Mapping[str, object]:
    """Inspect the literal external evaluator runtime and freeze its full identity."""
    path = runtime_path or EVALUATOR_PYTHON
    try:
        lexical = path.lstat()
        canonical = path.resolve(strict=True)
        if (
            canonical != path
            or stat.S_ISLNK(lexical.st_mode)
            or not stat.S_ISREG(lexical.st_mode)
            or lexical.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(lexical.st_mode) & 0o022
            or not stat.S_IMODE(lexical.st_mode) & 0o111
        ):
            raise EvaluatorNoGoError("runtime-security")
        current = Path(canonical.anchor)
        for part in canonical.parent.relative_to(current).parts:
            current /= part
            ancestor = current.lstat()
            if (
                stat.S_ISLNK(ancestor.st_mode)
                or not stat.S_ISDIR(ancestor.st_mode)
                or ancestor.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(ancestor.st_mode) & 0o022
            ):
                raise EvaluatorNoGoError("runtime-security")
        for root in forbidden_roots:
            resolved = root.resolve(strict=False)
            try:
                canonical.relative_to(resolved)
                raise EvaluatorNoGoError("runtime-overlap")
            except ValueError:
                pass
        digest = _digest_file(path)
        if expected_sha256 is not None and (
            not _DIGEST.fullmatch(expected_sha256) or digest != expected_sha256
        ):
            raise EvaluatorNoGoError("runtime-identity")
        completed = subprocess.run(
            [
                str(path),
                "-I",
                "-c",
                (
                    "import json,platform,sys;"
                    "print(json.dumps({'version':platform.python_version(),"
                    "'implementation':platform.python_implementation(),"
                    "'cache_tag':sys.implementation.cache_tag},sort_keys=True))"
                ),
            ],
            cwd="/private/tmp",
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        rebound = path.lstat()
        if completed.returncode != 0 or (
            lexical.st_dev,
            lexical.st_ino,
            lexical.st_mode,
            lexical.st_uid,
            lexical.st_gid,
            lexical.st_nlink,
            lexical.st_size,
        ) != (
            rebound.st_dev,
            rebound.st_ino,
            rebound.st_mode,
            rebound.st_uid,
            rebound.st_gid,
            rebound.st_nlink,
            rebound.st_size,
        ):
            raise EvaluatorNoGoError("runtime-identity")
        version = json.loads(completed.stdout)
        if not isinstance(version, Mapping) or set(version) != {
            "version",
            "implementation",
            "cache_tag",
        }:
            raise EvaluatorNoGoError("runtime-identity")
        identity: Mapping[str, object] = {
            "schema": "ai-sdlc-v2-benefit-python-runtime/v1",
            "path": str(path),
            "sha256": digest,
            "version": version["version"],
            "implementation": version["implementation"],
            "cache_tag": version["cache_tag"],
            "device": lexical.st_dev,
            "inode": lexical.st_ino,
            "uid": lexical.st_uid,
            "gid": lexical.st_gid,
            "mode": stat.S_IMODE(lexical.st_mode),
            "nlink": lexical.st_nlink,
            "size": lexical.st_size,
        }
        evaluator_runtime_identity_sha256(identity)
        return identity
    except EvaluatorNoGoError:
        raise
    except (
        OSError,
        subprocess.TimeoutExpired,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise EvaluatorNoGoError("runtime-identity") from error


def _tree_digest(root: Path) -> str:
    if not root.is_dir():
        raise ValueError("fixture tree root is unavailable")
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if ".git" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError("public fixture trees cannot contain symlinks")
        if path.is_file():
            entries.append(
                {
                    "path": relative,
                    "mode": path.stat().st_mode & 0o777,
                    "size": path.stat().st_size,
                    "sha256": _digest_file(path),
                }
            )
    return sha256(_canonical_json_bytes(entries)).hexdigest()


def _dependency_tree_digest(root: Path) -> str:
    """Hash one preinstalled dependency tree without its absolute path."""
    if not root.is_dir():
        raise ValueError("preinstalled dependency tree is unavailable")
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append({"p": relative, "t": "l", "v": os.readlink(path)})
        elif path.is_file():
            entries.append(
                {
                    "p": relative,
                    "t": "f",
                    "s": path.stat().st_size,
                    "h": _digest_file(path),
                }
            )
    return sha256(_canonical_json_bytes(entries)).hexdigest()


def validate_frontend_runtime(
    task_root: Path,
    dependency_root: Path,
    *,
    node_binary: Path,
    browser_binary: Path,
) -> list[BenchmarkIssue]:
    """Validate the lockfile, shared preinstall and executable identities."""
    issues: list[BenchmarkIssue] = []
    try:
        environment = json.loads(
            (task_root / "environment-lock.json").read_text(encoding="utf-8")
        )
        if environment.get("package_lock_sha256") != _digest_file(
            task_root / "package-lock.json"
        ):
            issues.append(BenchmarkIssue("frontend.lockfile", "package-lock drift"))
        if environment.get(
            "preinstalled_dependency_tree_sha256"
        ) != _dependency_tree_digest(dependency_root):
            issues.append(
                BenchmarkIssue("frontend.dependencies", "preinstalled tree drift")
            )
        if environment.get("node", {}).get(
            "executable_identity_sha256"
        ) != _digest_file(node_binary):
            issues.append(BenchmarkIssue("frontend.node", "node identity drift"))
        if environment.get("browser", {}).get(
            "executable_identity_sha256"
        ) != _digest_file(browser_binary):
            issues.append(BenchmarkIssue("frontend.browser", "browser identity drift"))
    except (
        AttributeError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        issues.append(BenchmarkIssue("frontend.environment", str(error)))
    return issues


def fixture_tree_digest(fixture_root: Path = _FIXTURE_ROOT) -> str:
    """Hash public fixture trees and paired contract/commitment files without cycles."""
    inputs: list[dict[str, str]] = []
    for fixture_id in FIXTURE_IDS:
        public = fixture_root / fixture_id / "public"
        inputs.append(
            {"fixture_id": fixture_id, "public_tree_sha256": _tree_digest(public)}
        )
    for name in ("evidence-contract.template.json",):
        path = fixture_root / name
        inputs.append({"fixture_id": name, "public_tree_sha256": _digest_file(path)})
    return sha256(_canonical_json_bytes(inputs)).hexdigest()


def load_fixture_manifest(
    path: Path = _FIXTURE_ROOT / "manifest.json",
) -> FixtureManifest:
    canonical_bytes = path.read_bytes()
    raw = _closed_object(
        json.loads(canonical_bytes),
        {
            "schema",
            "fixture_ids",
            "fixtures",
            "canonical_sha256",
            "evidence_contract_template_sha256",
        },
        "fixture manifest",
    )
    raw_fixtures = raw["fixtures"]
    if not isinstance(raw_fixtures, list):
        raise ValueError("fixture manifest fixtures must be a list")
    entries: list[FixtureManifestEntry] = []
    for raw_entry in raw_fixtures:
        entry = _closed_object(
            raw_entry,
            {
                "fixture_id",
                "public_root",
                "public_tree_sha256",
                "input_contract_sha256",
                "provenance_commit",
                "provenance_paths",
                "visible_commands",
            },
            "fixture manifest entry",
        )
        raw_commands = entry["visible_commands"]
        if not isinstance(raw_commands, list):
            raise ValueError("visible commands must be a list")
        commands: list[VisibleCommand] = []
        for raw_command in raw_commands:
            command = _closed_object(
                raw_command,
                {
                    "command_id",
                    "argv",
                    "cwd",
                    "expected_exit_code",
                    "expected_signature",
                    "signature_stream",
                },
                "visible command",
            )
            argv = command["argv"]
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) and item for item in argv)
            ):
                raise ValueError("visible command argv must be non-empty strings")
            commands.append(
                VisibleCommand(
                    command_id=str(command["command_id"]),
                    argv=tuple(argv),
                    cwd=_safe_relative(command["cwd"]),
                    expected_exit_code=int(command["expected_exit_code"]),
                    expected_signature=str(command["expected_signature"]),
                    signature_stream=str(command["signature_stream"]),
                )
            )
        provenance_paths = entry["provenance_paths"]
        if not isinstance(provenance_paths, list) or not all(
            isinstance(item, str) and item for item in provenance_paths
        ):
            raise ValueError("provenance paths must be non-empty strings")
        entries.append(
            FixtureManifestEntry(
                fixture_id=str(entry["fixture_id"]),
                public_root=_safe_relative(entry["public_root"]),
                public_tree_sha256=str(entry["public_tree_sha256"]),
                input_contract_sha256=str(entry["input_contract_sha256"]),
                provenance_commit=str(entry["provenance_commit"]),
                provenance_paths=tuple(provenance_paths),
                visible_commands=tuple(commands),
            )
        )
    fixture_ids = raw["fixture_ids"]
    if not isinstance(fixture_ids, list) or not all(
        isinstance(item, str) for item in fixture_ids
    ):
        raise ValueError("fixture_ids must be a list of strings")
    return FixtureManifest(
        schema=str(raw["schema"]),
        fixture_ids=tuple(fixture_ids),
        fixtures=tuple(entries),
        canonical_sha256=str(raw["canonical_sha256"]),
        evidence_contract_template_sha256=str(raw["evidence_contract_template_sha256"]),
        canonical_bytes=canonical_bytes,
    )


def validate_fixture_manifest(
    manifest: FixtureManifest, fixture_root: Path = _FIXTURE_ROOT
) -> list[BenchmarkIssue]:
    issues: list[BenchmarkIssue] = []
    if manifest.schema != "ai-sdlc-v2-benefit-fixture-manifest/v1":
        issues.append(BenchmarkIssue("fixture.manifest.schema", "unexpected schema"))
    if (
        manifest.fixture_ids != FIXTURE_IDS
        or tuple(item.fixture_id for item in manifest.fixtures) != FIXTURE_IDS
    ):
        issues.append(
            BenchmarkIssue(
                "fixture.manifest.coverage", "fixture order must match protocol"
            )
        )
    for entry in manifest.fixtures:
        public = fixture_root / entry.public_root
        try:
            public.relative_to(fixture_root)
            actual_tree = _tree_digest(public)
            actual_input = _digest_file(
                public / "benchmark-task" / "input-contract.json"
            )
        except (OSError, ValueError) as error:
            issues.append(BenchmarkIssue("fixture.manifest.path", str(error)))
            continue
        if actual_tree != entry.public_tree_sha256:
            issues.append(
                BenchmarkIssue(
                    "fixture.manifest.tree", f"{entry.fixture_id} tree drift"
                )
            )
        if actual_input != entry.input_contract_sha256:
            issues.append(
                BenchmarkIssue(
                    "fixture.manifest.input-contract",
                    f"{entry.fixture_id} input contract drift",
                )
            )
        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", entry.provenance_commit):
            issues.append(
                BenchmarkIssue(
                    "fixture.manifest.provenance",
                    f"{entry.fixture_id} provenance commit is invalid",
                )
            )
    try:
        if manifest.canonical_sha256 != fixture_tree_digest(fixture_root):
            issues.append(
                BenchmarkIssue("fixture.manifest.digest", "fixture digest drift")
            )
        template = fixture_root / "evidence-contract.template.json"
        if _digest_file(template) != manifest.evidence_contract_template_sha256:
            issues.append(
                BenchmarkIssue(
                    "fixture.manifest.evidence-contract",
                    "evidence contract template drift",
                )
            )
    except (OSError, ValueError) as error:
        issues.append(BenchmarkIssue("fixture.manifest.digest", str(error)))
    return issues


def _entry_for(fixture_id: str, manifest: FixtureManifest) -> FixtureManifestEntry:
    if fixture_id not in FIXTURE_IDS:
        raise ValueError("fixture id is not frozen")
    entry = next(
        (item for item in manifest.fixtures if item.fixture_id == fixture_id), None
    )
    if entry is None:
        raise ValueError("fixture manifest does not cover fixture")
    return entry


def _run_visible_commands(
    root: Path, entry: FixtureManifestEntry
) -> tuple[VisibleCommandResult, ...]:
    results: list[VisibleCommandResult] = []
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    for command in entry.visible_commands:
        completed = subprocess.run(
            list(command.argv),
            cwd=root / command.cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        stdout = re.sub(
            r"(?m)^(Ran \d+ tests? in )\d+(?:\.\d+)?s$",
            r"\g<1><elapsed>s",
            completed.stdout,
        )
        stderr = re.sub(
            r"(?m)^(Ran \d+ tests? in )\d+(?:\.\d+)?s$",
            r"\g<1><elapsed>s",
            completed.stderr,
        )
        stream = stdout if command.signature_stream == "stdout" else stderr
        results.append(
            VisibleCommandResult(
                command_id=command.command_id,
                argv=command.argv,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                stdout_sha256=sha256(stdout.encode()).hexdigest(),
                stderr_sha256=sha256(stderr.encode()).hexdigest(),
                expected_exit_code=command.expected_exit_code,
                expected_signature=command.expected_signature,
                matches_expected=(
                    completed.returncode == command.expected_exit_code
                    and command.expected_signature in stream
                ),
            )
        )
    return tuple(results)


def prepare_fixture(
    fixture_id: str,
    destination: Path,
    *,
    fixture_root: Path = _FIXTURE_ROOT,
) -> PreparedFixture:
    """Copy one public fixture into a clean deterministic single-root Git repository."""
    manifest = load_fixture_manifest(fixture_root / "manifest.json")
    issues = validate_fixture_manifest(manifest, fixture_root)
    if issues:
        raise ValueError("fixture manifest is invalid")
    entry = _entry_for(fixture_id, manifest)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("fixture destination must be absent or empty")
    source = fixture_root / entry.public_root
    shutil.copytree(source, destination, dirs_exist_ok=True)
    public_digest = _tree_digest(destination)
    if public_digest != entry.public_tree_sha256:
        raise ValueError("copied fixture does not match the frozen public tree")
    env = {**os.environ, **_DETERMINISTIC_GIT_ENV}
    commands = (
        ["git", "init", "--quiet", "--initial-branch=main"],
        ["git", "config", "core.autocrlf", "false"],
        ["git", "add", "--all"],
        [
            "git",
            "-c",
            "user.name=AI-SDLC Fixture Builder",
            "-c",
            "user.email=fixture@invalid.example",
            "commit",
            "--quiet",
            "--message",
            f"fixture: freeze {fixture_id}",
        ],
    )
    for argv in commands:
        subprocess.run(argv, cwd=destination, env=env, check=True, capture_output=True)
    initial_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=destination,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    visible_results = _run_visible_commands(destination, entry)
    if not all(result.matches_expected for result in visible_results):
        raise ValueError("visible baseline does not match the frozen command contract")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=destination,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ValueError("visible baseline commands modified the fixture")
    return PreparedFixture(
        fixture_id=fixture_id,
        root=destination,
        public_tree_sha256=public_digest,
        initial_commit=initial_commit,
        visible_results=visible_results,
    )


_SEMANTIC_SURFACES: Mapping[str, object] = {
    "requirement-contract-ambiguity": {
        "business_goal": None,
        "requirement": {
            "state": None,
            "actors": None,
            "capabilities": None,
            "constraints": None,
        },
        "project_summary": {
            "runtime": None,
            "storage": None,
            "integration": None,
            "existing_endpoints": None,
        },
        "deliverable": {"path": None, "required_sections": None},
        "question_taxonomy": None,
    },
    "frontend-recovery-delivery": {
        "business_goal": None,
        "requirement": {"state": None, "acceptance_criteria": None},
        "design_contract": {
            "state": None,
            "controller_api": None,
            "response_shape": None,
            "stale_response_policy": None,
            "submit_policy": None,
            "error_policy": None,
        },
        "deliverable": {"root": None, "visible_commands": None},
        "solution_target": {
            "frontend_stack": None,
            "provider_id": None,
            "style_pack_id": None,
        },
    },
    "multi-tenant-security-review": {
        "business_goal": None,
        "requirement": {"state": None, "rules": None},
        "design_contract": {"state": None, "invariants": None},
        "deliverable": {"source": None, "visible_command": None, "boundary": None},
    },
}


def _validate_closed_surface(value: object, surface: object, label: str) -> None:
    if surface is None:
        return
    if not isinstance(value, Mapping) or not isinstance(surface, Mapping):
        raise ValueError(f"{label} must be a closed object")
    if set(value) != set(surface):
        raise ValueError(f"{label} must be a closed object")
    for key, child_surface in surface.items():
        _validate_closed_surface(value[key], child_surface, f"{label}.{key}")


def normalized_semantic_view(value: Mapping[str, object]) -> Mapping[str, object]:
    """Validate and project the complete method-neutral semantic surface."""
    fixture_id = value.get("fixture_id")
    if fixture_id not in FIXTURE_IDS:
        raise ValueError("semantic fixture binding is invalid")
    semantics = value.get("semantics")
    if not isinstance(semantics, Mapping):
        raise ValueError("semantic contract is missing")
    _validate_closed_surface(
        semantics, _SEMANTIC_SURFACES[str(fixture_id)], "semantic contract"
    )
    normalized: dict[str, object] = {
        "fixture_id": fixture_id,
        "target_stage": value.get("target_stage"),
        "canonical_pre_state": value.get("canonical_pre_state"),
        "semantics": semantics,
    }
    if fixture_id == "frontend-recovery-delivery":
        normalized["solution_target"] = value.get("solution_target")
    expected_top = {
        "schema",
        "fixture_id",
        "target_stage",
        "canonical_pre_state",
        "semantics",
        *(("solution_target",) if fixture_id == "frontend-recovery-delivery" else ()),
    }
    canonical_top = expected_top | {"source_input_contract_sha256"}
    if set(value) not in {frozenset(expected_top), frozenset(canonical_top)}:
        raise ValueError("semantic envelope must be a closed object")
    return json.loads(_canonical_json_bytes(normalized))


def build_canonical_pre_state(
    fixture_id: str, prepared_root: Path, destination: Path
) -> Mapping[str, object]:
    """Build deterministic A-arm state without adding semantics to the public contract."""
    if fixture_id not in FIXTURE_IDS:
        raise ValueError("fixture id is not frozen")
    source = prepared_root / "benchmark-task" / "input-contract.json"
    contract = json.loads(source.read_text(encoding="utf-8"))
    normalized = normalized_semantic_view(contract)
    semantics = normalized["semantics"]
    target_stage = str(contract.get("target_stage"))
    canonical_pre_state = (
        ["requirement"]
        if target_stage == "design-contract"
        else ["requirement", "design-contract"]
    )
    state = {
        "schema": "ai-sdlc-v2-benefit-canonical-pre-state/v1",
        "fixture_id": fixture_id,
        "target_stage": target_stage,
        "canonical_pre_state": canonical_pre_state,
        "semantics": semantics,
        "source_input_contract_sha256": _digest_file(source),
    }
    if fixture_id == "frontend-recovery-delivery":
        state["solution_target"] = normalized["solution_target"]
    if normalized_semantic_view(state) != normalized:
        raise ValueError("canonical pre-state semantic parity failed")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "canonical-pre-state.json").write_bytes(
        _canonical_json_bytes(state) + b"\n"
    )
    return state


class FrozenIntentApprovalService:
    """Deterministic, automated-only intent and proposal-digest approval service."""

    def __init__(self, sealed_mapping: Path, event_log: Path):
        raw = _closed_object(
            json.loads(sealed_mapping.read_text(encoding="utf-8")),
            {"schema", "questions", "approvals"},
            "intent map",
        )
        if raw["schema"] not in {
            "ai-sdlc-v2-benefit-intent-map/v1",
            "ai-sdlc-v2-benefit-intent-map/v2",
        }:
            raise ValueError("intent map schema is invalid")
        if not isinstance(raw["questions"], Mapping) or not isinstance(
            raw["approvals"], list
        ):
            raise ValueError("intent map content is invalid")
        self._questions = raw["questions"]
        self._approvals = frozenset(raw["approvals"])
        self._expected_proposals: dict[tuple[str, str], str] = {}
        self._expired_runs: set[str] = set()
        self._event_log = event_log

        for question_id, question in self._questions.items():
            if (
                not isinstance(question_id, str)
                or not isinstance(question, Mapping)
                or set(question) != {"answer", "delay_ms"}
                or isinstance(question["delay_ms"], bool)
                or not isinstance(question["delay_ms"], int)
                or question["delay_ms"] < 0
            ):
                raise ValueError("intent map question surface is invalid")
        if not all(isinstance(item, str) and item for item in self._approvals):
            raise ValueError("intent map approval surface is invalid")

    @classmethod
    def from_sealed_root(
        cls, sealed_root: Path, event_log: Path
    ) -> FrozenIntentApprovalService:
        root = sealed_root.resolve(strict=True)
        manifest_path = root / "sealed-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, Mapping):
            raise ValueError("sealed manifest is invalid")
        intent = manifest.get("intent_map")
        if not isinstance(intent, Mapping) or set(intent) != {"path", "sha256"}:
            raise ValueError("sealed intent-map commitment is invalid")
        relative = _safe_relative(intent["path"])
        path = root / relative
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise ValueError("sealed intent map escapes evaluator root") from error
        if _digest_file(path) != intent["sha256"]:
            raise ValueError("sealed intent-map commitment mismatch")
        return cls(path, event_log)

    def _record(self, payload: Mapping[str, object]) -> None:
        self._event_log.parent.mkdir(parents=True, exist_ok=True)
        with self._event_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def answer(self, run_id: str, question_id: str) -> Mapping[str, object]:
        item = self._questions.get(question_id)
        if isinstance(item, Mapping) and "answer" in item:
            result: Mapping[str, object] = {
                "status": "answered",
                "answer": item["answer"],
            }
        else:
            result = {"status": "unresolved"}
        self._record(
            {
                "type": "intent_service_event",
                "actor": "automated_service",
                "run_id": run_id,
                "question_id": question_id,
                "configured_delay_ms": (
                    item.get("delay_ms", 0) if isinstance(item, Mapping) else 0
                ),
                "result_sha256": sha256(_canonical_json_bytes(result)).hexdigest(),
            }
        )
        return result

    def register_proposal(
        self, run_id: str, approval_type: str, proposal_digest: str
    ) -> None:
        """Bind the controller-observed proposal before the Provider requests approval."""
        if approval_type not in self._approvals or not _DIGEST.fullmatch(
            proposal_digest
        ):
            raise ValueError("proposal registration is invalid")
        key = (run_id, approval_type)
        existing = self._expected_proposals.get(key)
        if existing is not None and existing != proposal_digest:
            raise ValueError("proposal registration is immutable")
        self._expected_proposals[key] = proposal_digest

    def expire_run(self, run_id: str) -> None:
        """Deterministically invalidate later approval requests for one run."""
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run expiry is invalid")
        self._expired_runs.add(run_id)
        self._record(
            {
                "type": "approval_expiry_event",
                "actor": "automated_service",
                "run_id": run_id,
            }
        )

    def approval_request(
        self, run_id: str, approval_type: str, proposal_digest: str
    ) -> Mapping[str, object]:
        expected = self._expected_proposals.get((run_id, approval_type))
        valid_digest = (
            run_id not in self._expired_runs
            and bool(_DIGEST.fullmatch(proposal_digest))
            and proposal_digest != "0" * 64
            and proposal_digest == expected
        )
        if approval_type in self._approvals and valid_digest:
            result: Mapping[str, object] = {
                "status": "approved",
                "proposal_digest": proposal_digest,
            }
        else:
            result = {"status": "revise"}
        self._record(
            {
                "type": "approval_service_event",
                "actor": "automated_service",
                "run_id": run_id,
                "approval_type": approval_type,
                "proposal_digest": proposal_digest,
                "result_sha256": sha256(_canonical_json_bytes(result)).hexdigest(),
            }
        )
        return result


def _load_sealed_payload(fixture_id: str, sealed_root: Path) -> Mapping[str, object]:
    root = sealed_root.resolve(strict=True)
    manifest_path = root / "sealed-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_raw = json.loads(manifest_bytes)
    if not isinstance(manifest_raw, Mapping) or set(manifest_raw) not in {
        frozenset({"schema", "lock_id", "entries"}),
        frozenset({"schema", "lock_id", "entries", "intent_map"}),
        frozenset(
            {
                "schema",
                "lock_id",
                "entries",
                "intent_map",
                "evaluator_python_runtime_sha256",
            }
        ),
    }:
        raise ValueError("sealed manifest must be a closed object")
    manifest = manifest_raw
    if manifest["schema"] not in {
        "ai-sdlc-v2-benefit-sealed-manifest/v1",
        "ai-sdlc-v2-benefit-sealed-manifest/v2",
        "ai-sdlc-v2-benefit-sealed-manifest/v3",
    }:
        raise ValueError("sealed manifest schema is invalid")
    entries = manifest["entries"]
    if not isinstance(entries, list) or len(entries) != len(FIXTURE_IDS):
        raise ValueError("sealed manifest coverage is invalid")
    entry = next(
        (
            item
            for item in entries
            if isinstance(item, Mapping) and item.get("fixture_id") == fixture_id
        ),
        None,
    )
    if not isinstance(entry, Mapping) or set(entry) != {"fixture_id", "path", "sha256"}:
        raise ValueError("sealed manifest entry is invalid")
    relative = _safe_relative(entry["path"])
    payload_path = root / relative
    try:
        payload_path.resolve(strict=True).relative_to(root)
    except ValueError as error:
        raise ValueError("sealed payload escapes evaluator root") from error
    payload_bytes = payload_path.read_bytes()
    if sha256(payload_bytes).hexdigest() != entry["sha256"]:
        raise ValueError("sealed payload commitment mismatch")
    payload = json.loads(payload_bytes)
    if not isinstance(payload, Mapping) or payload.get("fixture_id") != fixture_id:
        raise ValueError("sealed payload fixture binding is invalid")
    return payload


def _json_path_present(value: object, path: Sequence[object]) -> bool:
    current = value
    for part in path:
        if (
            not isinstance(part, str)
            or not isinstance(current, Mapping)
            or part not in current
        ):
            return False
        current = current[part]
    return current not in (None, "", [], {})


def _json_value_at(value: object, path: Sequence[object]) -> object:
    current = value
    for part in path:
        if (
            not isinstance(part, str)
            or not isinstance(current, Mapping)
            or part not in current
        ):
            raise KeyError("JSON path is absent")
        current = current[part]
    return current


def _subset_matches(actual: object, expected: object) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _subset_matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _subset_matches(item, expected[index])
                for index, item in enumerate(actual)
            )
        )
    return actual == expected


_SECURITY_ADAPTER = r"""
import importlib.util,json,sys
from datetime import datetime
source,raw=sys.argv[1:3]
scenario=json.loads(raw)
spec=importlib.util.spec_from_file_location("candidate_access_control",source)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
actor=module.Actor(scenario["actor_id"],scenario["actor_tenant"],frozenset(scenario["roles"]))
request=module.ApprovalRequest(scenario["request_id"],scenario["request_tenant"],scenario["requester_id"],datetime.fromisoformat(scenario["expires_at"]),scenario.get("status","pending"))
class FailingAudit(list):
    def append(self,value):
        raise RuntimeError("audit unavailable")
mode=scenario.get("audit_mode","list")
audit=None if mode=="none" else FailingAudit() if mode=="failing" else []
result=None
error=None
before={"status":request.status}
try:
    result=module.approve_request(request,actor,action=scenario.get("action","approve"),now=datetime.fromisoformat(scenario["now"]),audit_log=audit)
except Exception as exc:
    error=type(exc).__name__
events=[] if audit is None else [{"actor_id":getattr(item,"actor_id",None),"decision":getattr(item,"decision",None),"reason":getattr(item,"reason",None),"timestamp":getattr(item,"timestamp",None).isoformat() if getattr(item,"timestamp",None) else None} for item in audit]
print(json.dumps({"allowed":getattr(result,"allowed",None),"reason":getattr(result,"reason",None),"status":request.status,"status_unchanged":request.status==before["status"],"audit_count":None if audit is None else len(audit),"audit_events":events,"error":error},sort_keys=True))
"""


def _load_bound_evaluator_runtime(sealed_root: Path, candidate: Path) -> Path:
    """Revalidate the exact external runtime committed by the sealed compiler."""
    try:
        manifest_path = sealed_root / "sealed-manifest.json"
        commitments_path = sealed_root / "candidate-commitments.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        commitments = json.loads(commitments_path.read_bytes())
        if (
            not isinstance(manifest, Mapping)
            or set(manifest)
            != {
                "schema",
                "lock_id",
                "entries",
                "intent_map",
                "evaluator_python_runtime_sha256",
            }
            or manifest.get("schema") != "ai-sdlc-v2-benefit-sealed-manifest/v3"
            or not isinstance(commitments, Mapping)
            or set(commitments) != _RUNTIME_COMMITMENT_KEYS
            or commitments.get("schema")
            != "ai-sdlc-v2-benefit-candidate-commitments/v2"
            or commitments.get("lock_id") != manifest.get("lock_id")
            or commitments.get("sealed_manifest_sha256")
            != sha256(manifest_bytes).hexdigest()
        ):
            raise EvaluatorNoGoError("runtime-binding")
        identity = commitments.get("evaluator_python_runtime")
        if not isinstance(identity, Mapping):
            raise EvaluatorNoGoError("runtime-binding")
        commitment = evaluator_runtime_identity_sha256(identity)
        if commitment != commitments.get(
            "evaluator_python_runtime_sha256"
        ) or commitment != manifest.get("evaluator_python_runtime_sha256"):
            raise EvaluatorNoGoError("runtime-binding")
        runtime_path = identity.get("path")
        runtime_sha256 = identity.get("sha256")
        if not isinstance(runtime_path, str) or not isinstance(runtime_sha256, str):
            raise EvaluatorNoGoError("runtime-binding")
        current = evaluator_python_runtime_identity(
            runtime_path=Path(runtime_path),
            forbidden_roots=(
                _BENCHMARK_ROOT.parent.parent,
                sealed_root,
                candidate,
            ),
            expected_sha256=runtime_sha256,
        )
        if current != identity:
            raise EvaluatorNoGoError("runtime-identity")
        return Path(runtime_path)
    except EvaluatorNoGoError:
        raise
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise EvaluatorNoGoError("runtime-binding") from error


def _build_candidate_isolation_profile(
    *,
    candidate: Path,
    sealed_root: Path,
    raw_results: Path,
    argv: Sequence[str],
) -> ProviderIsolationProfile:
    control_root = _BENCHMARK_ROOT.parent.parent
    git_surfaces = derive_repo_git_surfaces(control_root)
    return build_provider_isolation_profile(
        run_root=candidate,
        sealed_root=sealed_root,
        control_root=control_root,
        other_run_roots=[],
        argv=argv,
        environment={"PATH": os.environ.get("PATH", "")},
        raw_results_root=raw_results,
        protected_roots=git_surfaces,
    )


def _run_candidate_adapter(
    candidate: Path,
    sealed_root: Path,
    *,
    source: Path,
    scenario: Mapping[str, object],
) -> Mapping[str, object]:
    if sys.platform != "darwin":
        raise EvaluatorNoGoError("adapter-platform")
    runtime = _load_bound_evaluator_runtime(sealed_root, candidate)
    argv = [
        str(runtime),
        "-I",
        "-c",
        _SECURITY_ADAPTER,
        str(source),
        json.dumps(scenario),
    ]
    raw_results = candidate.parent / ".evaluation-raw-results"
    raw_results.mkdir(exist_ok=True)
    profile = _build_candidate_isolation_profile(
        candidate=candidate,
        sealed_root=sealed_root,
        raw_results=raw_results,
        argv=argv,
    )
    if not profile.executable:
        raise EvaluatorNoGoError("adapter-preflight")
    try:
        completed = run_provider_isolated(profile, argv)
    except subprocess.TimeoutExpired as error:
        raise EvaluatorNoGoError("adapter-timeout") from error
    except OSError as error:
        raise EvaluatorNoGoError("adapter-launch") from error
    if "sandbox_apply: Operation not permitted" in completed.stderr:
        raise EvaluatorNoGoError("adapter-sandbox")
    if completed.returncode != 0:
        raise EvaluatorNoGoError("adapter-exit")
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise EvaluatorNoGoError("adapter-output") from None
    if not isinstance(parsed, Mapping) or "adapter_error" in parsed:
        raise EvaluatorNoGoError("adapter-output")
    return parsed


def _criterion_passes(
    candidate: Path,
    sealed_root: Path,
    criterion: Mapping[str, object],
    runtime_cache: dict[str, Mapping[str, object]] | None = None,
    browser_program: Mapping[str, object] | None = None,
) -> bool:
    kind = criterion.get("kind")
    if kind == "json_key_present":
        path = criterion.get("path")
        if not isinstance(path, list):
            return False
        target = candidate / "benchmark-task" / "design-contract.json"
        if not target.is_file():
            return False
        try:
            return _json_path_present(json.loads(target.read_text()), path)
        except json.JSONDecodeError:
            return False
    if kind in {
        "json_literal",
        "json_enum",
        "json_set_contains",
        "json_relation",
        "json_no_contradiction",
        "verification_command",
    }:
        path = criterion.get("path")
        if not isinstance(path, list):
            return False
        target = candidate / "benchmark-task" / "design-contract.json"
        if not target.is_file():
            return False
        try:
            value = _json_value_at(json.loads(target.read_text()), path)
        except (json.JSONDecodeError, KeyError):
            return False
        if kind == "json_literal":
            return value == criterion.get("expected")
        if kind == "json_enum":
            allowed = criterion.get("allowed")
            return isinstance(allowed, list) and value in allowed
        if kind == "json_set_contains":
            expected = criterion.get("expected")
            return (
                isinstance(value, list)
                and isinstance(expected, list)
                and all(item in value for item in expected)
            )
        if kind == "json_relation":
            relation = criterion.get("relation")
            if relation == "committed_fact_survives_notification_failure":
                return isinstance(value, Mapping) and value == {
                    "transaction_boundary": "commit-before-notify",
                    "notification_failure": "retry_without_rollback",
                }
            if relation == "version_guard_precedes_terminal_transition":
                return (
                    isinstance(value, Mapping)
                    and value.get("guard")
                    == [
                        "pending",
                        "request_version_matches",
                    ]
                    and value.get("effect") in {"approved", "rejected"}
                )
            return False
        if kind == "json_no_contradiction":
            forbidden = criterion.get("forbidden")
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            return isinstance(forbidden, list) and all(
                isinstance(item, str) and item not in encoded for item in forbidden
            )
        expected = criterion.get("expected")
        return (
            isinstance(value, list)
            and isinstance(expected, list)
            and value == expected
            and all(
                isinstance(item, str) and item.startswith(("python -m ", "npm run "))
                for item in value
            )
        )
    if kind in {"file_contains", "file_not_contains"}:
        relative = criterion.get("path")
        value = criterion.get("value")
        if not isinstance(relative, str) or not isinstance(value, str):
            return False
        try:
            path = candidate.joinpath(*_safe_relative(relative).split("/"))
            path.resolve(strict=True).relative_to(candidate.resolve(strict=True))
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            return False
        present = value in content
        return present if kind == "file_contains" else not present
    if kind in {"security_scenario", "security_oracle"}:
        relative = criterion.get("path")
        scenario = criterion.get("scenario")
        expected = criterion.get("expected")
        if (
            not isinstance(relative, str)
            or not isinstance(scenario, Mapping)
            or not isinstance(expected, Mapping)
        ):
            return False
        try:
            source = candidate.joinpath(*_safe_relative(relative).split("/"))
            source.resolve(strict=True).relative_to(candidate.resolve(strict=True))
        except (OSError, ValueError):
            return False
        actual = _run_candidate_adapter(
            candidate,
            sealed_root,
            source=source,
            scenario=scenario,
        )
        return _subset_matches(actual, expected)
    if kind == "frontend_browser_suite":
        expected = criterion.get("expected")
        if not isinstance(expected, Mapping) or browser_program is None:
            return False
        try:
            cache = runtime_cache if runtime_cache is not None else {}
            actual = cache.get("frontend_browser_suite")
            if actual is None:
                actual = run_frontend_browser_e2e(
                    candidate / "benchmark-task", browser_program
                )
                cache["frontend_browser_suite"] = actual
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            return False
        return _subset_matches(actual, expected)
    raise ValueError("sealed evaluator criterion kind is unsupported")


def evaluate_fixture(
    fixture_id: str, candidate: Path, sealed_root: Path
) -> EvaluationResult:
    """Score one candidate from a committed sealed payload, never from model output."""
    if fixture_id not in FIXTURE_IDS:
        raise ValueError("fixture id is not frozen")
    candidate_root = candidate.resolve(strict=True)
    sealed = sealed_root.resolve(strict=True)
    try:
        sealed.relative_to(candidate_root)
        raise ValueError("sealed evaluator root must be outside the candidate")
    except ValueError as error:
        if str(error) == "sealed evaluator root must be outside the candidate":
            raise
    payload = _load_sealed_payload(fixture_id, sealed)
    if payload.get("schema") == "ai-sdlc-v2-benefit-sealed-evaluator/v2":
        expected_surface = {
            "requirement-contract-ambiguity": {"schema", "fixture_id", "criteria"},
            "frontend-recovery-delivery": {
                "schema",
                "fixture_id",
                "criteria",
                "held_out_variant_classes",
                "browser_program",
            },
            "multi-tenant-security-review": {
                "schema",
                "fixture_id",
                "criteria",
                "held_out_variant_classes",
                "root_causes",
            },
        }[fixture_id]
        if set(payload) != expected_surface:
            raise ValueError("sealed evaluator payload must be a closed object")
    criteria = payload.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("sealed evaluator criteria are invalid")
    satisfied: list[str] = []
    failed: list[str] = []
    total_weight = 0.0
    satisfied_weight = 0.0
    severe = 0
    root_causes: set[str] = set()
    runtime_cache: dict[str, Mapping[str, object]] = {}
    for raw in criteria:
        kind = raw.get("kind") if isinstance(raw, Mapping) else None
        required_by_kind = {
            "json_key_present": {"id", "weight", "severity", "kind", "path"},
            "file_contains": {"id", "weight", "severity", "kind", "path", "value"},
            "file_not_contains": {"id", "weight", "severity", "kind", "path", "value"},
            "security_scenario": {
                "id",
                "weight",
                "severity",
                "kind",
                "path",
                "scenario",
                "expected",
            },
            "security_oracle": {
                "id",
                "weight",
                "severity",
                "kind",
                "path",
                "root_cause",
                "scenario",
                "expected",
            },
            "frontend_browser_suite": {"id", "weight", "severity", "kind", "expected"},
            "json_literal": {"id", "weight", "severity", "kind", "path", "expected"},
            "json_enum": {"id", "weight", "severity", "kind", "path", "allowed"},
            "json_set_contains": {
                "id",
                "weight",
                "severity",
                "kind",
                "path",
                "expected",
            },
            "json_relation": {"id", "weight", "severity", "kind", "path", "relation"},
            "json_no_contradiction": {
                "id",
                "weight",
                "severity",
                "kind",
                "path",
                "forbidden",
            },
            "verification_command": {
                "id",
                "weight",
                "severity",
                "kind",
                "path",
                "expected",
            },
        }
        if (
            not isinstance(raw, Mapping)
            or kind not in required_by_kind
            or set(raw) != required_by_kind[kind]
        ):
            raise ValueError("sealed evaluator criterion surface is invalid")
        if (
            fixture_id == "multi-tenant-security-review"
            and payload.get("schema") == "ai-sdlc-v2-benefit-sealed-evaluator/v2"
            and kind != "security_oracle"
        ):
            raise ValueError("security scoring must use behavioral root-cause oracles")
        if kind == "security_oracle":
            root_cause = raw.get("root_cause")
            if not isinstance(root_cause, str) or not root_cause:
                raise ValueError("security root-cause binding is invalid")
            root_causes.add(root_cause)
        identifier = str(raw["id"])
        weight = raw["weight"]
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or weight <= 0
        ):
            raise ValueError("sealed evaluator criterion weight is invalid")
        total_weight += float(weight)
        browser_program = payload.get("browser_program")
        if (
            fixture_id == "frontend-recovery-delivery"
            and payload.get("schema") == "ai-sdlc-v2-benefit-sealed-evaluator/v2"
        ):
            try:
                validate_frontend_browser_program(browser_program)
            except ValueError as error:
                raise ValueError("sealed browser program is invalid") from error
        passed = _criterion_passes(
            candidate_root,
            sealed,
            raw,
            runtime_cache,
            browser_program if isinstance(browser_program, Mapping) else None,
        )
        if passed:
            satisfied.append(identifier)
            satisfied_weight += float(weight)
        else:
            failed.append(identifier)
            if raw["severity"] in {"blocker", "important"}:
                severe += 1
    if (
        fixture_id == "requirement-contract-ambiguity"
        and payload.get("schema") == "ai-sdlc-v2-benefit-sealed-evaluator/v2"
    ):
        required_kinds = {
            "json_literal",
            "json_enum",
            "json_set_contains",
            "json_relation",
            "json_no_contradiction",
            "verification_command",
        }
        if {str(item["kind"]) for item in criteria} != required_kinds:
            raise ValueError("requirement rubric is not structurally complete")
    if (
        fixture_id == "frontend-recovery-delivery"
        and payload.get("schema") == "ai-sdlc-v2-benefit-sealed-evaluator/v2"
    ):
        if any(item.get("kind") != "frontend_browser_suite" for item in criteria):
            raise ValueError("frontend scoring must use real-browser behavior")
        if not {"FRD-AC001", "FRD-AC002", "FRD-AC006"}.issubset(
            {str(item["id"]) for item in criteria}
        ):
            raise ValueError("frontend acceptance behavior coverage is incomplete")
    if fixture_id == "multi-tenant-security-review" and root_causes:
        declared = payload.get("root_causes")
        if (
            not isinstance(declared, list)
            or len(declared) != 6
            or len(set(declared)) != 6
            or set(declared) != root_causes
        ):
            raise ValueError("security root-cause oracle coverage is invalid")
    coverage = satisfied_weight / total_weight
    tp = fp = fn = severe_miss = 0
    precision: float | None = None
    recall: float | None = None
    if fixture_id == "multi-tenant-security-review" and root_causes:
        findings_path = candidate_root / "benchmark-task" / "findings.json"
        finding_roots: set[str] = set()
        try:
            findings_raw = json.loads(findings_path.read_text(encoding="utf-8"))
            findings = findings_raw.get("findings", [])
            if isinstance(findings, list):
                finding_roots = {
                    str(item["root_cause"])
                    for item in findings
                    if isinstance(item, Mapping)
                    and isinstance(item.get("root_cause"), str)
                }
        except (OSError, json.JSONDecodeError):
            pass
        tp = len(finding_roots & root_causes)
        fp = len(finding_roots - root_causes)
        fn = len(root_causes - finding_roots)
        severe_miss = fn
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
    public_result = {
        "fixture_id": fixture_id,
        "external_verified_delivery": not failed,
        "weighted_ac_coverage": coverage,
        "severe_defect_escape_count": severe,
        "satisfied_criteria": satisfied,
        "failed_criteria": failed,
        "root_cause_count": len(root_causes),
        "finding_true_positive_count": tp,
        "finding_false_positive_count": fp,
        "finding_false_negative_count": fn,
        "finding_precision": precision,
        "finding_recall": recall,
        "severe_finding_miss_count": severe_miss,
    }
    return EvaluationResult(
        fixture_id=fixture_id,
        external_verified_delivery=not failed,
        weighted_ac_coverage=coverage,
        severe_defect_escape_count=severe,
        satisfied_criteria=tuple(satisfied),
        failed_criteria=tuple(failed),
        result_sha256=sha256(_canonical_json_bytes(public_result)).hexdigest(),
        root_cause_count=len(root_causes),
        finding_true_positive_count=tp,
        finding_false_positive_count=fp,
        finding_false_negative_count=fn,
        finding_precision=precision,
        finding_recall=recall,
        severe_finding_miss_count=severe_miss,
    )


def scan_candidate_for_sealed_leak(
    candidate: Path, sealed_manifest: Path
) -> list[BenchmarkIssue]:
    """Scan bytes, names, links and every reachable Git object using opaque findings."""
    sealed_root_path = sealed_manifest.parent.resolve(strict=True)
    raw = json.loads(sealed_manifest.read_text(encoding="utf-8"))
    entries = raw.get("entries", []) if isinstance(raw, Mapping) else []
    references = list(entries) if isinstance(entries, list) else []
    intent_reference = raw.get("intent_map") if isinstance(raw, Mapping) else None
    if isinstance(intent_reference, Mapping):
        references.append(intent_reference)
    filenames = {
        Path(str(item.get("path"))).name.encode()
        for item in references
        if isinstance(item, Mapping)
    }
    digests = {
        str(item.get("sha256")).encode()
        for item in references
        if isinstance(item, Mapping)
    }
    payload_tokens: set[bytes] = set()

    def collect(value: object) -> None:
        if not isinstance(value, Mapping):
            return
        boundary = value.get("rubric_boundary")
        if isinstance(boundary, str) and boundary:
            payload_tokens.add(boundary.encode())
        canaries = value.get("leak_canaries")
        if isinstance(canaries, list):
            payload_tokens.update(
                item.encode() for item in canaries if isinstance(item, str) and item
            )
        criteria = value.get("criteria")
        if isinstance(criteria, list):
            for criterion in criteria:
                if not isinstance(criterion, Mapping):
                    continue
                identifier = criterion.get("id")
                if isinstance(identifier, str) and identifier:
                    payload_tokens.add(identifier.encode())
                scenario = criterion.get("scenario")
                if isinstance(scenario, Mapping):
                    for key, item in scenario.items():
                        if (
                            isinstance(key, str)
                            and key.endswith("_id")
                            and isinstance(item, str)
                            and item
                        ):
                            payload_tokens.add(item.encode())

    for item in references:
        if not isinstance(item, Mapping):
            continue
        try:
            relative = _safe_relative(item.get("path"))
            payload = json.loads(
                (sealed_root_path / relative).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return [BenchmarkIssue("fixture.leak.scan-error", "sealed-payload")]
        collect(payload)
    phrase_markers = {item.encode() for item in _SEALED_PHRASE_MARKERS}
    inventory = (
        filenames
        | digests
        | payload_tokens
        | phrase_markers
        | {
            sealed_root_path.as_posix().encode(),
            f"file://{sealed_root_path.as_posix()}".encode(),
        }
    )
    try:
        protected_inodes = _protected_inodes(sealed_root_path)
    except OSError:
        return [BenchmarkIssue("fixture.leak.scan-error", "sealed-inventory")]
    issues: list[BenchmarkIssue] = []

    def opaque_location(prefix: str, value: str) -> str:
        return f"{prefix}:{sha256(value.encode()).hexdigest()[:12]}"

    def scan_blob(blob: bytes, location: str, *, git_object: bool = False) -> None:
        matches = [token for token in inventory if token and token in blob]
        if not matches:
            return
        codes: set[str] = set()
        if any(token in filenames for token in matches):
            codes.add("fixture.leak.filename")
        if any(token in digests for token in matches):
            codes.add("fixture.leak.digest")
        if any(token in payload_tokens or token in phrase_markers for token in matches):
            codes.add("fixture.leak.rubric-phrase")
        if any(b"/" in token or token.startswith(b"file://") for token in matches):
            codes.add("fixture.leak.path")
        if git_object:
            codes.add("fixture.leak.git-object")
            codes.add("fixture.leak.path-name")
        for code in sorted(codes):
            issues.append(BenchmarkIssue(code, location))

    try:
        paths = sorted(candidate.rglob("*"), key=lambda item: item.as_posix())
    except OSError:
        return [BenchmarkIssue("fixture.leak.scan-error", "candidate-tree")]
    for path in paths:
        relative = path.relative_to(candidate).as_posix()
        encoded_name = relative.encode(errors="surrogateescape")
        if any(token and token in encoded_name for token in inventory):
            issues.append(
                BenchmarkIssue(
                    "fixture.leak.path-name", opaque_location("candidate", relative)
                )
            )
        if path.is_symlink():
            issues.append(
                BenchmarkIssue(
                    "fixture.leak.symlink", opaque_location("candidate", relative)
                )
            )
            continue
        if not path.is_file() or ".git" in path.relative_to(candidate).parts:
            continue
        try:
            stat = path.stat()
            if stat.st_nlink > 1 or (stat.st_dev, stat.st_ino) in protected_inodes:
                issues.append(
                    BenchmarkIssue(
                        "fixture.leak.hardlink", opaque_location("candidate", relative)
                    )
                )
            scan_blob(path.read_bytes(), opaque_location("candidate", relative))
        except OSError:
            issues.append(
                BenchmarkIssue(
                    "fixture.leak.scan-error", opaque_location("candidate", relative)
                )
            )
            continue
    git = candidate / ".git"
    if git.exists():
        completed = subprocess.run(
            ["git", "cat-file", "--batch-all-objects", "--batch"],
            cwd=candidate,
            input=b"",
            capture_output=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            issues.append(BenchmarkIssue("fixture.leak.scan-error", "git-objects"))
        else:
            scan_blob(completed.stdout, "git:all-objects", git_object=True)
        for git_path, label in (
            (git / "index", "git:index"),
            (git / "logs", "git:reflog"),
        ):
            if git_path.is_file():
                try:
                    scan_blob(git_path.read_bytes(), label, git_object=True)
                except OSError:
                    issues.append(BenchmarkIssue("fixture.leak.scan-error", label))
            elif git_path.is_dir():
                try:
                    for child in git_path.rglob("*"):
                        if child.is_file():
                            scan_blob(child.read_bytes(), label, git_object=True)
                except OSError:
                    issues.append(BenchmarkIssue("fixture.leak.scan-error", label))
    return issues


def validate_sealed_commitments(
    commitments_path: Path, sealed_root: Path, fixture_root: Path | None = None
) -> list[BenchmarkIssue]:
    """Verify tracked digests against an evaluator root without returning plaintext."""
    issues: list[BenchmarkIssue] = []
    try:
        commitments = _closed_object(
            json.loads(commitments_path.read_text(encoding="utf-8")),
            {
                "schema",
                "lock_id",
                "sealed_manifest_sha256",
                "fixture_tree_sha256",
                "fixture_commitment",
                "evidence_contract_template_sha256",
                "evidence_contract_commitment",
                "fixture_payloads",
                "intent_map_sha256",
                "publication_state",
            },
            "sealed commitments",
        )
        if commitments["schema"] != "ai-sdlc-v2-benefit-sealed-commitments/v2":
            raise ValueError("sealed commitment schema is invalid")
        if commitments["lock_id"] != sealed_root.name:
            raise ValueError("sealed root lock id is invalid")
        public_root = fixture_root or commitments_path.parent
        fixture_digest = fixture_tree_digest(public_root)
        if not (
            commitments["fixture_tree_sha256"]
            == commitments["fixture_commitment"]
            == fixture_digest
        ):
            raise ValueError("fixture commitment pair is invalid")
        evidence_digest = _digest_file(public_root / "evidence-contract.template.json")
        if not (
            commitments["evidence_contract_template_sha256"]
            == commitments["evidence_contract_commitment"]
            == evidence_digest
        ):
            raise ValueError("evidence contract commitment pair is invalid")
        manifest = sealed_root / "sealed-manifest.json"
        if _digest_file(manifest) != commitments["sealed_manifest_sha256"]:
            raise ValueError("sealed manifest commitment is invalid")
        manifest_raw = json.loads(manifest.read_text(encoding="utf-8"))
        manifest_entries = {
            item["fixture_id"]: item["sha256"] for item in manifest_raw["entries"]
        }
        payloads = commitments["fixture_payloads"]
        if (
            not isinstance(payloads, list)
            or {
                item.get("fixture_id"): item.get("sha256")
                for item in payloads
                if isinstance(item, Mapping)
            }
            != manifest_entries
        ):
            raise ValueError("sealed payload commitments are invalid")
        intent = manifest_raw.get("intent_map")
        if (
            not isinstance(intent, Mapping)
            or set(intent) != {"path", "sha256"}
            or commitments["intent_map_sha256"] != intent["sha256"]
        ):
            raise ValueError("sealed intent-map commitment is invalid")
        intent_path = sealed_root / _safe_relative(intent["path"])
        if _digest_file(intent_path) != intent["sha256"]:
            raise ValueError("sealed intent-map digest is invalid")
        FrozenIntentApprovalService.from_sealed_root(
            sealed_root, sealed_root.parent / ".intent-validation-events.jsonl"
        )
        for fixture_id in FIXTURE_IDS:
            _load_sealed_payload(fixture_id, sealed_root)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        issues.append(BenchmarkIssue("fixture.sealed-commitment", str(error)))
    return issues


def _seatbelt_literal(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')


def _protected_inodes(root: Path) -> set[tuple[int, int]]:
    return {
        (path.stat().st_dev, path.stat().st_ino)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def _link_issues(run_root: Path) -> list[BenchmarkIssue]:
    issues: list[BenchmarkIssue] = []
    try:
        for path in run_root.rglob("*"):
            relative = sha256(
                path.relative_to(run_root).as_posix().encode()
            ).hexdigest()[:12]
            try:
                if path.is_symlink():
                    target = path.resolve(strict=False)
                    try:
                        target.relative_to(run_root)
                    except ValueError:
                        issues.append(BenchmarkIssue("isolation.symlink", relative))
                elif path.is_file() and path.stat().st_nlink > 1:
                    issues.append(BenchmarkIssue("isolation.hardlink", relative))
            except OSError:
                issues.append(BenchmarkIssue("isolation.scan-error", relative))
    except OSError:
        issues.append(BenchmarkIssue("isolation.scan-error", "run-root"))
    return issues


def _contains_path(value: str, roots: Iterable[Path]) -> bool:
    normalized = value.replace("file://", "")
    return any(str(root.resolve()) in normalized for root in roots)


def _git_surface_failure() -> ValueError:
    return ValueError("git-surface-validation")


def _git_surface_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _canonical_owned_path(
    path: Path, *, allow_file: bool
) -> tuple[Path, os.stat_result]:
    candidate = Path(os.path.abspath(path))
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise _git_surface_failure() from error
    allowed = stat.S_ISDIR(metadata.st_mode) or (
        allow_file and stat.S_ISREG(metadata.st_mode)
    )
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not allowed
        or metadata.st_uid != os.geteuid()
        or resolved != candidate
    ):
        raise _git_surface_failure()
    return resolved, metadata


def _read_gitfile(path: Path, expected: os.stat_result) -> str:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
            opened.st_size,
            opened.st_mtime_ns,
        )
        expected_identity = (
            expected.st_dev,
            expected.st_ino,
            expected.st_mode,
            expected.st_uid,
            expected.st_nlink,
            expected.st_size,
            expected.st_mtime_ns,
        )
        if (
            identity != expected_identity
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > 4096
        ):
            raise _git_surface_failure()
        data = os.read(descriptor, 4097)
        if len(data) != opened.st_size or os.read(descriptor, 1):
            raise _git_surface_failure()
        return data.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as error:
        raise _git_surface_failure() from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _git_rev_parse_path(repo_root: Path, argument: str) -> tuple[Path, os.stat_result]:
    try:
        completed = subprocess.run(
            ["/usr/bin/git", "rev-parse", "--path-format=absolute", argument],
            cwd=repo_root,
            env={
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _git_surface_failure() from error
    lines = completed.stdout.splitlines()
    if (
        completed.returncode != 0
        or len(lines) != 1
        or not lines[0]
        or lines[0] != lines[0].strip()
    ):
        raise _git_surface_failure()
    path = Path(lines[0])
    if not path.is_absolute():
        raise _git_surface_failure()
    return _canonical_owned_path(path, allow_file=False)


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def derive_repo_git_surfaces(repo_root: Path) -> tuple[Path, Path, Path]:
    """Derive and validate the worktree gitfile, gitdir and common Git directory."""
    repo, repo_metadata = _canonical_owned_path(repo_root, allow_file=False)
    git_entry = repo / ".git"
    entry, entry_metadata = _canonical_owned_path(git_entry, allow_file=True)
    pointer_value: str | None = None
    gitfile_raw: str | None = None
    if stat.S_ISREG(entry_metadata.st_mode):
        gitfile_raw = _read_gitfile(entry, entry_metadata)
        lines = gitfile_raw.splitlines()
        if len(lines) != 1 or not lines[0].startswith("gitdir: "):
            raise _git_surface_failure()
        pointer_value = lines[0][len("gitdir: ") :]
        if (
            not pointer_value
            or pointer_value != pointer_value.strip()
            or "\x00" in pointer_value
        ):
            raise _git_surface_failure()
    absolute_git_dir, gitdir_metadata = _git_rev_parse_path(repo, "--absolute-git-dir")
    common_git_dir, common_metadata = _git_rev_parse_path(repo, "--git-common-dir")
    try:
        entry_after = git_entry.lstat()
    except OSError as error:
        raise _git_surface_failure() from error
    if _git_surface_identity(entry_after) != _git_surface_identity(entry_metadata):
        raise _git_surface_failure()
    if gitfile_raw is not None and _read_gitfile(entry, entry_after) != gitfile_raw:
        raise _git_surface_failure()
    if pointer_value is None:
        if entry != absolute_git_dir or common_git_dir != absolute_git_dir:
            raise _git_surface_failure()
    else:
        pointer = Path(pointer_value)
        if not pointer.is_absolute():
            pointer = entry.parent / pointer
        try:
            pointer = pointer.resolve(strict=True)
        except OSError as error:
            raise _git_surface_failure() from error
        if pointer != absolute_git_dir:
            raise _git_surface_failure()
        if _paths_overlap(repo, absolute_git_dir) or _paths_overlap(
            repo, common_git_dir
        ):
            raise _git_surface_failure()
        try:
            gitdir_relative = absolute_git_dir.relative_to(common_git_dir)
        except ValueError as error:
            raise _git_surface_failure() from error
        if len(gitdir_relative.parts) < 2 or gitdir_relative.parts[0] != "worktrees":
            raise _git_surface_failure()
    repo_after, repo_after_metadata = _canonical_owned_path(repo, allow_file=False)
    gitdir_after, gitdir_after_metadata = _canonical_owned_path(
        absolute_git_dir, allow_file=False
    )
    common_after, common_after_metadata = _canonical_owned_path(
        common_git_dir, allow_file=False
    )
    if (
        repo_after != repo
        or gitdir_after != absolute_git_dir
        or common_after != common_git_dir
        or _git_surface_identity(repo_after_metadata)
        != _git_surface_identity(repo_metadata)
        or _git_surface_identity(gitdir_after_metadata)
        != _git_surface_identity(gitdir_metadata)
        or _git_surface_identity(common_after_metadata)
        != _git_surface_identity(common_metadata)
    ):
        raise _git_surface_failure()
    return entry, absolute_git_dir, common_git_dir


def _resolve_extra_protected_root(
    path: Path, *, index: int, issues: list[BenchmarkIssue]
) -> Path:
    """Resolve a directory or regular-file protection root without following links."""
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
    except OSError:
        issues.append(
            BenchmarkIssue("isolation.protected-root-scan", f"protected-{index}")
        )
        return candidate.absolute()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
        or metadata.st_uid != os.geteuid()
    ):
        issues.append(
            BenchmarkIssue("isolation.protected-root-type", f"protected-{index}")
        )
        return candidate.absolute()
    try:
        resolved = candidate.resolve(strict=True)
        if resolved != Path(os.path.abspath(candidate)):
            raise OSError("protected root is not canonical")
        return resolved
    except OSError:
        issues.append(
            BenchmarkIssue("isolation.protected-root-scan", f"protected-{index}")
        )
        return candidate.absolute()


def _deny_rule_for_protected_root(
    root: Path, *, index: int, issues: list[BenchmarkIssue]
) -> str | None:
    try:
        metadata = root.lstat()
    except OSError:
        issues.append(BenchmarkIssue("isolation.protected-root-scan", f"root-{index}"))
        return None
    literal = _seatbelt_literal(root)
    if stat.S_ISDIR(metadata.st_mode):
        return f'  (deny file-read* file-write* (subpath "{literal}"))'
    if stat.S_ISREG(metadata.st_mode):
        return f'  (deny file-read* file-write* (literal "{literal}"))'
    issues.append(BenchmarkIssue("isolation.protected-root-type", f"root-{index}"))
    return None


def build_provider_isolation_profile(
    *,
    run_root: Path,
    sealed_root: Path,
    control_root: Path,
    other_run_roots: Sequence[Path],
    argv: Sequence[str],
    environment: Mapping[str, str],
    raw_results_root: Path,
    protected_roots: Sequence[Path] = (),
) -> ProviderIsolationProfile:
    """Create a fail-closed macOS Provider profile plus link/env/add-dir preflight."""
    run = run_root.resolve(strict=True)
    sealed = sealed_root.resolve(strict=True)
    control = control_root.resolve(strict=True)
    raw_results = raw_results_root.resolve(strict=True)
    other = tuple(path.resolve(strict=True) for path in other_run_roots)
    issues = _link_issues(run)
    extra_protected = tuple(
        _resolve_extra_protected_root(Path(path), index=index, issues=issues)
        for index, path in enumerate(protected_roots)
    )
    protected = tuple(
        dict.fromkeys(
            (
                sealed,
                sealed.parent,
                control,
                raw_results,
                *extra_protected,
                *other,
            )
        )
    )
    for root in protected:
        try:
            run.relative_to(root)
            issues.append(BenchmarkIssue("isolation.root-overlap", "run-in-protected"))
        except ValueError:
            pass
        try:
            root.relative_to(run)
            issues.append(BenchmarkIssue("isolation.root-overlap", "protected-in-run"))
        except ValueError:
            pass
    for key, value in environment.items():
        if key != "PATH" and _contains_path(value, protected):
            issues.append(BenchmarkIssue("isolation.environment", key))
    for index, value in enumerate(argv):
        if value == "--add-dir" or value.startswith("--add-dir="):
            issues.append(BenchmarkIssue("isolation.add-dir", f"argument-{index}"))
    deny_rules = "\n".join(
        rule
        for index, path in enumerate(protected)
        if (rule := _deny_rule_for_protected_root(path, index=index, issues=issues))
        is not None
    )
    sandbox_text = f"(version 1)\n(allow default)\n{deny_rules}\n"
    executable = sys.platform == "darwin" and not issues
    return ProviderIsolationProfile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        raw_results_root=raw_results,
        protected_roots=extra_protected,
        other_run_roots=other,
        argv=tuple(argv),
        environment={"PATH": environment.get("PATH", "")},
        sandbox_text=sandbox_text,
        issues=tuple(issues),
        executable=executable,
    )


def run_provider_isolated(
    profile: ProviderIsolationProfile,
    argv: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Apply the final dynamic preflight and Seatbelt profile to one subprocess."""
    requested_environment = (
        environment if environment is not None else profile.environment
    )
    refreshed = build_provider_isolation_profile(
        run_root=profile.run_root,
        sealed_root=profile.sealed_root,
        control_root=profile.control_root,
        raw_results_root=profile.raw_results_root,
        protected_roots=profile.protected_roots,
        other_run_roots=profile.other_run_roots,
        argv=argv,
        environment=requested_environment,
    )
    if refreshed.issues:
        return subprocess.CompletedProcess(list(argv), 126, "", "ISOLATION_REFUSED\n")
    return subprocess.run(
        ["/usr/bin/sandbox-exec", "-p", refreshed.sandbox_text, *argv],
        cwd=profile.run_root,
        env=dict(refreshed.environment),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _sandbox_denies(profile: ProviderIsolationProfile, target: Path) -> bool:
    completed = run_provider_isolated(profile, ["/bin/cat", str(target)])
    if "sandbox_apply: Operation not permitted" in completed.stderr:
        raise RuntimeError(completed.stderr.strip())
    return completed.returncode != 0 and (
        "Operation not permitted" in completed.stderr
        or "ISOLATION_REFUSED" in completed.stderr
    )


def _create_protected_directory_canary(root: Path, index: int) -> Path:
    """Create one exclusive canary beneath a pinned, verified directory."""
    directory_fd = -1
    descriptor = -1
    created = False
    complete = False
    name = f".provider-isolation-canary-{index}"
    try:
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(directory_fd)
        current = root.lstat()
        if not stat.S_ISDIR(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (current.st_dev, current.st_ino):
            raise RuntimeError("protected root canary directory changed")
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        created = True
        payload = b"protected"
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short protected root canary write")
            offset += written
        os.fsync(descriptor)
        os.fsync(directory_fd)
        complete = True
        return root / name
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        cleanup_error: OSError | None = None
        if created and not complete and directory_fd >= 0:
            try:
                os.unlink(name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except OSError as error:
                cleanup_error = error
        if directory_fd >= 0:
            os.close(directory_fd)
        if cleanup_error is not None:
            raise RuntimeError(
                "protected root canary cleanup failed"
            ) from cleanup_error


def _protected_root_canary(root: Path, index: int, created: list[Path]) -> Path:
    try:
        metadata = root.lstat()
        if stat.S_ISREG(metadata.st_mode):
            return root
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("protected root canary type is unsupported")
        for path in root.rglob("*"):
            entry = path.lstat()
            if stat.S_ISREG(entry.st_mode):
                return path
            if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
                raise RuntimeError("protected root canary type is unsupported")
        canary = _create_protected_directory_canary(root, index)
        created.append(canary)
        return canary
    except (OSError, RuntimeError) as error:
        if isinstance(error, RuntimeError) and str(error).startswith(
            "protected root canary"
        ):
            raise
        raise RuntimeError("protected root canary scan failed") from error


def _cleanup_isolation_canaries(paths: Iterable[Path]) -> None:
    cleanup_failed = False
    for path in reversed(tuple(paths)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            cleanup_failed = True
    if cleanup_failed:
        raise RuntimeError("protected root canary cleanup failed")


def probe_provider_isolation(profile: ProviderIsolationProfile) -> IsolationProbeResult:
    """Exercise the exact final profile against direct, parent, link and policy canaries."""
    if sys.platform != "darwin":
        raise RuntimeError("OS deny-read profile is unavailable on this platform")
    if profile.issues:
        raise ValueError("Provider isolation preflight has unresolved issues")
    roots = tuple(
        dict.fromkeys(
            (
                profile.sealed_root,
                profile.sealed_root.parent,
                profile.control_root,
                profile.raw_results_root,
                *profile.protected_roots,
                *profile.other_run_roots,
            )
        )
    )
    created: list[Path] = []
    canaries: list[Path] = []
    symlink_path = profile.run_root / ".isolation-symlink-canary"
    hardlink_path = profile.run_root / ".isolation-hardlink-canary"
    symlink_path.unlink(missing_ok=True)
    hardlink_path.unlink(missing_ok=True)
    hardlink_created = False
    try:
        canaries = [
            _protected_root_canary(root, index, created)
            for index, root in enumerate(roots)
        ]
        denied_roots = [_sandbox_denies(profile, target) for target in canaries]
        direct = all(denied_roots)
        protected_results = tuple(
            (f"protected-root-{index}", denied)
            for index, denied in enumerate(denied_roots)
        )
        parent = _sandbox_denies(profile, profile.sealed_root.parent)
        sealed_file = canaries[0]
        os.symlink(sealed_file, symlink_path)
        try:
            os.link(sealed_file, hardlink_path)
            hardlink_created = True
        except OSError as error:
            raise RuntimeError("hardlink isolation canary is unavailable") from error
        symlink = _sandbox_denies(profile, symlink_path)
        hardlink_launch = run_provider_isolated(
            profile, ["/bin/cat", str(hardlink_path)]
        )
        hardlink = (
            hardlink_launch.returncode == 126
            and "ISOLATION_REFUSED" in hardlink_launch.stderr
        )
        other_run = all(
            _sandbox_denies(profile, target)
            for root, target in zip(roots, canaries, strict=True)
            if root in profile.other_run_roots
        )
        environment_launch = run_provider_isolated(
            profile,
            ["/usr/bin/true"],
            environment={
                "PATH": profile.environment.get("PATH", ""),
                "CANARY": str(sealed_file),
            },
        )
        environment = (
            environment_launch.returncode == 126
            and "ISOLATION_REFUSED" in environment_launch.stderr
        )
        add_dir_launches = (
            run_provider_isolated(profile, ["/usr/bin/true", "--add-dir"]),
            run_provider_isolated(
                profile,
                ["/usr/bin/true", "--add-dir=../protected"],
            ),
        )
        add_dir = all(
            launch.returncode == 126 and "ISOLATION_REFUSED" in launch.stderr
            for launch in add_dir_launches
        )
        return IsolationProbeResult(
            direct=direct,
            parent=parent,
            symlink=symlink,
            hardlink=hardlink,
            environment=environment,
            other_run=other_run,
            add_dir=add_dir,
            protected_root_results=protected_results,
        )
    finally:
        cleanup_paths = [symlink_path]
        if hardlink_created:
            cleanup_paths.append(hardlink_path)
        cleanup_paths.extend(created)
        _cleanup_isolation_canaries(cleanup_paths)
