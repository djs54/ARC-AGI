import pytest
from agents.arc3.phase import PhaseController, SolvePhase, IllegalPhaseTransition


def test_default_phase():
    pc = PhaseController()
    assert pc.phase == SolvePhase.PERCEIVE
    assert pc.phase_name == "perceive"


def test_legal_transition_sequence_and_history():
    pc = PhaseController()
    assert pc.can_advance(SolvePhase.MODEL)
    pc.advance(SolvePhase.MODEL)
    assert pc.phase == SolvePhase.MODEL
    pc.advance(SolvePhase.HYPOTHESIZE, force=True)  # allow gate-free in unit test
    pc.advance(SolvePhase.ROUTE, force=True)
    pc.advance(SolvePhase.EXECUTE, force=True)
    pc.advance(SolvePhase.EVALUATE, force=True)
    # EVALUATE -> PERCEIVE -> HYPOTHESIZE under per-step changes
    pc.advance(SolvePhase.PERCEIVE, force=True)
    pc.advance(SolvePhase.HYPOTHESIZE, force=True)
    # History should contain the transitions
    hist = pc.history
    assert any(h["from"] == "perceive" for h in hist)
    assert any(h["to"] == "evaluate" for h in hist)
    assert pc.step_count >= 1


def test_gate_blocks_transition():
    pc = PhaseController()

    def false_gate():
        return False

    pc.register_gate(SolvePhase.PERCEIVE, SolvePhase.MODEL, false_gate)
    with pytest.raises(IllegalPhaseTransition):
        pc.advance(SolvePhase.MODEL)


def test_checkpoint_roundtrip():
    pc = PhaseController()
    pc.advance(SolvePhase.MODEL, force=True)
    pc.advance(SolvePhase.HYPOTHESIZE, force=True)
    ck = pc.to_checkpoint()
    pc2 = PhaseController.from_checkpoint(ck)
    assert pc2.phase == pc.phase
    assert pc2.history == pc.history


def test_evaluate_to_perceive_transition():
    pc = PhaseController()
    pc.advance(SolvePhase.MODEL, force=True)
    pc.advance(SolvePhase.HYPOTHESIZE, force=True)
    pc.advance(SolvePhase.ROUTE, force=True)
    pc.advance(SolvePhase.EXECUTE, force=True)
    pc.advance(SolvePhase.EVALUATE, force=True)
    assert pc.can_advance(SolvePhase.PERCEIVE)
    pc.advance(SolvePhase.PERCEIVE, force=True)
    assert pc.phase == SolvePhase.PERCEIVE


def test_perceive_to_hypothesize_transition():
    pc = PhaseController()
    # From initial PERCEIVE, HYPOTHESIZE should be reachable
    assert pc.can_advance(SolvePhase.HYPOTHESIZE)
    pc.advance(SolvePhase.HYPOTHESIZE, force=True)
    assert pc.phase == SolvePhase.HYPOTHESIZE


def test_evaluate_to_hypothesize_now_illegal():
    pc = PhaseController()
    pc.advance(SolvePhase.MODEL, force=True)
    pc.advance(SolvePhase.HYPOTHESIZE, force=True)
    pc.advance(SolvePhase.ROUTE, force=True)
    pc.advance(SolvePhase.EXECUTE, force=True)
    pc.advance(SolvePhase.EVALUATE, force=True)
    assert not pc.can_advance(SolvePhase.HYPOTHESIZE)
    with pytest.raises(IllegalPhaseTransition):
        pc.advance(SolvePhase.HYPOTHESIZE)


def test_full_per_step_cycle():
    pc = PhaseController()
    pc.advance(SolvePhase.MODEL, force=True)
    pc.advance(SolvePhase.HYPOTHESIZE, force=True)
    pc.advance(SolvePhase.ROUTE, force=True)
    pc.advance(SolvePhase.EXECUTE, force=True)
    pc.advance(SolvePhase.EVALUATE, force=True)
    pc.advance(SolvePhase.PERCEIVE, force=True)
    pc.advance(SolvePhase.HYPOTHESIZE, force=True)
    assert pc.phase == SolvePhase.HYPOTHESIZE


def test_replan_bypasses_perceive():
    pc = PhaseController()
    pc.advance(SolvePhase.MODEL, force=True)
    pc.advance(SolvePhase.HYPOTHESIZE, force=True)
    pc.advance(SolvePhase.ROUTE, force=True)
    pc.advance(SolvePhase.EXECUTE, force=True)
    pc.advance(SolvePhase.EVALUATE, force=True)
    pc.advance(SolvePhase.REPLAN, force=True)
    # After REPLAN, PERCEIVE should not be a direct target
    assert not pc.can_advance(SolvePhase.PERCEIVE)
