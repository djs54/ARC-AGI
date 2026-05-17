"""Test A092: Terminal-aligned meaningful progress scoring.

Tests that local object progress is demoted when it does not improve
terminal distance, preventing the solver from pursuing false progress.
"""

import pytest
from agents.arc3.world_model_compiler import WorldModelCompiler, ActionEffectClaim


class TestTerminalAlignmentClassification:
    """Test _classify_terminal_alignment classification logic."""

    def test_terminal_progress_with_improving_trend(self):
        """Terminal progress with improving distance is terminal-aligned."""
        compiler = WorldModelCompiler()
        alignment = compiler._classify_terminal_alignment(
            effect_class="terminal_progress",
            terminal_trend="improving",
            goal_distance=5.0,
            delayed_effect_pending=False
        )
        
        assert alignment == "terminal_aligned"

    def test_object_progress_with_improving_trend(self):
        """Object progress with improving distance is terminal-aligned."""
        compiler = WorldModelCompiler()
        alignment = compiler._classify_terminal_alignment(
            effect_class="object_progress",
            terminal_trend="improving",
            goal_distance=5.0,
            delayed_effect_pending=False
        )
        
        assert alignment == "terminal_aligned"

    def test_object_progress_with_flat_trend(self):
        """Object progress with flat terminal distance is local-only."""
        compiler = WorldModelCompiler()
        alignment = compiler._classify_terminal_alignment(
            effect_class="object_progress",
            terminal_trend="flat",
            goal_distance=10.0,
            delayed_effect_pending=False
        )
        
        assert alignment == "local_only"

    def test_object_progress_with_regressing_trend(self):
        """Object progress with regressing distance is regressing."""
        compiler = WorldModelCompiler()
        alignment = compiler._classify_terminal_alignment(
            effect_class="object_progress",
            terminal_trend="regressing",
            goal_distance=15.0,
            delayed_effect_pending=False
        )
        
        assert alignment == "regressing"

    def test_object_progress_with_oscillating_trend(self):
        """Object progress with oscillating distance is oscillating."""
        compiler = WorldModelCompiler()
        alignment = compiler._classify_terminal_alignment(
            effect_class="object_progress",
            terminal_trend="oscillating",
            goal_distance=None,
            delayed_effect_pending=False
        )
        
        assert alignment == "oscillating"

    def test_object_progress_with_delayed_effect_pending(self):
        """Object progress with delayed effect pending is marked as such."""
        compiler = WorldModelCompiler()
        alignment = compiler._classify_terminal_alignment(
            effect_class="object_progress",
            terminal_trend="flat",
            goal_distance=10.0,
            delayed_effect_pending=True
        )
        
        assert alignment == "delayed_effect_pending"

    def test_no_op_has_no_alignment(self):
        """No-op effects have no terminal alignment."""
        compiler = WorldModelCompiler()
        alignment = compiler._classify_terminal_alignment(
            effect_class="no_op",
            terminal_trend="flat",
            goal_distance=10.0,
            delayed_effect_pending=False
        )
        
        assert alignment == "unknown"

    def test_harmful_has_no_alignment(self):
        """Harmful effects have no terminal alignment."""
        compiler = WorldModelCompiler()
        alignment = compiler._classify_terminal_alignment(
            effect_class="harmful",
            terminal_trend="regressing",
            goal_distance=15.0,
            delayed_effect_pending=False
        )
        
        assert alignment == "unknown"


