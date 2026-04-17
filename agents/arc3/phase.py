"""Durable phase-state machine for ARC orchestrator (B201).

Defines `SolvePhase` and a checkpointable `PhaseController` used by DurableARCRunner.
"""
from __future__ import annotations

import time
import logging
from enum import Enum
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)


class SolvePhase(Enum):
    PERCEIVE = "perceive"
    MODEL = "model"
    HYPOTHESIZE = "hypothesize"
    ROUTE = "route"
    EXECUTE = "execute"
    EVALUATE = "evaluate"
    REPLAN = "replan"


class IllegalPhaseTransition(Exception):
    pass


class PhaseController:
    """Durable, checkpointable phase controller.

    - Keeps an explicit current phase (as an Enum)
    - Enforces a legal transition table
    - Allows gate conditions to be registered per transition
    - Records a compact history for diagnostics and checkpointing
    """

    TRANSITIONS: dict[SolvePhase, set[SolvePhase]] = {
        SolvePhase.PERCEIVE:    {SolvePhase.MODEL, SolvePhase.HYPOTHESIZE},
        SolvePhase.MODEL:       {SolvePhase.HYPOTHESIZE},
        SolvePhase.HYPOTHESIZE: {SolvePhase.ROUTE},
        SolvePhase.ROUTE:       {SolvePhase.EXECUTE},
        SolvePhase.EXECUTE:     {SolvePhase.EVALUATE},
        SolvePhase.EVALUATE:    {SolvePhase.PERCEIVE, SolvePhase.REPLAN},
        SolvePhase.REPLAN:      {SolvePhase.MODEL, SolvePhase.HYPOTHESIZE, SolvePhase.ROUTE},
    }

    def __init__(self, initial: SolvePhase = SolvePhase.PERCEIVE) -> None:
        self._phase: SolvePhase = initial
        self._history: List[tuple[SolvePhase, SolvePhase, float]] = []
        self._gates: Dict[tuple[SolvePhase, SolvePhase], Callable[[], bool]] = {}

    @property
    def phase(self) -> SolvePhase:
        return self._phase

    @property
    def phase_name(self) -> str:
        return self._phase.value

    def register_gate(self, from_phase: SolvePhase, to_phase: SolvePhase, condition: Callable[[], bool]) -> None:
        self._gates[(from_phase, to_phase)] = condition

    def can_advance(self, to: SolvePhase) -> bool:
        if to not in self.TRANSITIONS.get(self._phase, set()):
            return False
        gate = self._gates.get((self._phase, to))
        return bool(gate()) if gate is not None else True

    def advance(self, to: SolvePhase, *, force: bool = False) -> SolvePhase:
        if to not in self.TRANSITIONS.get(self._phase, set()):
            raise IllegalPhaseTransition(
                f"Cannot transition {self._phase.value} -> {to.value}; legal: {[t.value for t in self.TRANSITIONS.get(self._phase, set())]}"
            )
        gate = self._gates.get((self._phase, to))
        if gate is not None and not gate():
            if not force:
                raise IllegalPhaseTransition(f"Gate not satisfied for {self._phase.value} -> {to.value}")
            logger.warning("Force-advancing %s -> %s with unsatisfied gate", self._phase.value, to.value)
        self._history.append((self._phase, to, time.time()))
        self._phase = to
        return self._phase

    def reset(self, to: SolvePhase = SolvePhase.PERCEIVE) -> None:
        self._phase = to
        self._history.clear()

    # Checkpoint helpers
    def to_checkpoint(self) -> dict:
        return {
            "phase": self._phase.value,
            "history": [{"from": f.value, "to": t.value, "ts": ts} for f, t, ts in self._history],
        }

    @classmethod
    def from_checkpoint(cls, data: dict) -> "PhaseController":
        ctrl = cls(initial=SolvePhase(data.get("phase", SolvePhase.PERCEIVE.value)))
        ctrl._history = [
            (SolvePhase(h["from"]), SolvePhase(h["to"]), float(h.get("ts", 0.0)))
            for h in data.get("history", [])
        ]
        return ctrl

    @property
    def history(self) -> List[dict]:
        return [{"from": f.value, "to": t.value, "timestamp": ts} for f, t, ts in self._history]

    @property
    def step_count(self) -> int:
        return sum(1 for _, to, _ in self._history if to == SolvePhase.EVALUATE)
