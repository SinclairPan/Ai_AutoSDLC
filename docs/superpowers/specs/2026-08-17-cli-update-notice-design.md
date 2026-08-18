# CLI Update Notice Design

## Goal

Make an installed `ai-sdlc` reliably tell a user or AI coding agent that a newer
public release exists before it runs an ordinary human-readable CLI command.
The notice must cover the five Loop entry points without delaying or breaking
the requested command when version discovery is unavailable.

## Observed Root Cause

The existing feature is present but misses the dominant product path:

1. `src/ai_sdlc/cli/main.py` excludes the complete `loop` command group from
   update notices. Codex, Cursor, and Claude Code primarily use those commands,
   so the notice is absent where it matters most.
2. Automatic discovery uses the anonymous GitHub Releases API with a 1.5 second
   timeout. When that refresh fails, an installed CLI can only use an old cache.
   The reproduced local runtime is `0.9.7`, its cache says `1.0.2`, while the
   current immutable latest GitHub Release is `v2.0.0`.
3. Existing integration tests use forced in-memory release data and explicitly
   require Loop commands to skip the hook, so they prove the synthetic path but
   preserve the real product gap.

## Considered Approaches

### A. Increase the API timeout and retry count

This retains anonymous API rate-limit exposure and makes ordinary commands wait
longer. It treats the symptom, not the missing Loop coverage or fragile source.

### B. Add a separate background update service

This could hide network latency, but it introduces a process, lifecycle,
persistence, and support surface that is disproportionate to a version notice.

### C. Recommended: repair the existing synchronous advisory path

Remove only the over-broad Loop bypass and obtain latest-public-release truth
from GitHub's stable `/releases/latest` redirect. Keep the existing cache,
24-hour refresh interval, short timeout, failure backoff, installed-runtime
guard, and fail-open command behavior. This has the smallest new surface and no
new daemon, store, policy engine, or dependency.

## Behavior Contract

- Run the update-notice hook before every ordinary human-readable installed-CLI
  command, including `loop ...`.
- Do not run it for `self-update`, `--json`, help, completion, bare invocation,
  or source/editable development runtimes.
- Open GitHub's HTTPS latest-release redirect with the existing 1.5 second
  timeout. Read only `response.geturl()` and close the response immediately;
  never download or parse the release HTML body and do not add retries.
- Parse the final URL with `urlsplit`. Require scheme `https`, host
  `github.com`, no userinfo, explicit port, query, or fragment, and the exact
  path `/SinclairPan/Ai_AutoSDLC/releases/tag/<tag>`, where `<tag>` matches
  `^v\d+\.\d+\.\d+$`. Normalize that tag to `X.Y.Z`.
- Reuse the existing per-installation cache. A successful result remains fresh
  for 24 hours, so ordinary commands do not make a network request each time.
- On timeout, network error, malformed redirect, or unwritable cache, continue
  the requested command. A still-usable cached version may continue to drive a
  notice.
- Display the existing bilingual update confirmation. Never modify an
  installation without explicit `y` confirmation or an explicit
  `ai-sdlc self-update check` command.
- Keep `AI_SDLC_DISABLE_UPDATE_CHECK=1` as the opt-out.

## Components and Data Flow

1. The Typer root callback decides whether the current invocation is
   human-readable and eligible, then calls `maybe_render_update_notice()`.
2. `evaluate_update_advisor()` reads the installed runtime identity and cache.
3. When the cache is stale, the existing fetch function resolves the public
   latest-release redirect within the existing timeout. It adapts the validated
   final URL back to the current internal fetch contract as
   `{"tag_name": tag, "html_url": final_url, "draft": false,
   "prerelease": false}` so `_refresh_cache()`, the cache model, and callers do
   not change.
4. If the cached or refreshed tag is newer, the existing rendering path writes
   the prompt to stderr before the command proceeds.
5. Any discovery failure returns a non-actionable evaluation; it never changes
   the main command's exit status.

## Test Contract

- A failing integration test must first prove `loop status` does not invoke the
  notice hook; after the fix it must invoke it once.
- Machine-readable Loop output must remain notice-free.
- Unit tests must cover a valid `v2.0.0` latest redirect and reject a different
  scheme, host, userinfo, port, repository, path, query, fragment, or malformed
  version. The response double must fail if `.read()` is called, and the test
  must assert the configured timeout is forwarded unchanged.
- Unit tests must prove cache freshness prevents a second fetch and discovery
  failure does not block evaluation.
- Existing ordinary-command, self-update, help, JSON, source-runtime, cache, and
  offline tests must remain green.
- Finish with targeted tests, the complete suite, Ruff, constraints, build, and
  a real installed-bundle smoke using an older local fixture against injected
  latest-release truth.

## Non-Goals

- No automatic update without confirmation.
- No shell-start hook.
- No background process, telemetry, remote policy, authority, certificate,
  attestation, or new persistent store.
- No generic package manager or cross-product update framework.
- No release, tag, asset upload, or publication in this PR.
