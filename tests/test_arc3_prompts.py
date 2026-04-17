"""Tests for prompt extraction — B122.

Verify all prompts are properly extracted to agents/arc3/prompts.py
and no hardcoded prompts remain in runtime files.
"""

import pytest
from agents.arc3 import prompts


def test_all_prompt_constants_are_nonempty_strings():
    """B122: All prompt constants should be non-empty strings."""
    names = [
        "SYSTEM_PROMPT",
        "INSTRUCTION_TEMPLATE",
        "SANDBOX_INSTRUCTION",
        "SANDBOX_SYSTEM_MESSAGE",
        "QUERY_LLM_SYSTEM_MESSAGE",
        "VICTORY_HYPOTHESIS_TEMPLATE",
    ]
    for name in names:
        val = getattr(prompts, name)
        assert isinstance(val, str), f"{name} is not a string"
        assert len(val) > 10, f"{name} is suspiciously short"


def test_system_prompt_has_placeholder():
    """B122: SYSTEM_PROMPT should have {available_actions} placeholder."""
    assert "{available_actions}" in prompts.SYSTEM_PROMPT


def test_instruction_template_has_placeholder():
    """B122: INSTRUCTION_TEMPLATE should have {effect_summary} placeholder."""
    assert "{effect_summary}" in prompts.INSTRUCTION_TEMPLATE


def test_victory_template_has_all_placeholders():
    """B122: VICTORY_HYPOTHESIS_TEMPLATE should have all required placeholders."""
    required_keys = ("archetype", "object_roles", "past_plans", "lessons", "reward_summary")
    for key in required_keys:
        assert f"{{{key}}}" in prompts.VICTORY_HYPOTHESIS_TEMPLATE, f"Missing placeholder: {{{key}}}"


def test_sandbox_instruction_is_nonempty():
    """B122: SANDBOX_INSTRUCTION should be a meaningful string."""
    assert "MENTAL SANDBOX" in prompts.SANDBOX_INSTRUCTION
    assert "sandbox_thought" in prompts.SANDBOX_INSTRUCTION


def test_repl_sandbox_instruction_exists():
    """B122: REPL_SANDBOX_INSTRUCTION should exist (added in B123)."""
    assert hasattr(prompts, "REPL_SANDBOX_INSTRUCTION")
    assert "REPL SANDBOX" in prompts.REPL_SANDBOX_INSTRUCTION
    assert len(prompts.REPL_SANDBOX_INSTRUCTION) > 50


def test_sandbox_system_message_nonempty():
    """B122: SANDBOX_SYSTEM_MESSAGE should be non-empty."""
    assert len(prompts.SANDBOX_SYSTEM_MESSAGE) > 10
    assert "sandbox" in prompts.SANDBOX_SYSTEM_MESSAGE.lower()


def test_query_llm_system_message_nonempty():
    """B122: QUERY_LLM_SYSTEM_MESSAGE should be non-empty."""
    assert len(prompts.QUERY_LLM_SYSTEM_MESSAGE) > 10
    assert "ARC" in prompts.QUERY_LLM_SYSTEM_MESSAGE


def test_verifier_prompts_exist():
    """B122/B126: Verifier prompts should exist (added in B126)."""
    assert hasattr(prompts, "VERIFIER_SYSTEM_PROMPT")
    assert hasattr(prompts, "VERIFIER_PROMPT_TEMPLATE")
    assert "verif" in prompts.VERIFIER_SYSTEM_PROMPT.lower()
    assert "approved" in prompts.VERIFIER_PROMPT_TEMPLATE.lower()


def test_orchestrator_imports_from_prompts():
    """B122: Verify orchestrator.py imports from prompts module."""
    from agents.arc3 import orchestrator

    # Check that prompts constants are available in orchestrator module
    assert hasattr(orchestrator, "SYSTEM_PROMPT")
    assert hasattr(orchestrator, "INSTRUCTION_TEMPLATE")
    assert hasattr(orchestrator, "SANDBOX_INSTRUCTION")


def test_solver_imports_from_prompts():
    """B122: Verify solver.py imports VICTORY_HYPOTHESIS_TEMPLATE."""
    from agents.arc3 import solver

    # VictoryHypothesizer should use the imported template
    assert hasattr(solver, "VictoryHypothesizer")
    assert hasattr(solver.VictoryHypothesizer, "PROMPT_TEMPLATE")

    # The PROMPT_TEMPLATE should be the same as VICTORY_HYPOTHESIS_TEMPLATE
    assert solver.VictoryHypothesizer.PROMPT_TEMPLATE == prompts.VICTORY_HYPOTHESIS_TEMPLATE


@pytest.mark.asyncio
async def test_system_prompt_formatting():
    """B122: SYSTEM_PROMPT should format correctly with available_actions."""
    actions = ["ACTION1", "ACTION2", "ACTION3"]
    formatted = prompts.SYSTEM_PROMPT.format(available_actions=', '.join(actions))
    assert "ACTION1, ACTION2, ACTION3" in formatted
    assert "SYSTEM:" in formatted


@pytest.mark.asyncio
async def test_instruction_template_formatting():
    """B122: INSTRUCTION_TEMPLATE should format correctly with effect_summary."""
    effect_summary = "Test effect summary"
    formatted = prompts.INSTRUCTION_TEMPLATE.format(effect_summary=effect_summary)
    assert effect_summary in formatted
    assert "INSTRUCTION:" in formatted


@pytest.mark.asyncio
async def test_victory_template_formatting():
    """B122: VICTORY_HYPOTHESIS_TEMPLATE should format correctly with all placeholders."""
    test_data = {
        "archetype": "race",
        "object_roles": "player, goal, wall",
        "past_plans": "move right, reach goal",
        "lessons": "walls block movement",
        "reward_summary": "last 5 rewards: [0, 1, 0, 0, 0]",
    }
    formatted = prompts.VICTORY_HYPOTHESIS_TEMPLATE.format(**test_data)
    assert "race" in formatted
    assert "player" in formatted
    assert "condition_type" in formatted
