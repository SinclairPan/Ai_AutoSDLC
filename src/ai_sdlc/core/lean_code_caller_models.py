"""Private data shapes shared by Lean caller analysis."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

from ai_sdlc.core.lean_code_flow import ReferenceState
from ai_sdlc.core.lean_code_identity_models import _IdentityValue

_TargetExports = dict[str, frozenset[str]]
_ModuleTarget = tuple[str, str, str, str, _TargetExports]
_ExceptionStateStacks = tuple[list[ReferenceState], ...]
_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
_CallableNode = _FunctionNode | ast.Lambda
_ImportedCallables = dict[str, _IdentityValue]
_CallableOrigins = dict[int, tuple[str, str]]
_DynamicScope = _FunctionNode | ast.Lambda | ast.ClassDef
_ComprehensionScope = ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
_ImportlibBindings = tuple[set[str], set[str], bool]
_TargetCallContext = tuple[_ModuleTarget, set[str], set[str], set[str]]
_SourceShape = tuple[
    dict[ast.AST, ast.AST],
    tuple[_FunctionNode, ...],
    dict[int, tuple[ast.Import | ast.ImportFrom, ...]],
    dict[str, set[str]],
    set[int],
]

_BindingState = Literal["exact", "plausible", "disproven"]
_ExecutionState = Literal[
    "executed", "contractual", "referenced_only", "unreachable", "unknown"
]


@dataclass(frozen=True)
class _TargetEvidence:
    binding_state: _BindingState = "disproven"
    execution_state: _ExecutionState = "unreachable"
    invocation_evidence: frozenset[str] = frozenset()
    reference_evidence: frozenset[str] = frozenset()
    unlinked_evidence: frozenset[str] = frozenset()

    def merge(self, other: _TargetEvidence) -> _TargetEvidence:
        binding_rank = {"disproven": 0, "plausible": 1, "exact": 2}
        execution_rank = {
            "unreachable": 0,
            "unknown": 1,
            "referenced_only": 2,
            "contractual": 3,
            "executed": 4,
        }
        binding = max(
            (self.binding_state, other.binding_state),
            key=binding_rank.__getitem__,
        )
        execution = max(
            (self.execution_state, other.execution_state),
            key=execution_rank.__getitem__,
        )
        return _TargetEvidence(
            binding_state=binding,
            execution_state=execution,
            invocation_evidence=(
                self.invocation_evidence | other.invocation_evidence
            ),
            reference_evidence=self.reference_evidence | other.reference_evidence,
            unlinked_evidence=self.unlinked_evidence | other.unlinked_evidence,
        )

@dataclass(frozen=True)
class _SourceEvidenceIndex:
    nodes: tuple[ast.AST, ...]
    calls: tuple[ast.Call, ...]

__all__: list[str] = []
