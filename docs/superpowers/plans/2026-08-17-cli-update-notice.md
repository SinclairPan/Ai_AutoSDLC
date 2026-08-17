# Reliable CLI Update Notice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure an older installed `ai-sdlc` discovers the current public GitHub Release and prints its update prompt before ordinary human-readable commands, including all Loop commands.

**Architecture:** Repair the existing root callback and update-advisor fetch boundary only. The callback will stop exempting the entire `loop` group, while the existing fetch function will resolve GitHub's public latest-release redirect, validate the final tag URL, and adapt it back to the current cache input dictionary.

**Tech Stack:** Python 3.11+, Typer, standard-library `urllib.request` and `urllib.parse`, pytest, Ruff.

## Global Constraints

- Start from protected main `f98c9d10e9a22071297826a106701896baabcf35`.
- Preserve `self-update`, `--json`, help, completion, bare invocation, source runtime, and editable runtime exclusions.
- Keep the existing 24-hour fresh cache, failure backoff, 1.5-second automatic timeout, opt-out environment variable, prompt UI, and explicit-confirmation update behavior.
- A network, redirect, parse, or cache-write failure must not change the requested command's exit status.
- Do not add dependencies, background processes, services, stores, authority, certificates, attestations, release operations, or unrelated refactors.

---

### Task 1: Cover human-readable Loop commands

**Files:**
- Modify: `tests/integration/test_cli_loop.py:180-196`
- Modify: `src/ai_sdlc/cli/main.py:62`

**Interfaces:**
- Consumes: Typer root callback `ctx.invoked_subcommand` and existing machine-output argument exclusions.
- Produces: exactly one call to `maybe_render_update_notice()` before a human-readable `loop` command; JSON Loop output remains notice-free.

- [ ] **Step 1: Write the failing human-readable Loop test**

Rename the existing regression and change only the expected behavior:

```python
def test_loop_status_human_runs_update_notice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / ".ai-sdlc").mkdir()
    calls: list[bool] = []
    monkeypatch.setattr(
        "ai_sdlc.cli.main.maybe_render_update_notice",
        lambda: calls.append(True),
    )

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(app, ["loop", "status"])

    assert result.exit_code == 0
    assert calls == [True]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run pytest -q tests/integration/test_cli_loop.py::test_loop_status_human_runs_update_notice
```

Expected: FAIL because `calls` is still `[]`, proving the whole-group bypass.

- [ ] **Step 3: Add the JSON non-regression test**

Add a real output assertion whose guard raises if the notice path is entered:

```python
def test_loop_status_json_remains_notice_free(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".ai-sdlc").mkdir()

    def unexpected_notice() -> None:
        raise AssertionError("JSON Loop output must not render an update notice")

    monkeypatch.setattr(
        "ai_sdlc.cli.main.maybe_render_update_notice",
        unexpected_notice,
    )

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(app, ["loop", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "no_current"
    assert payload["result"] == "No current loop."
```

The assertion stays on parseable product output rather than on mock call count.

- [ ] **Step 4: Implement the minimal callback fix**

In `src/ai_sdlc/cli/main.py`, preserve only the recursive update command bypass:

```python
_UPDATE_NOTICE_BYPASS_SUBCOMMANDS = ("self-update",)
```

Do not change adapter-hook eligibility or any subordinate Loop callback.

- [ ] **Step 5: Run Task 1 tests and verify GREEN**

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run pytest -q tests/integration/test_cli_loop.py::test_loop_status_human_runs_update_notice tests/integration/test_cli_loop.py::test_loop_status_json_remains_notice_free tests/integration/test_cli_self_update.py
uv run ruff check src/ai_sdlc/cli/main.py tests/integration/test_cli_loop.py
git diff --check
```

Expected: all selected tests and checks pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add src/ai_sdlc/cli/main.py tests/integration/test_cli_loop.py
git commit -m "fix: show updates before Loop commands"
```

---

### Task 2: Resolve the public latest-release redirect without reading HTML

**Files:**
- Modify: `tests/unit/test_update_advisor.py`
- Modify: `src/ai_sdlc/core/update_advisor.py:18-25,499-513`
- Modify: `USER_GUIDE.zh-CN.md`
- Modify: `docs/product-contract.md`

