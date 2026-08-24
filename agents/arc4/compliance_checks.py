"""Deterministic post-execution checks for the graph-control-plane design
principles (Shift A/B/C, graph-engineering review, 2026-08-22). Pure
functions only -- no LLM, no graph call, inspect data already in hand."""

from __future__ import annotations

from typing import Mapping

from .types import ExecutionResult


DETERMINISTIC_PHASES = frozenset({"perceive", "vet", "execute", "evaluate"})


def check_shift_a_invariants(phase_token_costs: Mapping[str, int]) -> list[str]:
    """Shift A: perceive/vet/execute/evaluate must never invoke an LLM. A
    nonzero token cost during any of them means something bypassed the
    deterministic-phase boundary -- resolve/plan are the only phases allowed
    real cost here."""
    violations: list[str] = []
    for phase_name in DETERMINISTIC_PHASES:
        cost = int(phase_token_costs.get(phase_name, 0) or 0)
        if cost > 0:
            violations.append(f"phase={phase_name!r} incurred {cost} tokens of LLM cost; this phase must be strictly deterministic")
    return violations


def check_shift_b_invariants(execution: ExecutionResult) -> list[str]:
    """Shift B: no module (least of all a one-shot LLM patch) should be able
    to smuggle a graph-falsified candidate into execution. Once A191 excludes
    repeated_falsified candidates from ever being built, this should always
    return []; a non-empty result means that guarantee broke somewhere
    upstream (A184's patch guard, A188's vetter veto, or A191's pre-filter)."""
    violations: list[str] = []
    candidate = execution.candidate
    if candidate is not None:
        metadata = candidate.metadata if isinstance(candidate.metadata, Mapping) else {}
        if bool(metadata.get("repeated_falsified")):
            violations.append(
                f"executed candidate action_id={candidate.action_id!r} book_id={candidate.book_id!r} "
                "was repeated_falsified -- A191's pre-filter (or an earlier guard) should have excluded it before execution"
            )
    return violations
