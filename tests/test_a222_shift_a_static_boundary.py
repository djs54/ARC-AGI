"""A222: static, no-live-run-required enforcement of Shift A (agents/arc4/perceive.py,
plan_vetter.py, evaluator.py must never invoke an LLM). Mirrors
tests/test_import_boundary.py's pattern exactly -- same reasoning: catch a boundary
violation with a fast grep-based check instead of requiring a live smoke trace
(scripts/check_compliance_violations.py) to exist before the violation is visible.

A221 Finding 4: no live trace existed to mechanically confirm A218/A219 were
Shift-A-clean; this test closes that gap going forward, at PR time, without
needing a `make smoke` run at all."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The three phases Shift A names as strictly deterministic (ARCHITECTURE.md
# "Graph-Engineering Principles" section). annatar_signals.py is deliberately
# NOT included here -- Annatar is a core-agent reasoning layer (Shift B),
# analogous to resolve/plan, with its own already-shipped, intentional bounded
# LLM escalation tier (resolve_llm_vote). goal_resolver.py and plan_generator.py
# are also deliberately excluded -- Shift A explicitly permits them to invoke
# an LLM behind a deterministic escalation gate.
DETERMINISTIC_PHASE_FILES = [
    ROOT / "agents" / "arc4" / "perceive.py",
    ROOT / "agents" / "arc4" / "plan_vetter.py",
    ROOT / "agents" / "arc4" / "evaluator.py",
]

# The one shape an LLM call takes in this codebase (agents/arc4/ports.py's
# LLMPort protocol: a `.chat(...)` method, threaded through as an `llm_port`
# parameter). Any of these three appearing in a deterministic-phase file means
# something bypassed the phase boundary.
FORBIDDEN_REGEXES = [
    r"\bLLMPort\b",
    r"\bllm_port\b",
    r"\.chat\(",
]


def _find_violations() -> list[str]:
    violations: list[str] = []
    for path in DETERMINISTIC_PHASE_FILES:
        if not path.exists():
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in FORBIDDEN_REGEXES:
                if re.search(pattern, line):
                    violations.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    return violations


def test_deterministic_phases_never_reference_llm_port():
    violations = _find_violations()
    assert violations == [], (
        "Shift A violation: agents/arc4/perceive.py, plan_vetter.py, and "
        "evaluator.py must never reference LLMPort/llm_port/.chat( -- these "
        "phases must be strictly deterministic (ARCHITECTURE.md Graph-Engineering "
        f"Principles). Found:\n" + "\n".join(violations)
    )
