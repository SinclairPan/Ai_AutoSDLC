from __future__ import annotations

import json
from pathlib import Path


contract = json.loads(Path("input-contract.json").read_text(encoding="utf-8"))
assert contract["schema"] == "ai-sdlc-v2-benefit-input-contract/v1"
assert contract["target_stage"] == "design-contract"
assert contract["canonical_pre_state"] == ["requirement"]
assert set(contract["semantics"]["deliverable"]["required_sections"]) == {
    "decisions",
    "state_machine",
    "failure_policy",
    "verification",
    "open_questions",
}
print("VISIBLE_OK: frozen requirement input is structurally valid")