class TestCompileStepWithTerminalAlignment:
    """Test that compile_step correctly captures terminal alignment."""

    def test_compile_step_includes_terminal_alignment(self):
        """Compiled claims include terminal alignment field."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": True},
            terminal_trend="improving",
            object_progress={"score": 0.5},
            available_actions=["ACTION1", "ACTION2"],
            goal_distance=5.0
        )
        
        assert len(delta.claims) > 0
        claim = delta.claims[0]
        assert isinstance(claim, ActionEffectClaim)
        assert claim.terminal_alignment == "terminal_aligned"

    def test_compile_step_tracks_goal_distance_history(self):
        """Compiler maintains goal distance history for trend analysis."""
        compiler = WorldModelCompiler()
        
        # Compile multiple steps to build history
        for step in range(1, 4):
            compiler.compile_step(
                step=step,
                prev_hash=f"hash{step-1}",
                curr_hash=f"hash{step}",
                action={"action_id": "ACTION1"},
                reward_components={"meaningful_progress": True},
                terminal_trend="improving",
                object_progress={"score": 0.5},
                available_actions=["ACTION1", "ACTION2"],
                goal_distance=10.0 - step  # Decreasing distance = improving
            )
        
        # History should be preserved
        assert len(compiler._goal_distance_window) == 3
        assert compiler._goal_distance_window[0][1] == 9.0
        assert compiler._goal_distance_window[2][1] == 7.0

    def test_terminal_alignment_in_props(self):
        """Terminal alignment stored in claim props."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": True},
            terminal_trend="flat",
            object_progress={"score": 0.5},
            available_actions=["ACTION1", "ACTION2"],
            goal_distance=10.0
        )
        
        claim = delta.claims[0]
        assert claim.props.get("terminal_aligned") is False
        assert claim.props.get("terminal_trend") == "flat"
        assert claim.props.get("goal_distance") == 10.0


class TestMeaningfulProgressRequiresTerminalAlignment:
    """Test that meaningful progress is only granted for terminal-aligned effects."""

    def test_object_progress_only_meaningful_if_terminal_aligned(self):
        """Object progress should only be marked meaningful if terminal-aligned."""
        compiler = WorldModelCompiler()
        
        # Local-only progress: object moves but terminal unchanged
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": True},
            terminal_trend="flat",
            object_progress={"score": 0.5},
            available_actions=["ACTION1", "ACTION2"],
            goal_distance=10.0
        )
        
        claim = delta.claims[0]
        assert claim.terminal_alignment == "local_only"
        # Props should indicate this is not truly meaningful
        assert claim.props.get("terminal_aligned") is False

    def test_terminal_progress_always_meaningful(self):
        """Terminal progress is always meaningful regardless of trend."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": True},
            terminal_trend="improving",
            object_progress={"score": 0.0},
            available_actions=["ACTION1", "ACTION2"],
            goal_distance=5.0
        )
        
        claim = delta.claims[0]
        assert claim.effect_class == "terminal_progress"
        assert claim.props.get("terminal_aligned") is True


class TestRegressionAndOscillationDetection:
    """Test detection of regressing and oscillating terminal trends."""

    def test_regressing_terminal_distance_detected(self):
        """Regressing terminal distance is properly classified."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": True},
            terminal_trend="regressing",
            object_progress={"score": 0.5},
            available_actions=["ACTION1", "ACTION2"],
            goal_distance=15.0
        )
        
        claim = delta.claims[0]
        # When terminal_trend is "regressing", effect_class becomes "harmful"
        # So we check that harmful is detected and alignment is unknown
        assert claim.effect_class == "harmful"
        assert claim.terminal_alignment == "unknown"

    def test_oscillating_terminal_distance_detected(self):
        """Oscillating terminal distance is properly classified."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": True},
            terminal_trend="oscillating",
            object_progress={"score": 0.5},
            available_actions=["ACTION1", "ACTION2"],
            goal_distance=None
        )
        
        claim = delta.claims[0]
        assert claim.terminal_alignment == "oscillating"


class TestDelayedEffectGuard:
    """Test delayed effect pending logic."""

    def test_delayed_effect_pending_overrides_flat_trend(self):
        """Delayed effect pending prevents local-only classification."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={
                "meaningful_progress": True,
                "delayed_effect_pending": True
            },
            terminal_trend="flat",
            object_progress={"score": 0.5},
            available_actions=["ACTION1", "ACTION2"],
            goal_distance=10.0
        )
        
        claim = delta.claims[0]
        assert claim.terminal_alignment == "delayed_effect_pending"
        assert claim.props.get("terminal_aligned") is True

    def test_no_delayed_effect_for_harmful(self):
        """Harmful effects are not covered by delayed effect guard."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={
                "meaningful_progress": False,
                "delayed_effect_pending": True
            },
            terminal_trend="regressing",
            object_progress={"score": 0.0},
            available_actions=["ACTION1", "ACTION2"],
            goal_distance=15.0
        )
        
        claim = delta.claims[0]
        assert claim.effect_class == "harmful"
        # Harmful has unknown alignment regardless of delayed_effect_pending
        assert claim.terminal_alignment == "unknown"
