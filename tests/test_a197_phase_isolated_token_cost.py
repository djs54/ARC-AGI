"""Test A197: phase-isolated token cost assertions and Shift-A invariant checking."""

import pytest
from agents.arc4.compliance_checks import check_shift_a_invariants
from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import EvaluationResult, WorkflowDecision


class TestCheckShiftAInvariants:
    """Tests for check_shift_a_invariants function."""

    def test_no_violations_with_zero_deterministic_and_nonzero_resolve_plan(self):
        """Test 1: check_shift_a_invariants returns [] when deterministic phases are
        zero and resolve/plan have nonzero cost (normal case)."""
        phase_costs = {
            "perceive": 0,
            "vet": 0,
            "execute": 0,
            "evaluate": 0,
            "resolve": 150,
            "plan": 40,
        }
        violations = check_shift_a_invariants(phase_costs)
        assert violations == []

    def test_violation_single_deterministic_phase(self):
        """Test 2: check_shift_a_invariants returns exactly one violation string
        when perceive has nonzero cost."""
        phase_costs = {
            "perceive": 12,
            "vet": 0,
            "execute": 0,
            "evaluate": 0,
        }
        violations = check_shift_a_invariants(phase_costs)
        assert len(violations) == 1
        assert "perceive" in violations[0]
        assert "12" in violations[0]

    def test_violations_multiple_deterministic_phases(self):
        """Test 3: check_shift_a_invariants returns one violation string per
        offending deterministic phase when multiple have nonzero cost."""
        phase_costs = {
            "perceive": 5,
            "vet": 0,
            "execute": 8,
            "evaluate": 0,
            "resolve": 100,
        }
        violations = check_shift_a_invariants(phase_costs)
        assert len(violations) == 2
        phase_names = [v for v in violations if "perceive" in v or "execute" in v]
        assert len(phase_names) == 2

    def test_no_violations_empty_dict(self):
        """Test 4: check_shift_a_invariants returns [] when called with empty dict
        (absence is not evidence of violation, .get(..., 0) defaults cleanly)."""
        phase_costs = {}
        violations = check_shift_a_invariants(phase_costs)
        assert violations == []

    def test_wrap_phase_delta_capture(self):
        """Test 5: wrap_phase captures per-phase token delta correctly.
        Construct an ArcV2Telemetry, simulate a phase callable that increments
        tokens mid-call, confirm the captured delta matches exactly, and verify
        that a subsequent phase call with no token change reports a 0 delta."""

        # Mock LLM port that tracks tokens
        class MockLLMPort:
            def __init__(self):
                self.total_tokens_in = 0
                self.total_tokens_out = 0

        telemetry = ArcV2Telemetry(task_id="test", game_id="game1")
        mock_llm_port = MockLLMPort()
        telemetry._llm_port = mock_llm_port

        # Phase 1: callable that increments tokens
        def phase_with_tokens(*args, **kwargs):
            # Simulate LLM call incrementing tokens
            mock_llm_port.total_tokens_in += 10
            mock_llm_port.total_tokens_out += 5

            class Result:
                status = None

            return Result()

        # Phase 2: callable with no token change
        def phase_no_tokens(*args, **kwargs):
            class Result:
                status = None

            return Result()

        # Wrap and call phase 1
        wrapped_phase1 = telemetry.wrap_phase("resolve", phase_with_tokens)
        wrapped_phase1()

        # Verify phase token costs were captured
        assert telemetry._phase_token_costs.get("resolve") == 15  # 10 + 5

        # Wrap and call phase 2 (no tokens)
        wrapped_phase2 = telemetry.wrap_phase("plan", phase_no_tokens)
        wrapped_phase2()

        # Verify phase 2 has zero delta
        assert telemetry._phase_token_costs.get("plan") == 0

        # Verify phase 1 still has its cost (not cumulative misattribution)
        assert telemetry._phase_token_costs.get("resolve") == 15

    def test_end_to_end_compliance_count(self):
        """Test 6: end-to-end simulated episode with LLM cost only during
        resolve/plan reports zero Shift-A violations in final compliance_violation_count
        across all steps."""

        # Mock LLM port
        class MockLLMPort:
            def __init__(self):
                self.total_tokens_in = 0
                self.total_tokens_out = 0

        telemetry = ArcV2Telemetry(task_id="test", game_id="game1")
        mock_llm_port = MockLLMPort()
        telemetry._llm_port = mock_llm_port

        # Simulate multiple phases in an episode
        phases = [
            ("perceive", 0),  # No LLM cost
            ("resolve", 50),  # LLM cost allowed
            ("plan", 30),  # LLM cost allowed
            ("vet", 0),  # No LLM cost
            ("execute", 0),  # No LLM cost
            ("evaluate", 0),  # No LLM cost
        ]

        for phase_name, token_cost in phases:
            def make_phase_callable(cost, pname):
                def phase_callable(*args, **kwargs):
                    if cost > 0:
                        mock_llm_port.total_tokens_in += cost // 2
                        mock_llm_port.total_tokens_out += cost // 2

                    # Create simple phase result mock
                    class Result:
                        status = None
                        payload = None

                    return Result()

                return phase_callable

            wrapped_phase = telemetry.wrap_phase(phase_name, make_phase_callable(token_cost, phase_name))
            wrapped_phase()

        # Compute compliance violation count (should be 0 for normal run)
        violation_count = telemetry._compute_compliance_violation_count(None)
        assert violation_count == 0

        # Verify phase token costs match expectations
        assert telemetry._phase_token_costs["perceive"] == 0
        assert telemetry._phase_token_costs["resolve"] == 50
        assert telemetry._phase_token_costs["plan"] == 30
        assert telemetry._phase_token_costs["vet"] == 0
        assert telemetry._phase_token_costs["execute"] == 0
        assert telemetry._phase_token_costs["evaluate"] == 0

    def test_violation_does_not_repeat_across_subsequent_steps(self):
        """A step boundary is the "evaluate" phase completing with a real
        EvaluationResult payload -- that's what _record_phase_result uses to
        decide a step_snapshot is due, and (per this card's fix) it's also
        where _phase_token_costs must reset. Without the reset, a single
        Shift-A violation on step 1 would silently re-count itself into
        every subsequent step's compliance_violation_count for the rest of
        the episode, turning "one incident" into "N steps flagged," which
        would badly distort A196's future rate-based reporting."""

        class MockLLMPort:
            def __init__(self):
                self.total_tokens_in = 0
                self.total_tokens_out = 0

        emitted_snapshots = []
        telemetry = ArcV2Telemetry(task_id="test", game_id="game1", append_snapshot=emitted_snapshots.append)
        mock_llm_port = MockLLMPort()
        telemetry._llm_port = mock_llm_port

        def make_evaluate_result():
            return EvaluationResult(decision=WorkflowDecision.CONTINUE, meaningful_progress=False)

        class _PhaseResult:
            def __init__(self, payload):
                self.payload = payload
                self.status = None

        def run_step(*, perceive_leaks_tokens: bool) -> int:
            def perceive_callable(*args, **kwargs):
                if perceive_leaks_tokens:
                    mock_llm_port.total_tokens_in += 7
                return _PhaseResult(payload=None)

            def resolve_callable(*args, **kwargs):
                mock_llm_port.total_tokens_in += 20
                return _PhaseResult(payload=None)

            def other_callable(*args, **kwargs):
                return _PhaseResult(payload=None)

            def evaluate_callable(*args, **kwargs):
                return _PhaseResult(payload=make_evaluate_result())

            telemetry.wrap_phase("perceive", perceive_callable)()
            telemetry.wrap_phase("resolve", resolve_callable)()
            telemetry.wrap_phase("plan", other_callable)()
            telemetry.wrap_phase("vet", other_callable)()
            telemetry.wrap_phase("execute", other_callable)()
            telemetry.wrap_phase("evaluate", evaluate_callable)()

            step_snapshot = next(s for s in reversed(emitted_snapshots) if s.get("snapshot_type") == "step")
            return step_snapshot["compliance_violation_count"]

        step_1_count = run_step(perceive_leaks_tokens=True)
        assert step_1_count == 1, "step 1's own perceive leak must be flagged exactly once"

        step_2_count = run_step(perceive_leaks_tokens=False)
        assert step_2_count == 0, (
            "step 2 must not re-report step 1's already-resolved violation -- "
            "_phase_token_costs must reset at each step boundary, not accumulate "
            "across the whole episode"
        )