**Interfaces:**
- Consumes: `fetch_latest_github_release(timeout_seconds: float)` and GitHub's `https://github.com/SinclairPan/Ai_AutoSDLC/releases/latest` redirect.
- Produces: the existing `dict[str, Any]` fetch result with `tag_name`, `html_url`, `draft`, and `prerelease`; `_refresh_cache()` and `UpdateCache` remain unchanged.

- [ ] **Step 1: Write the failing valid-redirect test**

Add `pytest` and `fetch_latest_github_release` imports, then add:

```python
def test_latest_release_redirect_returns_existing_fetch_contract(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0"

        def read(self) -> bytes:
            raise AssertionError("release HTML body must not be read")

    def fake_urlopen(request, *, timeout: float):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(
        "ai_sdlc.core.update_advisor.urllib.request.urlopen",
        fake_urlopen,
    )

    result = fetch_latest_github_release(1.5)

    assert seen == {
        "url": "https://github.com/SinclairPan/Ai_AutoSDLC/releases/latest",
        "method": "HEAD",
        "timeout": 1.5,
    }
    assert result == {
        "tag_name": "v2.0.0",
        "html_url": "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0",
        "draft": False,
        "prerelease": False,
    }
```

- [ ] **Step 2: Run the valid test and verify RED**

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run pytest -q tests/unit/test_update_advisor.py::test_latest_release_redirect_returns_existing_fetch_contract
```

Expected: FAIL because the current fetcher requests the API and calls `.read()`/`json.loads()`.

- [ ] **Step 3: Add strict invalid-final-URL tests**

Use a literal table and the same no-body response shape:

```python
@pytest.mark.parametrize(
    "final_url",
    [
        "http://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0",
        "https://example.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0",
        "https://user@github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0",
        "https://github.com:443/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0",
        "https://github.com/Other/Ai_AutoSDLC/releases/tag/v2.0.0",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/latest",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0?x=1",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0#x",
    ],
)
def test_latest_release_redirect_rejects_noncanonical_final_url(
    monkeypatch,
    final_url: str,
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return final_url

        def read(self) -> bytes:
            raise AssertionError("release HTML body must not be read")

    monkeypatch.setattr(
        "ai_sdlc.core.update_advisor.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    with pytest.raises(ValueError, match="canonical GitHub release tag URL"):
        fetch_latest_github_release(1.5)
```

- [ ] **Step 4: Implement the minimal redirect parser**

In `src/ai_sdlc/core/update_advisor.py`, import `urlsplit`, change the constant to
the public redirect URL, and replace the fetcher's body with:

```python
def fetch_latest_github_release(timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        GITHUB_RELEASES_LATEST_URL,
        headers={"User-Agent": "ai-sdlc-update-advisor"},
        method="HEAD",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        final_url = response.geturl()

    parsed = urlsplit(final_url)
    match = re.fullmatch(
        r"/SinclairPan/Ai_AutoSDLC/releases/tag/(v\d+\.\d+\.\d+)",
        parsed.path,
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or match is None
    ):
        raise ValueError("latest release did not resolve to the canonical GitHub release tag URL")
    tag = match.group(1)
    return {
        "tag_name": tag,
        "html_url": final_url,
        "draft": False,
        "prerelease": False,
    }
```

The exact `netloc == "github.com"` check rejects userinfo and all explicit ports.
Do not add `.read()`, retry logic, a second endpoint, or a new cache field.

- [ ] **Step 5: Run unit and integration verification**

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run pytest -q tests/unit/test_update_advisor.py tests/integration/test_cli_self_update.py tests/integration/test_cli_loop.py
uv run ruff check src/ai_sdlc/core/update_advisor.py src/ai_sdlc/cli/main.py tests/unit/test_update_advisor.py tests/integration/test_cli_self_update.py tests/integration/test_cli_loop.py
git diff --check
```

Expected: all selected tests and checks pass.

- [ ] **Step 6: Document the visible behavior**

Add this paragraph to `USER_GUIDE.zh-CN.md` after the current-version notice:

```markdown
安装版 `ai-sdlc` 在执行普通人类可读命令时会检查新版本；版本真相每 24 小时至多联网刷新一次。发现更新时，CLI 会在正常结果前显示中英双语提示，只有用户明确确认后才升级。断网、超时或检查失败不会阻断当前命令；可设置 `AI_SDLC_DISABLE_UPDATE_CHECK=1` 关闭检查。
```

Add this bullet under `docs/product-contract.md` → `项目入口`:

```markdown
- 安装版 CLI 在普通人类可读命令前提供非阻断的新版本提示，并且只在用户明确确认后执行升级；
```

Do not describe internal cache paths or implementation.

- [ ] **Step 7: Commit Task 2**

```powershell
git add src/ai_sdlc/core/update_advisor.py tests/unit/test_update_advisor.py USER_GUIDE.zh-CN.md docs/product-contract.md
git commit -m "fix: discover latest CLI release reliably"
```

---

### Task 3: Verify and freeze PR2

**Files:**
- No planned file changes.

**Interfaces:**
- Consumes: completed Task 1 and Task 2 behavior.
- Produces: a clean exact PR2 commit ready for independent local review.

- [ ] **Step 1: Run the complete local gate**

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run pytest -q
uv run ruff check .
uv run ai-sdlc verify constraints --profile self-development
uv build
git diff --check origin/main...HEAD
git status --short
```

Expected: the full suite, Ruff, constraints, and build pass; the tracked tree is clean.

- [ ] **Step 2: Run installed-runtime smoke**

Install the built wheel into a disposable environment and invoke its real
console script with controlled older-installed/newer-latest truth:

```powershell
$smokeRoot = '.tmp/pr2-update-smoke'
if (Test-Path $smokeRoot) { Remove-Item -Recurse -Force $smokeRoot }
uv venv $smokeRoot
$wheel = Get-ChildItem 'dist/ai_sdlc-2.0.0-*.whl' | Select-Object -First 1
uv pip install --python "$smokeRoot/bin/python" $wheel.FullName
$env:AI_SDLC_UPDATE_ADVISOR_TEST_INSTALLED='1'
$env:AI_SDLC_UPDATE_ADVISOR_TEST_VERSION='1.0.0'
$env:AI_SDLC_UPDATE_ADVISOR_TEST_CHANNEL='github-archive'
$env:AI_SDLC_UPDATE_ADVISOR_TEST_LATEST_VERSION='v2.0.0'
$env:AI_SDLC_UPDATE_ADVISOR_CACHE_DIR="$smokeRoot/cache"
& "$smokeRoot/bin/ai-sdlc" status 2>&1 | Tee-Object "$smokeRoot/status.txt"
& "$smokeRoot/bin/ai-sdlc" loop status 2>&1 | Tee-Object "$smokeRoot/loop.txt"
& "$smokeRoot/bin/ai-sdlc" loop status --json > "$smokeRoot/loop.json"
if (-not (Select-String -Quiet 'AI-SDLC Update' "$smokeRoot/status.txt")) { throw 'status notice missing' }
if (-not (Select-String -Quiet 'AI-SDLC Update' "$smokeRoot/loop.txt")) { throw 'Loop notice missing' }
$loopJson = Get-Content -Raw "$smokeRoot/loop.json" | ConvertFrom-Json
if ($loopJson.status -ne 'no_current') { throw 'Loop JSON contract changed' }
if (Select-String -Quiet 'AI-SDLC Update' "$smokeRoot/loop.json") { throw 'Loop JSON contains notice' }
```

This uses only test environment injection for latest-version truth and does not
call or mutate the public Release.

- [ ] **Step 3: Update continuity handoff**

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run ai-sdlc handoff update --goal "Deliver PR2 reliable CLI update notice" --state "Implementation and verification complete" --command "Full pytest PASS; Ruff PASS; self-development constraints PASS; build PASS; installed-runtime smoke PASS" --next-step "Freeze exact SHA/tree and run independent local review" --reason "PR2 verification checkpoint"
```

- [ ] **Step 4: Freeze and review**

Record `HEAD`, `HEAD^{tree}`, merge-base, patch-id, and clean status. Run two
independent read-only local reviewers: product behavior/scope and
reliability/regression. Allow only one focused repair/re-review round. Do not
push until both reviewers PASS the same exact SHA.
