#!/usr/bin/env python3
"""Offline-only entrypoint for the lightweight AI-SDLC benefit rehearsal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_sdlc.benefit_directional_demo import (
    build_budget_confirmation,
    build_directional_preflight,
    load_directional_manifest,
    run_fake_rehearsal,
    write_preflight_artifacts,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--output-root", type=Path, required=True)
    rehearse = subparsers.add_parser("rehearse")
    rehearse.add_argument("--workspace-root", type=Path, required=True)
    rehearse.add_argument("--output-root", type=Path, required=True)
    rehearse.add_argument(
        "--materialize-arms",
        action="store_true",
        help="Prepare all 15 real zero-Provider arm workspaces.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = load_directional_manifest()
    preflight = build_directional_preflight(manifest, args.output_root)
    if args.command == "validate":
        payload = {
            "status": "preflight-only",
            "manifest_sha256": manifest.canonical_sha256,
            "provider_calls_started": 0,
            "budget_confirmation": build_budget_confirmation(preflight),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    result = run_fake_rehearsal(
        manifest,
        workspace_root=args.workspace_root,
        output_root=args.output_root,
        materialize_arms=args.materialize_arms,
    )
    artifacts = write_preflight_artifacts(args.output_root / "preflight", preflight)
    payload = {
        "status": "fake-provider-rehearsal-complete",
        "prepared_workspaces": result.prepared_workspaces,
        "simulated_sessions": result.simulated_sessions,
        "external_provider_calls": result.external_provider_calls,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "currency_cost": result.currency_cost,
        "preflight_artifacts": [str(path) for path in artifacts],
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
